"""
Input readers: convert various file formats to a plain list of UTF-8 lines.

Supported formats
-----------------
  txt         Plain UTF-8 text (one segment per line)
  pdf         PDF via pdftotext binary
  docx        DOCX via python-docx
  doc         Legacy Word (.doc) via antiword
  rtf/odt     Rich Text / OpenDocument via abiword
  html        HTML/XHTML via BeautifulSoup4
  xliff       SDLXLIFF bilingual files (extracts source segments)
  tmx         TMX translation memory (extracts source segments)
"""

from __future__ import annotations

import html
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

# Optional heavy dependencies — imported lazily so the package is still
# importable even if they are not installed.

def _require(module: str, pip_name: str = ""):
    try:
        import importlib
        return importlib.import_module(module)
    except ImportError:
        pkg = pip_name or module
        raise ImportError(
            f"Module '{module}' is not installed.  Run:  pip install {pkg}"
        )


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------

def read_txt(path: str | Path) -> list[str]:
    """
    Read a plain text file.  Handles UTF-8, UTF-8-BOM and CRLF line endings.
    Each non-empty line becomes one potential segment.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.splitlines()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def read_pdf(path: str | Path,
             pdftotext_bin: str = "pdftotext",
             layout: bool = True) -> list[str]:
    """
    Convert a PDF to text using the pdftotext binary (Poppler).

    Parameters
    ----------
    layout:
        Pass -layout to pdftotext for formatted output (preserves columns).
        Set False for plain flowing text.
    """
    cmd = [pdftotext_bin, "-enc", "UTF-8"]
    if layout:
        cmd.append("-layout")
    cmd += [str(path), "-"]

    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(
            f"pdftotext failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    text = result.stdout
    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Replace form-feed (page break) with line break
    text = text.replace("\f", "\n")
    return text.splitlines()


def _pdf_paragraph_merge(lines: list[str], lang: str = "") -> list[str]:
    """
    Post-process raw pdftotext output.
    Mirrors the Perl logic:
      - Strip leading spaces
      - Detect Hungarian character corruption in Council PDFs and fix it
      - Add paragraph breaks at blank lines
      - Collapse continuation lines into single lines
    """
    # Detect Hungarian PDF character corruption (ı present = corrupted)
    fix_hu = (lang == "hu") and any("ı" in l for l in lines)

    paragraphs: list[str] = []
    current: list[str] = []

    for line in lines:
        line = line.lstrip()
        if fix_hu:
            line = line.replace("ő", "ű").replace("Ő", "Ű").replace("ı", "ő")

        # Detect new list item / numbered paragraph
        line = re.sub(r'^(\(?[0-9]?[0-9]?[a-z0-9]\)|\.)', r'\n\1', line)
        line = re.sub(r'^([–-]) ', r'\n\1 ', line)

        if line.strip() == "":
            if current:
                paragraphs.append(" ".join(current))
                current = []
        else:
            current.append(line.strip())

    if current:
        paragraphs.append(" ".join(current))

    return paragraphs


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def read_docx(path: str | Path) -> list[str]:
    """
    Extract text from a .docx file using python-docx.
    Returns one entry per non-empty paragraph.
    """
    docx = _require("docx", "python-docx")
    doc = docx.Document(str(path))
    lines = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            lines.append(text)
    # Also extract text from tables
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if text:
                    lines.append(text)
    return lines


# ---------------------------------------------------------------------------
# Legacy .doc — antiword
# ---------------------------------------------------------------------------

def read_doc(path: str | Path,
             antiword_bin: str = "antiword") -> list[str]:
    """
    Extract text from a legacy Word .doc file using antiword.

    antiword is called with:
      antiword -m UTF-8.txt -w 0 <file>

    -m UTF-8.txt  — output in UTF-8
    -w 0          — disable line wrapping (one paragraph per line)
    """
    cmd = [antiword_bin, "-m", "UTF-8.txt", "-w", "0", str(path)]
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                            errors="replace")
    if result.returncode != 0:
        raise RuntimeError(
            f"antiword failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    text = result.stdout.replace("\r\n", "\n").replace("\r", "\n")
    return [l for l in text.splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# RTF / ODT / other formats — abiword
# ---------------------------------------------------------------------------

def read_abiword(path: str | Path,
                 abiword_bin: str = "abiword") -> list[str]:
    """
    Extract text from RTF, ODT, ABW, or any other format supported by abiword.

    abiword is called with:
      abiword --to=txt --to-name=fd://1 <file>

    Output is written to stdout (fd://1) as plain UTF-8 text.
    """
    cmd = [abiword_bin, "--to=txt", "--to-name=fd://1", str(path)]
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                            errors="replace")
    if result.returncode != 0:
        raise RuntimeError(
            f"abiword failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    text = result.stdout.replace("\r\n", "\n").replace("\r", "\n")
    return [l for l in text.splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def read_html(content: str) -> list[str]:
    """
    Convert an HTML string to a list of text lines.

    Uses BeautifulSoup4 for robust parsing.  Block-level tags (p, br, li, td,
    tr, div, h1-h6) become segment boundaries, mirroring the Perl behaviour.
    """
    bs4 = _require("bs4", "beautifulsoup4")
    BeautifulSoup = bs4.BeautifulSoup

    # Decode HTML entities before parsing
    content = html.unescape(content)

    soup = BeautifulSoup(content, "html.parser")

    BLOCK_TAGS = {"p", "br", "li", "td", "tr", "div",
                  "h1", "h2", "h3", "h4", "h5", "h6"}

    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")

    text = soup.get_text()
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            lines.append(line)
    return lines


def read_html_file(path: str | Path) -> list[str]:
    """Read an HTML file from disk and convert to lines."""
    content = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return read_html(content)


def read_url(url: str, wget_bin: str = "wget") -> str:
    """Download a URL with wget and return the content as a string."""
    result = subprocess.run(
        [wget_bin, "-q", "-O", "-", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise RuntimeError(f"wget failed for {url}: {result.stderr.strip()}")
    return result.stdout


# ---------------------------------------------------------------------------
# XLIFF (SDLXLIFF)
# ---------------------------------------------------------------------------

_XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"

def read_xliff(path: str | Path, extract: str = "source") -> list[str]:
    """
    Extract segments from an SDLXLIFF / XLIFF 1.2 file.

    Parameters
    ----------
    extract:
        "source" or "target"
    """
    tree = ET.parse(str(path))
    root = tree.getroot()
    tag = f"{{{_XLIFF_NS}}}{extract}"
    lines = []
    for elem in root.iter(f"{{{_XLIFF_NS}}}trans-unit"):
        node = elem.find(tag)
        if node is not None:
            text = _xliff_text(node)
            if text.strip():
                lines.append(text.strip())
    return lines


def _xliff_text(elem) -> str:
    """Recursively extract plain text from an XLIFF inline element."""
    parts = [elem.text or ""]
    for child in elem:
        parts.append(child.tail or "")
    return "".join(parts)


# ---------------------------------------------------------------------------
# TMX input
# ---------------------------------------------------------------------------

_TMX_NS = {"tmx": ""}   # TMX has no default namespace prefix

def read_tmx(path: str | Path, lang: Optional[str] = None,
             src_lang: Optional[str] = None) -> list[str]:
    """
    Extract segments from a TMX file.

    Parameters
    ----------
    lang:
        BCP-47 language tag to extract (e.g. "en-GB").  Case-insensitive.
        If None, the first TUV encountered in each TU is returned.
    src_lang:
        If provided, used to identify the source TUV when lang is None.
    """
    tree = ET.parse(str(path))
    root = tree.getroot()

    lines = []
    for tu in root.iter("tu"):
        for tuv in tu.findall("tuv"):
            tuv_lang = (tuv.get("{http://www.w3.org/XML/1998/namespace}lang")
                        or tuv.get("lang") or "")
            if lang and tuv_lang.lower() != lang.lower():
                continue
            seg = tuv.find("seg")
            if seg is not None:
                text = (seg.text or "").strip()
                if text:
                    lines.append(text)
                break
    return lines


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def read_input(path: str | Path,
               filetype: str = "t",
               lang: str = "",
               pdftotext_bin: str = "pdftotext",
               pdf_layout: bool = True,
               antiword_bin: str = "antiword",
               abiword_bin: str = "abiword") -> list[str]:
    """
    Unified entry point.  Routes to the appropriate reader based on file
    extension (or explicit filetype override).

    filetype values:
      t    = plain text (default)
      p    = PDF
      h    = HTML
      w    = legacy Word .doc  (antiword)
      docx = Word .docx        (python-docx)
      rtf  = RTF / ODT / ABW   (abiword)
    """
    p = Path(path)
    ft = filetype.lower()
    ext = p.suffix.lower()

    if ft == "p" or ext == ".pdf":
        raw = read_pdf(p, pdftotext_bin=pdftotext_bin, layout=pdf_layout)
        return _pdf_paragraph_merge(raw, lang=lang)
    elif ft == "h" or ext in {".html", ".htm", ".xhtml"}:
        return read_html_file(p)
    elif ft == "w" or ext == ".doc":
        return read_doc(p, antiword_bin=antiword_bin)
    elif ft == "rtf" or ext in {".rtf", ".odt", ".abw", ".zabw"}:
        return read_abiword(p, abiword_bin=abiword_bin)
    elif ext == ".docx":
        return read_docx(p)
    elif ext in {".xliff", ".sdlxliff", ".txlf"}:
        return read_xliff(p)
    elif ext == ".tmx":
        return read_tmx(p, lang=lang)
    else:
        return read_txt(p)
