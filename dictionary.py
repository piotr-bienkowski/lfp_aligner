"""
Dictionary management for hunalign.

hunalign dictionary format
--------------------------
  target_word @ source_word

one entry per line, UTF-8.  "null.dic" is an empty placeholder used when no
dictionary is available.

This module handles:
  1. Finding a pre-built dictionary for a language pair (tries both orderings).
  2. Generating a dictionary on-the-fly from raw single-language word lists.
  3. Falling back to null.dic when no data is available.
"""

from __future__ import annotations

from pathlib import Path

from .utils import get_logger


def select_dictionary(lang1: str,
                      lang2: str,
                      dic_dir: str | Path,
                      raw_dir: str | Path) -> Path:
    """
    Return the path to the best available dictionary for the *lang1*/*lang2*
    pair, generating one from raw word lists if necessary.

    Search order
    ------------
    1. ``<dic_dir>/<lang1>-<lang2>.dic``
    2. ``<dic_dir>/<lang2>-<lang1>.dic``
    3. Generate from ``<raw_dir>/<lang1>.txt`` + ``<raw_dir>/<lang2>.txt``
    4. ``<dic_dir>/null.dic`` (empty dictionary)
    """
    dic_dir = Path(dic_dir)
    raw_dir = Path(raw_dir)
    log     = get_logger()

    # 1 — direct match
    direct = dic_dir / f"{lang1}-{lang2}.dic"
    if direct.exists():
        log.info("Dictionary: %s", direct)
        return direct

    # 2 — reversed
    reversed_ = dic_dir / f"{lang2}-{lang1}.dic"
    if reversed_.exists():
        log.info("Dictionary (reversed): %s", reversed_)
        return reversed_

    # 3 — generate from raw word lists
    raw1 = raw_dir / f"{lang1}.txt"
    raw2 = raw_dir / f"{lang2}.txt"
    if raw1.exists() and raw2.exists():
        generated = dic_dir / f"{lang1}-{lang2}.dic"
        count = build_dictionary(raw1, raw2, generated)
        log.info("Generated %s with %d entries", generated, count)
        return generated

    # 4 — null dictionary
    null = dic_dir / "null.dic"
    if not null.exists():
        if not dic_dir.is_dir():
            raise RuntimeError(
                f"Dictionary directory not found: {dic_dir}\n"
                "Check that --script-dir (or 'Aligner dir' in the GUI) points "
                "to the LF Aligner installation directory."
            )
        null.write_text("", encoding="utf-8")
    log.warning("No dictionary found for %s-%s, using null.dic", lang1, lang2)
    return null


def build_dictionary(src_wordlist: str | Path,
                     tgt_wordlist: str | Path,
                     out_path: str | Path) -> int:
    """
    Build a hunalign dictionary by pairing lines positionally.

    hunalign format: ``target_word @ source_word``
    (note: target first, then source — the reversed order is intentional,
    matching the Perl script's comment "hunalign takes dictionaries in
    reverse order!")

    Returns the number of unique entries written.
    """
    src_wordlist = Path(src_wordlist)
    tgt_wordlist = Path(tgt_wordlist)
    out_path     = Path(out_path)

    src_lines = src_wordlist.read_text(encoding="utf-8").splitlines()
    tgt_lines = tgt_wordlist.read_text(encoding="utf-8").splitlines()

    seen:  set[str] = set()
    entries: list[str] = []

    for src_word, tgt_word in zip(src_lines, tgt_lines):
        src_word = src_word.strip()
        tgt_word = tgt_word.strip()
        if not src_word or not tgt_word:
            continue
        entry = f"{tgt_word} @ {src_word}"
        if entry not in seen:
            seen.add(entry)
            entries.append(entry)

    out_path.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return len(entries)
