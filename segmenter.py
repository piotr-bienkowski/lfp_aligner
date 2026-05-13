"""
Sentence segmenter — Python port of split-sentences.perl (EuroParl / Moses).

This is a faithful port of the Perl script by Philipp Koehn distributed with
the EuroParl corpus.  The algorithm:

  1. Accumulate lines into paragraphs (blank lines / XML tags are boundaries).
  2. For each paragraph apply a cascade of regex rules to insert \\n at
     sentence boundaries.
  3. Special handling for periods: look up each word's prefix in a per-language
     list of known non-breaking abbreviations.

Additionally implements a CJK segmenter for Chinese and Japanese, mirroring
the manual segmenter in the original Perl script.

Unicode character properties (\\p{Lu}, \\p{Pi}, etc.) require the ``regex``
module (pip install regex).  If it is not available the code falls back to a
simplified ASCII-only heuristic.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

try:
    import regex as _re_unicode
    _HAS_REGEX = True
except ImportError:
    import re as _re_unicode  # type: ignore[no-redef]
    _HAS_REGEX = False

# ---------------------------------------------------------------------------
# Prefix loading
# ---------------------------------------------------------------------------

NUMERIC_ONLY = 2
ALWAYS = 1


def _load_prefixes(lang: str, prefix_dir: Path) -> dict[str, int]:
    """
    Load the nonbreaking-prefix file for *lang*.
    Falls back to English if no language-specific file exists.
    Returns a dict mapping prefix string → ALWAYS (1) or NUMERIC_ONLY (2).
    """
    candidate = prefix_dir / f"nonbreaking_prefix.{lang}"
    if not candidate.exists():
        candidate = prefix_dir / "nonbreaking_prefix.en"
        if not candidate.exists():
            return {}

    prefixes: dict[str, int] = {}
    for line in candidate.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^(.*?)\s+#NUMERIC_ONLY#', line)
        if m:
            prefixes[m.group(1)] = NUMERIC_ONLY
        else:
            prefixes[line] = ALWAYS
    return prefixes


# ---------------------------------------------------------------------------
# EuroParl segmenter
# ---------------------------------------------------------------------------

# Regex fragments, compiled once.  We use the `regex` module when available so
# that Unicode properties (\p{Lu} etc.) work correctly for non-Latin scripts.
# When falling back to stdlib `re`, capital letters are restricted to [A-Z].

if _HAS_REGEX:
    _U  = r"[\p{Lu}]"          # uppercase letter
    _PI = r"[\p{Pi}]"          # initial punctuation (opening quotes)
    _PF = r"[\p{Pf}]"          # final punctuation (closing quotes)
    _AN = r"[\p{L}\p{N}]"      # letter or digit
else:
    _U  = r"[A-Z]"
    _PI = r"[\"']"
    _PF = r"[\"']"
    _AN = r"[A-Za-z0-9]"

_STARTER = rf"['\"\(\[\¿\¡{_PI[1:-1]}]*{_U}"
_STARTER_WITH_PUNCT = rf"['\"\(\[\¿\¡{_PI[1:-1]}]+\s*{_U}"


def _compile(pattern: str) -> re.Pattern:
    """Compile with UNICODE flag (or the regex-module equivalent)."""
    return _re_unicode.compile(pattern, _re_unicode.UNICODE)


_RE_NON_PERIOD_END  = _compile(rf"([?!]) +({_STARTER})")
_RE_MULTI_DOT       = _compile(rf"(\.[\.]+) +({_STARTER})")
_RE_PAREN_QUOTE_END = _compile(rf"([?!\.][ ]*['\"\)\]{_PF[1:-1]}]+) +(\s*{_STARTER})")
_RE_STARTER_PUNCT   = _compile(rf"([?!\.]) +({_STARTER_WITH_PUNCT})")
if _HAS_REGEX:
    _RE_PERIOD_WORD = _compile(rf"({_AN[:-1]}\.\-]*)([\'\"\)\]\%{_PF[1:-1]}]*)(\.+)$")
    _RE_ACRONYM     = _compile(r"(\.)[\p{Lu}\-]+(\.+)$")
    _RE_NEXT_UPPER  = _compile(rf"^[ ]*['\"\(\[\¿\¡{_PI[1:-1]}]*[ ]*[{_U[1:-1]}0-9]")
else:
    _RE_PERIOD_WORD = re.compile(r"([\w\.\-]*)([\'\"\)\]\%]*)(\.+)$")
    _RE_ACRONYM     = re.compile(r"(\.)([A-Z\-]+)(\.+)$")
    _RE_NEXT_UPPER  = re.compile(r"^[ ]*['\"(\[¿¡]*[ ]*[A-Z0-9]")


def _preprocess(text: str, prefixes: dict[str, int]) -> list[str]:
    """
    Apply sentence-split rules to one paragraph (the core of the algorithm).
    Returns a list of sentences.
    """
    # Collapse whitespace
    text = re.sub(r" +", " ", text)
    text = re.sub(r"\n ", "\n", text)
    text = re.sub(r" \n", "\n", text)
    text = text.strip()
    if not text:
        return []

    # --- Rule 1: non-period end of sentence markers ---
    text = _RE_NON_PERIOD_END.sub(r"\1\n\2", text)

    # --- Rule 2: multi-dots followed by sentence starters ---
    text = _RE_MULTI_DOT.sub(r"\1\n\2", text)

    # --- Rule 3: punctuation inside quotes/brackets ---
    text = _RE_PAREN_QUOTE_END.sub(r"\1\n\2", text)

    # --- Rule 4: punctuation + starter-punctuation + uppercase ---
    text = _RE_STARTER_PUNCT.sub(r"\1\n\2", text)

    # --- Rule 5: remaining periods (word by word) ---
    words = text.split(" ")
    result: list[str] = []

    for i, word in enumerate(words):
        if i == len(words) - 1:
            result.append(word)
            break

        m = _RE_PERIOD_WORD.search(word)
        if m:
            prefix       = m.group(1)
            starting_punct = m.group(2)

            if prefix and prefixes.get(prefix) == ALWAYS and not starting_punct:
                pass  # known non-breaker → no split

            elif _RE_ACRONYM.search(word):
                pass  # upper-case acronym (U.S.A.) → no split

            else:
                next_word = words[i + 1]
                if _RE_NEXT_UPPER.match(next_word):
                    # Numeric-only prefix before a number → no split
                    if (prefix
                            and prefixes.get(prefix) == NUMERIC_ONLY
                            and not starting_punct
                            and re.match(r"^[0-9]+", next_word)):
                        pass
                    else:
                        word = word + "\n"

        result.append(word)

    text = " ".join(result)

    # Final cleanup
    text = re.sub(r" +", " ", text)
    text = re.sub(r"\n ", "\n", text)
    text = re.sub(r" \n", "\n", text)
    text = text.strip()

    return [s.strip() for s in text.split("\n") if s.strip()]


def segment_europarl(text: str, lang: str, prefix_dir: str | Path) -> list[str]:
    """
    Split *text* into sentences using EuroParl / Moses rules.

    Each non-blank, non-tag input line is processed independently so that
    line breaks in the source document are always preserved as segment
    boundaries (important for text without end-of-sentence punctuation).
    Lines that contain multiple sentences are still split by the EuroParl
    rules.
    """
    prefix_dir = Path(prefix_dir)
    prefixes   = _load_prefixes(lang, prefix_dir)

    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    sentences: list[str] = []

    for line in lines:
        if re.match(r"^<.+>$", line) or re.match(r"^\s*$", line):
            continue
        sentences.extend(_preprocess(line.strip() + " ", prefixes))

    return sentences


# ---------------------------------------------------------------------------
# CJK segmenter (Chinese / Japanese)
# ---------------------------------------------------------------------------

# CJK sentence-ending punctuation
_CJK_PERIOD = "\u3002"   # 。 ideographic full stop
_CJK_QUESTION = "\uff1f"  # ？ fullwidth question mark
_CJK_EXCL = "\uff01"     # ！ fullwidth exclamation mark
_CJK_ELLIPSIS = "\u2026\u2026"  # …… double ellipsis

# Characters that follow punctuation and should stay on the same segment
if _HAS_REGEX:
    _CJK_CLOSING = _re_unicode.compile(rf"[»\'\"\)\]{_PF[1:-1]}]{{1,2}}")
else:
    _CJK_CLOSING = re.compile(r"[»'\")\]]{1,2}")

_CJK_PUNCT = {_CJK_PERIOD, _CJK_QUESTION, _CJK_EXCL}


def segment_cjk(text: str) -> list[str]:
    """
    Segment Chinese / Japanese text at CJK sentence-ending punctuation.
    Mirrors the manual segmenter in the original Perl script.
    """
    # Handle double ellipsis (…… )
    for pattern, insert in [
        (_re_unicode.compile(rf"(\u2026\u2026)([»\'\"\)\]{_PF[1:-1]}]{{1,2}})") if _HAS_REGEX
         else re.compile(r"(……)([»'\")\]]{1,2})"), r"\1\2\n"),
        (_re_unicode.compile(rf"(\u2026\u2026)([^»\'\"\)\]{_PF[1:-1]}])") if _HAS_REGEX
         else re.compile(r"(……)([^»'\")\]])"), r"\1\n\2"),
    ]:
        text = pattern.sub(insert, text)

    # Punctuation + optional closing chars → break after
    if _HAS_REGEX:
        pat_with_close = _re_unicode.compile(
            rf"([{_CJK_PERIOD}{_CJK_QUESTION}{_CJK_EXCL}?!])"
            rf"([»\'\"\)\]{_PF[1:-1]}]{{1,2}})"
        )
        pat_alone = _re_unicode.compile(
            rf"([{_CJK_PERIOD}{_CJK_QUESTION}{_CJK_EXCL}?!])"
            rf"([^»\'\"\)\]{_PF[1:-1]}])"
        )
    else:
        pat_with_close = re.compile(r"([。？！?!])([»'\")\]]{1,2})")
        pat_alone      = re.compile(r"([。？！?!])([^»'\")\]])")

    text = pat_with_close.sub(r"\1\2\n", text)
    text = pat_alone.sub(r"\1\n\2", text)

    return [s.strip() for s in text.split("\n") if s.strip()]


# ---------------------------------------------------------------------------
# SRX 2.0 segmentation engine
# ---------------------------------------------------------------------------

class SrxSegmenter:
    """
    SRX 2.0 segmentation engine.

    Parses a .srx file and applies language-specific rules to segment text.
    ``cascade="yes"`` semantics: rules from all matching languagerule sets are
    concatenated and applied in order (first-match-wins at each candidate
    break position).

    ``beforebreak`` pattern must match ending exactly at the candidate
    position; ``afterbreak`` pattern must match starting at that position.
    An empty pattern matches unconditionally.
    """

    # Fallback rule: lowercase letter + period + space → break before uppercase.
    # Appended after all SRX rules so every break="no" abbreviation rule wins first.
    _FALLBACK_RULE: tuple = (
        True,
        _re_unicode.compile(r"\p{Ll}\.\s" if _HAS_REGEX else r"[a-z]\.\s",
                            _re_unicode.UNICODE),
        _re_unicode.compile(r"\p{Lu}"     if _HAS_REGEX else r"[A-Z]",
                            _re_unicode.UNICODE),
    )

    def __init__(self, srx_path: str | Path) -> None:
        self._lang_rules: dict[str, list] = {}  # rulename → [(is_break, before_re, after_re)]
        self._lang_maps:  list[tuple]     = []  # [(compiled_lang_pattern, rulename), ...]
        self._rule_cache: dict[str, list] = {}  # lang code → combined rule list
        self._load(Path(srx_path))

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> None:
        ns   = {"srx": "http://www.lisa.org/srx20"}
        tree = ET.parse(str(path))
        root = tree.getroot()
        body = root.find("srx:body", ns)

        # --- language rules ---
        lr_container = body.find("srx:languagerules", ns)
        for lr_el in lr_container.findall("srx:languagerule", ns):
            name  = lr_el.get("languagerulename", "")
            rules: list = []
            for rule_el in lr_el.findall("srx:rule", ns):
                is_break   = rule_el.get("break", "yes").lower() == "yes"
                bb_el      = rule_el.find("srx:beforebreak", ns)
                ab_el      = rule_el.find("srx:afterbreak",  ns)
                before_pat = (bb_el.text or "") if bb_el is not None else ""
                after_pat  = (ab_el.text or "") if ab_el is not None else ""
                try:
                    before_re = (_re_unicode.compile(before_pat, _re_unicode.UNICODE)
                                 if before_pat else None)
                    after_re  = (_re_unicode.compile(after_pat,  _re_unicode.UNICODE)
                                 if after_pat else None)
                    rules.append((is_break, before_re, after_re))
                except Exception:
                    pass  # skip patterns that don't compile in Python
            self._lang_rules[name] = rules

        # --- map rules ---
        mr_container = body.find("srx:maprules", ns)
        for lm_el in mr_container.findall("srx:languagemap", ns):
            lang_pat  = lm_el.get("languagepattern", "")
            rule_name = lm_el.get("languagerulename", "")
            try:
                compiled = re.compile(lang_pat, re.IGNORECASE)
                self._lang_maps.append((compiled, rule_name))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Rule lookup
    # ------------------------------------------------------------------

    def _get_rules(self, lang: str) -> list:
        """Return combined rule list for *lang* (cached).

        The fallback ``lowercase. Uppercase`` rule is appended last so that
        every SRX ``break="no"`` abbreviation rule retains priority over it.
        """
        if lang in self._rule_cache:
            return self._rule_cache[lang]
        combined: list = []
        for lang_pat, rule_name in self._lang_maps:
            if lang_pat.fullmatch(lang):
                combined.extend(self._lang_rules.get(rule_name, []))
        combined.append(self._FALLBACK_RULE)
        self._rule_cache[lang] = combined
        return combined

    def has_rules(self, lang: str) -> bool:
        """Return True if any rules are defined for *lang*."""
        return bool(self._get_rules(lang))

    # ------------------------------------------------------------------
    # Segmentation
    # ------------------------------------------------------------------

    def segment(self, text: str, lang: str) -> list[str]:
        """Split *text* into sentences for *lang*. Returns list of segments."""
        text = text.strip()
        if not text:
            return []
        rules = self._get_rules(lang)
        if not rules:
            return [text]
        result = self._apply_rules(text, rules)
        return result if result else [text]

    def _apply_rules(self, text: str, rules: list) -> list[str]:
        # Pass 1 — collect candidate break positions (end of any break="yes"
        # beforebreak match in the text).
        candidates: set[int] = set()
        for is_break, before_re, after_re in rules:
            if is_break and before_re:
                for m in before_re.finditer(text):
                    candidates.add(m.end())

        if not candidates:
            return [text]

        # Pass 2 — for each candidate, find the first matching rule.
        break_positions: list[int] = []
        for pos in sorted(candidates):
            text_before = text[:pos]
            text_after  = text[pos:]

            for is_break, before_re, after_re in rules:
                # beforebreak must end exactly at pos
                if before_re:
                    last_m = None
                    for m in before_re.finditer(text_before):
                        last_m = m
                    if last_m is None or last_m.end() != len(text_before):
                        continue
                # (empty beforebreak matches unconditionally)

                # afterbreak must match at the start of the remaining text
                if after_re:
                    if not after_re.match(text_after):
                        continue
                # (empty afterbreak matches unconditionally)

                # First matching rule wins
                if is_break:
                    break_positions.append(pos)
                break

        if not break_positions:
            return [text]

        # Build segments from the confirmed break positions.
        segments: list[str] = []
        prev = 0
        for pos in break_positions:
            seg = text[prev:pos].strip()
            if seg:
                segments.append(seg)
            prev = pos
        tail = text[prev:].strip()
        if tail:
            segments.append(tail)
        return segments


def segment_srx(text: str, lang: str, srx: SrxSegmenter) -> list[str]:
    """
    Split *text* into sentences using SRX rules, one input line at a time.

    Each non-blank, non-tag line is segmented independently.  This preserves
    line breaks from the source document as segment boundaries, which is
    critical for text where sentences lack end-of-sentence punctuation (e.g.
    bullet lists, headings, or fragmented prose).  Lines that contain multiple
    sentences separated by punctuation are still split by the SRX engine.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    sentences: list[str] = []

    for line in lines:
        line = line.strip()
        if not line or re.match(r"^<.+>$", line):
            continue
        sentences.extend(srx.segment(line, lang))

    return sentences


# Module-level cache so the SRX file is parsed only once per session.
_srx_instance:    Optional[SrxSegmenter] = None
_srx_loaded_path: Optional[Path]         = None


def _get_srx_segmenter(srx_path: Optional[Path] = None) -> Optional[SrxSegmenter]:
    """
    Return a cached ``SrxSegmenter`` loaded from *srx_path*, or ``None`` if
    the file does not exist or cannot be parsed.

    If *srx_path* is ``None`` the following locations are tried in order:
      1. ``~/segment.srx``
      2. Next to this module (``lfp_aligner/segment.srx``)
      3. One directory above this module (project root ``segment.srx``)
    """
    global _srx_instance, _srx_loaded_path

    if srx_path is None:
        # Search candidate locations in order
        candidates = [
            Path.home() / "segment.srx",                          # ~/segment.srx
            Path(__file__).parent / "segment.srx",                # next to this module
            Path(__file__).parent.parent / "segment.srx",        # project root
        ]
        for candidate in candidates:
            if candidate.exists():
                srx_path = candidate
                break
        else:
            return None

    if not srx_path.exists():
        return None

    if _srx_instance is not None and _srx_loaded_path == srx_path:
        return _srx_instance

    log = logging.getLogger("lfp_aligner")
    try:
        _srx_instance    = SrxSegmenter(srx_path)
        _srx_loaded_path = srx_path
        log.info("SRX rules loaded from %s", srx_path)
        return _srx_instance
    except Exception as exc:
        log.warning("Could not load SRX file %s: %s", srx_path, exc)
        return None


# ---------------------------------------------------------------------------
# Auto-revert evaluation (mirrors Perl's confirm_segmenting="auto" logic)
# ---------------------------------------------------------------------------

def should_use_segmented(unseg_counts: list[int],
                          seg_counts:   list[int]) -> bool:
    """
    Return True if sentence segmentation is worth keeping.

    The heuristic (ported from the Perl script) compares the normalised
    post-segmentation imbalance to the pre-segmentation imbalance:

      ratio_unseg = (max/min - 1) * 100  (imbalance before segmentation)
      growth      = sum(seg) / sum(unseg) (how much segmentation grew the files)
      ratio_seg   = (max/min - 1) * 100  (imbalance after segmentation)

      Keep segmented if  (ratio_seg / growth) < max(ratio_unseg, THRESHOLD)

    The ``max(ratio_unseg, THRESHOLD)`` floor fixes the original formula's
    failure mode: when the input files are already balanced (ratio_unseg = 0),
    the bare comparison ``ratio_seg/growth < 0`` can never be satisfied, so
    segmentation would always be reverted.  A threshold of 25 means we accept
    up to ~25 % normalised imbalance before reverting.
    """
    _THRESHOLD = 40.0  # minimum acceptable normalised imbalance

    if 0 in unseg_counts or min(unseg_counts) == 0:
        return True  # can't compute; keep segmented

    ratio_unseg = (max(unseg_counts) / min(unseg_counts) - 1) * 100

    total_unseg = sum(unseg_counts)
    total_seg   = sum(seg_counts)
    if total_unseg == 0:
        return True
    growth = total_seg / total_unseg

    if min(seg_counts) == 0:
        return False  # segmentation emptied a file
    ratio_seg = (max(seg_counts) / min(seg_counts) - 1) * 100

    if growth == 0:
        return False
    return (ratio_seg / growth) < max(ratio_unseg, _THRESHOLD)


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------

def segment(lines: list[str],
            lang: str,
            prefix_dir: str | Path,
            merge_numbers_headings: bool = False,
            srx_path: Optional[str | Path] = None) -> list[str]:
    """
    Sentence-segment a list of pre-normalised lines.

    Segmenter priority:
      1. CJK segmenter for "zh", "ja", "jp".
      2. SRX 2.0 engine when ``~/segment.srx`` (or *srx_path*) is present
         and contains rules for *lang*.
      3. EuroParl / Moses segmenter as fallback.

    Parameters
    ----------
    lines:
        Input as list of lines (one paragraph/segment per entry).
    lang:
        ISO 639-1 language code.
    prefix_dir:
        Path to directory with ``nonbreaking_prefix.*`` files.
    merge_numbers_headings:
        If True, purely numeric / short heading segments are merged with
        the following segment.
    srx_path:
        Explicit path to an SRX 2.0 file.  Defaults to ``~/segment.srx``.
    """
    text = "\n".join(lines)

    if lang in {"zh", "ja", "jp"}:
        segments = segment_cjk(text)
    else:
        srx = _get_srx_segmenter(
            Path(srx_path) if srx_path is not None else None
        )
        if srx is not None and srx.has_rules(lang):
            segments = segment_srx(text, lang, srx)
        else:
            segments = segment_europarl(text, lang, prefix_dir)

    # Remove <P> markers left by the EuroParl segmenter
    segments = [s for s in segments
                if s.strip().upper() != "<P>" and s.strip()]

    if merge_numbers_headings:
        segments = _merge_numbers_and_headings(segments)

    return segments


def _merge_numbers_and_headings(segments: list[str]) -> list[str]:
    """
    Merge purely numeric / short heading segments with the next segment.
    Mirrors the Perl regex: s/^([\\d\\s\\.,]*)\\n/$1 /
    """
    result: list[str] = []
    pending = ""
    _num_re = re.compile(r"^[\d\s\.,]*$")
    _head_re = re.compile(r"^\(?[0-9]?[a-zA-Z0-9][0-9]{0,2}[.)]?$")

    for seg in segments:
        if _num_re.match(seg.strip()) or _head_re.match(seg.strip()):
            pending = (pending + " " + seg).strip() if pending else seg
        else:
            if pending:
                result.append((pending + " " + seg).strip())
                pending = ""
            else:
                result.append(seg)

    if pending:
        result.append(pending)
    return result
