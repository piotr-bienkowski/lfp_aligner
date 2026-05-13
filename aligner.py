"""
Hunalign wrapper and large-file chopping (Python 3 port of partialAlign.py).

hunalign invocation
-------------------
  hunalign -text <dic_file> <src_file> <tgt_file>  →  stdout (tab-delimited)

Large-file mode
---------------
When the source file has more lines than *chop_threshold* (default 15 000),
the files are split into chunks using the anchor-word algorithm from
partialAlign.py (Python 2, ported here to Python 3).  Each chunk is aligned
separately with ``hunalign -text``, and the results are concatenated.

Output format
-------------
  source_segment\ttarget_segment\tconfidence_score
one aligned pair per line.  Empty records (two leading tabs) are cleaned up
by :func:`parse_aligned`.
"""

from __future__ import annotations

import itertools
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .utils import get_logger, safe_rename


# ---------------------------------------------------------------------------
# hunalign binary detection
# ---------------------------------------------------------------------------

def _default_hunalign_bin(script_dir: str | Path) -> str:
    """
    Return the path to the hunalign binary.

    Candidate names are tried in order:
      1. Platform-specific name  (hunalign_linux / hunalign_mac / hunalign.exe)
      2. Plain 'hunalign' (or 'hunalign.exe' on Windows) — covers self-built
         binaries compiled from source, which default to this name.
      3. 'hunalign' on the system PATH as a last resort.
    """
    import platform
    script_dir = Path(script_dir)
    system = platform.system()

    if system == "Windows":
        candidates = ["hunalign.exe"]
    elif system == "Darwin":
        candidates = ["hunalign_mac", "hunalign"]
    else:
        candidates = ["hunalign_linux", "hunalign"]

    hun_dir = script_dir / "scripts" / "hunalign"
    for name in candidates:
        candidate = hun_dir / name
        if candidate.exists():
            get_logger().debug("hunalign binary: %s", candidate)
            return str(candidate)

    # Fall back to PATH
    get_logger().warning(
        "hunalign binary not found in %s — falling back to PATH", hun_dir)
    return "hunalign"


# ---------------------------------------------------------------------------
# Core hunalign call
# ---------------------------------------------------------------------------

def run_hunalign(src_file:     str | Path,
                 tgt_file:     str | Path,
                 dic_file:     str | Path,
                 out_file:     str | Path,
                 hunalign_bin: str = "hunalign",
                 realign:      bool = False) -> None:
    """
    Run hunalign on a single pair of files.

    Parameters
    ----------
    src_file, tgt_file:
        Plain-text files, one sentence per line, UTF-8.
    dic_file:
        Bilingual dictionary (target @ source format).
    out_file:
        Destination for hunalign's tab-delimited output.
    hunalign_bin:
        Path to the hunalign executable.
    realign:
        If True, pass ``-realign`` to hunalign for a three-phase alignment
        (initial alignment → auto-build dictionary → re-align).  Produces
        better quality at roughly triple the runtime.
    """
    cmd = [
        str(hunalign_bin),
        "-text",
    ]
    if realign:
        cmd.append("-realign")
    cmd += [
        str(dic_file),
        str(src_file),
        str(tgt_file),
    ]
    get_logger().debug("hunalign: %s", " ".join(cmd))

    with open(out_file, "w", encoding="utf-8") as fout:
        result = subprocess.run(
            cmd, stdout=fout, stderr=subprocess.PIPE,
            text=True, encoding="utf-8"
        )

    # hunalign generates an empty translate.txt in cwd — remove it
    junk = Path("translate.txt")
    if junk.exists() and junk.stat().st_size == 0:
        junk.unlink(missing_ok=True)

    if result.returncode != 0:
        get_logger().warning("hunalign stderr: %s", result.stderr.strip())


# ---------------------------------------------------------------------------
# Python 3 port of partialAlign.py (Python 2 original by MOKK / BME)
# ---------------------------------------------------------------------------

def _token_freq(corpus: list[list[str]]) -> dict[str, int]:
    freq: dict[str, int] = {}
    for line in corpus:
        for token in line:
            freq[token] = freq.get(token, 0) + 1
    return freq


def _hapaxes(freq: dict[str, int]) -> set[str]:
    return {token for token, cnt in freq.items() if cnt == 1}


def _hapax_positions(hap_set: set[str],
                     corpus: list[list[str]]) -> dict[str, int]:
    positions: dict[str, int] = {}
    for idx, line in enumerate(corpus):
        for token in line:
            if token in hap_set:
                positions[token] = idx
    return positions


def _uniq_sort(lst: list) -> list:
    return [p for p, _ in itertools.groupby(sorted(lst))]


def _less(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[0] and a[1] < b[1]


def _maximal_chain(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    lattice: dict[tuple[int, int], tuple[int, Optional[tuple[int, int]]]] = {}
    for p in pairs:
        best_len = 0
        best_pred = None
        for q in pairs:
            if _less(q, p):
                length, _ = lattice[q]
                if best_len < length + 1:
                    best_len = length + 1
                    best_pred = q
        lattice[p] = (best_len, best_pred)

    _, best_p = max((lattice[p][0], p) for p in pairs)
    chain: list[tuple[int, int]] = []
    cur: Optional[tuple[int, int]] = best_p
    while cur is not None:
        chain.append(cur)
        _, cur = lattice[cur]
    chain.reverse()
    return chain


def _select_from_chain(chain:            list[tuple[int, int]],
                       max_chunk_size:   int
                       ) -> list[tuple[int, int]]:
    filtered = []
    cursor   = chain[0]
    filtered.append(cursor)

    for idx, p in enumerate(chain):
        if idx == 0:
            continue
        if (p[0] - cursor[0] > max_chunk_size
                or p[1] - cursor[1] > max_chunk_size):
            last = chain[idx - 1] if idx > 0 else (0, 0)
            if last != cursor:
                filtered.append(last)
            else:
                filtered.append(p)  # forced: can't obey maximalChunkSize
            cursor = filtered[-1]

    if filtered[-1] != chain[-1]:
        filtered.append(chain[-1])
    return filtered


def _chop_files(src_file:   Path,
                tgt_file:   Path,
                work_dir:   Path,
                lang1:      str,
                lang2:      str,
                chunk_size: int = 14000
                ) -> list[tuple[Path, Path, Path]]:
    """
    Split *src_file* and *tgt_file* into chunks using the anchor-word
    algorithm.  Returns a list of (src_chunk, tgt_chunk, out_chunk) triples.
    """
    log = get_logger()

    src_corpus = [l.strip().split()
                  for l in src_file.read_text(encoding="utf-8").splitlines()]
    tgt_corpus = [l.strip().split()
                  for l in tgt_file.read_text(encoding="utf-8").splitlines()]

    src_freq = _token_freq(src_corpus)
    tgt_freq = _token_freq(tgt_corpus)
    src_hap  = _hapaxes(src_freq)
    tgt_hap  = _hapaxes(tgt_freq)
    common   = src_hap & tgt_hap

    src_pos = _hapax_positions(common, src_corpus)
    tgt_pos = _hapax_positions(common, tgt_corpus)

    pairs: list[tuple[int, int]] = [(src_pos[t], tgt_pos[t]) for t in common]
    pairs.append((0, 0))
    pairs.append((len(src_corpus), len(tgt_corpus)))
    pairs = _uniq_sort(pairs)

    log.info("Computing maximal chain for chopping (%d pairs)…", len(pairs))
    chain = _maximal_chain(pairs)
    log.info("Chain length: %d", len(chain))

    if chunk_size > 0:
        chain = _select_from_chain(chain, chunk_size)
        log.info("After filtering: %d chunks", len(chain))

    def _join_lines(corpus_slice: list[list[str]]) -> str:
        return "\n".join(" ".join(tokens) for tokens in corpus_slice) + "\n"

    chunks: list[tuple[Path, Path, Path]] = []
    last = (0, 0)
    for idx, pos in enumerate(chain, start=1):
        if pos == last:
            continue
        base    = work_dir / f"aligned_part_{idx}"
        src_out = base.with_suffix(f".{lang1}")
        tgt_out = base.with_suffix(f".{lang2}")
        aln_out = base.with_suffix(".align")

        src_out.write_text(_join_lines(src_corpus[last[0]:pos[0]]),
                           encoding="utf-8")
        tgt_out.write_text(_join_lines(tgt_corpus[last[1]:pos[1]]),
                           encoding="utf-8")
        chunks.append((src_out, tgt_out, aln_out))
        last = pos

    return chunks


# ---------------------------------------------------------------------------
# Main alignment function
# ---------------------------------------------------------------------------

def align(src_file:      str | Path,
          tgt_file:      str | Path,
          dic_file:      str | Path,
          out_file:      str | Path,
          hunalign_bin:  str = "hunalign",
          chop_threshold: int = 15000,
          realign:        bool = False) -> Path:
    """
    Align two plain-text files with hunalign.

    Uses normal mode for small files and automatic chopping for large ones.

    Parameters
    ----------
    src_file, tgt_file:
        One sentence per line, UTF-8.
    dic_file:
        hunalign dictionary (target @ source).
    out_file:
        Destination for the tab-delimited aligned output.
    hunalign_bin:
        Path to hunalign executable.
    chop_threshold:
        If source file has more lines than this, use chopping mode.
        Set to 0 to disable chopping.

    Returns
    -------
    Path to *out_file*.
    """
    src_file = Path(src_file)
    tgt_file = Path(tgt_file)
    out_file = Path(out_file)
    log      = get_logger()

    src_lines = sum(1 for _ in src_file.open("rb"))

    if chop_threshold and src_lines >= chop_threshold:
        log.info("Chopping mode: %d lines ≥ threshold %d",
                 src_lines, chop_threshold)
        _align_chunked(src_file, tgt_file, dic_file, out_file,
                       hunalign_bin, chop_threshold, realign)
    else:
        log.info("Normal mode: %d lines", src_lines)
        run_hunalign(src_file, tgt_file, dic_file, out_file, hunalign_bin,
                     realign)

    # Sanity check
    if not out_file.exists() or out_file.stat().st_size == 0:
        raise RuntimeError(
            f"hunalign produced an empty output file ({out_file}).  "
            "The input files may be empty or too different."
        )

    log.info("Aligned file: %s (%d bytes)", out_file, out_file.stat().st_size)
    return out_file


def _align_chunked(src_file:     Path,
                   tgt_file:     Path,
                   dic_file:     Path | str,
                   out_file:     Path,
                   hunalign_bin: str,
                   chunk_size:   int,
                   realign:      bool = False) -> None:
    """Split, align each chunk, concatenate results."""
    log = get_logger()

    with tempfile.TemporaryDirectory(prefix="lfa_chop_") as tmpdir:
        work = Path(tmpdir)
        lang1 = "L1"
        lang2 = "L2"

        chunks = _chop_files(src_file, tgt_file, work,
                             lang1, lang2, chunk_size)
        log.info("Chopped into %d chunks", len(chunks))

        with open(out_file, "w", encoding="utf-8") as merged:
            for i, (src_chunk, tgt_chunk, aln_chunk) in enumerate(chunks, 1):
                log.info("Aligning chunk %d/%d…", i, len(chunks))
                run_hunalign(src_chunk, tgt_chunk, dic_file,
                             aln_chunk, hunalign_bin, realign)
                if aln_chunk.exists():
                    merged.write(aln_chunk.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Parse hunalign output into Python tuples
# ---------------------------------------------------------------------------

def parse_aligned(path: str | Path) -> list[tuple[str, ...]]:
    """
    Read a hunalign output file and return a list of tuples.

    Each line:  source\\ttarget\\tconfidence
    Empty records (both source and target empty) are dropped.
    """
    pairs: list[tuple[str, ...]] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        # hunalign always emits at least 3 columns; guard anyway
        while len(parts) < 2:
            parts.append("")
        src = parts[0]
        tgt = parts[1]
        rest = tuple(parts[2:])
        # Skip empty records that hunalign places at the end
        if not src.strip() and not tgt.strip():
            continue
        pairs.append((src, tgt) + rest)
    return pairs
