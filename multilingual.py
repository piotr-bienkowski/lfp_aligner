"""
Iterative N-way alignment for 3 or more languages.

Since hunalign is strictly bilingual, multi-language alignment is done by
chaining pairwise alignments:

  align(L0, L1)  →  aligned_01   (cols: L0, L1)
  extract L1 column from aligned_01  →  L1_extracted
  align(L1_extracted, L2)  →  aligned_12   (cols: L1, L2)
  reconcile rows of aligned_01 with aligned_12  →  add L2 column
  repeat for L3, L4, …

The hard part is row reconciliation: hunalign may merge several source
segments into one target segment (marked with ~~~) or stretch them apart
(empty cells).  This module undoes those transformations so all languages
share the same row count.

Mirrors the logic in LF_aligner_4.24.pl lines 2529–2634.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .aligner import align, parse_aligned
from .dictionary import select_dictionary
from .filters import cleanup, remove_empty
from .utils import get_logger, write_utf8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_column(pairs: list[tuple[str, ...]], col: int) -> list[str]:
    """Return the col-th element of each tuple (empty string if missing)."""
    return [p[col] if col < len(p) else "" for p in pairs]


def _write_temp(lines: list[str], work_dir: Path, name: str) -> Path:
    path = work_dir / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# ~~~ expansion: undo hunalign's segment merging in the L1 column
# ---------------------------------------------------------------------------

def _expand_tilde_merges(pairs: list[tuple[str, ...]],
                          l1_col: int = 0) -> list[tuple[str, ...]]:
    """
    If hunalign merged several L1 segments into one output row using ~~~,
    split them back into separate rows.

    Example:
      ("seg1 ~~~ seg2", "target") → ("seg1", "target"), ("seg2", "")

    Mirrors the Perl regex: s/^([^\t]*) ~~~ ([^\t]*)\t(.*)$/$1\t$3\n$2\t/
    Applied repeatedly until no ~~~ remain in the L1 column.
    """
    changed = True
    while changed:
        changed = False
        expanded: list[tuple[str, ...]] = []
        for row in pairs:
            l1 = row[l1_col] if l1_col < len(row) else ""
            if " ~~~ " in l1:
                parts = l1.split(" ~~~ ", 1)
                rest  = row[l1_col + 1:]
                # First sub-row: first L1 fragment + all other columns
                row1 = row[:l1_col] + (parts[0],) + rest
                # Second sub-row: second L1 fragment, other cols empty
                empty_rest = tuple("" for _ in rest)
                row2 = row[:l1_col] + (parts[1],) + empty_rest
                expanded.append(row1)
                expanded.append(row2)
                changed = True
            else:
                expanded.append(row)
        pairs = expanded
    return pairs


# ---------------------------------------------------------------------------
# Stretch merging: join orphan rows back to the previous row
# ---------------------------------------------------------------------------

def _merge_stretched(pairs: list[tuple[str, ...]],
                      key_col: int = 0) -> list[tuple[str, ...]]:
    """
    After hunalign aligns the extracted L1 vs L2, some rows may have an empty
    key (L1) cell.  These are "orphan" rows that hunalign stretched apart.
    Append their content to the previous row, separated by a space.

    Mirrors the Perl loop at lines 2569-2580.
    """
    if not pairs:
        return pairs

    result: list[list[str]] = []

    # Special case: if the very first row has an empty key, defer it
    first_empty = None
    start_idx   = 0
    if not pairs[0][key_col].strip():
        first_empty = list(pairs[0])
        start_idx   = 1

    for idx, row in enumerate(pairs[start_idx:], start=start_idx):
        row_list = list(row)
        if not row_list[key_col].strip():
            # Append to previous row
            if result:
                for c in range(len(result[-1])):
                    if c < len(row_list) and row_list[c].strip():
                        result[-1][c] = (result[-1][c] + " " + row_list[c]).strip()
        else:
            if first_empty and idx == start_idx:
                # Merge deferred first row into this row
                for c in range(len(row_list)):
                    if c < len(first_empty) and first_empty[c].strip():
                        row_list[c] = (first_empty[c] + " " + row_list[c]).strip()
                first_empty = None
            result.append(row_list)

    return [tuple(r) for r in result]


# ---------------------------------------------------------------------------
# Align N languages
# ---------------------------------------------------------------------------

def align_multilingual(input_files:   list[str | Path],
                       langs:         list[str],
                       dic_dir:       str | Path,
                       raw_dir:       str | Path,
                       hunalign_bin:  str = "hunalign",
                       chop_threshold: int = 15000) -> list[tuple[str, ...]]:
    """
    Align 3 or more language files iteratively.

    Parameters
    ----------
    input_files:
        One plain-text file per language (one sentence per line, UTF-8).
        Must be the same length as *langs*.
    langs:
        ISO 639-1 language codes in the same order as *input_files*.
    dic_dir, raw_dir:
        Paths to hunalign dictionary and raw word-list directories.
    hunalign_bin:
        Path to hunalign executable.
    chop_threshold:
        Line count above which chopping mode is used (0 = disabled).

    Returns
    -------
    List of tuples, one per aligned row.  Each tuple has len(*langs*) elements.
    """
    if len(input_files) < 3:
        raise ValueError("align_multilingual requires at least 3 files.")
    if len(input_files) != len(langs):
        raise ValueError("Number of files must equal number of languages.")

    log      = get_logger()
    input_files = [Path(f) for f in input_files]
    dic_dir  = Path(dic_dir)
    raw_dir  = Path(raw_dir)

    with tempfile.TemporaryDirectory(prefix="lfa_multi_") as tmpdir:
        work = Path(tmpdir)

        # ----------------------------------------------------------------
        # Step 1: align L0 vs L1
        # ----------------------------------------------------------------
        dic01  = select_dictionary(langs[0], langs[1], dic_dir, raw_dir)
        aln01  = work / "aligned_01.txt"
        log.info("Aligning %s vs %s…", langs[0], langs[1])
        align(input_files[0], input_files[1], dic01, aln01,
              hunalign_bin, chop_threshold)

        pairs = parse_aligned(aln01)
        pairs = _expand_tilde_merges(pairs, l1_col=0)
        pairs = _merge_stretched(pairs, key_col=0)
        pairs = remove_empty(pairs)
        pairs = cleanup(pairs)

        # current columns: L0, L1[, confidence]
        # drop confidence
        pairs = [(p[0], p[1]) for p in pairs]

        # ----------------------------------------------------------------
        # Steps 2…N: align previous L(i-1) extracted vs L(i)
        # ----------------------------------------------------------------
        for loop_idx, (lang_next, file_next) in enumerate(
                zip(langs[2:], input_files[2:]), start=2):

            lang_prev = langs[loop_idx - 1]

            # Extract the L(i-1) column to use as input for the next round
            prev_col = _extract_column(pairs, col=1)
            prev_file = _write_temp(prev_col, work, f"extracted_{loop_idx}.txt")

            dic_next = select_dictionary(lang_prev, lang_next, dic_dir, raw_dir)
            aln_next = work / f"aligned_{loop_idx:02d}.txt"

            log.info("Aligning %s vs %s (loop %d)…", lang_prev, lang_next, loop_idx)
            align(prev_file, file_next, dic_next, aln_next,
                  hunalign_bin, chop_threshold)

            new_pairs = parse_aligned(aln_next)

            # Undo ~~~ merging in the extracted-L(i-1) column
            new_pairs = _expand_tilde_merges(new_pairs, l1_col=0)
            # Merge back rows that hunalign stretched
            new_pairs = _merge_stretched(new_pairs, key_col=0)
            new_pairs = remove_empty(new_pairs)
            new_pairs = cleanup(new_pairs)

            # Drop confidence; keep only (prev_col, new_lang_col)
            new_pairs = [(p[0], p[1]) for p in new_pairs]

            # Reconcile row count with the accumulated result
            pairs = _reconcile_columns(pairs, new_pairs)

        return pairs


def _reconcile_columns(accumulated:  list[tuple[str, ...]],
                        new_pairs:   list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """
    Merge a newly aligned pair-list into the accumulated multi-column result.

    The second column of *accumulated* (= the extracted L(i-1)) should match
    the first column of *new_pairs*.  Append the second column of *new_pairs*
    (= L(i)) as a new column in the result.

    Where the row counts differ (due to remaining alignment asymmetries),
    we fall back to positional merging, padding shorter sides with empty strings.
    """
    log = get_logger()

    acc_len = len(accumulated)
    new_len = len(new_pairs)

    if acc_len != new_len:
        log.warning(
            "Row count mismatch during multilingual reconciliation: "
            "%d (accumulated) vs %d (new).  Padding shorter side.",
            acc_len, new_len
        )

    result: list[tuple[str, ...]] = []
    for i in range(max(acc_len, new_len)):
        acc_row = accumulated[i] if i < acc_len else tuple("" for _ in accumulated[0])
        new_row = new_pairs[i]   if i < new_len else ("", "")
        # Append the new language column to the accumulated row
        result.append(acc_row + (new_row[1],))

    return result
