"""
Utility functions: logging, file-rename with retry, timestamps.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_path: str | Path, append: bool = False) -> logging.Logger:
    """
    Create (or return existing) logger that writes to *log_path* and to stderr.
    Pass append=True to append to an existing log file (batch/cmdline mode).

    The log file is always created at *log_path* AND mirrored to a fallback
    location (~/.lfp_aligner/last_run.log) so that it can be found even if the
    requested output directory is inaccessible or if the run failed before the
    output directory was created.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("lfp_aligner")
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    mode = "a" if append else "w"

    # Primary log — next to the output files
    fh = logging.FileHandler(log_path, mode=mode, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Fallback log — always in a fixed, known location
    fallback = Path.home() / ".lfp_aligner" / "last_run.log"
    fallback.parent.mkdir(parents=True, exist_ok=True)
    fh2 = logging.FileHandler(fallback, mode="w", encoding="utf-8")
    fh2.setFormatter(fmt)
    logger.addHandler(fh2)

    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(sh)

    logger.debug("Log file: %s", log_path)
    logger.debug("Fallback log: %s", fallback)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("lfp_aligner")


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

def safe_rename(src: str | Path, dst: str | Path, max_attempts: int = 5) -> None:
    """
    Rename *src* to *dst* with retries (mirrors Perl's ren() sub).
    Retries are useful on Windows where backup software may briefly lock files.
    """
    src, dst = Path(src), Path(dst)
    for attempt in range(1, max_attempts + 1):
        try:
            src.rename(dst)
            return
        except OSError as exc:
            if attempt >= max_attempts:
                raise RuntimeError(
                    f"Could not rename {src} → {dst} after {max_attempts} attempts: {exc}"
                ) from exc
            get_logger().warning("Rename attempt %d failed (%s), retrying…", attempt, exc)
            time.sleep(attempt)


def write_utf8(path: str | Path, lines: list[str], bom: bool = False) -> None:
    """Write a list of lines to *path* as UTF-8, optionally with BOM."""
    encoding = "utf-8-sig" if bom else "utf-8"
    Path(path).write_text("\n".join(lines) + "\n", encoding=encoding)


def read_utf8(path: str | Path) -> list[str]:
    """Read a UTF-8 (or UTF-8-BOM) file and return lines without newlines."""
    return Path(path).read_text(encoding="utf-8-sig").splitlines()


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def tmx_timestamp() -> str:
    """Return current UTC time in TMX format: yyyymmddThhmmssZ."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def log_timestamp() -> str:
    """Return current local time as a human-readable string for log entries."""
    return datetime.now().strftime("%Y.%m.%d_%H.%M.%S")


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def count_nonempty_lines(path: str | Path) -> int:
    """Count non-blank lines in a file."""
    lines = read_utf8(path)
    return sum(1 for l in lines if l.strip())


def apply_charconv(text: str, table: dict[str, str]) -> str:
    """Apply a character-conversion mapping to *text*."""
    for src_char, dst_char in table.items():
        text = text.replace(src_char, dst_char)
    return text
