"""Data Studio - a friendly desktop app for everyday data work.

Open a file, look at it, tidy it, chart it and save it. Everything else
lives behind the same five simple screens. Launch with:

    python -m data_studio.ui            (or: data-studio-ui, or run_data_studio.bat)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QByteArray, QSettings, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..kernel.chart_render import render_chart
from ..reporting import write_html_report
from ..version import __version__ as APP_VERSION
from . import core
from .analyses import list_analyses, run_analysis
from .process import apply_step, list_steps, parse_aggregations, parse_mapping

STYLE = """
QMainWindow, QWidget { background: #f6f7fb; color: #1f2933; font-size: 13px; }
#sidebar { background: #ffffff; border-right: 1px solid #e2e5ec; }
#appName { font-size: 18px; font-weight: 700; color: #1d4ed8; padding: 6px 10px; }
#appTag { color: #68737f; font-size: 11px; padding: 0 10px 12px 10px; }
QToolButton#nav { border: none; border-radius: 8px; padding: 10px 12px;
  font-size: 13px; text-align: left; }
QToolButton#nav:hover { background: #eef1fb; }
QToolButton#nav:checked { background: #dbe7ff; color: #1d4ed8; font-weight: 600; }
#pageTitle { font-size: 22px; font-weight: 700; }
#pageHint { color: #68737f; }
#card { background: #ffffff; border: 1px solid #e2e5ec; border-radius: 12px; }
#cardTitle { font-size: 15px; font-weight: 700; }
QPushButton { border: 1px solid #d4d9e3; background: #ffffff;
  border-radius: 8px; padding: 8px 14px; }
QPushButton:hover { background: #eef1fb; border-color: #b9c4ff; }
QPushButton#primary { background: #1d4ed8; border-color: #1d4ed8; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #1e40af; }
QPushButton#big { padding: 14px 18px; font-size: 14px; }
QLineEdit, QComboBox { border: 1px solid #d4d9e3; border-radius: 8px;
  padding: 6px 8px; background: white; }
QTableView { background: white; border: 1px solid #e2e5ec;
  border-radius: 8px; gridline-color: #eceff5; }
QStatusBar { background: #ffffff; border-top: 1px solid #e2e5ec; }
"""


class DataFrameModel(QAbstractTableModel):
    """Editable table model - missing values are highlighted amber."""

    def __init__(self) -> None:
        super().__init__()
        self._df = pd.DataFrame()
        self._visible = []

    def set_frame(self, df: pd.DataFrame) -> None:
        self.beginResetModel()
        self._df = df.reset_index(drop=True).copy()
        self._visible = list(range(len(self._df)))
        self.endResetModel()

    def frame(self) -> pd.DataFrame:
        return self._df.copy()

    def set_filter(self, text: str) -> None:
        self.beginResetModel()
        needle = text.strip().lower()
        if not needle:
            self._visible = list(range(len(self._df)))
        else:
            self._visible = [
                i for i, row in self._df.iterrows()
                if any(needle in str(v).lower() for v in row.values)
            ]
        self.endResetModel()

    def rowCount(self, parent=None) -> int:
        return len(self._visible)

    def columnCount(self, parent=None) -> int:
        return len(self._df.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._visible[index.row()]
        value = self._df.iat[row, index.column()]
        if role in (Qt.DisplayRole, Qt.EditRole):
            return "" if pd.isna(value) else str(value)
        if role == Qt.BackgroundRole and pd.isna(value):
            return QColor("#fff4d6")
        if role == Qt.ForegroundRole and pd.isna(value):
            return QColor("#a16207")
        is_num = pd.api.types.is_numeric_dtype(self._df.iloc[:, index.column()])
        if role == Qt.TextAlignmentRole and is_num:
            return Qt.AlignRight | Qt.AlignVCenter
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        row = self._visible[index.row()]
        column = self._df.columns[index.column()]
        try:
            if pd.api.types.is_numeric_dtype(self._df[column]):
                value = float("nan") if str(value).strip() == "" else float(value)
            self._df.iat[row, index.column()] = value
            self.dataChanged.emit(index, index)
            return True
        except (ValueError, TypeError):
            return False

    def flags(self, index):
        return super().flags(index) | Qt.ItemIsEditable

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        return str(self._df.columns[section]) if orientation == Qt.Horizontal else str(section + 1)


def heading(title: str, hint: str) -> tuple[QVBoxLayout, None]:
    layout = QVBoxLayout()
    title_label = QLabel(title)
    title_label.setObjectName("pageTitle")
    hint_label = QLabel(hint)
    hint_label.setObjectName("pageHint")
    hint_label.setWordWrap(True)
    layout.addWidget(title_label)
    layout.addWidget(hint_label)
    return layout, None


class MainWindow(QMainWindow):
    """The whole app: one window, five simple screens."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Data Studio")
        self.resize(1180, 760)
        self.setStyleSheet(STYLE)
        self.settings = QSettings("Data Studio", "Data Studio")
        self.undo_stack: list[tuple[pd.DataFrame, str]] = []
        self.current_file: Path | None = None
        self._last_analysis_result: Any = None
        self._build()
        self.load_frame(core.SAMPLE, name="Sample data", remember=False, announce=False)
        self.statusBar().showMessage("Ready - try the buttons on the left, starting with Home.")

    # ------------------------------------------------------------- layout

    def _build(self) -> None:
        root = QWidget()
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 14, 10, 14)
        name = QLabel("Data Studio")
        name.setObjectName("appName")
        tag = QLabel(f"Everyday data work, made simple\nVersion {APP_VERSION}")
        tag.setObjectName("appTag")
        side.addWidget(name)
        side.addWidget(tag)

        self.nav_buttons: dict[str, QToolButton] = {}
        for key, label in [
            ("home", "Home"),
            ("table", "My table"),
            ("clean", "Clean"),
            ("process", "Process"),
            ("analyze", "Analyze"),
            ("charts", "Charts"),
            ("export", "Save & share"),
        ]:
            button = QToolButton()
            button.setObjectName("nav")
            button.setText(label)
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.clicked.connect(lambda _=False, k=key: self.show_page(k))
            button.setToolTip({
                "home": "Start here - open a file or try the sample",
                "table": "See and search your data, fix cells by hand",
                "clean": "One-click fixes for gaps, duplicates and messy text",
                "process": "Do real data processing: filter, cast, rename, derive, pivot...",
                "analyze": "Summarise, profile and find patterns in your data",
                "charts": "Turn a column into a picture",
                "export": "Save a copy and copy a plain-English summary",
            }[key])
            side.addWidget(button)
            self.nav_buttons[key] = button
        side.addStretch(1)
        help_button = QPushButton("Help & shortcuts")
        help_button.setToolTip(
            "Open the quick guide: what to do first, and answers to common questions"
        )
        help_button.clicked.connect(self.show_help)
        side.addWidget(help_button)
        shell.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._home_page())
        self.pages.addWidget(self._table_page())
        self.pages.addWidget(self._clean_page())
        self.pages.addWidget(self._process_page())
        self.pages.addWidget(self._analyze_page())
        self.pages.addWidget(self._charts_page())
        self.pages.addWidget(self._export_page())
        shell.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.show_page("home")

    def show_page(self, key: str) -> None:
        order = ["home", "table", "clean", "process", "analyze", "charts", "export"]
        self.pages.setCurrentIndex(order.index(key))
        for name, button in self.nav_buttons.items():
            button.setChecked(name == key)
        hints = {
            "home": "Pick one of the three actions to get going.",
            "table": "Click any cell to fix it by hand. Use the search box to find rows fast.",
            "clean": "Hover any button to see exactly what it will do. Every fix can be undone.",
            "process": "Real processing steps on the loaded table. Every step can be undone.",
            "analyze": "Pick an analysis, set any needed options, then press Run.",
            "charts": "Choose what to plot, then press 'Make the chart'.",
            "export": "Choose where to save, or copy the summary to paste anywhere.",
        }
        self.statusBar().showMessage(hints[key])

    # ------------------------------------------------------------- state

    def load_frame(
        self, df: pd.DataFrame, name: str, remember: bool = True, announce: bool = True
    ) -> None:
        self.model.set_frame(df)
        self.undo_stack.clear()
        self.history.clear()
        self.current_file = Path(name) if remember and Path(name).suffix else None
        self._refresh_counts()
        self._refresh_columns()
        self._refresh_history()
        if announce:
            self.statusBar().showMessage(
                f"Opened {name} - {len(df):,} rows, {len(df.columns)} columns.", 6000
            )

    def current_frame(self) -> pd.DataFrame:
        return self.model.frame()

    def apply_change(self, df: pd.DataFrame, description: str) -> None:
        self.undo_stack.append((self.current_frame(), description))
        self.model.set_frame(df)
        self.history.addItem(f"- {description}")
        self._refresh_counts()
        self._refresh_columns()
        self.statusBar().showMessage(f"Done: {description}. (Ctrl+Z to undo)", 6000)

    def undo(self) -> None:
        if not self.undo_stack:
            self.statusBar().showMessage("Nothing to undo yet.", 4000)
            return
        frame, description = self.undo_stack.pop()
        self.model.set_frame(frame)
        self.history.addItem(f"- Undid: {description}")
        self._refresh_counts()
        self._refresh_columns()
        self.statusBar().showMessage(f"Undid: {description}", 5000)

    def _refresh_counts(self) -> None:
        df = self.model.frame()
        summary = core.quality_summary(df)
        self.table_stats.setText(
            f"{summary['rows']:,} rows  ·  {summary['columns']} columns  ·  "
            f"{summary['missing']} missing  ·  {summary['duplicates']} duplicates"
        )
        self.export_stats.setText(
            f"{summary['rows']:,} rows · {summary['columns']} columns · "
            f"{summary['completeness']}% complete"
        )

    def _refresh_columns(self) -> None:
        df = self.model.frame()
        numeric = list(df.columns)
        for combo in (self.x_combo, self.y_combo):
            current = combo.currentText()
            combo.clear()
            combo.addItems([str(c) for c in numeric])
            if current in [str(c) for c in numeric]:
                combo.setCurrentText(current)

    def _refresh_history(self) -> None:
        self.history.clear()
        self.history.addItem("Nothing changed yet")

    def _remember_recent(self, path: str) -> None:
        recents = self.settings.value("recentFiles", [])
        if isinstance(recents, str):
            recents = [recents]
        recents = [r for r in recents if r != path][:9]
        recents.insert(0, path)
        self.settings.setValue("recentFiles", recents)
        self._fill_recents()

    # ------------------------------------------------------------- pages

    def _home_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        column, _ = heading("Welcome to Data Studio",
                            "Three steps: open your data, tidy it, and save or chart it. "
                            "You are looking at sample data - click anything, nothing can break.")
        layout.addLayout(column)

        row = QHBoxLayout()
        for title, desc, action, tip in [
            ("Open my file", "CSV, Excel, JSON or Parquet from this computer", self.open_file,
             "Pick a file - it opens instantly in the My table screen"),
            ("Keep the sample", "Play with built-in machine sensor data", self.open_sample,
             "Reload the sample dataset"),
            ("Tidy everything", "Fill gaps, drop duplicates, trim spaces - one click",
             self.clean_everything,
             "Runs all gentle fixes safely; nothing is deleted without undo"),
        ]:
            card = QFrame()
            card.setObjectName("card")
            card.setFixedSize(300, 150)
            card_layout = QVBoxLayout(card)
            card_title = QLabel(title)
            card_title.setObjectName("cardTitle")
            card_desc = QLabel(desc)
            card_desc.setWordWrap(True)
            button = QPushButton("Go")
            button.setObjectName("primary")
            button.setToolTip(tip)
            button.clicked.connect(action)
            card_layout.addWidget(card_title)
            card_layout.addWidget(card_desc)
            card_layout.addStretch(1)
            card_layout.addWidget(button)
            row.addWidget(card)
        layout.addLayout(row)

        recent_label = QLabel("Recent files")
        recent_label.setObjectName("cardTitle")
        layout.addWidget(recent_label)
        self.recent_list = QListWidget()
        self.recent_list.setMaximumHeight(150)
        self.recent_list.itemDoubleClicked.connect(self._open_recent)
        self.recent_list.setToolTip("Double-click a file to reopen it")
        layout.addWidget(self.recent_list)
        self._fill_recents()
        layout.addStretch(1)
        return page

    def _fill_recents(self) -> None:
        if not hasattr(self, "recent_list"):
            return
        self.recent_list.clear()
        recents = self.settings.value("recentFiles", [])
        if isinstance(recents, str):
            recents = [recents]
        if not recents:
            item = QListWidgetItem("No files opened yet - use 'Open my file' above.")
            item.setFlags(Qt.NoItemFlags)
            self.recent_list.addItem(item)
            return
        for path in recents:
            self.recent_list.addItem(QListWidgetItem(path))

    def _open_recent(self, item: QListWidgetItem) -> None:
        path = Path(item.text())
        if path.exists():
            self._load_path(path)
        else:
            QMessageBox.warning(
                self, "File not found",
                f"Could not find:\n{path}\n\nIt may have moved or been renamed.",
            )

    def _table_page(self) -> QWidget:
        from PySide6.QtWidgets import QTableView
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        column, _ = heading(
            "My table",
            "Your data, front and centre. Click any cell to fix it;"
            " amber cells are missing values.",
        )
        layout.addLayout(column)

        toolbar = QHBoxLayout()
        open_button = QPushButton("Open file…")
        open_button.setToolTip("Open a CSV, Excel, JSON or Parquet file")
        open_button.clicked.connect(self.open_file)
        sample_button = QPushButton("Load sample")
        sample_button.setToolTip("Replace current data with the built-in sample")
        sample_button.clicked.connect(self.open_sample)
        save_button = QPushButton("Save a copy…")
        save_button.setToolTip("Save the current table as CSV, Excel or Parquet")
        save_button.clicked.connect(self.save_file)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search all columns…")
        self.search_box.setToolTip("Type to filter rows; clear to see everything again")
        self.search_box.textChanged.connect(lambda text: self.model.set_filter(text))
        for widget in (open_button, sample_button, save_button):
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        toolbar.addWidget(self.search_box)
        layout.addLayout(toolbar)

        self.model = DataFrameModel()
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.view.setAlternatingRowColors(True)
        self.view.setToolTip(
            "Edit cells directly - changes stay in this window only until you save a copy"
        )
        layout.addWidget(self.view, 1)

        self.table_stats = QLabel()
        self.table_stats.setObjectName("pageHint")
        layout.addWidget(self.table_stats)
        return page

    def _clean_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        column, _ = heading(
            "Clean",
            "One button per common problem. Every change can be undone, so experiment freely.",
        )
        layout.addLayout(column)

        tidy = QPushButton("Tidy everything for me")
        tidy.setObjectName("primary")
        tidy.setStyleSheet("padding: 14px 18px; font-size: 14px;")
        tidy.setToolTip(
            "Trims spaces, converts number-like text, removes duplicates and fills gaps "
            "- in that order"
        )
        tidy.clicked.connect(self.clean_everything)
        layout.addWidget(tidy)

        grid = QHBoxLayout()
        for label, tip, handler in [
            ("Fill in missing values",
             "Numbers get the median, text gets the most common value", self._fill_missing),
            ("Remove duplicate rows",
             "Keeps the first copy of each repeated row", self._drop_duplicates),
            ("Trim extra spaces",
             "Removes accidental spaces at the start and end of text", self._trim_text),
            ("Convert text to numbers",
             "Turns columns like '1,200' or '82.4' into real numbers", self._to_numbers),
        ]:
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            grid.addWidget(button)
        layout.addLayout(grid)

        row = QHBoxLayout()
        undo_button = QPushButton("Undo last change")
        undo_button.setToolTip("Go back one step (same as Ctrl+Z)")
        undo_button.clicked.connect(self.undo)
        row.addWidget(undo_button)
        row.addStretch(1)
        layout.addLayout(row)

        history_label = QLabel("What changed")
        history_label.setObjectName("cardTitle")
        layout.addWidget(history_label)
        self.history = QListWidget()
        self.history.setToolTip("A running list of everything you changed in this session")
        layout.addWidget(self.history, 1)
        return page

    def _process_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        column, _ = heading(
            "Process",
            "Do real data processing steps to clean and reshape your data. "
            "Pick a step, fill its options, press Apply. Every step can be undone.",
        )
        layout.addLayout(column)

        # Step list
        list_label = QLabel("Available steps")
        list_label.setObjectName("cardTitle")
        layout.addWidget(list_label)

        self.step_list = QListWidget()
        self.step_list.setToolTip("Select a step, fill the options, press Apply")
        self.step_list.itemClicked.connect(self._on_step_selected)
        self._populate_step_list()
        layout.addWidget(self.step_list, 1)

        # Description of selected step
        self._step_desc = QLabel("")
        self._step_desc.setObjectName("pageHint")
        self._step_desc.setWordWrap(True)
        layout.addWidget(self._step_desc)

        # Parameter widgets
        self.step_param_widget = QWidget()
        self.step_param_widget.setLayout(QVBoxLayout())
        layout.addWidget(self.step_param_widget)

        # Apply button + undo, on one row
        btn_layout = QHBoxLayout()
        self.apply_step_btn = QPushButton("Apply step")
        self.apply_step_btn.setObjectName("primary")
        self.apply_step_btn.clicked.connect(self._on_apply_step)
        btn_layout.addWidget(self.apply_step_btn)
        btn_layout.addStretch(1)
        undo_button = QPushButton("Undo (Ctrl+Z)")
        undo_button.setToolTip("Go back one processing step")
        undo_button.clicked.connect(self.undo)
        btn_layout.addWidget(undo_button)
        layout.addLayout(btn_layout)
        return page

    def _populate_step_list(self) -> None:
        """Populate the steps list from the built-in registry with friendly names."""
        self.step_list.clear()
        for entry in list_steps():
            item = QListWidgetItem(str(entry.get("friendly_name") or entry.get("title")))
            item.setData(Qt.UserRole, str(entry.get("name") or ""))
            item.setToolTip(str(entry.get("description") or ""))
            self.step_list.addItem(item)

    def _on_step_selected(self, item: QListWidgetItem) -> None:
        """Handle selection of a step - build its parameter widgets."""
        code = item.data(Qt.UserRole)
        if not code:
            return

        entries = list_steps()
        entry = next((e for e in entries if e.get("name") == code), None)
        self._build_params_from_entry(entry, self.step_param_widget, "_step_param_widgets")
        self._step_desc.setText(str(entry.get("description") or ""))

    def _collect_step_params(self) -> dict[str, Any]:
        """Collect resolved parameter values from the step parameter widgets."""
        result: dict[str, Any] = {}
        for pname, widget in getattr(self, "_step_param_widgets", {}).items():
            if isinstance(widget, QCheckBox):
                result[pname] = bool(widget.isChecked())
            elif isinstance(widget, QComboBox):
                text = widget.currentText()
                result[pname] = text if text.strip() else None
            elif isinstance(widget, QLineEdit):
                text = widget.text().strip()
                if not text:
                    continue
                result[pname] = text
            elif isinstance(widget, QDoubleSpinBox):
                result[pname] = float(widget.value())
            elif isinstance(widget, QSpinBox):
                result[pname] = int(widget.value())
        return result

    def _on_apply_step(self) -> None:
        """Apply the selected processing step to the currently loaded DataFrame."""
        df = self.current_frame()
        if df.empty:
            QMessageBox.information(self, "No data", "Open a file first.")
            return

        item = self.step_list.currentItem()
        if not item:
            return
        step_name = item.data(Qt.UserRole)
        if not step_name:
            return

        raw = self._collect_step_params()

        # Look up declared kinds to know how to coerce structured params
        entries = list_steps()
        entry = next((e for e in entries if e.get("name") == step_name), None)
        params: dict[str, Any] = {}
        if entry:
            for param in entry.get("params", []):
                pname = param.get("name")
                kind = param.get("kind")
                value = raw.get(pname)
                if value is None:
                    continue
                try:
                    if kind == "mapping":
                        params[pname] = parse_mapping(str(value))
                    elif kind == "aggregations":
                        parsed = parse_aggregations(str(value))
                        if not parsed:
                            continue
                        params[pname] = parsed
                    else:
                        params[pname] = value
                except ValueError as exc:
                    QMessageBox.critical(self, "Fix the options", str(exc))
                    return

        try:
            updated = apply_step(df, step_name, params)
        except Exception as exc:
            QMessageBox.critical(self, "Could not apply step", str(exc))
            return

        self.apply_change(updated, f"Processed: {step_name}")
        self.statusBar().showMessage(f"Applied {step_name}. Ctrl+Z to undo.", 6000)

    def _analyze_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        column, _ = heading(
            "Analyze",
            "Pick an analysis to run on the data currently loaded in 'My table'. "
            "Clean your data first for best results.",
        )
        layout.addLayout(column)

        # Analysis list
        list_label = QLabel("Available analyses")
        list_label.setObjectName("cardTitle")
        layout.addWidget(list_label)

        self.analysis_list = QListWidget()
        self.analysis_list.setToolTip("Double-click to run, or select and press Run")
        self.analysis_list.itemClicked.connect(self._on_analysis_selected)
        self.analysis_list.itemDoubleClicked.connect(self._on_run_analysis)
        self._populate_analysis_list()
        layout.addWidget(self.analysis_list, 1)

        # Parameter widgets (hidden until an analysis needs them)
        self.param_widget = QWidget()
        self.param_layout = QVBoxLayout(self.param_widget)
        self.param_widget.setVisible(False)
        layout.addWidget(self.param_widget)

        # Run button + Save report
        btn_layout = QHBoxLayout()
        self.run_analysis_btn = QPushButton("Run analysis")
        self.run_analysis_btn.setObjectName("primary")
        self.run_analysis_btn.clicked.connect(self._on_run_analysis)
        self.save_report_btn = QPushButton("Save report as HTML...")
        self.save_report_btn.setToolTip(
            "Save the result of the last analysis as a standalone HTML report"
        )
        self.save_report_btn.clicked.connect(self._on_save_analysis_report)
        self.save_report_btn.setEnabled(False)
        btn_layout.addWidget(self.run_analysis_btn)
        btn_layout.addWidget(self.save_report_btn)
        btn_layout.addStretch(1)
        layout.addLayout(btn_layout)

        # Metrics line
        self._metrics_label = QLabel("")
        self._metrics_label.setObjectName("pageHint")
        layout.addWidget(self._metrics_label)

        # Result area
        result_label = QLabel("Result")
        result_label.setObjectName("cardTitle")
        layout.addWidget(result_label)

        self.result_box = QPlainTextEdit()
        self.result_box.setReadOnly(True)
        self.result_box.setMaximumHeight(140)
        self.result_box.setToolTip("Plain-language result; copy with Ctrl+C")
        layout.addWidget(self.result_box, 1)

        # Result table (rendered as a scrollable widget, replacing text preview)
        result_table_label = QLabel("Result table")
        result_table_label.setObjectName("cardTitle")
        layout.addWidget(result_table_label)

        self.result_table_scroll = QScrollArea()
        self.result_table_scroll.setWidgetResizable(True)
        self.result_table_scroll.setMinimumHeight(150)
        self.result_table_scroll.setMaximumHeight(260)
        self.result_table_scroll.setFrameShape(QFrame.NoFrame)
        self.result_table_scroll.setVisible(False)
        self.result_table_container = QWidget()
        self.result_table_container_layout = QVBoxLayout(self.result_table_container)
        self.result_table_scroll.setWidget(self.result_table_container)
        layout.addWidget(self.result_table_scroll)

        # Diagnostics / warnings
        diag_label = QLabel("Diagnostics / warnings")
        diag_label.setObjectName("cardTitle")
        layout.addWidget(diag_label)

        self._diag_box = QPlainTextEdit()
        self._diag_box.setReadOnly(True)
        self._diag_box.setMaximumHeight(100)
        self._diag_box.setToolTip("Machine-readable diagnostics and warnings")
        layout.addWidget(self._diag_box)

        # Charts (analyses that emit ChartSpec)
        self._charts_scroll = QScrollArea()
        self._charts_scroll.setWidgetResizable(True)
        self._charts_scroll.setMinimumHeight(160)
        self._charts_scroll.setMaximumHeight(280)
        self._charts_scroll.setFrameShape(QFrame.NoFrame)
        self._charts_container = QWidget()
        self._charts_layout = QVBoxLayout(self._charts_container)
        self._charts_scroll.setWidget(self._charts_container)
        layout.addWidget(self._charts_scroll)
        return page

    def _populate_analysis_list(self) -> None:
        """Populate the analysis list from the built-in registry with friendly names."""
        self.analysis_list.clear()
        for entry in list_analyses():
            item = QListWidgetItem(str(entry.get("title") or entry.get("name")))
            item.setData(Qt.UserRole, str(entry.get("name") or ""))
            self.analysis_list.addItem(item)

    def _build_parameter_widgets(self, analysis_code: str) -> None:
        """Build parameter widgets for the selected analysis in the param_widget area."""
        entries = list_analyses()
        entry = next((e for e in entries if e.get("name") == analysis_code), None)
        self._build_params_from_entry(entry, self.param_widget, "_param_widgets")

    def _build_params_from_entry(
        self,
        entry: dict[str, Any] | None,
        container_widget: QWidget,
        attr_name: str,
    ) -> None:
        """Build parameter widgets for one analysis/step entry into a container widget.

        Args:
            entry: The catalog entry with declared StepParam specs.
            container_widget: The QWidget whose layout holds the param rows.
            attr_name: Attribute name (on ``self``) to store the {name: widget} map.
        """
        # Clear existing widget children
        while container_widget.layout().count():
            child = container_widget.layout().takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        setattr(self, attr_name, {})
        if not entry:
            return

        column_names = list(self.current_frame().columns) if hasattr(self, "current_frame") else []
        numeric_columns: list[str] = []
        datetime_columns: list[str] = []
        object_columns: list[str] = []
        for col in column_names:
            try:
                series = self.current_frame()[col]
                import pandas.api.types as pdtypes

                if pdtypes.is_numeric_dtype(series.dtype):
                    numeric_columns.append(col)
                elif pdtypes.is_datetime64_any_dtype(series.dtype):
                    datetime_columns.append(col)
                else:
                    # Object/string series that look like ISO dates ('2026-...', '2025-...')
                    if column_names and len(series) > 0:
                        try:
                            first_str = str(series.iloc[0]).strip()
                            if first_str.startswith(("2026-", "2025-", "2024-")):
                                datetime_columns.append(col)
                            else:
                                object_columns.append(col)
                        except Exception:
                            object_columns.append(col)
                    else:
                        object_columns.append(col)
            except Exception:
                pass

        def default_for(param: dict[str, Any]) -> Any:
            """Return the param's declared default, or a sensible one for the current df."""
            kind = param.get("kind")
            name = str(param.get("name", "")).lower()
            if kind == "columns":
                # Numeric columns by default; for grouping columns users edit manually
                return ",".join(numeric_columns[:2] or column_names[:2] or [])
            if kind == "column":
                # Smart pick: time-like params -> date col; value-like -> numeric col
                if any(tok in name for tok in ("time", "date", "ts", "when")):
                    if datetime_columns:
                        return datetime_columns[0]
                    if object_columns:
                        return object_columns[0]
                if any(tok in name for tok in ("value", "metric", "y", "response", "target")):
                    return numeric_columns[0] if numeric_columns else (
                        column_names[0] if column_names else ""
                    )
                # Fallback: numeric if available, else first column
                return numeric_columns[0] if numeric_columns else (
                    column_names[0] if column_names else ""
                )
            default = param.get("default")
            if default is not None:
                return default
            return None

        # Create a widget for each param
        for param in entry.get("params", []):
            pname = param.get("name", "")
            kind = param.get("kind", "any")
            choices = param.get("choices", ())
            description = param.get("description", "")
            default = default_for(param)

            # Build widget
            w: QWidget
            if kind == "integer":
                w = QSpinBox()
                w.setRange(-10_000_000, 10_000_000)
                w.setValue(default if default is not None else 0)
            elif kind == "number":
                w = QDoubleSpinBox()
                w.setRange(-10_000_000.0, 10_000_000.0)
                w.setValue(default if default is not None else 0.0)
                w.setDecimals(4)
            elif kind == "boolean":
                w = QCheckBox()
                w.setChecked(bool(default) if default is not None else False)
            elif kind == "enum":
                w = QComboBox()
                w.addItems(list(choices))
                if default is not None and str(default) in choices:
                    w.setCurrentText(str(default))
                elif choices:
                    w.setCurrentIndex(0)
            elif kind == "columns":
                w = QLineEdit()
                w.setPlaceholderText("Comma-separated column names (e.g. 'col1,col2')")
                if default:
                    w.setText(str(default))
                w.setToolTip(description)
            elif kind == "column":
                w = QComboBox()
                w.addItems(column_names)
                if default and default in column_names:
                    w.setCurrentText(default)
                elif column_names:
                    w.setCurrentIndex(0)
            elif kind == "mapping":
                w = QLineEdit()
                w.setPlaceholderText("old=new, old2=new2  (comma-separated)")
                w.setToolTip(description or "old=new pairs separated by commas")
            elif kind == "aggregations":
                w = QLineEdit()
                w.setPlaceholderText("column:function, column2:function2")
                w.setToolTip(description or "Comma-separated pairs: col:sum, col2:mean")
            else:  # any, string, text, expression, etc.
                w = QLineEdit()
                if default:
                    w.setText(str(default))
                w.setPlaceholderText(description or "")

            # The param name is tracked in the attr_name map
            setattr(self, attr_name, getattr(self, attr_name, {}) | {pname: w})

            # Add widget row to the container's layout
            row = QHBoxLayout()
            label = QLabel(pname.title())
            label.setFixedWidth(140)
            row.addWidget(label)
            row.addWidget(w, 1)
            row.setContentsMargins(0, 4, 0, 4)
            row_widget = QWidget()
            row_widget.setLayout(row)
            container_widget.layout().addWidget(row_widget)

    def _collect_param_values(self) -> dict[str, Any]:
        """Collect resolved parameter values from the parameter widgets of the current analysis.

        Returns a mapping of StepParam name -> resolved value (or None if no widget).
        """
        return self._param_widgets

    def _on_analysis_selected(self, item: QListWidgetItem) -> None:
        """Handle selection of an analysis item - build and show parameter widgets."""
        code = item.data(Qt.UserRole)
        if not code:
            return

        # Build parameter widgets for this analysis
        self._build_parameter_widgets(code)

        # Enable the run button
        self.run_analysis_btn.setEnabled(True)

    def _on_run_analysis(self) -> None:
        """Run the selected analysis on the currently loaded DataFrame."""
        df = self.current_frame()
        if df.empty:
            QMessageBox.information(self, "No data", "Open a file first.")
            return

        # Get the selected analysis code from the list widget
        item = self.analysis_list.currentItem()
        if not item:
            return
        selected_name = item.data(Qt.UserRole)
        if not selected_name:
            return

        # Collect parameters from the parameter widgets
        params: dict[str, Any] = {}
        for pname, w in self._param_widgets.items():
            if isinstance(w, QCheckBox):
                params[pname] = bool(w.isChecked())
            elif isinstance(w, QComboBox):
                t = w.currentText()
                params[pname] = t if t.strip() else None
            elif isinstance(w, QLineEdit):
                t = w.text().strip()
                params[pname] = t if t else None
            elif isinstance(w, QDoubleSpinBox):
                params[pname] = float(w.value())
            elif isinstance(w, QSpinBox):
                params[pname] = int(w.value())

        try:
            result = run_analysis(df, analysis_name=selected_name, params=params)
            self._display_result(result)
            self._last_analysis_result = result
            self.run_analysis_btn.setEnabled(True)
            self.save_report_btn.setEnabled(True)
        except Exception as exc:
            QMessageBox.critical(self, "Analysis failed", str(exc))

    def _display_result(self, result: Any) -> None:
        """Display an AnalysisResult in the UI widgets."""
        # Render the narrative
        self.result_box.setPlainText(result.narrative or "")

        # Render metrics
        metrics = result.metrics or {}
        metric_parts = []
        metric_keys = (
            "rows",
            "columns",
            "missing",
            "missing_pct",
            "duplicate_rows",
            "constant_columns",
            "strongest_pair",
            "strongest_value",
        )
        for key in metric_keys:
            if key in metrics:
                metric_parts.append(f"{key.replace('_', ' ').title()}: {metrics[key]}")
        if hasattr(self, "_metrics_label"):
            self._metrics_label.setText("  |  ".join(metric_parts) if metric_parts else "")

        # Render diagnostics
        diags = result.diagnostics or []
        diag_parts = []
        for d in diags:
            code = d.code or ""
            msg = d.message or ""
            detail = d.details or {}
            parts = [f"[{code}] {msg}"]
            for k, v in detail.items():
                parts.append(f"{k}={v}")
            diag_parts.append(" ".join(parts))
        if hasattr(self, "_diag_box"):
            self._diag_box.setPlainText("\n".join(diag_parts) if diag_parts else "")

        # Render tables if present
        self._render_result_tables(result)

        # Render charts emitted by the analysis
        self._render_charts(getattr(result, "charts", ()))

    def _render_result_tables(self, result: Any) -> None:
        """Render all ResultTable(s) from an AnalysisResult as scrollable widgets."""
        # Clear previous table widgets
        while self.result_table_container_layout.count():
            child = self.result_table_container_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        tables = result.tables or ()
        if not tables:
            self.result_table_scroll.setVisible(False)
            return

        MAX_ROWS = 200

        for table in tables:
            title = getattr(table, "title", None) or "Table"
            columns = list(getattr(table, "columns", ()) or ())
            rows = list(getattr(table, "rows", ()) or ())
            note = getattr(table, "note", None)

            if not columns:
                continue

            table_widget = QLabel(str(title))
            table_widget.setObjectName("cardTitle")
            self.result_table_container_layout.addWidget(table_widget)

            qtable = QTableWidget()
            shown = rows[:MAX_ROWS]
            qtable.setRowCount(len(shown))
            qtable.setColumnCount(len(columns))
            qtable.setHorizontalHeaderLabels([str(c) for c in columns])
            qtable.setEditTriggers(QAbstractItemView.NoEditTriggers)
            qtable.setAlternatingRowColors(True)
            qtable.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            qtable.horizontalHeader().setStretchLastSection(True)
            qtable.verticalHeader().setDefaultSectionSize(24)

            for r, row in enumerate(shown):
                for c, value in enumerate(row):
                    text = "" if value is None else str(value)
                    qtable.setItem(r, c, QTableWidgetItem(text))

            self.result_table_container_layout.addWidget(qtable)

            meta_parts = [f"{len(rows)} row(s)"]
            if len(rows) > MAX_ROWS:
                meta_parts.append(f"showing first {MAX_ROWS}")
            if note:
                meta_parts.append(str(note))
            meta = QLabel(" · ".join(meta_parts))
            meta.setObjectName("pageHint")
            meta.setWordWrap(True)
            self.result_table_container_layout.addWidget(meta)

        self.result_table_container_layout.addStretch(1)
        self.result_table_scroll.setVisible(True)

    def _render_charts(self, charts: Any) -> None:
        """Render kernel ChartSpec charts as images on the Analyze page."""
        if charts is None:
            charts = ()
        # Clear any existing chart widgets
        while self._charts_layout.count():
            child = self._charts_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        rendered = 0
        for chart in list(charts)[:4]:  # cap to keep the page responsive
            try:
                svg = render_chart(chart, width=560, height=300)
                renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
                size = renderer.defaultSize()
                if size.width() <= 0 or size.height() <= 0:
                    continue
                image = QImage(size.width(), size.height(), QImage.Format_ARGB32)
                image.fill(0xFFFFFFFF)
                painter = QPainter(image)
                renderer.render(painter)
                painter.end()

                title = getattr(chart, "title", None) or "Chart"
                caption = QLabel(str(title))
                caption.setObjectName("cardTitle")
                pixmap_label = QLabel()
                pixmap_label.setAlignment(Qt.AlignCenter)
                pixmap_label.setPixmap(QPixmap.fromImage(image))
                self._charts_layout.addWidget(caption)
                self._charts_layout.addWidget(pixmap_label)
                rendered += 1
            except Exception:
                # Charts are helpful, not mandatory - skip on failure
                continue

        self._charts_layout.addStretch(1)
        self._charts_scroll.setVisible(rendered > 0)

    def _on_save_analysis_report(self) -> None:
        """Save the last analysis result as a standalone HTML report."""
        result = getattr(self, "_last_analysis_result", None)
        if result is None:
            QMessageBox.information(
                self, "No result yet", "Run an analysis first, then choose 'Save report'."
            )
            return
        suggested = f"data-studio-report-{result.kind}.html"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save analysis report", suggested,
            "HTML report (*.html);;All files (*)",
        )
        if not path:
            return
        try:
            write_html_report(
                [result],
                path,
                title=f"Data Studio report: {result.title}",
                subtitle=f"{result.title} on the current table",
                theme="light",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Could not save the report", str(exc))
            return
        self.statusBar().showMessage(f"Saved report to {path}", 6000)

    def _charts_page(self) -> QWidget:
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.figure import Figure
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        column, _ = heading(
            "Charts",
            "Pick a column for each axis and press Make the chart. "
            "Save it as an image when it looks right.",
        )
        layout.addLayout(column)

        controls = QHBoxLayout()
        self.chart_type = QComboBox()
        self.chart_type.addItems(["Bar chart", "Line chart", "Scatter plot", "Histogram"])
        self.chart_type.setToolTip(
            "Bar = compare categories, Line = trends over time, "
            "Scatter = relationship between two numbers"
        )
        self.x_combo = QComboBox()
        self.x_combo.setToolTip("Column to use on the horizontal axis")
        self.y_combo = QComboBox()
        self.y_combo.setToolTip("Column to use on the vertical axis (not needed for a histogram)")
        make = QPushButton("Make the chart")
        make.setObjectName("primary")
        make.clicked.connect(self.draw_chart)
        save = QPushButton("Save chart as image…")
        save.setToolTip("Save the current chart as a PNG file")
        save.clicked.connect(self.save_chart)
        combos = [("Type", self.chart_type), ("X axis / category", self.x_combo),
                    ("Y values", self.y_combo)]
        for label, widget in combos:
            box = QVBoxLayout()
            box.addWidget(QLabel(label))
            box.addWidget(widget)
            controls.addLayout(box)
        controls.addWidget(make)
        controls.addWidget(save)
        layout.addLayout(controls)

        self.figure = Figure(figsize=(8, 4.5))
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas, 1)
        return page

    def _export_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        column, _ = heading(
            "Save & share",
            "Keep a copy of the current table, or copy a plain-English summary of it.",
        )
        layout.addLayout(column)
        self.export_stats = QLabel()
        self.export_stats.setObjectName("pageHint")
        layout.addWidget(self.export_stats)

        row = QHBoxLayout()
        for label, tip, action in [
            ("Save as CSV…", "Best for Excel and most tools", lambda: self.save_file(".csv")),
            ("Save as Excel…", "Keeps everything in one workbook", lambda: self.save_file(".xlsx")),
            ("Save as Parquet…", "Compact format for big data", lambda: self.save_file(".parquet")),
        ]:
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(action)
            row.addWidget(button)
        layout.addLayout(row)

        summary_label = QLabel("Summary you can paste anywhere")
        summary_label.setObjectName("cardTitle")
        layout.addWidget(summary_label)
        self.summary_box = QPlainTextEdit()
        self.summary_box.setReadOnly(True)
        self.summary_box.setToolTip("Refreshed automatically - select all and copy with Ctrl+C")
        layout.addWidget(self.summary_box, 1)
        row2 = QHBoxLayout()
        refresh = QPushButton("Refresh summary")
        refresh.clicked.connect(self.refresh_summary)
        copy = QPushButton("Copy summary")
        copy.setObjectName("primary")
        copy.clicked.connect(self.copy_summary)
        row2.addWidget(refresh)
        row2.addWidget(copy)
        row2.addStretch(1)
        layout.addLayout(row2)
        self.refresh_summary()
        return page

    # ------------------------------------------------------------- actions

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open your data", "",
            "Data files (*.csv *.xlsx *.xls *.json *.parquet);;All files (*)",
        )
        if path:
            self._load_path(Path(path))

    def _load_path(self, path: Path) -> None:
        try:
            frame = core.load_table(path)
        except Exception as exc:  # show the real reason, in plain words
            QMessageBox.critical(self, "Could not open the file", str(exc))
            return
        self.load_frame(frame, name=str(path), remember=True)
        self._remember_recent(str(path))
        self.show_page("table")

    def open_sample(self) -> None:
        self.load_frame(core.SAMPLE, name="Sample data", remember=False)
        self.show_page("table")

    def save_file(self, suffix: str = "") -> None:
        suggested = f"data-studio-export{suffix or '.csv'}"
        filters = "CSV (*.csv);;Excel (*.xlsx);;Parquet (*.parquet);;All files (*)"
        path, _ = QFileDialog.getSaveFileName(self, "Save a copy", suggested, filters)
        if not path:
            return
        try:
            core.save_table(self.current_frame(), path)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save", str(exc))
            return
        self.statusBar().showMessage(f"Saved {path}", 6000)

    # cleaning actions ----------------------------------------------------

    def _fill_missing(self) -> None:
        df, n = core.fill_missing(self.current_frame())
        self.apply_change(df, f"Filled {n} missing value(s)")

    def _drop_duplicates(self) -> None:
        df, n = core.drop_duplicates(self.current_frame())
        self.apply_change(df, f"Removed {n} duplicate row(s)")

    def _trim_text(self) -> None:
        df, n = core.trim_text(self.current_frame())
        self.apply_change(df, f"Trimmed spaces in {n} cell(s)")

    def _to_numbers(self) -> None:
        df, n = core.to_numbers(self.current_frame())
        self.apply_change(df, f"Converted {n} text column(s) to numbers")

    def clean_everything(self) -> None:
        df, steps = core.clean_all(self.current_frame())
        self.apply_change(df, "+".join(steps))
        self.show_page("clean")

    # charts ---------------------------------------------------------------

    def draw_chart(self) -> None:
        df = self.current_frame()
        if df.empty:
            QMessageBox.information(self, "No data", "Open a file first.")
            return
        kind = self.chart_type.currentText()
        x = self.x_combo.currentText()
        y = self.y_combo.currentText()
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        try:
            if kind == "Bar chart":
                if df[y].dtype.kind in "biufc":
                    sample = df.groupby(x)[y].mean().head(30)
                else:
                    sample = df[x].value_counts().head(30)
                sample.plot.bar(ax=ax, color="#1d4ed8")
                ax.set_ylabel(f"Average {y}" if df[y].dtype.kind in "biufc" else "Count")
            elif kind == "Line chart":
                ax.plot(df[x], df[y], color="#1d4ed8", marker="o")
                ax.set_ylabel(y)
            elif kind == "Scatter plot":
                ax.scatter(df[x], df[y], color="#1d4ed8", alpha=0.75)
                ax.set_ylabel(y)
            else:  # Histogram
                ax.hist(pd.to_numeric(df[x], errors="coerce").dropna(), bins=20, color="#1d4ed8")
                ax.set_ylabel("Frequency")
        except Exception as exc:
            QMessageBox.warning(self, "Could not draw that chart",
                                f"Try different columns.\n\nDetails: {exc}")
            return
        ax.set_xlabel(x)
        ax.set_title(f"{kind}: {y if kind != 'Histogram' else x}")
        self.figure.tight_layout()
        self.canvas.draw()

    def save_chart(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save chart", "chart.png", "PNG image (*.png)")
        if path:
            self.figure.savefig(path, dpi=150, bbox_inches="tight")
            self.statusBar().showMessage(f"Chart saved to {path}", 5000)

    # export ----------------------------------------------------------------

    def refresh_summary(self) -> None:
        self.summary_box.setPlainText(core.describe(self.current_frame()))

    def copy_summary(self) -> None:
        self.refresh_summary()
        QApplication.clipboard().setText(self.summary_box.toPlainText())
        self.statusBar().showMessage("Summary copied - paste it anywhere with Ctrl+V.", 5000)

    # help -------------------------------------------------------------------

    def show_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Help & shortcuts")
        dialog.resize(560, 480)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(HELP_TEXT)
        layout.addWidget(text)
        close = QPushButton("Got it")
        close.setObjectName("primary")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def keyPressEvent(self, event):
        if event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_Z:
            self.undo()
            return
        super().keyPressEvent(event)


HELP_TEXT = """QUICK GUIDE
===========

1. Open your data
   Home -> 'Open my file'. CSV, Excel, JSON and Parquet all work.
   No file handy? Double-click the sample data - it is built in.

2. Look at it
   'My table' shows every row. Click any cell to fix it by hand.
   Amber cells are missing values. Use the search box to find things fast.

3. Tidy it
   'Clean' has one button per common problem:
     - Fill in missing values  (numbers get the median, text the most common)
     - Remove duplicate rows
     - Trim extra spaces
     - Convert text to numbers (e.g. '1,200' -> 1200)
   Or press 'Tidy everything for me' to do them all in one safe pass.
   Ctrl+Z (or 'Undo last change') steps back one fix at a time.

   'Process' is for real data processing steps:
      - Filter, cast, rename, select/drop columns, sort
      - Add calculated columns with expressions
      - Derive groups, split/join text, parse dates
      - Pivot/unpivot, sample, limit
   Every process step can be undone with Ctrl+Z or 'Undo last change'.

4. Chart it
   'Charts' -> pick a type, pick columns, press 'Make the chart'.
   'Save chart as image' exports a PNG you can drop into a report.

5. Analyze it
   'Analyze' picks from 17 built-in analyses (summary stats, tests, regression,
   time-series). Clean your data first for best results.

6. Keep it
   'Save & share' saves CSV / Excel / Parquet copies, or copies a
   plain-English summary of the table to paste into an email or chat.

COMMON QUESTIONS
================
Does my original file change?  No - only the copy in this window changes.
                               Your file on disk is never overwritten.
What if I make a mistake?      Ctrl+Z undoes the last fix, as far back as you like.
Where did my file go?          Recent files are listed on the Home screen.
It will not open my file        Make sure it is CSV/Excel/JSON/Parquet and not
                               password-protected. The error message says why.
"""


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Data Studio")
    app.setOrganizationName("Data Studio")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
