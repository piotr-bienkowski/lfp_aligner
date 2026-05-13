"""
Configuration loader for LF Aligner.

Parses the original LF_aligner_setup.txt format (values between square brackets)
and exposes settings as a typed dataclass.  A default setup file is written if
none is found, matching LF Aligner 4.25 defaults.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SETUP_TEXT = """\
Here, you can specify settings for LF aligner. Put your choice (usually y or n)
between the square brackets, and don't change anything else in this file.
If you want to restore the default settings or you think you may have corrupted
the file, just delete it. It will be recreated with default settings the next
time the aligner runs.


*** INPUT ***

Filetype default (t/c/com/epr/w/h/p): [t]
Prompt user for filetype: [y]

Language 1 default: [en]
Prompt user for language 1: [y]
Language 2 default: [pl]
Prompt user for language 2: [y]


*** OUTPUT ***

Segment to sentences: [y]
Ask for confirmation after segmenting (y/n/auto) - n and auto allow the aligner to run unattended (see readme): [auto]

Merge numbers and chapter/point headings with the next segment: [n]

Cleanup default: [y]
Prompt user whether to do cleanup: [n]

Remove match confidence value: [y]

Delete duplicate entries: [n]

Delete entries where the text is the same in both languages (filters out untranslated text and segments than only contain numbers etc.): [n]

Review default (n/t/x): [n]
Prompt user whether/how to review pairings: [n]

Offer to write to txt (allows you to add all aligned files to the same master TM): [n]
Master TM path: []


*** TMX ***

Make TMX by default: [y]
Prompt user whether to make TMX: [n]

Language code 1 default: []
Prompt user for language code 1: [y]
Language code 2 default: []
Prompt user for language code 2: [y]

Prompt user for creation date and time: [n]

Creator ID default: []
Prompt user for creator ID: [n]

Prompt user for TMX note: [n]

Skip half-empty segments: [y]


*** MISC ***

Chop up files larger than this size (0 deactivates the feature): [15000]

Pdf conversion mode; formatted or not (-layout option in pdftotext): [y]

Force GUI on (y) or off (n) []

GUI language: [en]


Character conversion: provide character pairs separated by a tab, one pair per
line. The aligner will replace the first character with the second in your
aligned file. The replacement is case-sensitive and can be used to decode
character entities or fix corrupted characters.

Character conversion table for language 1:


Character conversion table for language 2:

"""


@dataclass
class AlignmentConfig:
    # --- Input ---
    filetype: str = "t"          # t/p/h/w
    filetype_prompt: bool = True
    lang1_default: str = "en"
    lang2_default: str = "pl"
    lang1_prompt: bool = True
    lang2_prompt: bool = True

    # --- Output ---
    segment: str = "y"           # y/n/auto
    confirm_segmenting: str = "auto"   # y/n/auto
    merge_numbers_headings: bool = False
    cleanup: bool = True
    cleanup_prompt: bool = False
    remove_confidence: bool = True
    delete_dupes: bool = False
    delete_untranslated: bool = False
    review: str = "n"            # n/t/x/nx
    review_prompt: bool = False
    offer_master_tm: bool = False
    master_tm_path: str = ""

    # --- TMX ---
    make_tmx: bool = True
    make_tmx_prompt: bool = False
    tmx_lang1_default: str = ""
    tmx_lang2_default: str = ""
    tmx_lang1_prompt: bool = True
    tmx_lang2_prompt: bool = True
    creationdate_prompt: bool = False
    creator_id: str = ""
    creatorid_prompt: bool = False
    tmxnote_prompt: bool = False
    skip_half_empty: bool = True

    # --- Misc ---
    chop_threshold: int = 15000
    realign: bool = False
    pdf_layout: bool = True
    force_gui: str = ""          # "y" / "n" / ""
    gui_language: str = "en"

    # --- Character conversion ---
    charconv_lang1: dict = field(default_factory=dict)
    charconv_lang2: dict = field(default_factory=dict)


def _bracket_value(line: str) -> str:
    """Extract value between the last pair of square brackets on a line."""
    m = re.search(r'\[([^\]]*)\]\s*$', line)
    return m.group(1).strip() if m else ""


def _yn(val: str) -> bool:
    return val.lower() == "y"


def load(path: str | Path) -> AlignmentConfig:
    """
    Load an LF_aligner_setup.txt file and return an AlignmentConfig.
    If the file does not exist it is created with defaults.
    """
    p = Path(path)
    if not p.exists():
        p.write_text(DEFAULT_SETUP_TEXT, encoding="utf-8")

    text = p.read_text(encoding="utf-8-sig")  # strips BOM
    lines = text.splitlines()

    cfg = AlignmentConfig()
    charconv_section = None  # None / 1 / 2

    for line in lines:
        s = line.strip()

        # Detect character conversion section headers
        if "Character conversion table for language 1" in s:
            charconv_section = 1
            continue
        if "Character conversion table for language 2" in s:
            charconv_section = 2
            continue

        # If we're in a char-conv section, parse tab-separated pairs
        if charconv_section and "\t" in s and not s.startswith("#"):
            parts = s.split("\t", 1)
            if len(parts) == 2 and parts[0] and parts[1]:
                if charconv_section == 1:
                    cfg.charconv_lang1[parts[0]] = parts[1]
                else:
                    cfg.charconv_lang2[parts[0]] = parts[1]
            continue

        # Reset char-conv section on blank lines only if in section 1
        # (section 2 runs to EOF)
        if charconv_section == 1 and not s:
            charconv_section = None

        # Named settings
        if "Filetype default" in s:
            cfg.filetype = _bracket_value(s) or cfg.filetype
        elif s.startswith("Prompt user for filetype"):
            cfg.filetype_prompt = _yn(_bracket_value(s))
        elif s.startswith("Language 1 default"):
            cfg.lang1_default = _bracket_value(s) or cfg.lang1_default
        elif s.startswith("Language 2 default"):
            cfg.lang2_default = _bracket_value(s) or cfg.lang2_default
        elif s.startswith("Prompt user for language 1"):
            cfg.lang1_prompt = _yn(_bracket_value(s))
        elif s.startswith("Prompt user for language 2"):
            cfg.lang2_prompt = _yn(_bracket_value(s))
        elif s.startswith("Segment to sentences"):
            cfg.segment = _bracket_value(s) or cfg.segment
        elif s.startswith("Ask for confirmation after segmenting"):
            cfg.confirm_segmenting = _bracket_value(s) or cfg.confirm_segmenting
        elif s.startswith("Merge numbers and chapter"):
            cfg.merge_numbers_headings = _yn(_bracket_value(s))
        elif s.startswith("Cleanup default"):
            cfg.cleanup = _yn(_bracket_value(s))
        elif s.startswith("Prompt user whether to do cleanup"):
            cfg.cleanup_prompt = _yn(_bracket_value(s))
        elif s.startswith("Remove match confidence value"):
            cfg.remove_confidence = _yn(_bracket_value(s))
        elif s.startswith("Delete duplicate entries"):
            cfg.delete_dupes = _yn(_bracket_value(s))
        elif s.startswith("Delete entries where the text is the same"):
            cfg.delete_untranslated = _yn(_bracket_value(s))
        elif s.startswith("Review default"):
            cfg.review = _bracket_value(s) or cfg.review
        elif s.startswith("Prompt user whether/how to review"):
            cfg.review_prompt = _yn(_bracket_value(s))
        elif s.startswith("Offer to write to txt"):
            cfg.offer_master_tm = _yn(_bracket_value(s))
        elif s.startswith("Master TM path"):
            cfg.master_tm_path = _bracket_value(s)
        elif s.startswith("Make TMX by default"):
            cfg.make_tmx = _yn(_bracket_value(s))
        elif s.startswith("Prompt user whether to make TMX"):
            cfg.make_tmx_prompt = _yn(_bracket_value(s))
        elif s.startswith("Language code 1 default"):
            cfg.tmx_lang1_default = _bracket_value(s)
        elif s.startswith("Language code 2 default"):
            cfg.tmx_lang2_default = _bracket_value(s)
        elif s.startswith("Prompt user for language code 1"):
            cfg.tmx_lang1_prompt = _yn(_bracket_value(s))
        elif s.startswith("Prompt user for language code 2"):
            cfg.tmx_lang2_prompt = _yn(_bracket_value(s))
        elif s.startswith("Prompt user for creation date"):
            cfg.creationdate_prompt = _yn(_bracket_value(s))
        elif s.startswith("Creator ID default"):
            cfg.creator_id = _bracket_value(s)
        elif s.startswith("Prompt user for creator ID"):
            cfg.creatorid_prompt = _yn(_bracket_value(s))
        elif s.startswith("Prompt user for TMX note"):
            cfg.tmxnote_prompt = _yn(_bracket_value(s))
        elif s.startswith("Skip half-empty segments"):
            cfg.skip_half_empty = _yn(_bracket_value(s))
        elif s.startswith("Chop up files larger than"):
            try:
                cfg.chop_threshold = int(_bracket_value(s))
            except ValueError:
                pass
        elif s.startswith("Pdf conversion mode"):
            cfg.pdf_layout = _yn(_bracket_value(s))
        elif s.startswith("Force GUI"):
            cfg.force_gui = _bracket_value(s)
        elif s.startswith("GUI language"):
            cfg.gui_language = _bracket_value(s) or cfg.gui_language

    return cfg
