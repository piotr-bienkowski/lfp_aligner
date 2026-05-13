"""
Main alignment pipeline.

Orchestrates the full LF Aligner workflow:

  1. Read input files (txt / pdf / docx / html)
  2. Normalise text (newlines, BOM, bullets, whitespace)
  3. Optionally sentence-segment
  4. Write normalised temp files
  5. Select / generate hunalign dictionary
  6. Run hunalign (with chopping if needed)
  7. Apply post-alignment filters
  8. Write output files (TXT and/or TMX)

For 3+ languages the multilingual module is used instead of the direct
pairwise call.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .aligner import align, parse_aligned, _default_hunalign_bin
from .config import AlignmentConfig
from .dictionary import select_dictionary
from .filters import apply_filters
from .io.readers import read_input
from .io.writers import write_tmx, write_txt, write_xlsx
from .multilingual import align_multilingual
from .segmenter import segment, should_use_segmented
from .utils import apply_charconv, get_logger, log_timestamp, write_utf8


# ---------------------------------------------------------------------------
# Text normalisation (mirrors the Perl pre-processing passes)
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(r"([\●►•])")
_MULTI_SPACE_RE = re.compile(r"  +")
_PIPE_RE = re.compile(r"^[\s|]+|[\s|]+$")


def normalise_lines(lines: list[str], lang: str = "",
                    merge_numbers: bool = False) -> list[str]:
    """
    Apply all pre-segmentation normalisations to a list of raw lines.

    Steps (matching the Perl passes):
      - Remove UTF-8 BOM (handled by the reader's utf-8-sig encoding)
      - Normalise CRLF → LF (handled by the reader)
      - Convert bullet characters to newline + bullet
      - Replace non-breaking spaces (U+00A0) with regular spaces
      - Remove form-feeds (already done in pdf reader)
      - Collapse multiple spaces
      - Strip leading/trailing whitespace and pipe characters
      - Drop purely empty lines
    """
    result: list[str] = []

    for line in lines:
        # Bullets: insert line break before each bullet character
        line = _BULLET_RE.sub(r"\n\1", line)

        # Non-breaking space → regular space
        line = line.replace("\u00a0", " ")

        # Remove private-use character U+F022
        line = line.replace("\uf0b7", "\n")

        # Form feed → line break (safety net)
        line = line.replace("\f", "\n")

        # Expand any newly introduced line breaks
        for sub in line.split("\n"):
            sub = _MULTI_SPACE_RE.sub(" ", sub)
            sub = _PIPE_RE.sub("", sub)
            if sub:
                result.append(sub)

    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _find_script_dir() -> Path:
    """
    Search for the LF Aligner installation directory (the one that contains
    scripts/hunalign/) by checking a set of candidate locations relative to
    this file.  Raises RuntimeError with a helpful message if none is found.
    """
    pkg = Path(__file__).parent   # /home/piotr/lfp_aligner/

    candidates = [
        pkg / "aligner",              # package_dir/aligner/
        pkg.parent / "aligner",       # parent_dir/aligner/   (most common layout)
        pkg.parent,                   # parent_dir/           (aligner IS the parent)
        pkg,                          # package_dir itself
    ]

    for candidate in candidates:
        if (candidate / "scripts" / "hunalign").is_dir():
            return candidate

    searched = "\n  ".join(str(c) for c in candidates)
    raise RuntimeError(
        "Could not auto-detect the LF Aligner installation directory.\n"
        "Searched:\n  " + searched + "\n"
        "Please set the 'Aligner dir' field in the GUI Run tab, or pass "
        "--script-dir on the command line."
    )


@dataclass
class RunResult:
    txt_path:  Optional[Path] = None
    tmx_path:  Optional[Path] = None
    xlsx_path: Optional[Path] = None
    pairs:     list[tuple[str, ...]] = field(default_factory=list)
    seg_counts_before: list[int] = field(default_factory=list)
    seg_counts_after:  list[int] = field(default_factory=list)


def run(input_files:   list[str | Path],
        langs:         list[str],
        output_dir:    str | Path,
        tmx_langs:     Optional[list[str]] = None,
        cfg:           Optional[AlignmentConfig] = None,
        script_dir:    Optional[str | Path] = None,
        output_stem:   str = "aligned",
        antiword_bin:  str = "antiword",
        abiword_bin:   str = "abiword") -> RunResult:
    """
    Run the full alignment pipeline.

    Parameters
    ----------
    input_files:
        Source files, one per language.
    langs:
        ISO 639-1 language codes, same order as *input_files*.
    output_dir:
        Directory where output files are written.
    tmx_langs:
        BCP-47 language codes for the TMX header (e.g. ["en-GB", "pl-PL"]).
        Defaults to *langs* uppercased if not provided.
    cfg:
        AlignmentConfig (loaded from setup file or constructed programmatically).
        Defaults to AlignmentConfig() if not provided.
    script_dir:
        Root of the LF Aligner installation (parent of the ``scripts/``
        directory that contains hunalign, dictionaries, etc.).
    output_stem:
        Base name (without extension) for output files.

    Returns
    -------
    RunResult with paths to generated files and the aligned pairs.
    """
    if cfg is None:
        cfg = AlignmentConfig()

    log = get_logger()
    log.info("LF Aligner Python port — %s", log_timestamp())

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    num_langs = len(input_files)
    if num_langs != len(langs):
        raise ValueError("Number of input files must equal number of languages.")

    # Resolve paths to hunalign resources
    if script_dir is None:
        script_dir = _find_script_dir()
    script_dir = Path(script_dir)

    hunalign_bin = _default_hunalign_bin(script_dir)
    dic_dir = script_dir / "scripts" / "hunalign" / "data"
    raw_dir = dic_dir / "raw"
    prefix_dir = (script_dir / "scripts" / "sentence_splitter"
                  / "nonbreaking_prefixes")

    log.info("hunalign: %s", hunalign_bin)
    log.info("dic_dir:  %s", dic_dir)

    result = RunResult()

    with tempfile.TemporaryDirectory(prefix="lfa_run_") as tmpdir:
        work = Path(tmpdir)

        # -------------------------------------------------------------------
        # 1. Read and normalise each input file
        # -------------------------------------------------------------------
        raw_lines: list[list[str]] = []
        for i, (fpath, lang) in enumerate(zip(input_files, langs)):
            log.info("Reading file %d (%s): %s", i + 1, lang, fpath)
            lines = read_input(fpath,
                               filetype=cfg.filetype,
                               lang=lang,
                               pdf_layout=cfg.pdf_layout,
                               antiword_bin=antiword_bin,
                               abiword_bin=abiword_bin)
            lines = normalise_lines(lines, lang=lang,
                                    merge_numbers=cfg.merge_numbers_headings)
            raw_lines.append(lines)
            log.info("  %d lines after normalisation", len(lines))
            result.seg_counts_before.append(len(lines))

        # -------------------------------------------------------------------
        # 2. Optionally sentence-segment
        # -------------------------------------------------------------------
        seg_lines: list[list[str]] = []

        if cfg.segment == "n":
            seg_lines = raw_lines
        else:
            for i, (lines, lang) in enumerate(zip(raw_lines, langs)):
                log.info("Segmenting language %d (%s)…", i + 1, lang)
                segs = segment(lines, lang, prefix_dir,
                               merge_numbers_headings=cfg.merge_numbers_headings)
                seg_lines.append(segs)
                log.info("  %d segments after segmentation", len(segs))
                result.seg_counts_after.append(len(segs))

            # Auto-revert if segmentation made things worse
            if cfg.confirm_segmenting == "auto":
                if not should_use_segmented(result.seg_counts_before,
                                            result.seg_counts_after):
                    log.info("Auto-revert: segmentation not beneficial, "
                             "using unsegmented lines.")
                    seg_lines = raw_lines
                    result.seg_counts_after = result.seg_counts_before[:]

        # -------------------------------------------------------------------
        # 3. Apply character conversion if configured
        # -------------------------------------------------------------------
        if cfg.charconv_lang1 and len(seg_lines) >= 1:
            seg_lines[0] = [apply_charconv(l, cfg.charconv_lang1)
                            for l in seg_lines[0]]
        if cfg.charconv_lang2 and len(seg_lines) >= 2:
            seg_lines[1] = [apply_charconv(l, cfg.charconv_lang2)
                            for l in seg_lines[1]]

        # -------------------------------------------------------------------
        # 4. Write temp plain-text files for hunalign
        # -------------------------------------------------------------------
        tmp_files: list[Path] = []
        for i, (lines, lang) in enumerate(zip(seg_lines, langs)):
            p = work / f"input_{i}_{lang}.txt"
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            tmp_files.append(p)

        # -------------------------------------------------------------------
        # 5. Align
        # -------------------------------------------------------------------
        if num_langs == 2:
            dic = select_dictionary(langs[0], langs[1], dic_dir, raw_dir)
            aln_file = work / "aligned.txt"
            align(tmp_files[0], tmp_files[1], dic, aln_file,
                  hunalign_bin, cfg.chop_threshold, cfg.realign)
            pairs = parse_aligned(aln_file)
        else:
            pairs = align_multilingual(
                tmp_files, langs, dic_dir, raw_dir,
                hunalign_bin, cfg.chop_threshold
            )

        # -------------------------------------------------------------------
        # 6. Apply post-alignment filters
        # -------------------------------------------------------------------
        pairs = apply_filters(
            pairs,
            do_cleanup=cfg.cleanup,
            do_remove_confidence=cfg.remove_confidence,
            do_remove_dupes=cfg.delete_dupes,
            do_remove_untranslated=cfg.delete_untranslated,
            num_langs=num_langs,
        )

        result.pairs = pairs
        log.info("Final aligned pairs: %d", len(pairs))

        # -------------------------------------------------------------------
        # 7. Write outputs
        # -------------------------------------------------------------------
        stem = output_stem

        # Tab-delimited text
        txt_path = output_dir / f"{stem}.txt"
        write_txt(pairs, txt_path,
                  source_note=" | ".join(
                      str(Path(f).name) for f in input_files))
        result.txt_path = txt_path
        log.info("TXT written: %s", txt_path)

        # TMX
        if cfg.make_tmx:
            effective_tmx_langs = tmx_langs or [l.upper() for l in langs]
            tmx_path = output_dir / f"{stem}.tmx"
            write_tmx(pairs, tmx_path,
                      langs=effective_tmx_langs,
                      creator_id=cfg.creator_id,
                      skip_half_empty=cfg.skip_half_empty)
            result.tmx_path = tmx_path
            log.info("TMX written: %s", tmx_path)

    return result
