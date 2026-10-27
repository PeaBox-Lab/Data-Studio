import sys
import traceback
from pathlib import Path

try:
    import pandas as pd
    import numpy as np
    from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
    from PySide6.QtGui import QAction, QColor, QFont
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
        QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
        QPushButton, QPlainTextEdit, QProgressBar, QSplitter, QStackedWidget,
        QTableView, QTabWidget, QTextEdit, QToolBar, QVBoxLayout, QWidget
    )
except ImportError as exc:
    print("Missing dependency:", exc)
    print("Run: pip install PySide6 pandas numpy openpyxl pyarrow")
    raise


SAMPLE_DATA = pd.DataFrame({
    "time": ["2026-10-01 08:00", "2026-10-01 09:00", "2026-10-01 10:00", "2026-10-01 11:00", "2026-10-01 12:00", "2026-10-01 13:00"],
    "machine": ["M-101", "M-102", "M-103", "M-101", "M-104", "M-102"],
    "temperature": [82.4, 91.8, np.nan, 84.2, 96.5, 88.1],
    "pressure": [4.9, 5.6, 5.1, 4.8, 6.2, 5.3],
    "vibration": [2.1, 4.8, 2.5, 2.3, 6.4, 3.2],
    "output": [1180, 1042, 1158, 1204, 906, 1098],
    "status": ["Normal", "Review", "Missing", "Normal", "Anomaly", "Normal"],
})


class DataFrameModel(QAbstractTableModel):
    changed = Signal()

    def __init__(self, dataframe=None):
        super().__init__()
        self._df = dataframe.copy() if dataframe is not None else pd.DataFrame()
        self._visible_rows = list(range(len(self._df)))

    @property
    def dataframe(self):
        return self._df

    def set_dataframe(self, dataframe):
        self.beginResetModel()
        self._df = dataframe.copy()
        self._visible_rows = list(range(len(self._df)))
        self.endResetModel()
        self.changed.emit()

    def set_filter(self, text):
        self.beginResetModel()
        if not text.strip():
            self._visible_rows = list(range(len(self._df)))
        else:
            needle = text.lower()
            self._visible_rows = [
                i for i, row in self._df.iterrows()
                if any(needle in str(value).lower() for value in row.values)
            ]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self._visible_rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self._df.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        source_row = self._visible_rows[index.row()]
        value = self._df.iat[source_row, index.column()]
        column = self._df.columns[index.column()]
        if role in (Qt.DisplayRole, Qt.EditRole):
            return "" if pd.isna(value) else str(value)
        if role == Qt.BackgroundRole:
            if pd.isna(value):
                return QColor("#fff4d6")
            if column == "status" and str(value).lower() == "anomaly":
                return QColor("#ffe5e8")
        if role == Qt.ForegroundRole and pd.isna(value):
            return QColor("#a16207")
        if role == Qt.TextAlignmentRole and pd.api.types.is_numeric_dtype(self._df[column]):
            return Qt.AlignRight | Qt.AlignVCenter
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        source_row = self._visible_rows[index.row()]
        column = self._df.columns[index.column()]
        try:
            if pd.api.types.is_numeric_dtype(self._df[column]):
                value = np.nan if str(value).strip() == "" else float(value)
            self._df.iat[source_row, index.column()] = value
            self.dataChanged.emit(index, index)
            self.changed.emit()
            return True
        except Exception:
            return False

    def flags(self, index):
        return super().flags(index) | Qt.ItemIsEditable | Qt.ItemIsSelectable | Qt.ItemIsEnabled

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        return str(self._df.columns[section]) if orientation == Qt.Horizontal else str(section + 1)

    def sort(self, column, order):
        if self._df.empty:
            return
        name = self._df.columns[column]
        self.layoutAboutToBeChanged.emit()
        self._df = self._df.sort_values(name, ascending=order == Qt.AscendingOrder, na_position="last").reset_index(drop=True)
        self._visible_rows = list(range(len(self._df)))
        self.layoutChanged.emit()
        self.changed.emit()


class ToolCard(QFrame):
    clicked = Signal(str)

    def __init__(self, title, description):
        super().__init__()
        self.title = title
        self.setObjectName("toolCard")
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        name = QLabel(title)
        name.setObjectName("toolTitle")
        desc = QLabel(description)
        desc.setWordWrap(True)
        desc.setObjectName("muted")
        layout.addWidget(name)
        layout.addWidget(desc)

    def mousePressEvent(self, event):
        self.clicked.emit(self.title)
        super().mousePressEvent(event)


class Workbench(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Python Data Workbench v0.1")
        self.resize(1450, 900)
        self.history = []
        self.model = DataFrameModel(SAMPLE_DATA)
        self.model.changed.connect(self.refresh_status)
        self.build_ui()
        self.apply_styles()
        self.refresh_status()

    def build_ui(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for text, callback in [("Import", self.import_data), ("Export", self.export_data), ("Save snapshot", self.snapshot), ("Undo", self.undo)]:
            action = QAction(text, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)
        toolbar.addSeparator()
        python_action = QAction("Python Lab", self)
        python_action.triggered.connect(lambda: self.tabs.setCurrentWidget(self.python_page))
        toolbar.addAction(python_action)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        header = QFrame()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        logo = QLabel("Py")
        logo.setObjectName("logo")
        title_box = QVBoxLayout()
        title = QLabel("Python Data Workbench")
        title.setObjectName("appTitle")
        subtitle = QLabel("Spreadsheet simplicity with Python processing, science and engineering")
        subtitle.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        hl.addWidget(logo)
        hl.addLayout(title_box)
        hl.addStretch()
        self.status_label = QLabel()
        hl.addWidget(self.status_label)
        root.addWidget(header)

        splitter = QSplitter()
        splitter.addWidget(self.build_project_panel())
        splitter.addWidget(self.build_workspace())
        splitter.setSizes([235, 1215])
        root.addWidget(splitter, 1)

    def build_project_panel(self):
        panel = QFrame()
        panel.setObjectName("sidebar")
        layout = QVBoxLayout(panel)
        heading = QLabel("PROJECT")
        heading.setObjectName("sectionHeading")
        layout.addWidget(heading)
        dataset = QFrame()
        dataset.setObjectName("datasetCard")
        dl = QVBoxLayout(dataset)
        self.dataset_name = QLabel("sensor_readings")
        self.dataset_name.setObjectName("toolTitle")
        self.dataset_size = QLabel()
        self.dataset_size.setObjectName("muted")
        dl.addWidget(self.dataset_name)
        dl.addWidget(self.dataset_size)
        layout.addWidget(dataset)
        layout.addWidget(QLabel("Data quality"))
        self.quality = QProgressBar()
        self.quality.setRange(0, 100)
        layout.addWidget(self.quality)
        self.quality_note = QLabel()
        self.quality_note.setObjectName("muted")
        layout.addWidget(self.quality_note)
        layout.addWidget(QLabel("Workflow"))
        self.workflow = QListWidget()
        self.workflow.addItems(["1. Import sample data", "2. Profile dataset"])
        layout.addWidget(self.workflow, 1)
        return panel

    def build_workspace(self):
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        definitions = {
            "Prepare": [("Missing values", "Fill, interpolate or remove"), ("Duplicates", "Remove exact duplicate rows"), ("Data types", "Infer and convert types"), ("Text cleanup", "Trim and standardize text"), ("Join tables", "Merge another dataset"), ("Pivot / reshape", "Pivot, melt and aggregate")],
            "Explore": [("Data profile", "Summary, types and nulls"), ("Distributions", "Describe numeric distributions"), ("Correlation", "Numeric correlation matrix"), ("Outliers", "Detect using IQR"), ("Group analysis", "Aggregate by category"), ("Time patterns", "Resample and rolling metrics")],
            "Statistics": [("Descriptive statistics", "Mean, spread and percentiles"), ("Hypothesis tests", "t-test, ANOVA and chi-square"), ("Regression", "Linear and logistic models"), ("Reliability", "Failure and survival analysis"), ("Quality control", "SPC and capability"), ("Experimental design", "Power and sample size")],
            "Data Science": [("Predict", "Classification and regression"), ("Forecast", "Time-series prediction"), ("Cluster", "Segmentation"), ("Feature engineering", "Scale, encode and derive"), ("Model evaluation", "Cross-validation and metrics"), ("Anomaly detection", "Isolation-based detection")],
            "Engineering": [("Units", "Unit conversion"), ("Signal processing", "FFT, filtering and peaks"), ("Numerical methods", "Integration and equation solving"), ("Optimization", "Constrained solving"), ("Simulation", "Monte Carlo analysis"), ("Matrix operations", "Linear algebra")],
        }
        for section, cards in definitions.items():
            self.tabs.addTab(self.make_section(section, cards), section)
        self.tabs.addTab(self.build_data_page(), "Data Grid")
        self.python_page = self.build_python_page()
        self.tabs.addTab(self.python_page, "Python Lab")
        return self.tabs

    def make_section(self, title, cards):
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel(title)
        label.setObjectName("pageTitle")
        layout.addWidget(label)
        grid_row = QHBoxLayout()
        for i, (name, desc) in enumerate(cards):
            card = ToolCard(name, desc)
            card.clicked.connect(self.select_tool)
            grid_row.addWidget(card)
            if i == 2:
                layout.addLayout(grid_row)
                grid_row = QHBoxLayout()
        layout.addLayout(grid_row)
        layout.addStretch()
        return page

    def build_data_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        bar = QHBoxLayout()
        title = QLabel("Live DataFrame")
        title.setObjectName("pageTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search all columns...")
        self.search.textChanged.connect(self.model.set_filter)
        bar.addWidget(title)
        bar.addStretch()
        bar.addWidget(self.search)
        layout.addLayout(bar)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        return page

    def build_python_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        intro = QLabel("Python Lab: the active table is available as df")
        intro.setObjectName("pageTitle")
        layout.addWidget(intro)
        split = QSplitter(Qt.Vertical)
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas", 11))
        self.editor.setPlainText("# Modify df and click Run\n# Available modules: pd, np\n\ndf['temperature'] = df['temperature'].fillna(df['temperature'].median())\ndf['efficiency'] = (df['output'] / df['pressure']).round(1)\ndf")
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(QFont("Consolas", 10))
        split.addWidget(self.editor)
        split.addWidget(self.console)
        split.setSizes([500, 160])
        layout.addWidget(split)
        run = QPushButton("Run Python on active DataFrame")
        run.setObjectName("primary")
        run.clicked.connect(self.run_python)
        layout.addWidget(run)
        return page

    def select_tool(self, name):
        handlers = {
            "Missing values": self.fill_missing,
            "Duplicates": self.remove_duplicates,
            "Data types": self.infer_types,
            "Text cleanup": self.clean_text,
            "Data profile": self.show_profile,
            "Distributions": self.show_profile,
            "Correlation": self.show_correlation,
            "Outliers": self.detect_outliers,
            "Descriptive statistics": self.show_profile,
            "Feature engineering": lambda: self.tabs.setCurrentWidget(self.python_page),
            "Matrix operations": self.matrix_summary,
        }
        if name in handlers:
            handlers[name]()
        else:
            QMessageBox.information(self, name, f"{name} is positioned in the interface and ready for its Python processing module.")

    def push_history(self):
        self.history.append(self.model.dataframe.copy())
        if len(self.history) > 20:
            self.history.pop(0)

    def apply_dataframe(self, df, step):
        self.push_history()
        self.model.set_dataframe(df)
        self.workflow.addItem(f"{self.workflow.count()+1}. {step}")
        self.tabs.setCurrentIndex(self.tabs.indexOf(self.data_page()) if self.data_page() else 5)
        self.refresh_status()

    def data_page(self):
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == "Data Grid":
                return self.tabs.widget(i)
        return None

    def fill_missing(self):
        df = self.model.dataframe.copy()
        numeric = df.select_dtypes(include=np.number).columns
        for col in numeric:
            df[col] = df[col].fillna(df[col].median())
        for col in df.columns.difference(numeric):
            if df[col].isna().any():
                mode = df[col].mode()
                df[col] = df[col].fillna(mode.iloc[0] if not mode.empty else "")
        self.apply_dataframe(df, "Fill missing values")

    def remove_duplicates(self):
        self.apply_dataframe(self.model.dataframe.drop_duplicates().reset_index(drop=True), "Remove duplicates")

    def infer_types(self):
        df = self.model.dataframe.copy()
        for col in df.columns:
            if df[col].dtype == object:
                converted = pd.to_numeric(df[col], errors="coerce")
                if converted.notna().sum() >= max(1, int(len(df) * .8)):
                    df[col] = converted
        self.apply_dataframe(df, "Infer data types")

    def clean_text(self):
        df = self.model.dataframe.copy()
        for col in df.select_dtypes(include="object"):
            df[col] = df[col].astype(str).str.strip().replace("nan", np.nan)
        self.apply_dataframe(df, "Clean text")

    def detect_outliers(self):
        df = self.model.dataframe.copy()
        numeric = list(df.select_dtypes(include=np.number).columns)
        flags = pd.Series(False, index=df.index)
        for col in numeric:
            q1, q3 = df[col].quantile([.25, .75])
            iqr = q3 - q1
            flags |= (df[col] < q1 - 1.5 * iqr) | (df[col] > q3 + 1.5 * iqr)
        df["is_outlier"] = flags
        self.apply_dataframe(df, "Detect IQR outliers")

    def show_profile(self):
        self.console.setPlainText(self.model.dataframe.describe(include="all").transpose().to_string())
        self.tabs.setCurrentWidget(self.python_page)

    def show_correlation(self):
        result = self.model.dataframe.select_dtypes(include=np.number).corr().round(3)
        self.console.setPlainText(result.to_string())
        self.tabs.setCurrentWidget(self.python_page)

    def matrix_summary(self):
        numeric = self.model.dataframe.select_dtypes(include=np.number)
        self.console.setPlainText(f"Shape: {numeric.shape}\nRank: {np.linalg.matrix_rank(numeric.fillna(0).to_numpy())}\n\nCovariance:\n{numeric.cov().round(3)}")
        self.tabs.setCurrentWidget(self.python_page)

    def run_python(self):
        self.push_history()
        namespace = {"df": self.model.dataframe.copy(), "pd": pd, "np": np}
        try:
            exec(self.editor.toPlainText(), {"__builtins__": __builtins__}, namespace)
            result = namespace.get("df")
            if not isinstance(result, pd.DataFrame):
                raise TypeError("Variable 'df' must remain a pandas DataFrame")
            self.model.set_dataframe(result)
            self.workflow.addItem(f"{self.workflow.count()+1}. Run Python Lab")
            self.console.setPlainText(f"Success\nRows: {len(result)}\nColumns: {len(result.columns)}\n\n{result.head(20).to_string()}")
        except Exception:
            self.console.setPlainText(traceback.format_exc())
        self.refresh_status()

    def import_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import data", "", "Data files (*.csv *.xlsx *.xls *.json *.parquet)")
        if not path:
            return
        try:
            suffix = Path(path).suffix.lower()
            if suffix == ".csv": df = pd.read_csv(path)
            elif suffix in (".xlsx", ".xls"): df = pd.read_excel(path)
            elif suffix == ".json": df = pd.read_json(path)
            elif suffix == ".parquet": df = pd.read_parquet(path)
            else: raise ValueError("Unsupported file format")
            self.model.set_dataframe(df)
            self.dataset_name.setText(Path(path).stem)
            self.workflow.clear(); self.workflow.addItem("1. Import data")
            self.tabs.setCurrentWidget(self.data_page())
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))

    def export_data(self):
        path, selected = QFileDialog.getSaveFileName(self, "Export data", "processed_data.xlsx", "Excel (*.xlsx);;CSV (*.csv);;Parquet (*.parquet)")
        if not path:
            return
        try:
            suffix = Path(path).suffix.lower()
            if suffix == ".csv": self.model.dataframe.to_csv(path, index=False)
            elif suffix == ".parquet": self.model.dataframe.to_parquet(path, index=False)
            else: self.model.dataframe.to_excel(path, index=False)
            self.statusBar().showMessage(f"Exported {path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def snapshot(self):
        self.push_history()
        self.statusBar().showMessage("Snapshot saved for Undo", 3000)

    def undo(self):
        if self.history:
            self.model.set_dataframe(self.history.pop())
            self.workflow.addItem(f"{self.workflow.count()+1}. Undo")

    def refresh_status(self):
        df = self.model.dataframe
        missing = int(df.isna().sum().sum()) if not df.empty else 0
        cells = max(1, df.size)
        score = int(100 * (1 - missing / cells))
        self.dataset_size.setText(f"{len(df):,} rows × {len(df.columns)} columns")
        self.quality.setValue(score)
        self.quality_note.setText(f"{missing} missing cells · {df.duplicated().sum()} duplicate rows")
        self.status_label.setText(f"{len(df):,} rows  |  {len(df.columns)} columns  |  Quality {score}%")

    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f6f8fb; color: #182230; font-family: Segoe UI, Arial; }
            QToolBar { background: white; border-bottom: 1px solid #dde3ea; spacing: 6px; padding: 5px; }
            QToolBar QToolButton { padding: 7px 10px; border-radius: 7px; }
            QToolBar QToolButton:hover { background: #eef4ff; }
            #header { background: white; border-bottom: 1px solid #dde3ea; }
            #logo { background: #2563eb; color: white; font-weight: 800; font-size: 16px; border-radius: 10px; padding: 9px; }
            #appTitle { font-size: 17px; font-weight: 700; }
            #pageTitle { font-size: 19px; font-weight: 700; }
            #sectionHeading { color: #778396; font-size: 11px; font-weight: 700; }
            #muted { color: #667085; font-size: 11px; }
            #sidebar { background: white; border-right: 1px solid #dde3ea; }
            #datasetCard { background: #eef4ff; border: 1px solid #bfd3ff; border-radius: 10px; }
            #toolCard { background: white; border: 1px solid #dde3ea; border-radius: 12px; min-height: 90px; }
            #toolCard:hover { border: 1px solid #6b9cff; background: #f8fbff; }
            #toolTitle { font-size: 13px; font-weight: 700; }
            QTabWidget::pane { border: none; }
            QTabBar::tab { background: #eef1f5; padding: 9px 14px; margin: 2px; border-radius: 7px; }
            QTabBar::tab:selected { background: #2563eb; color: white; }
            QTableView { background: white; alternate-background-color: #f8fafc; gridline-color: #e5e9ef; border: 1px solid #dde3ea; border-radius: 8px; }
            QHeaderView::section { background: #f1f4f8; padding: 8px; border: none; border-right: 1px solid #dde3ea; border-bottom: 1px solid #dde3ea; font-weight: 600; }
            QLineEdit, QComboBox, QPlainTextEdit, QTextEdit { background: white; border: 1px solid #ccd4df; border-radius: 7px; padding: 7px; }
            QPushButton { background: white; border: 1px solid #ccd4df; border-radius: 7px; padding: 8px 12px; }
            QPushButton:hover { background: #eef4ff; border-color: #7aa2f7; }
            QPushButton#primary { background: #2563eb; color: white; border: none; font-weight: 700; padding: 11px; }
            QProgressBar { border: none; background: #e7edf4; height: 9px; border-radius: 4px; text-align: center; }
            QProgressBar::chunk { background: #16a34a; border-radius: 4px; }
            QListWidget { background: transparent; border: none; }
        """)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Python Data Workbench")
    window = Workbench()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
