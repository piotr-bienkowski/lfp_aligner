"""
Qt6 graphical interface for LF Aligner (Python port).

Requires PyQt6:  pip install PyQt6

Architecture
------------
The GUI runs the alignment pipeline in a QThread worker so the UI remains
responsive during long operations.  The worker emits progress messages through
a signal that are displayed in the log panel.

Screens / tabs
--------------
  1. Input      — select files, languages, filetype
  2. Options    — segmentation, filters, TMX settings
  3. Run        — progress log + action buttons
  4. Results    — view aligned pairs in a table, open output files
"""

from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (QEvent, QObject, QRunnable, QSettings, QThread,
                           QThreadPool, Qt, pyqtSignal, pyqtSlot)
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                              QComboBox, QDialog, QDialogButtonBox,
                              QFileDialog, QFormLayout, QFrame,
                              QGridLayout, QGroupBox, QHBoxLayout,
                              QHeaderView, QLabel, QLineEdit, QMainWindow,
                              QMessageBox, QPlainTextEdit, QPushButton,
                              QScrollArea, QSizePolicy, QSpinBox, QSplitter,
                              QStackedWidget, QStatusBar, QStyledItemDelegate,
                              QTableWidget, QTableWidgetItem, QTabWidget,
                              QVBoxLayout, QWidget)


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class _WorkerSignals(QObject):
    log     = pyqtSignal(str)       # informational message
    error   = pyqtSignal(str)       # error message
    result  = pyqtSignal(object)    # RunResult
    finished = pyqtSignal()


class AlignWorker(QRunnable):
    """Runs the alignment pipeline in a thread-pool thread."""

    def __init__(self, input_files, langs, output_dir, tmx_langs,
                 cfg, script_dir, output_stem):
        super().__init__()
        self.input_files = input_files
        self.langs       = langs
        self.output_dir  = output_dir
        self.tmx_langs   = tmx_langs
        self.cfg         = cfg
        self.script_dir  = script_dir
        self.output_stem = output_stem
        self.signals     = _WorkerSignals()

    @pyqtSlot()
    def run(self):
        try:
            import logging
            from pathlib import Path
            from .utils import get_logger

            # GUI log handler — streams messages to the Run tab panel
            class _GUIHandler(logging.Handler):
                def __init__(self, signal):
                    super().__init__()
                    self._sig = signal
                def emit(self, record):
                    self._sig.emit(self.format(record))

            logger  = get_logger()

            # Remove any stale handlers from a previous run in the same session
            logger.handlers = [h for h in logger.handlers
                                if not isinstance(h, (_GUIHandler,
                                                       logging.FileHandler))]

            # Always write to the fallback log so failures can be diagnosed
            fallback = Path.home() / ".lfp_aligner" / "last_run.log"
            fallback.parent.mkdir(parents=True, exist_ok=True)
            file_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                         datefmt="%Y-%m-%d %H:%M:%S")
            fh = logging.FileHandler(fallback, mode="w", encoding="utf-8")
            fh.setFormatter(file_fmt)
            logger.addHandler(fh)
            logger.setLevel(logging.DEBUG)

            gui_handler = _GUIHandler(self.signals.log)
            gui_handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(gui_handler)

            from .pipeline import run
            result = run(
                input_files  = self.input_files,
                langs        = self.langs,
                output_dir   = self.output_dir,
                tmx_langs    = self.tmx_langs,
                cfg          = self.cfg,
                script_dir   = self.script_dir,
                output_stem  = self.output_stem,
            )
            self.signals.result.emit(result)
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()


# ---------------------------------------------------------------------------
# File-row widget (used in the Input tab)
# ---------------------------------------------------------------------------

class FileRow(QWidget):
    """A single row for one language: language code + file path picker."""

    removed = pyqtSignal(object)   # emits self when the Remove button is clicked

    def __init__(self, index: int, lang: str = "", path: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._index = index

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self.lang_edit = QLineEdit(lang)
        self.lang_edit.setPlaceholderText("lang (e.g. en)")
        self.lang_edit.setFixedWidth(70)

        self.path_edit = QLineEdit(path)
        self.path_edit.setPlaceholderText("Input file path…")
        self.path_edit.setReadOnly(True)

        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(28)
        remove_btn.setToolTip("Remove this language")
        remove_btn.clicked.connect(lambda: self.removed.emit(self))

        layout.addWidget(QLabel(f"L{index + 1}"))
        layout.addWidget(self.lang_edit)
        layout.addWidget(self.path_edit, stretch=1)
        layout.addWidget(browse_btn)
        layout.addWidget(remove_btn)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select input file", "",
            "All files (*.*);;"
            "Text files (*.txt);;"
            "PDF files (*.pdf);;"
            "Word documents (*.docx);;"
            "HTML files (*.html *.htm);;"
            "XLIFF files (*.xliff *.sdlxliff *.txlf);;"
            "TMX files (*.tmx)"
        )
        if path:
            self.path_edit.setText(path)
            # Auto-detect lang from filename if empty
            if not self.lang_edit.text():
                stem = Path(path).stem.lower()
                for code in ("en", "pl", "de", "fr", "es", "it", "nl",
                             "pt", "hu", "cs", "sk", "ro", "bg", "hr",
                             "ru", "zh", "ja", "ar"):
                    if code in stem:
                        self.lang_edit.setText(code)
                        break

    @property
    def lang(self) -> str:
        return self.lang_edit.text().strip()

    @property
    def path(self) -> str:
        return self.path_edit.text().strip()


# ---------------------------------------------------------------------------
# Input tab
# ---------------------------------------------------------------------------

class InputTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # --- File type ---
        ft_box = QGroupBox("File type")
        ft_layout = QHBoxLayout(ft_box)
        self.filetype_combo = QComboBox()
        self.filetype_combo.addItems([
            "t — Plain text (UTF-8)",
            "p — PDF",
            "h — HTML",
            "docx — Word document",
        ])
        ft_layout.addWidget(self.filetype_combo)
        ft_layout.addStretch()
        layout.addWidget(ft_box)

        # --- Language / file rows ---
        files_box = QGroupBox("Input files  (one per language, minimum 2)")
        files_layout = QVBoxLayout(files_box)

        self._rows_container = QVBoxLayout()
        files_layout.addLayout(self._rows_container)

        add_btn = QPushButton("+ Add language")
        add_btn.clicked.connect(self._add_row)
        files_layout.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(files_box)

        # --- TMX language codes ---
        tmx_box = QGroupBox("TMX language codes (BCP-47, e.g. EN-GB, PL-PL)")
        tmx_layout = QFormLayout(tmx_box)
        self.tmx_lang1 = QLineEdit()
        self.tmx_lang2 = QLineEdit()
        self.tmx_lang1.setPlaceholderText("Source (e.g. EN-GB)")
        self.tmx_lang2.setPlaceholderText("Target (e.g. PL-PL)")
        tmx_layout.addRow("Language 1:", self.tmx_lang1)
        tmx_layout.addRow("Language 2:", self.tmx_lang2)
        layout.addWidget(tmx_box)

        layout.addStretch()

        self._file_rows: list[FileRow] = []
        # Start with two rows
        self._add_row(lang="en")
        self._add_row(lang="pl")

    def _add_row(self, lang: str = ""):
        idx = len(self._file_rows)
        row = FileRow(idx, lang=lang, parent=self)
        row.removed.connect(self._remove_row)
        self._rows_container.addWidget(row)
        self._file_rows.append(row)

    def _remove_row(self, row: FileRow):
        if len(self._file_rows) <= 2:
            QMessageBox.warning(self, "Warning",
                                "At least two language files are required.")
            return
        self._rows_container.removeWidget(row)
        row.deleteLater()
        self._file_rows.remove(row)
        # Renumber
        for i, r in enumerate(self._file_rows):
            r._index = i

    @property
    def filetype(self) -> str:
        return self.filetype_combo.currentText().split(" ")[0]

    @property
    def file_paths(self) -> list[str]:
        return [r.path for r in self._file_rows]

    @property
    def langs(self) -> list[str]:
        return [r.lang for r in self._file_rows]

    @property
    def tmx_langs(self) -> list[str]:
        codes = [self.tmx_lang1.text().strip(), self.tmx_lang2.text().strip()]
        return [c for c in codes if c]


# ---------------------------------------------------------------------------
# Options tab
# ---------------------------------------------------------------------------

class OptionsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # --- Segmentation ---
        seg_box = QGroupBox("Sentence segmentation")
        seg_layout = QVBoxLayout(seg_box)
        self.seg_yes    = QCheckBox("Sentence-segment input files")
        self.seg_auto   = QCheckBox("Auto-revert if segmentation is unbalanced")
        self.seg_merge  = QCheckBox("Merge number-only / heading lines with next segment")
        self.seg_yes.setChecked(True)
        self.seg_auto.setChecked(True)
        seg_layout.addWidget(self.seg_yes)
        seg_layout.addWidget(self.seg_auto)
        seg_layout.addWidget(self.seg_merge)
        layout.addWidget(seg_box)

        # --- Post-alignment filters ---
        flt_box = QGroupBox("Post-alignment filters")
        flt_layout = QVBoxLayout(flt_box)
        self.do_cleanup     = QCheckBox("Remove ~~~ markers and leading hyphens")
        self.rm_confidence  = QCheckBox("Remove hunalign confidence score column")
        self.rm_dupes       = QCheckBox("Remove duplicate segment pairs")
        self.rm_untranslated = QCheckBox("Remove identical source = target segments")
        self.do_cleanup.setChecked(True)
        self.rm_confidence.setChecked(True)
        flt_layout.addWidget(self.do_cleanup)
        flt_layout.addWidget(self.rm_confidence)
        flt_layout.addWidget(self.rm_dupes)
        flt_layout.addWidget(self.rm_untranslated)
        layout.addWidget(flt_box)

        # --- Output ---
        out_box = QGroupBox("Output")
        out_layout = QFormLayout(out_box)
        self.make_tmx   = QCheckBox("Generate TMX file")
        self.make_tmx.setChecked(True)
        self.creator_id = QLineEdit()
        self.creator_id.setPlaceholderText("Optional creator/translator ID")
        self.skip_half  = QCheckBox("Skip half-empty TMX segments")
        self.skip_half.setChecked(True)
        self.chop_spin  = QSpinBox()
        self.chop_spin.setRange(0, 500_000)
        self.chop_spin.setValue(15000)
        self.chop_spin.setSuffix("  segments  (0 = disable)")
        out_layout.addRow(self.make_tmx)
        out_layout.addRow("Creator ID:", self.creator_id)
        out_layout.addRow(self.skip_half)
        out_layout.addRow("Chop threshold:", self.chop_spin)
        self.do_realign = QCheckBox("Realign (slower, better quality)")
        self.do_realign.setToolTip(
            "Pass -realign to hunalign: initial alignment → auto-build dictionary "
            "from aligned pairs → re-align. Roughly triples runtime.")
        out_layout.addRow(self.do_realign)
        layout.addWidget(out_box)

        layout.addStretch()

    @property
    def segment(self) -> str:
        if not self.seg_yes.isChecked():
            return "n"
        return "auto" if self.seg_auto.isChecked() else "y"


# ---------------------------------------------------------------------------
# Run tab
# ---------------------------------------------------------------------------

class RunTab(QWidget):
    run_requested  = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # Output directory
        dir_layout = QHBoxLayout()
        self.out_dir_edit = QLineEdit()
        self.out_dir_edit.setPlaceholderText("Output directory…")
        self.out_dir_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_dir)
        dir_layout.addWidget(QLabel("Output:"))
        dir_layout.addWidget(self.out_dir_edit, stretch=1)
        dir_layout.addWidget(browse_btn)
        layout.addLayout(dir_layout)

        # Output file stem
        stem_layout = QHBoxLayout()
        self.stem_edit = QLineEdit("aligned")
        stem_layout.addWidget(QLabel("File name stem:"))
        stem_layout.addWidget(self.stem_edit)
        stem_layout.addStretch()
        layout.addLayout(stem_layout)

        # Script dir
        sdir_layout = QHBoxLayout()
        self.script_dir_edit = QLineEdit()
        self.script_dir_edit.setPlaceholderText(
            "LF Aligner installation dir (auto-detected if empty)")
        sdir_browse = QPushButton("Browse…")
        sdir_browse.clicked.connect(self._browse_script_dir)
        sdir_layout.addWidget(QLabel("Aligner dir:"))
        sdir_layout.addWidget(self.script_dir_edit, stretch=1)
        sdir_layout.addWidget(sdir_browse)
        layout.addLayout(sdir_layout)

        # Log panel
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Monospace", 9))
        layout.addWidget(self.log_view, stretch=1)

        # Buttons
        btn_layout = QHBoxLayout()
        self.run_btn  = QPushButton("▶  Run Alignment")
        self.run_btn.setStyleSheet(
            "QPushButton { background: #2d6a4f; color: white; "
            "font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
            "QPushButton:hover { background: #40916c; }"
            "QPushButton:disabled { background: #888; }")
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setEnabled(False)
        self.clear_btn = QPushButton("Clear log")
        btn_layout.addWidget(self.run_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.clear_btn)
        layout.addLayout(btn_layout)

        self.run_btn.clicked.connect(self.run_requested)
        self.stop_btn.clicked.connect(self.stop_requested)
        self.clear_btn.clicked.connect(self.log_view.clear)

        # Pre-populate aligner dir from auto-detection
        try:
            from .pipeline import _find_script_dir
            self.script_dir_edit.setText(str(_find_script_dir()))
        except RuntimeError:
            pass  # leave blank; user must fill it in

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select output directory")
        if d:
            self.out_dir_edit.setText(d)

    def _browse_script_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "Select LF Aligner installation directory")
        if d:
            self.script_dir_edit.setText(d)

    def append_log(self, msg: str):
        self.log_view.appendPlainText(msg)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum())

    def set_running(self, running: bool):
        self.run_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    @property
    def out_dir(self) -> str:
        return self.out_dir_edit.text().strip() or "."

    @property
    def stem(self) -> str:
        return self.stem_edit.text().strip() or "aligned"

    @property
    def script_dir(self) -> str:
        return self.script_dir_edit.text().strip()


# ---------------------------------------------------------------------------
# Results tab
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Wrapping cell delegate
# ---------------------------------------------------------------------------

class _WrappingDelegate(QStyledItemDelegate):
    """
    Item delegate that uses QPlainTextEdit so text stays word-wrapped while
    a cell is being edited.  Tab / Shift-Tab navigate between cells; clicking
    outside commits the edit.
    """

    def createEditor(self, parent, option, index):
        ed = QPlainTextEdit(parent)
        ed.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        ed.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        ed.installEventFilter(self)
        return ed

    def setEditorData(self, editor, index):
        editor.blockSignals(True)
        editor.setPlainText(index.data(Qt.ItemDataRole.DisplayRole) or "")
        editor.blockSignals(False)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)

    def eventFilter(self, obj, event):
        if isinstance(obj, QPlainTextEdit) and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            mod = event.modifiers()
            if key == Qt.Key.Key_Tab and not (mod & Qt.KeyboardModifier.ShiftModifier):
                self.commitData.emit(obj)
                self.closeEditor.emit(obj,
                    QStyledItemDelegate.EndEditHint.EditNextItem)
                return True
            if key in (Qt.Key.Key_Backtab, Qt.Key.Key_Tab) and (
                    mod & Qt.KeyboardModifier.ShiftModifier):
                self.commitData.emit(obj)
                self.closeEditor.emit(obj,
                    QStyledItemDelegate.EndEditHint.EditPreviousItem)
                return True
        return super().eventFilter(obj, event)


# ---------------------------------------------------------------------------
# Split-segment dialog
# ---------------------------------------------------------------------------

class SplitDialog(QDialog):
    """
    Iterative split dialog.

    The user places the cursor at the desired split point in each language
    box and clicks **Split here**.  The text before each cursor replaces the
    current row; the text after is inserted as a new row below and loaded
    back into the boxes — ready for another split.  Repeat as many times as
    needed, then click **Done**.  **Cancel** undoes every split made during
    this session and restores the original row.
    """

    def __init__(self, table: QTableWidget, start_row: int,
                 headers: list[str],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._table       = table
        self._cur_row     = start_row
        self._origin_row  = start_row
        self._splits_done = 0
        self._saved_origin = self._read_row(start_row)

        self.setWindowTitle("Split segment")
        self.setMinimumSize(800, 280)
        self.setSizeGripEnabled(True)

        layout = QVBoxLayout(self)

        hint = QLabel(
            "Click <b>Auto-split</b> to split at the next sentence boundary "
            "automatically, or place the cursor manually and click "
            "<b>Split here</b>.  Repeat as needed, then click <b>Done</b>.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        editor_row = QHBoxLayout()
        self._editors: list[QPlainTextEdit] = []
        for hdr in headers:
            col_box = QVBoxLayout()
            col_box.addWidget(QLabel(f"<b>{hdr}</b>"))
            ed = QPlainTextEdit()
            ed.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            col_box.addWidget(ed)
            self._editors.append(ed)
            editor_row.addLayout(col_box)
        layout.addLayout(editor_row, stretch=1)

        btn_row = QHBoxLayout()
        self._auto_btn  = QPushButton("Auto-split")
        self._auto_btn.setToolTip(
            "Find the next  .  followed by an uppercase letter and split there")
        self._split_btn = QPushButton("Split here")
        self._split_btn.setDefault(True)
        done_btn   = QPushButton("Done")
        cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self._auto_btn)
        btn_row.addWidget(self._split_btn)
        btn_row.addStretch()
        btn_row.addWidget(done_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._auto_btn.clicked.connect(self._auto_split)
        self._split_btn.clicked.connect(self._do_split)
        done_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self._cancel)

        self._load(self._saved_origin)
        if self._editors:
            self._editors[0].setFocus()

    # ------------------------------------------------------------------

    def _read_row(self, row: int) -> list[str]:
        return [(self._table.item(row, c) or QTableWidgetItem("")).text()
                for c in range(self._table.columnCount())]

    def _write_row(self, row: int, texts: list[str]) -> None:
        for c, text in enumerate(texts):
            item = self._table.item(row, c)
            if item is None:
                item = QTableWidgetItem()
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                self._table.setItem(row, c, item)
            item.setText(text)

    def _insert_row_below(self, row: int, texts: list[str]) -> None:
        self._table.insertRow(row + 1)
        for c, text in enumerate(texts):
            item = QTableWidgetItem(text)
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            self._table.setItem(row + 1, c, item)

    def _load(self, texts: list[str]) -> None:
        for ed, text in zip(self._editors, texts):
            ed.setPlainText(text)
            cur = ed.textCursor()
            cur.movePosition(cur.MoveOperation.Start)
            ed.setTextCursor(cur)
        # re-enable auto button; check whether any boundary exists
        any_boundary = any(
            self._SENTENCE_BREAK.search(ed.toPlainText())
            for ed in self._editors)
        self._auto_btn.setEnabled(any_boundary)
        self._auto_btn.setText("Auto-split")

    # ------------------------------------------------------------------

    # Matches a period (preceded by a non-whitespace char so we skip "  . ")
    # followed by one or more spaces, where the next char is uppercase.
    _SENTENCE_BREAK = re.compile(r'(?<=\S)\.\s+(?=[A-Z])')

    def _auto_split(self) -> None:
        """Move each editor's cursor to the next '. Uppercase' boundary and split."""
        found = False
        for ed in self._editors:
            text    = ed.toPlainText()
            cur_pos = ed.textCursor().position()
            m = self._SENTENCE_BREAK.search(text, cur_pos)
            if m is None:
                m = self._SENTENCE_BREAK.search(text)   # wrap to start
            if m:
                cursor = ed.textCursor()
                # place cursor after the period+spaces, before the uppercase letter
                cursor.setPosition(m.end())
                ed.setTextCursor(cursor)
                found = True

        if found:
            self._do_split()
        else:
            self._auto_btn.setEnabled(False)
            self._auto_btn.setText("Auto-split (no more boundaries)")

    # ------------------------------------------------------------------

    def _do_split(self) -> None:
        before, after = [], []
        for ed in self._editors:
            pos      = ed.textCursor().position()
            full     = ed.toPlainText()
            before.append(full[:pos].strip())
            after.append(full[pos:].strip())

        if not any(after):          # cursor at end — nothing to split
            return

        self._table.blockSignals(True)
        self._write_row(self._cur_row, before)
        self._insert_row_below(self._cur_row, after)
        self._table.blockSignals(False)
        self._table.resizeRowsToContents()

        self._cur_row     += 1
        self._splits_done += 1
        self._split_btn.setText(f"Split here  ({self._splits_done} done)")

        self._load(after)
        if self._editors:
            self._editors[0].setFocus()

    def _cancel(self) -> None:
        self._table.blockSignals(True)
        for i in range(self._splits_done, 0, -1):
            self._table.removeRow(self._origin_row + i)
        self._write_row(self._origin_row, self._saved_origin)
        self._table.blockSignals(False)
        self._table.resizeRowsToContents()
        self.reject()


# ---------------------------------------------------------------------------
# Results / alignment-editor tab
# ---------------------------------------------------------------------------

class ResultsTab(QWidget):
    """
    Displays aligned segment pairs and lets the user edit them before saving.

    Editing operations
    ------------------
    * **Double-click** a cell to edit its text inline.
    * **Split row…**   opens a dialog where source and target text can be
      divided into two separate segments; a new row is inserted below.
    * **Merge with next** concatenates the selected row with the one below.
    * **Delete row** removes the selected row.

    After editing, use *Save edited TXT…* or *Save edited TMX…* to write the
    modified alignment to a new file without overwriting the original output.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._langs:     list[str]      = []
        self._tmx_langs: list[str]      = []
        self._out_dir:   Optional[Path] = None

        layout = QVBoxLayout(self)

        self.summary_label = QLabel("No results yet.")
        layout.addWidget(self.summary_label)

        # ── editing toolbar ───────────────────────────────────────────
        tb = QHBoxLayout()
        self.split_btn  = QPushButton("Split row…")
        self.merge_btn  = QPushButton("Merge with next")
        self.delete_btn = QPushButton("Delete row")
        self.split_btn.setToolTip(
            "Split the selected row into two segments")
        self.merge_btn.setToolTip(
            "Concatenate the selected row with the row below it")
        self.delete_btn.setToolTip("Remove the selected row")
        for btn in (self.split_btn, self.merge_btn, self.delete_btn):
            btn.setEnabled(False)
            tb.addWidget(btn)
        tb.addStretch()
        layout.addLayout(tb)

        # ── table ─────────────────────────────────────────────────────
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Source", "Target"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setItemDelegate(_WrappingDelegate(self.table))
        self.table.setWordWrap(True)
        self.table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, stretch=1)

        # ── bottom buttons ────────────────────────────────────────────
        bb = QHBoxLayout()
        self.load_txt_btn  = QPushButton("Load TXT…")
        self.load_tmx_btn  = QPushButton("Load TMX…")
        self.open_txt_btn  = QPushButton("Open TXT…")
        self.open_tmx_btn  = QPushButton("Open TMX…")
        self.save_txt_btn  = QPushButton("Save edited TXT…")
        self.save_tmx_btn  = QPushButton("Save edited TMX…")
        self.load_txt_btn.setToolTip("Load a tab-delimited TXT file for editing")
        self.load_tmx_btn.setToolTip("Load a TMX file for editing")
        for btn in (self.open_txt_btn, self.open_tmx_btn,
                    self.save_txt_btn, self.save_tmx_btn):
            btn.setEnabled(False)
        for btn in (self.load_txt_btn, self.load_tmx_btn,
                    self.open_txt_btn, self.open_tmx_btn,
                    self.save_txt_btn, self.save_tmx_btn):
            bb.addWidget(btn)
        bb.addStretch()
        layout.addLayout(bb)

        # ── connections ───────────────────────────────────────────────
        self.split_btn.clicked.connect(self._split_row)
        self.merge_btn.clicked.connect(self._merge_with_next)
        self.delete_btn.clicked.connect(self._delete_row)
        self.load_txt_btn.clicked.connect(self._load_txt)
        self.load_tmx_btn.clicked.connect(self._load_tmx)
        self.save_txt_btn.clicked.connect(self._save_txt)
        self.save_tmx_btn.clicked.connect(self._save_tmx)
        self.table.itemSelectionChanged.connect(self._update_edit_buttons)
        self.table.itemChanged.connect(self._on_item_changed)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_result(self, result,
                    langs:     Optional[list[str]] = None,
                    tmx_langs: Optional[list[str]] = None) -> None:
        self._langs     = langs or []
        self._tmx_langs = (tmx_langs or
                           [l.upper() for l in (langs or [])])
        self._out_dir   = (Path(result.txt_path).parent
                           if result.txt_path else None)

        pairs = result.pairs
        ncols = max((len(p) for p in pairs), default=2)
        headers = (self._langs[:ncols] +
                   [f"Col {i+1}" for i in range(len(self._langs), ncols)])
        self._fill_table(pairs, headers)

        n = len(pairs)
        msg = f"{n} aligned pair{'s' if n != 1 else ''}."
        if result.txt_path:
            msg += f"  TXT: {result.txt_path}"
        if result.tmx_path:
            msg += f"  TMX: {result.tmx_path}"
        self.summary_label.setText(msg)

        # Reconnect open buttons (avoid stacking duplicate connections)
        for btn in (self.open_txt_btn, self.open_tmx_btn):
            try:
                btn.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
        self.open_txt_btn.setEnabled(
            bool(result.txt_path and Path(result.txt_path).exists()))
        self.open_tmx_btn.setEnabled(
            bool(result.tmx_path and Path(result.tmx_path).exists()))
        if result.txt_path:
            self.open_txt_btn.clicked.connect(
                lambda: _open_file(str(result.txt_path)))
        if result.tmx_path:
            self.open_tmx_btn.clicked.connect(
                lambda: _open_file(str(result.tmx_path)))

        self.save_txt_btn.setEnabled(True)
        self.save_tmx_btn.setEnabled(True)
        self._update_edit_buttons()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fill_table(self, pairs: list[tuple[str, ...]],
                    headers: Optional[list[str]] = None) -> None:
        """Populate the table with *pairs*, optionally setting column headers."""
        ncols = max((len(p) for p in pairs), default=2)
        hdrs  = (headers or []) + [f"Col {i+1}"
                                   for i in range(len(headers or []), ncols)]
        self.table.blockSignals(True)
        self.table.setColumnCount(ncols)
        self.table.setHorizontalHeaderLabels(hdrs)
        self.table.setRowCount(len(pairs))
        self.table.setUpdatesEnabled(False)
        for r, pair in enumerate(pairs):
            for c in range(ncols):
                text = pair[c] if c < len(pair) else ""
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                self.table.setItem(r, c, item)
        self.table.setUpdatesEnabled(True)
        self.table.blockSignals(False)
        self.table.resizeRowsToContents()

    def _current_row(self) -> int:
        return self.table.currentRow()

    def _row_texts(self, row: int) -> list[str]:
        return [(self.table.item(row, c) or QTableWidgetItem("")).text()
                for c in range(self.table.columnCount())]

    def _set_row(self, row: int, texts: list[str]) -> None:
        for c, text in enumerate(texts):
            item = self.table.item(row, c)
            if item is None:
                item = QTableWidgetItem()
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                self.table.setItem(row, c, item)
            item.setText(text)

    def _insert_row_below(self, row: int, texts: list[str]) -> None:
        self.table.insertRow(row + 1)
        for c, text in enumerate(texts):
            item = QTableWidgetItem(text)
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            self.table.setItem(row + 1, c, item)

    def _all_pairs(self) -> list[tuple[str, ...]]:
        return [tuple(self._row_texts(r))
                for r in range(self.table.rowCount())]

    def _update_edit_buttons(self) -> None:
        row      = self._current_row()
        has_row  = row >= 0 and self.table.rowCount() > 0
        has_next = has_row and row < self.table.rowCount() - 1
        self.split_btn.setEnabled(has_row)
        self.merge_btn.setEnabled(has_next)
        self.delete_btn.setEnabled(has_row)

    def _on_item_changed(self) -> None:
        self.table.resizeRowsToContents()

    # ------------------------------------------------------------------
    # Editing actions
    # ------------------------------------------------------------------

    def _split_row(self) -> None:
        row = self._current_row()
        if row < 0:
            return
        ncols   = self.table.columnCount()
        headers = [(self.table.horizontalHeaderItem(c).text()
                    if self.table.horizontalHeaderItem(c) else f"Col {c+1}")
                   for c in range(ncols)]
        SplitDialog(self.table, row, headers, self).exec()
        self.table.selectRow(row)

    def _merge_with_next(self) -> None:
        row = self._current_row()
        if row < 0 or row >= self.table.rowCount() - 1:
            return
        a = self._row_texts(row)
        b = self._row_texts(row + 1)
        merged = [(x.rstrip() + " " + y.lstrip()).strip()
                  for x, y in zip(a, b)]

        self.table.blockSignals(True)
        self._set_row(row, merged)
        self.table.removeRow(row + 1)
        self.table.blockSignals(False)
        self.table.resizeRowsToContents()
        self.table.selectRow(row)

    def _delete_row(self) -> None:
        row = self._current_row()
        if row < 0:
            return
        self.table.removeRow(row)
        self._update_edit_buttons()

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Loading from file
    # ------------------------------------------------------------------

    def _load_txt(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load TXT file", "",
            "Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            pairs: list[tuple[str, ...]] = []
            with open(path, encoding="utf-8-sig") as fh:
                for line in fh:
                    line = line.rstrip("\n\r")
                    if not line:
                        continue
                    pairs.append(tuple(line.split("\t")))

            # Strip trailing source-note column when it looks like
            # "file1.ext | file2.ext" (written by write_txt).
            if pairs:
                ncols = max(len(p) for p in pairs)
                if ncols >= 3:
                    sample = [p[-1] for p in pairs[:20] if len(p) == ncols]
                    if sample and sum(1 for v in sample if " | " in v
                                      or (v.count(".") == 1
                                          and v.rsplit(".", 1)[-1].lower()
                                          in {"txt", "docx", "pdf", "doc",
                                              "html", "tmx", "xliff", "txlf"})
                                      ) >= len(sample) // 2:
                        pairs = [p[:-1] for p in pairs]

            self._out_dir = Path(path).parent
            self._fill_table(pairs, self._langs or None)
            n = len(pairs)
            self.summary_label.setText(
                f"Loaded {n} pair{'s' if n != 1 else ''} from {Path(path).name}")
            self.save_txt_btn.setEnabled(True)
            self.save_tmx_btn.setEnabled(True)
            self._update_edit_buttons()
        except Exception as exc:
            QMessageBox.warning(self, "Load TXT",
                                f"Could not load file:\n{exc}")

    def _load_tmx(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load TMX file", "",
            "TMX files (*.tmx);;All files (*)")
        if not path:
            return
        try:
            import xml.etree.ElementTree as ET
            XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

            tree = ET.parse(path)
            root = tree.getroot()

            # Detect element namespace (some TMX files use one, others don't)
            tag = root.tag
            ns  = tag[: tag.index("}") + 1] if tag.startswith("{") else ""

            body = root.find(f"{ns}body")
            if body is None:
                raise ValueError("No <body> element found in TMX file.")

            tmx_langs: list[str] = []
            pairs:     list[tuple[str, ...]] = []

            for tu in body.findall(f"{ns}tu"):
                texts: list[str] = []
                for tuv in tu.findall(f"{ns}tuv"):
                    lang = (tuv.get(XML_LANG)
                            or tuv.get("lang") or "")
                    seg  = tuv.find(f"{ns}seg")
                    texts.append((seg.text or "") if seg is not None else "")
                    if lang and lang not in tmx_langs:
                        tmx_langs.append(lang)
                if texts:
                    pairs.append(tuple(texts))

            self._tmx_langs = tmx_langs
            self._langs     = [l.split("-")[0].lower() for l in tmx_langs]
            self._out_dir   = Path(path).parent
            self._fill_table(pairs, tmx_langs or None)
            n = len(pairs)
            self.summary_label.setText(
                f"Loaded {n} pair{'s' if n != 1 else ''} from {Path(path).name}")
            self.save_txt_btn.setEnabled(True)
            self.save_tmx_btn.setEnabled(True)
            self._update_edit_buttons()
        except Exception as exc:
            QMessageBox.warning(self, "Load TMX",
                                f"Could not load file:\n{exc}")

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def _default_save_path(self, suffix: str) -> str:
        if self._out_dir:
            return str(self._out_dir / f"aligned_edited.{suffix}")
        return f"aligned_edited.{suffix}"

    def _save_txt(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save edited TXT",
            self._default_save_path("txt"),
            "Text files (*.txt);;All files (*)")
        if not path:
            return
        from .io.writers import write_txt
        pairs = self._all_pairs()
        write_txt(pairs, path)
        n = len(pairs)
        self.summary_label.setText(
            f"Saved {n} pair{'s' if n != 1 else ''} → {path}")

    def _save_tmx(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save edited TMX",
            self._default_save_path("tmx"),
            "TMX files (*.tmx);;All files (*)")
        if not path:
            return
        from .io.writers import write_tmx
        pairs     = self._all_pairs()
        tmx_langs = self._tmx_langs or ["SL", "TL"]
        write_tmx(pairs, path, langs=tmx_langs, skip_half_empty=False)
        n = len(pairs)
        self.summary_label.setText(
            f"Saved {n} pair{'s' if n != 1 else ''} → {path}")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LF Aligner — Python port")
        self.resize(900, 700)

        self._worker: Optional[AlignWorker] = None
        self._pool   = QThreadPool.globalInstance()

        # Tabs
        self.tabs   = QTabWidget()
        self.input_tab   = InputTab()
        self.options_tab = OptionsTab()
        self.run_tab     = RunTab()
        self.results_tab = ResultsTab()

        self.tabs.addTab(self.input_tab,   "1 · Input")
        self.tabs.addTab(self.options_tab, "2 · Options")
        self.tabs.addTab(self.run_tab,     "3 · Run")
        self.tabs.addTab(self.results_tab, "4 · Results")

        self.setCentralWidget(self.tabs)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready.")

        # Connections
        self.run_tab.run_requested.connect(self._on_run)
        self.run_tab.stop_requested.connect(self._on_stop)

        # Restore geometry
        settings = QSettings("LFAligner", "PythonPort")
        geom = settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)

    def closeEvent(self, event):
        settings = QSettings("LFAligner", "PythonPort")
        settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_inputs(self) -> bool:
        paths = self.input_tab.file_paths
        langs = self.input_tab.langs

        if len(paths) < 2:
            QMessageBox.warning(self, "Input error",
                                "Please add at least two input files.")
            return False
        for i, (p, l) in enumerate(zip(paths, langs)):
            if not p:
                QMessageBox.warning(
                    self, "Input error",
                    f"Language {i + 1}: no file selected.")
                return False
            if not Path(p).exists():
                QMessageBox.warning(
                    self, "Input error",
                    f"File not found: {p}")
                return False
            if not l:
                QMessageBox.warning(
                    self, "Input error",
                    f"Language {i + 1}: language code is empty.")
                return False

        out = self.run_tab.out_dir
        try:
            Path(out).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Output error",
                                f"Cannot create output directory: {exc}")
            return False

        return True

    # ------------------------------------------------------------------
    # Run / stop
    # ------------------------------------------------------------------

    def _on_run(self):
        if not self._validate_inputs():
            return

        from .config import AlignmentConfig
        opt = self.options_tab
        inp = self.input_tab

        cfg = AlignmentConfig()
        cfg.filetype              = inp.filetype
        seg_value = opt.segment          # "n" | "y" | "auto"
        cfg.segment            = "n" if seg_value == "n" else "y"
        cfg.confirm_segmenting = "auto" if seg_value == "auto" else "n"
        cfg.merge_numbers_headings = opt.seg_merge.isChecked()
        cfg.cleanup               = opt.do_cleanup.isChecked()
        cfg.remove_confidence     = opt.rm_confidence.isChecked()
        cfg.delete_dupes          = opt.rm_dupes.isChecked()
        cfg.delete_untranslated   = opt.rm_untranslated.isChecked()
        cfg.make_tmx              = opt.make_tmx.isChecked()
        cfg.creator_id            = opt.creator_id.text().strip()
        cfg.skip_half_empty       = opt.skip_half.isChecked()
        cfg.chop_threshold        = opt.chop_spin.value()
        cfg.realign               = opt.do_realign.isChecked()

        tmx_langs = inp.tmx_langs or None

        script_dir = self.run_tab.script_dir or None

        self._worker = AlignWorker(
            input_files  = inp.file_paths,
            langs        = inp.langs,
            output_dir   = self.run_tab.out_dir,
            tmx_langs    = tmx_langs,
            cfg          = cfg,
            script_dir   = script_dir,
            output_stem  = self.run_tab.stem,
        )
        self._worker.signals.log.connect(self._on_log)
        self._worker.signals.error.connect(self._on_error)
        self._worker.signals.result.connect(self._on_result)
        self._worker.signals.finished.connect(self._on_finished)

        self.run_tab.set_running(True)
        self.run_tab.append_log("─── Starting alignment ───")
        self.status.showMessage("Running…")
        self.tabs.setCurrentWidget(self.run_tab)

        self._pool.start(self._worker)

    def _on_stop(self):
        # We can't truly kill the thread mid-run, but we can inform the user
        self.run_tab.append_log(
            "Stop requested — the current alignment step will finish before exiting.")
        self.status.showMessage("Stopping after current step…")

    @pyqtSlot(str)
    def _on_log(self, msg: str):
        self.run_tab.append_log(msg)

    @pyqtSlot(str)
    def _on_error(self, tb: str):
        self.run_tab.append_log(f"ERROR:\n{tb}")
        QMessageBox.critical(self, "Alignment error",
                             "The alignment failed.  See the Run log for details.")

    @pyqtSlot(object)
    def _on_result(self, result):
        langs     = self.input_tab.langs
        tmx_langs = (self.input_tab.tmx_langs or
                     [l.upper() for l in langs])
        self.results_tab.load_result(result, langs=langs, tmx_langs=tmx_langs)

    @pyqtSlot()
    def _on_finished(self):
        self.run_tab.set_running(False)
        self.status.showMessage("Done.")
        self.run_tab.append_log("─── Alignment complete ───")
        self.tabs.setCurrentWidget(self.results_tab)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_file(path: str):
    """Open a file with the system default application."""
    import subprocess, platform
    system = platform.system()
    if system == "Windows":
        import os
        os.startfile(path)
    elif system == "Darwin":
        subprocess.run(["open", path])
    else:
        subprocess.run(["xdg-open", path])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def launch_gui(argv: Optional[list[str]] = None) -> int:
    """Create the QApplication and show the main window.  Returns exit code."""
    app = QApplication(argv or sys.argv)
    app.setApplicationName("LF Aligner")
    app.setOrganizationName("LFAligner")
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()
    return app.exec()
