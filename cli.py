"""
Command-line interface for LF Aligner (Python port).

Usage examples
--------------
  # Basic bilingual alignment
  python -m lfp_aligner en_text.txt pl_text.txt --langs en pl --out ./output

  # PDF input with explicit TMX language codes
  python -m lfp_aligner doc.en.pdf doc.pl.pdf --langs en pl \\
      --filetype p --tmx-langs EN-GB PL-PL --out ./output

  # Three languages
  python -m lfp_aligner a.txt b.txt c.txt --langs en pl de --out ./output

  # No segmentation, no TMX
  python -m lfp_aligner a.txt b.txt --langs en pl --no-segment --no-tmx

  # Launch the Qt6 GUI
  python -m lfp_aligner --gui
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lfp_aligner",
        description="LF Aligner — bilingual/multilingual sentence aligner (Python port)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Input
    p.add_argument("files", nargs="*", metavar="FILE",
                   help="Input files, one per language.")
    p.add_argument("--langs", nargs="+", metavar="LANG",
                   help="ISO 639-1 language codes in the same order as FILES.")
    p.add_argument("--filetype", choices=["t", "p", "h", "docx"],
                   default="t",
                   help="Input file type: t=text, p=pdf, h=html, docx=Word. "
                        "Default: t (auto-detected from extension).")

    # Output
    p.add_argument("--out", metavar="DIR", default=".",
                   help="Output directory. Default: current directory.")
    p.add_argument("--stem", metavar="NAME", default="aligned",
                   help="Output file base name. Default: aligned.")

    # Segmentation
    seg_grp = p.add_mutually_exclusive_group()
    seg_grp.add_argument("--segment", dest="segment",
                          action="store_const", const="y",
                          help="Always sentence-segment (default).")
    seg_grp.add_argument("--no-segment", dest="segment",
                          action="store_const", const="n",
                          help="Skip sentence segmentation.")
    seg_grp.add_argument("--auto-segment", dest="segment",
                          action="store_const", const="auto",
                          help="Segment then auto-revert if imbalanced.")
    p.set_defaults(segment="y")

    # Filters
    p.add_argument("--no-cleanup", action="store_true",
                   help="Do not remove ~~~ markers and leading hyphens.")
    p.add_argument("--keep-confidence", action="store_true",
                   help="Keep the hunalign confidence score column.")
    p.add_argument("--dedup", action="store_true",
                   help="Remove duplicate segment pairs.")
    p.add_argument("--filter-untranslated", action="store_true",
                   help="Remove segments where source == target.")
    p.add_argument("--merge-headings", action="store_true",
                   help="Merge number-only and heading lines with next segment.")

    # TMX options
    p.add_argument("--no-tmx", action="store_true",
                   help="Do not write a TMX file.")
    p.add_argument("--tmx-langs", nargs="+", metavar="CODE",
                   help="BCP-47 language codes for TMX (e.g. EN-GB PL-PL). "
                        "Defaults to LANG values uppercased.")
    p.add_argument("--creator-id", default="",
                   help="Creator ID string for the TMX header.")
    p.add_argument("--skip-half-empty", action="store_true", default=True,
                   help="Skip TMX TUs where any language is empty (default on).")

    # Chunking
    p.add_argument("--chop", type=int, default=15000, metavar="N",
                   help="Chop files larger than N segments (0=disable). Default: 15000.")

    # PDF options
    p.add_argument("--pdf-no-layout", action="store_true",
                   help="Use plain (non-layout) pdftotext mode.")

    # Setup file
    p.add_argument("--setup", metavar="FILE",
                   help="Path to LF_aligner_setup.txt to load defaults from.")

    # External binary overrides
    p.add_argument("--antiword", metavar="BIN", default="antiword",
                   help="Path to antiword binary (default: antiword).")
    p.add_argument("--abiword", metavar="BIN", default="abiword",
                   help="Path to abiword binary (default: abiword).")

    # Script directory (where hunalign, dictionaries etc. live)
    p.add_argument("--script-dir", metavar="DIR",
                   help="Root of the LF Aligner installation directory.")

    # GUI
    p.add_argument("--gui", action="store_true",
                   help="Launch the Qt6 graphical interface.")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args   = parser.parse_args(argv)

    # -----------------------------------------------------------------------
    # GUI mode
    # -----------------------------------------------------------------------
    if args.gui:
        from .gui import launch_gui
        return launch_gui()

    # -----------------------------------------------------------------------
    # Validate non-GUI arguments
    # -----------------------------------------------------------------------
    if not args.files:
        parser.print_help()
        return 0

    if not args.langs:
        parser.error("--langs is required when providing input files.")

    if len(args.files) != len(args.langs):
        parser.error(
            f"Number of files ({len(args.files)}) must match "
            f"number of --langs values ({len(args.langs)})."
        )

    # -----------------------------------------------------------------------
    # Build config from command-line arguments (optionally layered over setup)
    # -----------------------------------------------------------------------
    from .config import AlignmentConfig, load as load_config

    if args.setup:
        cfg = load_config(args.setup)
    else:
        cfg = AlignmentConfig()

    # Override config with explicit CLI flags
    cfg.filetype              = args.filetype
    cfg.segment               = args.segment
    cfg.cleanup               = not args.no_cleanup
    cfg.remove_confidence     = not args.keep_confidence
    cfg.delete_dupes          = args.dedup
    cfg.delete_untranslated   = args.filter_untranslated
    cfg.merge_numbers_headings = args.merge_headings
    cfg.make_tmx              = not args.no_tmx
    cfg.creator_id            = args.creator_id
    cfg.skip_half_empty       = args.skip_half_empty
    cfg.chop_threshold        = args.chop
    cfg.pdf_layout            = not args.pdf_no_layout

    # -----------------------------------------------------------------------
    # Set up logging
    # -----------------------------------------------------------------------
    from .utils import setup_logging
    log_path = Path(args.out) / "lfp_aligner.log"
    log = setup_logging(log_path, append=False)

    # -----------------------------------------------------------------------
    # Run pipeline
    # -----------------------------------------------------------------------
    from .pipeline import run

    try:
        result = run(
            input_files  = args.files,
            langs        = args.langs,
            output_dir   = args.out,
            tmx_langs    = args.tmx_langs,
            cfg          = cfg,
            script_dir   = args.script_dir,
            output_stem  = args.stem,
            antiword_bin = args.antiword,
            abiword_bin  = args.abiword,
        )
    except Exception as exc:
        log.error("FATAL: %s", exc)
        return 1

    if result.txt_path:
        print(f"TXT: {result.txt_path}")
    if result.tmx_path:
        print(f"TMX: {result.tmx_path}")
    print(f"Pairs: {len(result.pairs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
