# lfp_aligner
Python 3 port of the original LF Aligner written in Perl

## lfp\_aligner — User Manual

*(The **p** in `lfp\_aligner` stands for **Python**.)*


## Table of Contents

1. [Introduction](#1-introduction)

2. [Requirements and Installation](#2-requirements-and-installation)

3. [Quick Start](#3-quick-start)

4. [How It Works](#4-how-it-works)

5. [Input File Formats](#5-input-file-formats)

6. [The Command-Line Interface](#6-the-command-line-interface)

7. [The Graphical Interface (Qt6)](#7-the-graphical-interface-qt6)

8. [Configuration File](#8-configuration-file)

9. [Sentence Segmentation](#9-sentence-segmentation) — SRX 2.0, EuroParl, CJK, auto-revert

10. [The Alignment Engine (hunalign)](#10-the-alignment-engine-hunalign)

11. [The Dictionary System](#11-the-dictionary-system)

12. [Large File Handling](#12-large-file-handling)

13. [Post-Alignment Filters](#13-post-alignment-filters)

14. [Output Formats](#14-output-formats)

15. [Multilingual Alignment (3+ Languages)](#15-multilingual-alignment-3-languages)

16. [Character Conversion](#16-character-conversion)

17. [CLI Reference](#17-cli-reference)

18. [Troubleshooting](#18-troubleshooting)

19. [Differences from the Original Perl Version](#19-differences-from-the-original-perl-version)


## 1. Introduction

**lfp\_aligner** is a bilingual and multilingual sentence-alignment tool. Given two or more versions of the same document in different languages (a *parallel corpus*), it produces a tab-delimited aligned text file and/or a TMX translation memory that maps corresponding sentences across languages.

`lfp\_aligner` is a Python 3 port of **LF Aligner 4.25** by Laszlo Fewer (the **p** stands for **Python**).  The core alignment algorithm is unchanged — it still uses the **hunalign** binary by MOKK / BME — but all Perl scripts have been replaced by Python modules, and the GUI has been rebuilt in **Qt6** (PyQt6).

Typical uses:

- Building translation memories (TMX) from existing parallel translations

- Preparing training data for machine translation systems

- Creating bilingual glossary corpora for terminology extraction

- Aligning translated documents for quality review


## 2. Requirements and Installation

### 2.1 Python

Python **3.9** or later is required.

### 2.2 Python packages

Install all required packages with:

```
pip install regex python-docx beautifulsoup4 openpyxl PyQt6
```

| Package | Used for |
| - | - |
| `regex` | Unicode-aware sentence segmentation (\\p\{Lu\} etc.) |
| `python-docx` | Reading .docx files |
| `beautifulsoup4` | Parsing HTML input |
| `openpyxl` | Writing .xlsx review files |
| `PyQt6` | Qt6 graphical interface |


`regex`, `python-docx`, `beautifulsoup4`, and `openpyxl` are needed for their respective input/output types only; the tool will import them lazily and raise a clear error if they are missing when required.  `PyQt6` is only needed for the GUI (`--gui` flag or `python -m lfp\_aligner --gui`).

### 2.3 External binaries

The following external programs are called via `subprocess` and must be installed separately:

| Binary | Required for | Typical install |
| - | - | - |
| **hunalign** | Alignment (mandatory) | Bundled in lfp\_aligner installation |
| **pdftotext** | PDF input | `apt install poppler-utils` / `brew install poppler` |
| **antiword** | Legacy .doc input | `apt install antiword` |
| **abiword** | RTF / ODT input | `apt install abiword` |
| **wget** | Downloading URLs | Usually pre-installed on Linux/Mac |


The hunalign binary and its dictionaries are expected to be found inside the **lfp\_aligner installation directory** (referred to as `ALIGNER\_DIR` throughout this manual).  Pass this path with `--script-dir` on the CLI or set it in the GUI's Run tab.  Expected structure:

```
ALIGNER\_DIR/  
 aligner  
  scripts/  
    hunalign/  
      hunalign\_linux        ← Linux binary  
      hunalign\_mac          ← macOS binary  
      hunalign.exe          ← Windows binary  
      data/  
        en-pl.dic           ← pre-built dictionaries  
        null.dic  
        raw/  
          en.txt            ← word frequency lists  
          pl.txt  
    sentence\_splitter/  
      nonbreaking\_prefixes/  
        nonbreaking\_prefix.en  
        nonbreaking\_prefix.pl  
        …
```

### 2.4 Installing the package

From the directory that contains the `lfp\_aligner/` folder:

```
\# Run directly (no install needed)  
python -m lfp\_aligner --help  
  
\# Or install as a package  
pip install -e .        \# requires a pyproject.toml / setup.py
```


## 3. Quick Start

### 3.1 Minimal bilingual alignment (CLI)

```
python -m lfp\_aligner english.txt polish.txt \\  
    --langs en pl \\  
    --script-dir /path/to/lfp\_aligner/aligner \\  
    --out ./output
```

This will:

1. Read both text files

2. Sentence-segment them (EuroParl rules)

3. Align with hunalign using the en-pl dictionary

4. Write `output/aligned.txt` (tab-delimited) and `output/aligned.tmx`

### 3.2 Launch the GUI

```
python -m lfp\_aligner --gui
```

Set the **Aligner dir** field in the Run tab to your lfp\_aligner installation directory.

### 3.3 PDF input with explicit TMX language codes

```
python -m lfp\_aligner contract\_en.pdf contract\_pl.pdf \\  
    --langs en pl \\  
    --filetype p \\  
    --tmx-langs EN-GB PL-PL \\  
    --creator-id "translator@example.com" \\  
    --script-dir /path/to/aligner \\  
    --out ./output
```


## 4. How It Works

The pipeline has eight stages:

```
\[1\] READ        Read input files (txt / pdf / docx / html / xliff / tmx)  
      │  
\[2\] NORMALISE   Strip BOM, normalise line endings, bullets, non-breaking spaces  
      │  
\[3\] SEGMENT     Sentence-split each file (SRX 2.0, EuroParl rules, or CJK segmenter)  
      │  
\[4\] DICTIONARY  Select or generate a bilingual dictionary for hunalign  
      │  
\[5\] ALIGN       Run hunalign on the temp files (with chopping if needed)  
      │  
\[6\] FILTER      Cleanup, dedup, remove untranslated segments  
      │  
\[7\] WRITE TXT   Tab-delimited aligned text file (UTF-8 with BOM)  
      │  
\[8\] WRITE TMX   TMX 1.4 translation memory (optional)
```

For **3+ languages**, Stage 5 is replaced by an iterative pairwise loop (see [Section 15](#15-multilingual-alignment-3-languages)).


## 5. Input File Formats

| Format | `--filetype` | Notes |
| - | :-: | - |
| Plain text (UTF-8) | `t` (default) | One segment per line; CRLF normalised automatically |
| PDF | `p` | Converted via `pdftotext`; `-layout` mode on by default |
| DOCX | `docx` | Extracted via `python-docx`; paragraphs and tables |
| HTML / XHTML | `h` | Parsed via BeautifulSoup4; block tags become line breaks |
| SDLXLIFF / TXLF | auto (by extension)￼ | Source segments extracted |
| TMX | auto (by extension) | Segments extracted for a specified language |


**Tips for best results:**

- **Plain text** gives the most reliable alignment.  If you have a choice, export your documents to UTF-8 plain text before running the aligner.

- **PDF** quality varies greatly.  If pdftotext output looks garbled, export the PDF to text manually and use `--filetype t`.

- **DOCX** extracts paragraphs and table cells.  Embedded images and headers are ignored.

- Each input file should contain one language only.


## 6. The Command-Line Interface

### 6.1 Basic syntax

```
python -m lfp\_aligner \[FILES...\] \[OPTIONS\]  
python -m lfp\_aligner --gui
```

`FILES` and `--langs` must be given in the same order:

```
python -m lfp\_aligner en.txt pl.txt de.txt --langs en pl de
```

### 6.2 Use examples

**Align two plain text files:**

```
python -m lfp\_aligner source.txt target.txt --langs en pl \\  
    --script-dir ~/lfaligner --out ./aligned
```

**PDF input, no segmentation:**

```
python -m lfp\_aligner doc.en.pdf doc.pl.pdf --langs en pl \\  
    --filetype p --no-segment \\  
    --script-dir ~/lfaligner --out ./aligned
```

**Auto-segmentation (revert if imbalanced):**

```
python -m lfp\_aligner a.txt b.txt --langs en fr \\  
    --auto-segment --script-dir ~/lfaligner --out ./aligned
```

**Three languages:**

```
python -m lfp\_aligner en.txt pl.txt de.txt --langs en pl de \\  
    --tmx-langs EN-GB PL-PL DE-DE \\  
    --script-dir ~/lfaligner --out ./aligned
```

**With all filters enabled:**

```
python -m lfp\_aligner a.txt b.txt --langs en pl \\  
    --dedup --filter-untranslated \\  
    --creator-id "my.name@company.com" \\  
    --script-dir ~/lfaligner --out ./aligned
```

**Disable TMX output:**

```
python -m lfp\_aligner a.txt b.txt --langs en pl --no-tmx \\  
    --script-dir ~/lfaligner --out ./aligned
```

**Disable chopping (align as one block):**

```
python -m lfp\_aligner a.txt b.txt --langs en pl --chop 0 \\  
    --script-dir ~/lfaligner --out ./aligned
```


## 7. The Graphical Interface (Qt6)

Launch with:

```
python -m lfp\_aligner --gui
```

The GUI has four tabs:

### Tab 1 — Input

| Control | Purpose |
| - | - |
| **File type** | Drop-down: text / PDF / HTML / DOCX |
| **L1, L2, …** | Language code (e.g. `en`) + file path for each language |
| **Browse…** | Opens a file picker; auto-fills the language code from the filename if empty |
| **+ Add language** | Adds a third (or further) language row |
| **✕** | Removes a language row (minimum 2 required) |
| **TMX language codes** | BCP-47 codes written to the TMX header (e.g. `EN-GB`, `PL-PL`) |


### Tab 2 — Options

| Control | Purpose |
| - | - |
| **Sentence-segment** | Whether to apply sentence splitting |
| **Auto-revert** | Revert to unsegmented if the post-segmentation imbalance between languages exceeds a threshold |
| **Merge number/heading lines** | Join purely numeric or short heading lines with the next segment |
| **Remove ~~~** | Strip hunalign's merge markers and leading hyphens |
| **Remove confidence score** | Drop the third column (alignment confidence) |
| **Remove duplicates** | Hash-based deduplication |
| **Remove identical source=target** | Filter out untranslated segments |
| **Generate TMX** | Write a TMX 1.4 file |
| **Creator ID** | Optional string written to the TMX `\<header\>` |
| **Skip half-empty TMX segments** | Omit TUs where any language is empty |
| **Chop threshold** | Maximum lines per chunk for large-file mode (0 = disabled) |
| **Realign (slower, better quality)** | Pass `-realign` to hunalign for three-phase alignment (see [Section 10](#10-the-alignment-engine-hunalign)) |


### Tab 3 — Run

| Control | Purpose |
| - | - |
| **Output** | Directory where output files are written |
| **File name stem** | Base name for output files (default: `aligned`) |
| **Aligner dir** | Root of the lfp\_aligner installation (where `scripts/hunalign/` lives) |
| **Log panel** | Live log output from the alignment pipeline |
| **▶ Run Alignment** | Start the pipeline |
| **■ Stop** | Request a graceful stop after the current step |


### Tab 4 — Results

Displays the aligned pairs in an **editable table** and provides a full set of editing and file-management controls.

#### Editing toolbar

| Button | Shortcut | Action |
| - | - | - |
| **Split row…** | — | Opens the Split dialog for the selected row (see below) |
| **Merge with next** | — | Appends the next row's text to the selected row and deletes the next row |
| **Delete row** | — | Removes the selected row |


Double-click any cell to edit its text in place.  The cell expands to a word-wrapped editor; Tab / Shift-Tab move to the next/previous cell.

#### Split dialog

Selecting a row and clicking **Split row…** opens a dialog showing the text of each language column in a separate editor.

1. Position the cursor where you want the split in each editor.

2. Click **Split here** — the text before each cursor becomes the current row; the text after each cursor becomes a new row inserted below.

3. If the new row still contains multiple sentences, repeat from step 1.

4. Click **Done** when finished, or **Cancel** to undo all splits made in this session.

5. **Auto-split** finds the next `. Uppercase` boundary in each editor and places the cursors there automatically.

#### File buttons

| Button | Action |
| - | - |
| **Load TXT…** | Load an existing tab-delimited aligned text file for editing (without running alignment) |
| **Load TMX…** | Load an existing TMX file for editing |
| **Open TXT…** | Open the generated TXT file with the system default application |
| **Open TMX…** | Open the generated TMX file with the system default application |
| **Save edited TXT…** | Save the current table contents as a tab-delimited TXT file |
| **Save edited TMX…** | Save the current table contents as a TMX 1.4 file |



## 8. Configuration File

The tool reads (and will create if absent) `LF\_aligner\_setup.txt` in the format used by the original Perl version.  Pass its path with `--setup`:

```
python -m lfp\_aligner a.txt b.txt --langs en pl \\  
    --setup /path/to/LF\_aligner\_setup.txt \\  
    --script-dir ~/lfaligner --out ./output
```

CLI flags always take precedence over values in the setup file.

### Setup file format

Values are placed between square brackets.  Lines starting with `\#` are comments and are ignored.

```
\*\*\* INPUT \*\*\*  
  
Filetype default (t/c/com/epr/w/h/p): \[t\]  
Language 1 default: \[en\]  
Language 2 default: \[pl\]  
  
\*\*\* OUTPUT \*\*\*  
  
Segment to sentences: \[y\]  
Ask for confirmation after segmenting (y/n/auto): \[auto\]  
Cleanup default: \[y\]  
Remove match confidence value: \[y\]  
Delete duplicate entries: \[n\]  
Delete entries where the text is the same in both languages: \[n\]  
Review default (n/t/x): \[n\]  
  
\*\*\* TMX \*\*\*  
  
Make TMX by default: \[y\]  
Language code 1 default: \[EN-GB\]  
Language code 2 default: \[PL-PL\]  
Creator ID default: \[translator@example.com\]  
Skip half-empty segments: \[y\]  
  
\*\*\* MISC \*\*\*  
  
Chop up files larger than this size (0 deactivates the feature): \[15000\]  
Pdf conversion mode; formatted or not (-layout option in pdftotext): \[y\]  
  
Character conversion table for language 1:  
ı	ő  
  
Character conversion table for language 2:
```

### Character conversion tables

Each table entry is one line: the character to replace, a **tab**, and the replacement character.  See [Section 16](#16-character-conversion).


## 9. Sentence Segmentation

Sentence segmentation splits paragraphs into individual sentences before alignment, which gives hunalign much more signal to work with and generally produces better results.

Each input line is segmented **independently**: if a line contains multiple sentences separated by punctuation, those sentences are split; if a line has no terminal punctuation, it is kept as a single segment.  This means line breaks in the source document are always preserved as segment boundaries, which is essential for text where sentences lack end-of-sentence punctuation (e.g. bullet lists, headings, or fragmented prose).

### 9.1 SRX 2.0 segmenter (preferred)

If a file named `~/segment.srx` exists, the **SRX 2.0** engine is used for all non-CJK languages.  SRX (Segmentation Rules eXchange) is an XML format that encodes language-specific sentence-boundary rules as pairs of regular expressions — a *beforebreak* pattern that must match immediately before the candidate break position, and an *afterbreak* pattern that must match immediately after.

Rules can be `break="yes"` (insert a sentence boundary) or `break="no"` (suppress a boundary — used for abbreviations).  The engine uses `cascade="yes"` semantics: rules from all matching language sets are concatenated and applied in order, with first-match-wins at each candidate position.

**Fallback rule:** After all SRX rules, a built-in fallback is appended: lowercase letter + `.` before the break, uppercase letter after.  This catches the common `word. Word` prose pattern that is not covered by the Default rules in many SRX files.  All SRX `break="no"` abbreviation rules retain priority over this fallback.

The SRX file is parsed once and cached for the session.  The following locations are searched in order:

1. `~/segment.srx` (home directory)

2. `lfp\_aligner/segment.srx` (next to the package)

3. `segment.srx` in the project root (one level above the package)

To use a different file, pass an explicit `srx\_path` argument to `segment()`.

### 9.2 EuroParl segmenter (fallback)

When no SRX file is available (or no rules are defined for the requested language), the tool uses a Python port of the **Moses / EuroParl** **`split-sentences.perl`** script (originally by Philipp Koehn).  No external binary is required.

The algorithm applies these rules in order to each input line:

1. **Non-period sentence-enders** (`?`, `!`) followed by an uppercase letter → insert line break.

2. **Multiple dots** (`...`, `….`) followed by an uppercase letter → break.

3. **Punctuation inside quotes or brackets** followed by an uppercase letter → break.

4. **Periods** — each word ending in a period is checked:

   - If the prefix is in the *nonbreaking prefix list* → no break (e.g. `Dr.`, `Mr.`, `vs.`, `e.g`).

   - If the word is an uppercase acronym (e.g. `U.S.A.`) → no break.

   - If the next word starts with uppercase or a digit → break.

   - If the prefix is in the *numeric-only* list (e.g. `No.`, `p.`) and the next word is a number → no break.

**Nonbreaking prefix files** are read from:

```
ALIGNER\_DIR/scripts/sentence\_splitter/nonbreaking\_prefixes/
```

Files are named `nonbreaking\_prefix.\<lang\>` (e.g. `nonbreaking\_prefix.pl`). If no file exists for the requested language, English is used as a fallback.

Available language files (from LF Aligner 4.25): `bg, ca, cs, de, el, en, es, et, fr, hr, hu, is, it, mz, nl, pl, pt, ro, ru, sk, sl, sv`

### 9.3 CJK segmenter

For Chinese (`zh`), Japanese (`ja`, `jp`) the segmenter splits at:

| Character | Unicode | Name |
| - | - | - |
| 。 | U+3002 | Ideographic full stop |
| ？ | U+FF1F | Fullwidth question mark |
| ！ | U+FF01 | Fullwidth exclamation mark |
| …… | U+2026×2 | Double ellipsis |


Closing punctuation (quotes, brackets) following one of these characters stays on the same segment.

### 9.4 Segmentation modes

| Mode | `--segment` / config | Behaviour |
| - | :-: | - |
| Always segment | `y` | Segment unconditionally |
| Never segment | `n` | Use raw line breaks as segment boundaries |
| Auto | `auto` | Segment, then revert if post-segmentation imbalance exceeds threshold |


**Auto-revert heuristic:** The aligner computes the *imbalance ratio* before and after segmentation:

```
ratio = (max\_count / min\_count − 1) × 100
```

After segmentation the ratio is normalised by the growth factor (how much segmentation expanded the files).  If the normalised post-segmentation imbalance exceeds `max(ratio\_before, 40)`, the segmented files are discarded and the unsegmented ones are used instead.  The floor of 40 ensures that balanced input files (ratio\_before = 0, common for pre-aligned document pairs) are not always reverted — a normalised imbalance below 40 % is considered acceptable.

### 9.5 Merge numbers and headings

With `--merge-headings` (or `Merge numbers and chapter/point headings with the next segment: \[y\]` in the setup file), lines that consist only of numbers, punctuation, or short chapter markers (like `1.`, `(a)`, `Art.`) are joined with the following line.  This reduces noise from document structure elements that would otherwise appear as isolated segments.


## 10. The Alignment Engine (hunalign)

[Hunalign](http://mokk.bme.hu/resources/hunalign/) (by MOKK, Budapest) is a statistical sentence aligner.  The tool calls it as an external process:

```
hunalign -text \<dictionary\> \<source\_file\> \<target\_file\>
```

Output is written to stdout as tab-delimited lines:

```
source segment    target segment    confidence\_score
```

**Confidence scores** range from roughly −2 (bad) to +1 (good).  They are removed from the output by default (`--keep-confidence` retains them).

Hunalign can produce **1:1, 1:2, 2:1, 0:1, and 1:0 alignments**.  When it merges two source sentences into one target sentence it joins them with `~~~` — the cleanup filter removes these markers.

### Realign mode

Enabling **Realign** (checkbox in the Options tab, or `--realign` on the CLI) passes the `-realign` flag to hunalign.  This triggers a three-phase process:

1. Initial alignment with the provided dictionary.

2. Automatic construction of a supplementary dictionary from the co-occurring word pairs found in the initial alignment (bisentences).

3. Full re-alignment using the enriched dictionary.

Realign roughly triples runtime but typically improves quality, especially for language pairs with no pre-built dictionary.  It is not available in chopping mode (each chunk is aligned independently, so the auto-built dictionary from one chunk is not reused for others).


## 11. The Dictionary System

Hunalign performs better when it can match words across languages.  The tool selects a dictionary in this order:

1. **Pre-built dictionary** — `ALIGNER\_DIR/scripts/hunalign/data/\<l1\>-\<l2\>.dic`

2. **Reversed pre-built** — `\<l2\>-\<l1\>.dic`

3. **Generated on-the-fly** — from raw word-frequency files `data/raw/\<l1\>.txt` and `data/raw/\<l2\>.txt`.  Each file contains one word per line; the tool pairs them positionally and writes a new `.dic` file.

4. **null.dic** — an empty dictionary (alignment by character n-gram overlap only)

*Pre-built dictionaries are available for 32 languages in LF Aligner 4.25.*  For language pairs that lack both a pre-built dictionary and raw word lists, `null.dic` is used automatically — alignment quality will be lower but still usable for closely related languages or short documents.

### Dictionary format

```
target\_word @ source\_word
```

One entry per line, UTF-8.  The target word comes first (this is the format hunalign expects).  Example for an en→pl dictionary:

```
dom @ house  
samochód @ car  
pracować @ work
```


## 12. Large File Handling

By default, files with more than **15 000 segments** are processed in *chopping* *mode*.  This is configurable with `--chop N` (set to 0 to disable).

### How chopping works

This is a Python 3 port of the original `partialAlign.py` (which was Python 2):

1. **Find anchor words** — words that appear exactly once (hapax legomena) in both source and target.  These are reliable alignment anchors.

2. **Compute the maximal monotone chain** through the set of anchor-word position pairs using dynamic programming.  This gives a sequence of confident split points.

3. **Select split points** so that no chunk exceeds the chop threshold.

4. **Write sub-corpora** for each chunk.

5. **Align each chunk** separately with hunalign.

6. **Concatenate** the results into a single output file.

If the texts are very different (few or no hapax anchors in common), the algorithm will fall back to forced chunk boundaries regardless of anchor quality.


## 13. Post-Alignment Filters

Filters are applied in this fixed order:

### 13.1 Cleanup  (`--no-cleanup` to disable)

- Removes `~~~` markers inserted by hunalign when it merges segments.

- Strips leading `- ` from segments (list-item artefacts from PDF conversion).

### 13.2 Remove confidence score  (`--keep-confidence` to retain)

Drops the third column (hunalign's numerical confidence score) from every row. Always applied for 3+ language alignments regardless of this setting.

### 13.3 Remove duplicates  (`--dedup` to enable; off by default)

Uses a hash set to remove rows where both the source and target text have already appeared earlier in the file.  Only the first two columns are compared. Disabled for 3+ language alignments.

### 13.4 Remove untranslated segments  (`--filter-untranslated` to enable; off by default)

Removes rows where:

- The source text and target text are identical strings (segment was not translated), **or**

- The source text consists entirely of digits, whitespace, and punctuation (segment carries no translatable content).

Disabled for 3+ language alignments.


## 14. Output Formats

### 14.1 Tab-delimited text (`.txt`)

UTF-8 with BOM, one aligned pair per line:

```
source segment\<TAB\>target segment\<TAB\>source filename
```

The source filename is appended as the last column for traceability.  This file can be opened in any spreadsheet application or imported into most CAT tools.

**Default location:** `\<output\_dir\>/\<stem\>.txt`

### 14.2 TMX 1.4 (`.tmx`)

Standard Translation Memory eXchange format, accepted by all major CAT tools (memoQ, SDL Trados, Wordfast, OmegaT, etc.).

```
\<?xml version='1.0' encoding='utf-8'?\>  
\<tmx version="1.4"\>  
  \<header creationtool="lfp\_aligner"  
          creationtoolversion="1.0"  
          datatype="PlainText"  
          segtype="sentence"  
          adminlang="en-US"  
          srclang="EN-GB"  
          creationdate="20260316T120000Z"  
          creationid="translator@example.com"  
          o-tmf="TMX"/\>  
  \<body\>  
    \<tu tuid="1"\>  
      \<tuv xml:lang="EN-GB"\>\<seg\>Source sentence.\</seg\>\</tuv\>  
      \<tuv xml:lang="PL-PL"\>\<seg\>Zdanie docelowe.\</seg\>\</tuv\>  
    \</tu\>  
    …  
  \</body\>  
\</tmx\>
```

**Language codes in the TMX header** are set by `--tmx-langs`.  If not provided, the ISO 639-1 codes from `--langs` are uppercased.  For best compatibility with CAT tools use full BCP-47 codes such as `EN-GB` or `PL-PL`.

**Default location:** `\<output\_dir\>/\<stem\>.tmx`

### 14.3 XLSX spreadsheet (`.xlsx`)

The `write\_xlsx()` function in `io/writers.py` can produce an Excel spreadsheet for manual review.  This is not triggered automatically from the CLI but can be called from Python code:

```
from lfp\_aligner.io.writers import write\_xlsx  
write\_xlsx(pairs, "review.xlsx", langs=\["EN-GB", "PL-PL"\])
```

The spreadsheet has:

- An **Instructions** sheet explaining the format.

- An **Alignment** sheet with one language per column, header row frozen.

- Cells are word-wrapped for readability.

Unlike the original Perl version there is no row limit (the original XLS format capped at 65 535 rows; `.xlsx` supports over one million).


## 15. Multilingual Alignment (3+ Languages)

Because hunalign is a strictly bilingual tool, aligning three or more languages is done by chaining pairwise alignments:

```
Align L0 vs L1  →  aligned columns: \[L0 | L1\]  
Extract L1 column  →  L1\_extracted  
  
Align L1\_extracted vs L2  →  \[L1 | L2\]  
Reconcile with previous result  →  \[L0 | L1 | L2\]  
  
Repeat for L3, L4, …
```

### Row reconciliation

Hunalign may merge multiple source segments into one output row (marked with `~~~`), or stretch one source segment across multiple rows (leaving empty cells).  The reconciler:

1. **Expands ~~~ merges** — splits merged rows back into individual rows, distributing the other-language content to the first sub-row and leaving the rest empty.

2. **Merges stretched rows** — if a row has an empty key column, its content is appended to the previous row.

3. **Pads mismatches** — if row counts still differ after the above steps, the shorter side is padded with empty strings and a warning is logged.

### Quality considerations

Multilingual alignment quality degrades with each additional language because errors compound.  For best results:

- Use segmented input (the default).

- Ensure the source documents are translations of each other (not independent texts on the same topic).

- Review the output in a spreadsheet or CAT tool before use.


## 16. Character Conversion

The setup file supports per-language character replacement tables, useful for fixing encoding corruption in specific document types (the classic case being Hungarian text extracted from certain EU Council PDFs).

### Setup file syntax

```
Character conversion table for language 1:  
ı	ő  
Ő	Ű  
  
Character conversion table for language 2:
```

Each line is: `character\_to\_replace\<TAB\>replacement\_character`

- Case-sensitive.

- Applied after segmentation, before writing to hunalign.

- Can be used to fix corrupted characters or normalise typographic variants (e.g. replacing a curly apostrophe with a straight one).

### Hungarian PDF corruption fix

Council of the EU PDF documents in Hungarian sometimes have encoding corruption where `ő` is rendered as `ı`.  To fix this, add to language 1's conversion table:

```
ı	ő
```


## 17. CLI Reference

```
python -m lfp\_aligner \[FILES...\] \[OPTIONS\]  
python -m lfp\_aligner --gui
```

### Positional arguments

| Argument | Description |
| - | - |
| `FILES` | Input files, one per language (same order as `--langs`) |


### Input options

| Flag | Default | Description |
| - | - | - |
| `--langs LANG \[LANG ...\]` | — | ISO 639-1 language codes, same order as FILES |
| `--filetype \{t,p,h,docx\}` | `t` | Input file type (auto-detected from extension if omitted) |
| `--script-dir DIR` | auto | Root of LF Aligner installation (parent of `scripts/`) |
| `--setup FILE` | — | Path to `LF\_aligner\_setup.txt` |


### Segmentation options

| Flag | Description |
| - | - |
| `--segment` | Always sentence-segment (default) |
| `--no-segment` | Skip sentence segmentation |
| `--auto-segment` | Segment then auto-revert if ratio worsens |
| `--merge-headings` | Merge number-only / heading lines with next segment |


### Filter options

| Flag | Default | Description |
| - | - | - |
| `--no-cleanup` | off | Disable removal of ~~~ markers and leading hyphens |
| `--keep-confidence` | off | Retain the hunalign confidence score column |
| `--dedup` | off | Remove duplicate segment pairs |
| `--filter-untranslated` | off | Remove segments where source == target |


### Output options

| Flag | Default | Description |
| - | - | - |
| `--out DIR` | `.` | Output directory |
| `--stem NAME` | `aligned` | Base name for output files |
| `--no-tmx` | off | Do not write a TMX file |
| `--tmx-langs CODE \[CODE ...\]` | langs uppercased | BCP-47 codes for TMX header |
| `--creator-id STRING` | — | Creator ID written to TMX `\<header\>` |
| `--skip-half-empty` | on | Omit TMX TUs where any language segment is empty |


### Performance options

| Flag | Default | Description |
| - | - | - |
| `--chop N` | `15000` | Chop files larger than N segments (0 = disable) |
| `--realign` | off | Three-phase realignment for better quality (slower; see [Section 10](#10-the-alignment-engine-hunalign)) |
| `--pdf-no-layout` | off | Use plain pdftotext mode instead of `-layout` |


### Other

| Flag | Description |
| - | - |
| `--gui` | Launch the Qt6 graphical interface |
| `-h`, `--help` | Show help and exit |



## 18. Troubleshooting

### "hunalign produced an empty output file"

**Cause:** hunalign received one or two empty input files, or the files are too short / too different.

**Fix:**

- Check that both input files have content after normalisation (the log shows line counts after each stage).

- If using PDF input, try `--pdf-no-layout` or pre-convert to text.

- If the texts are very different (different versions, heavy editing), try `--no-segment` to use paragraph-level alignment.

### "File not found: hunalign\_linux"

**Cause:** `--script-dir` points to the wrong directory, or the hunalign binary for your OS is missing.

**Fix:** Set `--script-dir` to the directory that contains `scripts/hunalign/`. On Linux, ensure `scripts/hunalign/hunalign\_linux` is executable:

```
chmod +x /path/to/aligner/scripts/hunalign/hunalign\_linux
```

### Poor alignment quality

**Possible causes and fixes:**

| Symptom | Likely cause | Fix |
| - | - | - |
| Many empty source or target cells | Texts too different | Review source documents; try `--no-segment` |
| Segments merged with ~~~ visible | Cleanup disabled | Ensure `--no-cleanup` is not set |
| Short segments grouped with long ones | No dictionary for language pair | Add raw word lists to `data/raw/`; or enable `--realign` |
| Heading lines appear as isolated segments | Document structure noise | Enable `--merge-headings` |
| Numbers appear as segments | Numbers-only filtering off | Enable `--filter-untranslated` |
| Multi-sentence segments in output | Source text lacks end-of-sentence punctuation | Normal — the segmenter preserves each input line as-is when no punctuation boundary is found.  Use the Split dialog in the Results tab to correct individual segments manually. |


### "Module 'regex' is not installed"

```
pip install regex
```

Without `regex`, the segmenter falls back to ASCII-only rules which may miss sentence boundaries in languages with accented capitals.

### Segmentation made things worse

Use `--auto-segment` instead of `--segment`.  The auto mode will revert to unsegmented input if the normalised imbalance between language segment counts exceeds 40 % (or the pre-segmentation imbalance if that was already higher).

### TMX not accepted by my CAT tool

- Ensure `--tmx-langs` uses the exact codes your CAT tool expects (e.g. `EN-GB` not `en-gb`).

- Some tools require the source language code in the TMX `\<header srclang\>` to match their project source language exactly.

- Try opening the TMX in a text editor to verify it is valid XML.

### Log file location

A log file is written to `\<output\_dir\>/lfp\_aligner.log` for every CLI run. The GUI writes log output to the Run tab's log panel only (not to disk by default).


## 19. Differences from the Original Perl Version

| Feature | Original Perl (4.25) | lfp\_aligner |
| - | - | - |
| Sentence splitter | External binary (`.exe` / `.perl`) | Native Python port — no binary required; SRX 2.0 engine added |
| Large-file chopper | `partialAlign.py` (Python 2) | Ported to Python 3 (included in `aligner.py`) |
| GUI toolkit | Perl/Tk | PyQt6 |
| Excel output | `Spreadsheet::WriteExcel` (.xls, max 65 535 rows) | `openpyxl` (.xlsx, \>1 M rows) |
| HTML parsing | `HTML::Strip` + `HTML::Entities` | `BeautifulSoup4` |
| DOCX reading | `docx2txt.pl` | `python-docx` |
| Config format | `LF\_aligner\_setup.txt` (bracket syntax) | Same format — fully compatible |
| EU document download | wget + CELEX/EuroLex URLs | Not implemented (manual download) |
| Machine translation in editor | Google Translate API | Not implemented |
| Alignment editor | `alignedit\_2.3.pl` (Tk spreadsheet) | Implemented in the Results tab: split (with auto-split), merge, delete, load/save TXT and TMX |
| Realign option | `-realign` hunalign flag | Exposed as GUI checkbox and `--realign` CLI flag |
| Platform binaries | Separate exe for Windows | Single Python codebase; platform detected at runtime |


### Features not yet implemented

- Automatic EU document **download** (CELEX, EuroParl, Commission)

- **Machine translation** integration in the editor

- **Master TM** append mode

- **XLS/XLSX round-trip review** (write → edit → re-import)

These can be added as future modules following the same architecture.

### Requests for features, etc.

If you'd like me to add a feature, for example more supported file formats, please buy me a coffee here https://cuplink.to/onepolishtranslator to motivate me ;-) and then make the request.

