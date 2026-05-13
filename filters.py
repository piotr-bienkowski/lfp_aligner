"""
Post-alignment filters applied to the list of (source, target[, …]) tuples
that hunalign produces.

Filters (in application order)
--------------------------------
1. cleanup          — remove ~~~ merge markers and leading hyphens
2. remove_confidence — strip the third tab-column (confidence score)
3. remove_duplicates — hash-based deduplication on the first two columns
4. remove_untranslated — drop rows where source == target or source is numbers-only
"""

from __future__ import annotations

import re
from typing import Callable

from .utils import get_logger


# ---------------------------------------------------------------------------
# Individual filters
# ---------------------------------------------------------------------------

def cleanup(pairs: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """
    Remove ~~~ inserted by hunalign at merged-segment boundaries, and strip
    leading "- " from segments (list-item artefacts).
    """
    result = []
    for row in pairs:
        cleaned = []
        for i, cell in enumerate(row):
            cell = cell.replace(" ~~~", "").replace("~~~", "")
            if i == 0:
                cell = re.sub(r"^- ", "", cell)
            else:
                cell = re.sub(r"^- ", "", cell)
            cleaned.append(cell)
        result.append(tuple(cleaned))
    return result


def remove_confidence(pairs: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """
    Drop the third column (hunalign confidence score) from every row.
    Rows with fewer than three columns are returned unchanged.
    """
    result = []
    for row in pairs:
        if len(row) >= 3:
            result.append((row[0], row[1]) + row[3:])
        else:
            result.append(row)
    return result


def remove_duplicates(pairs: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """
    Remove rows where the first two columns have already been seen.
    Only considers the first two columns (source + target), matching the Perl
    behaviour.  Disabled automatically for 3+ language files.
    """
    seen:   set[tuple[str, str]] = set()
    result: list[tuple[str, ...]] = []

    for row in pairs:
        key = (row[0].strip(), row[1].strip()) if len(row) >= 2 else (row[0].strip(), "")
        if key not in seen:
            seen.add(key)
            result.append(row)

    before = len(pairs)
    after  = len(result)
    if before != after:
        get_logger().info("Deduplication: %d → %d rows", before, after)

    return result


_NUMBERS_ONLY = re.compile(r"^[\d\s\W]+$")


def remove_untranslated(pairs: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """
    Drop rows where:
      - source text == target text (identical in both languages)
      - source text consists entirely of numbers / whitespace / punctuation
    Only applied to bilingual (2-column) files.
    """
    result = []
    for row in pairs:
        src = row[0].strip()
        tgt = row[1].strip() if len(row) >= 2 else ""
        if src == tgt:
            continue
        if _NUMBERS_ONLY.fullmatch(src):
            continue
        result.append(row)

    before = len(pairs)
    after  = len(result)
    if before != after:
        get_logger().info("Untranslated filter: %d → %d rows", before, after)

    return result


def remove_empty(pairs: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """
    Drop rows where both source and target are empty (hunalign artefact at EOF).
    """
    return [row for row in pairs
            if any(cell.strip() for cell in row)]


# ---------------------------------------------------------------------------
# Apply a filter pipeline
# ---------------------------------------------------------------------------

FilterFn = Callable[[list[tuple[str, ...]]], list[tuple[str, ...]]]


def apply_filters(pairs:                list[tuple[str, ...]],
                  do_cleanup:          bool = True,
                  do_remove_confidence: bool = True,
                  do_remove_dupes:     bool = False,
                  do_remove_untranslated: bool = False,
                  num_langs:           int  = 2) -> list[tuple[str, ...]]:
    """
    Convenience function: apply the standard filter pipeline.

    Parameters
    ----------
    num_langs:
        Number of languages in *pairs*.  Dedup and untranslated filters are
        disabled for num_langs > 2, matching the original Perl behaviour.
    """
    pairs = remove_empty(pairs)

    if do_cleanup:
        pairs = cleanup(pairs)

    # For multilingual files the confidence column is always removed
    if do_remove_confidence or num_langs > 2:
        pairs = remove_confidence(pairs)

    if do_remove_dupes and num_langs == 2:
        pairs = remove_duplicates(pairs)

    if do_remove_untranslated and num_langs == 2:
        pairs = remove_untranslated(pairs)

    return pairs
