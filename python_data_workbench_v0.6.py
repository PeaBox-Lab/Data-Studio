import sys
import traceback
import json
import pickle
import re
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
        QTableView, QTabWidget, QTextEdit, QToolBar, QVBoxLayout, QWidget,
        QDialog, QDialogButtonBox, QFormLayout, QInputDialog, QScrollArea, QSpinBox, QCheckBox
    )
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
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
        self.setWindowTitle("Python Data Workbench v0.6 Analytics Platform")
        self.resize(1450, 900)
        self.history = []
        self.datasets = {"sensor_readings": SAMPLE_DATA.copy()}
        self.active_dataset = "sensor_readings"
        self.pipeline_steps = []
        self.trained_models = {}
        self.metric_definitions = []
        self.dashboard_cards = []
        self.saved_pipelines = {}
        self.kpi_definitions = []
        self.dashboard_filters = {}
        self.model = DataFrameModel(self.datasets[self.active_dataset])
        self.model.changed.connect(self.refresh_status)
        self.build_ui()
        self.apply_styles()
        self.refresh_dataset_list()
        self.refresh_status()

    def build_ui(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for text, callback in [("Import", self.import_data), ("Export", self.export_data), ("Save project", self.save_project), ("Open project", self.open_project), ("Save snapshot", self.snapshot), ("Undo", self.undo)]:
            action = QAction(text, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)
        toolbar.addSeparator()
        python_action = QAction("Python Lab", self)
        python_action.triggered.connect(lambda: self.tabs.setCurrentWidget(self.python_page))
        toolbar.addAction(python_action)
        pipeline_action = QAction("Export workflow", self)
        pipeline_action.triggered.connect(self.export_pipeline)
        toolbar.addAction(pipeline_action)
        sql_action = QAction("SQL Workspace", self)
        sql_action.triggered.connect(lambda: self.tabs.setCurrentWidget(self.sql_page))
        toolbar.addAction(sql_action)

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
        controls = QHBoxLayout()
        add_data = QPushButton("+ Dataset"); add_data.clicked.connect(self.import_data)
        remove_data = QPushButton("Remove"); remove_data.clicked.connect(self.remove_dataset)
        controls.addWidget(add_data); controls.addWidget(remove_data)
        layout.addLayout(controls)
        self.dataset_list = QListWidget()
        self.dataset_list.setMaximumHeight(150)
        self.dataset_list.itemSelectionChanged.connect(self.switch_dataset)
        layout.addWidget(self.dataset_list)
        self.dataset_name = QLabel("sensor_readings")
        self.dataset_name.setObjectName("toolTitle")
        self.dataset_size = QLabel(); self.dataset_size.setObjectName("muted")
        layout.addWidget(self.dataset_name); layout.addWidget(self.dataset_size)
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
            "Analytics": [("KPI summary", "Numeric KPIs, quality and coverage"), ("Define KPI", "Reusable calculated business metric"), ("Save pipeline", "Save workflow as reusable recipe"), ("Run pipeline", "Replay a saved visual pipeline"), ("Dashboard designer", "Configure filters and dashboard cards"), ("SQL analysis", "Query all project tables with DuckDB")],
            "Business Analysis": [("Segment analysis", "Compare performance across categories"), ("Trend analysis", "Time trends, growth and moving averages"), ("Pareto analysis", "Rank contributors and cumulative share"), ("Cohort table", "Cross-tab two business dimensions"), ("Executive summary", "Generate a concise analytical narrative"), ("KPI summary", "Refresh dashboard metrics")],
            "Statistics": [("Descriptive statistics", "Mean, spread and percentiles")],
            "Statistics": [("Descriptive statistics", "Mean, spread and percentiles"), ("Hypothesis tests", "t-test, ANOVA and chi-square"), ("Regression", "Linear and logistic models"), ("Reliability", "Failure and survival analysis"), ("Quality control", "SPC and capability"), ("Experimental design", "Power and sample size")],
            "Data Science": [("Train model", "Classification or regression with automatic preprocessing"), ("Compare models", "Cross-validated model leaderboard"), ("Forecast", "Time-series prediction"), ("Cluster", "K-means segmentation"), ("Feature engineering", "Scale, encode and derive"), ("Anomaly detection", "Isolation-based detection"), ("Save model", "Persist a trained model artifact"), ("Batch predict", "Apply saved model to another dataset")],
            "Engineering": [("Units", "Unit conversion"), ("Signal processing", "FFT, filtering and peaks"), ("Numerical methods", "Integration and equation solving"), ("Optimization", "Constrained solving"), ("Simulation", "Monte Carlo analysis"), ("Matrix operations", "Linear algebra")],
        }
        for section, cards in definitions.items():
            self.tabs.addTab(self.make_section(section, cards), section)
        self.tabs.addTab(self.build_dashboard_page(), "Dashboard")
        self.tabs.addTab(self.build_data_page(), "Data Grid")
        self.chart_page = self.build_chart_page()
        self.tabs.addTab(self.chart_page, "Charts")
        self.sql_page = self.build_sql_page()
        self.tabs.addTab(self.sql_page, "SQL")
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

    def define_kpi(self):
        df=self.model.dataframe
        numeric=list(df.select_dtypes(include=np.number).columns)
        if not numeric: QMessageBox.warning(self,"Define KPI","No numeric columns available."); return
        name,ok=QInputDialog.getText(self,"Define KPI","KPI name",text=f"Total {numeric[0]}")
        if not ok or not name.strip():return
        column,ok=QInputDialog.getItem(self,"Define KPI","Measure column",numeric,0,False)
        if not ok:return
        agg,ok=QInputDialog.getItem(self,"Define KPI","Aggregation",["sum","mean","median","min","max","count","nunique"],0,False)
        if not ok:return
        fmt,ok=QInputDialog.getItem(self,"Define KPI","Display format",["number","integer","percent","currency"],0,False)
        if not ok:return
        definition={"name":name.strip(),"column":column,"aggregation":agg,"format":fmt,"dataset":self.active_dataset}; self.kpi_definitions=[k for k in self.kpi_definitions if k["name"]!=definition["name"]]; self.kpi_definitions.append(definition); self.refresh_dashboard()

    def calculate_kpi(self,definition,df):
        col=definition["column"]; agg=definition["aggregation"]
        if col not in df.columns:return None
        series=df[col]; value=series.nunique() if agg=="nunique" else getattr(series,agg)()
        fmt=definition.get("format","number")
        if fmt=="integer":return f"{value:,.0f}"
        if fmt=="percent":return f"{value:.1%}"
        if fmt=="currency":return f"₹{value:,.2f}"
        return f"{value:,.2f}" if isinstance(value,(int,float,np.number)) else str(value)

    def save_pipeline_definition(self):
        if not self.pipeline_steps: QMessageBox.information(self,"Save pipeline","No workflow steps are available yet."); return
        name,ok=QInputDialog.getText(self,"Save pipeline","Pipeline name",text="Analysis pipeline")
        if ok and name.strip(): self.saved_pipelines[name.strip()]=[dict(x) for x in self.pipeline_steps]; QMessageBox.information(self,"Pipeline saved",f"Saved {len(self.pipeline_steps)} workflow steps as '{name.strip()}'.")

    def run_saved_pipeline(self):
        if not self.saved_pipelines: QMessageBox.information(self,"Run pipeline","No saved pipelines yet."); return
        name,ok=QInputDialog.getItem(self,"Run pipeline","Pipeline",list(self.saved_pipelines),0,False)
        if not ok:return
        runnable={"Fill missing values":self.fill_missing,"Remove duplicates":self.remove_duplicates,"Infer data types":self.infer_types,"Clean text":self.clean_text,"Detect IQR outliers":self.detect_outliers}
        executed=0; skipped=[]
        for item in self.saved_pipelines[name]:
            step=item.get("step",""); fn=runnable.get(step)
            if fn: fn(); executed+=1
            else: skipped.append(step)
        QMessageBox.information(self,"Pipeline complete",f"Executed {executed} reusable steps. Skipped {len(skipped)} parameterized or unsupported historical steps." + ("\n\nSkipped: " + ", ".join(skipped[:8]) if skipped else ""))

    def open_dashboard_designer(self):
        dialog=QDialog(self); dialog.setWindowTitle("Dashboard Designer"); dialog.resize(560,420); form=QFormLayout(dialog)
        dimension=QComboBox(); dimension.addItem("No filter"); dimension.addItems([str(c) for c in self.model.dataframe.columns]); form.addRow("Dashboard filter",dimension)
        chart=QComboBox(); chart.addItems(["Histogram","Bar","Line","Scatter"]); form.addRow("Primary chart",chart)
        kpis=QListWidget();
        for definition in self.kpi_definitions:
            item=QListWidgetItem(definition["name"]); item.setCheckState(Qt.Checked); kpis.addItem(item)
        form.addRow("KPI cards",kpis)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); form.addRow(buttons)
        def save_design():
            self.dashboard_filters={"column":dimension.currentText(),"value":None}; self.dashboard_cards=[{"type":"chart","chart":chart.currentText()}]+[{"type":"kpi","name":kpis.item(i).text()} for i in range(kpis.count()) if kpis.item(i).checkState()==Qt.Checked]; dialog.accept(); self.refresh_dashboard(); self.tabs.setCurrentWidget(next(self.tabs.widget(i) for i in range(self.tabs.count()) if self.tabs.tabText(i)=="Dashboard"))
        buttons.accepted.connect(save_design); buttons.rejected.connect(dialog.reject); dialog.exec()

    def dashboard_filtered_df(self):
        df=self.model.dataframe.copy(); col=self.dashboard_filters.get("column") if self.dashboard_filters else None; value=self.dashboard_filters.get("value") if self.dashboard_filters else None
        if col and col!="No filter" and col in df.columns and value is not None:return df[df[col].astype(str)==str(value)]
        return df

    def build_dashboard_page(self):
        page = QWidget(); outer = QVBoxLayout(page)
        top = QHBoxLayout(); title = QLabel("Analytics Dashboard"); title.setObjectName("pageTitle")
        refresh = QPushButton("Refresh dashboard"); refresh.setObjectName("primary"); refresh.clicked.connect(self.refresh_dashboard)
        self.dashboard_filter_value=QComboBox(); self.dashboard_filter_value.currentTextChanged.connect(self.apply_dashboard_filter_value)
        top.addWidget(title); top.addStretch(); top.addWidget(QLabel("Filter")); top.addWidget(self.dashboard_filter_value); top.addWidget(refresh); outer.addLayout(top)
        self.dashboard_summary = QLabel("Select or import a dataset to generate analytics."); self.dashboard_summary.setWordWrap(True); self.dashboard_summary.setObjectName("muted"); outer.addWidget(self.dashboard_summary)
        self.kpi_strip = QHBoxLayout(); outer.addLayout(self.kpi_strip)
        split = QSplitter()
        self.dashboard_table = QTableView(); self.dashboard_table_model = DataFrameModel(pd.DataFrame()); self.dashboard_table.setModel(self.dashboard_table_model)
        self.dashboard_figure = Figure(figsize=(7,4), tight_layout=True); self.dashboard_canvas = FigureCanvas(self.dashboard_figure)
        split.addWidget(self.dashboard_table); split.addWidget(self.dashboard_canvas); split.setSizes([500,700]); outer.addWidget(split,1)
        self.dashboard_insights = QTextEdit(); self.dashboard_insights.setReadOnly(True); self.dashboard_insights.setMaximumHeight(150); outer.addWidget(self.dashboard_insights)
        return page

    def clear_layout(self, layout):
        while layout.count():
            item=layout.takeAt(0); widget=item.widget()
            if widget: widget.deleteLater()

    def refresh_dashboard(self):
        if not hasattr(self, "kpi_strip"): return
        df=self.dashboard_filtered_df()
        self.clear_layout(self.kpi_strip)
        if hasattr(self,"dashboard_filter_value"):
            col=self.dashboard_filters.get("column") if self.dashboard_filters else None; current=self.dashboard_filter_value.currentText(); self.dashboard_filter_value.blockSignals(True); self.dashboard_filter_value.clear(); self.dashboard_filter_value.addItem("All")
            if col and col!="No filter" and col in self.model.dataframe.columns:self.dashboard_filter_value.addItems([str(x) for x in self.model.dataframe[col].dropna().unique()[:200]])
            if current:self.dashboard_filter_value.setCurrentText(current)
            self.dashboard_filter_value.blockSignals(False)
        numeric=list(df.select_dtypes(include=np.number).columns)
        metrics=[("Rows", f"{len(df):,}"),("Columns",str(len(df.columns))),("Missing",f"{int(df.isna().sum().sum()):,}"),("Duplicates",f"{int(df.duplicated().sum()):,}")]
        for col in numeric[:2]: metrics.append((f"Avg {col}", f"{df[col].mean():,.2f}"))
        for definition in self.kpi_definitions:
            if definition.get("dataset")==self.active_dataset:
                value=self.calculate_kpi(definition,df)
                if value is not None: metrics.append((definition["name"],value))
        for label,value in metrics[:8]:
            card=QFrame(); card.setObjectName("datasetCard"); cl=QVBoxLayout(card); a=QLabel(label); a.setObjectName("muted"); b=QLabel(value); b.setStyleSheet("font-size: 20px; font-weight: 700; color:#1d4ed8"); cl.addWidget(a); cl.addWidget(b); self.kpi_strip.addWidget(card)
        self.dashboard_summary.setText(f"Active dataset: {self.active_dataset}. The dashboard uses the same live DataFrame as visual tools and Python Lab.")
        if numeric:
            summary=df[numeric].describe().transpose().reset_index().rename(columns={"index":"metric"}).round(3); self.dashboard_table_model.set_dataframe(summary)
            self.dashboard_figure.clear(); ax=self.dashboard_figure.add_subplot(111); col=numeric[0]; clean=df[col].dropna(); ax.hist(clean,bins=min(20,max(5,int(max(1,len(clean))**.5))),color="#2563eb",edgecolor="white"); ax.set_title(f"Distribution of {col}"); ax.set_xlabel(col); ax.set_ylabel("Frequency"); ax.grid(True,alpha=.2); self.dashboard_canvas.draw()
        else: self.dashboard_table_model.set_dataframe(pd.DataFrame({"message":["No numeric columns"]}))
        self.dashboard_insights.setPlainText(self.make_executive_summary())

    def apply_dashboard_filter_value(self,value):
        if not self.dashboard_filters:return
        self.dashboard_filters["value"]=None if value in ("","All") else value
        self.refresh_dashboard()

    def make_executive_summary(self):
        df=self.model.dataframe; numeric=list(df.select_dtypes(include=np.number).columns); cats=list(df.select_dtypes(exclude=np.number).columns); lines=[f"Dataset {self.active_dataset} contains {len(df):,} rows and {len(df.columns)} columns.",f"Data quality: {int(df.isna().sum().sum())} missing cells and {int(df.duplicated().sum())} duplicate rows."]
        for c in numeric[:3]:
            series=df[c].dropna()
            if len(series): lines.append(f"{c}: mean {series.mean():,.2f}, median {series.median():,.2f}, range {series.min():,.2f} to {series.max():,.2f}.")
        if cats:
            c=cats[0]; top=df[c].value_counts(dropna=False)
            if len(top): lines.append(f"Largest {c} segment: {top.index[0]} with {top.iloc[0]:,} records ({top.iloc[0]/max(1,len(df))*100:.1f}%).")
        return "\n".join(lines)

    def kpi_summary(self):
        self.refresh_dashboard(); self.tabs.setCurrentWidget(self.tabs.widget(0) if self.tabs.tabText(0)=="Dashboard" else next(self.tabs.widget(i) for i in range(self.tabs.count()) if self.tabs.tabText(i)=="Dashboard"))

    def segment_analysis(self):
        group=self.choose_column("Segment analysis - dimension"); value=self.choose_column("Segment analysis - measure",True)
        if group and value:
            result=self.model.dataframe.groupby(group,dropna=False)[value].agg(records="count",total="sum",average="mean",median="median").reset_index().sort_values("total",ascending=False); result["share_pct"]=(result["total"]/result["total"].sum()*100).round(2); self.apply_dataframe(result,f"Segment analysis: {value} by {group}")

    def trend_analysis(self):
        time_col=self.choose_column("Trend - date/time"); value=self.choose_column("Trend - measure",True)
        if time_col and value:
            raw=self.model.dataframe.copy(); raw[time_col]=pd.to_datetime(raw[time_col],errors="coerce"); freq,ok=QInputDialog.getItem(self,"Trend analysis","Frequency",["D","W","ME","QE","YE"],0,False)
            if not ok:return
            result=raw.dropna(subset=[time_col]).set_index(time_col)[value].resample(freq).sum().to_frame(value).reset_index(); result["previous"]=result[value].shift(1); result["growth_pct"]=(result[value].pct_change()*100).round(2); result["moving_avg_3"]=result[value].rolling(3,min_periods=1).mean().round(3); self.apply_dataframe(result,f"Trend analysis: {value} ({freq})")

    def pareto_analysis(self):
        category=self.choose_column("Pareto - category"); value=self.choose_column("Pareto - measure",True)
        if category and value:
            result=self.model.dataframe.groupby(category,dropna=False)[value].sum().reset_index().sort_values(value,ascending=False); total=result[value].sum(); result["share_pct"]=(result[value]/total*100).round(2); result["cumulative_pct"]=result["share_pct"].cumsum().round(2); self.apply_dataframe(result,f"Pareto analysis: {value} by {category}")

    def cohort_table(self):
        row=self.choose_column("Cohort table - row dimension"); col=self.choose_column("Cohort table - column dimension")
        if row and col and row!=col:
            result=pd.crosstab(self.model.dataframe[row],self.model.dataframe[col],margins=True).reset_index(); self.apply_dataframe(result,f"Cohort table: {row} x {col}")

    def executive_summary(self):
        self.console.setPlainText(self.make_executive_summary()); self.tabs.setCurrentWidget(self.python_page)

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

    def build_sql_page(self):
        page=QWidget(); layout=QVBoxLayout(page)
        head=QHBoxLayout(); title=QLabel("DuckDB SQL Workspace"); title.setObjectName("pageTitle")
        self.sql_tables=QComboBox(); self.sql_tables.currentTextChanged.connect(self.insert_sql_table)
        run=QPushButton("Run SQL"); run.setObjectName("primary"); run.clicked.connect(self.run_sql)
        save=QPushButton("Save result as dataset"); save.clicked.connect(self.save_sql_result)
        head.addWidget(title); head.addStretch(); head.addWidget(QLabel("Tables")); head.addWidget(self.sql_tables); head.addWidget(run); head.addWidget(save); layout.addLayout(head)
        split=QSplitter(Qt.Vertical)
        self.sql_editor=QPlainTextEdit(); self.sql_editor.setFont(QFont("Consolas",11)); self.sql_editor.setPlainText('SELECT * FROM "sensor_readings" LIMIT 100;')
        self.sql_result_model=DataFrameModel(pd.DataFrame()); self.sql_result_table=QTableView(); self.sql_result_table.setModel(self.sql_result_model); self.sql_result_table.setSortingEnabled(True)
        split.addWidget(self.sql_editor); split.addWidget(self.sql_result_table); split.setSizes([220,480]); layout.addWidget(split,1)
        self.sql_status=QLabel("DuckDB queries can reference every project dataset by its dataset name."); self.sql_status.setObjectName("muted"); layout.addWidget(self.sql_status)
        self.last_sql_result=pd.DataFrame(); self.refresh_sql_tables(); return page

    def refresh_sql_tables(self):
        if not hasattr(self,"sql_tables"): return
        current=self.sql_tables.currentText(); self.sql_tables.blockSignals(True); self.sql_tables.clear(); self.sql_tables.addItems(list(self.datasets));
        if current in self.datasets:self.sql_tables.setCurrentText(current)
        self.sql_tables.blockSignals(False)

    def insert_sql_table(self,name):
        if name and hasattr(self,"sql_editor") and not self.sql_editor.toPlainText().strip(): self.sql_editor.setPlainText(f'SELECT * FROM "{name}" LIMIT 100;')

    def run_sql(self):
        try:
            import duckdb
            con=duckdb.connect(database=":memory:")
            for name,df in self.datasets.items():
                safe=re.sub(r"[^A-Za-z0-9_]","_",name); con.register(safe,df)
            query=self.sql_editor.toPlainText().strip()
            self.last_sql_result=con.execute(query).fetchdf(); self.sql_result_model.set_dataframe(self.last_sql_result)
            self.sql_status.setText(f"Query complete: {len(self.last_sql_result):,} rows × {len(self.last_sql_result.columns)} columns")
            self.workflow.addItem(f"{self.workflow.count()+1}. Run DuckDB SQL")
        except Exception as exc: self.sql_status.setText(f"SQL error: {exc}")

    def save_sql_result(self):
        if self.last_sql_result is None or self.last_sql_result.empty: QMessageBox.information(self,"SQL result","Run a query that returns rows first."); return
        name,ok=QInputDialog.getText(self,"Save SQL result","Dataset name",text="sql_result")
        if ok and name.strip():
            name=self.unique_dataset_name(name.strip()); self.datasets[name]=self.last_sql_result.copy(); self.active_dataset=name; self.model.set_dataframe(self.datasets[name]); self.refresh_dataset_list(select=name); self.refresh_sql_tables(); self.refresh_status()

    def build_chart_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QHBoxLayout()
        title = QLabel("Visual Chart Builder")
        title.setObjectName("pageTitle")
        self.chart_type = QComboBox(); self.chart_type.addItems(["Line", "Bar", "Scatter", "Histogram", "Box", "Correlation heatmap"])
        self.chart_x = QComboBox(); self.chart_y = QComboBox()
        self.chart_group = QComboBox(); self.chart_group.addItem("No grouping")
        refresh = QPushButton("Create chart"); refresh.setObjectName("primary"); refresh.clicked.connect(self.create_chart)
        export = QPushButton("Export PNG"); export.clicked.connect(self.export_chart)
        for label, widget in [("Type", self.chart_type), ("X", self.chart_x), ("Y", self.chart_y), ("Group", self.chart_group)]:
            top.addWidget(QLabel(label)); top.addWidget(widget)
        top.addStretch(); top.addWidget(refresh); top.addWidget(export)
        layout.addLayout(top)
        self.figure = Figure(figsize=(10, 6), tight_layout=True)
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas, 1)
        self.refresh_chart_columns()
        return page

    def build_python_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        head = QHBoxLayout()
        intro = QLabel("Python Lab: active table is df")
        intro.setObjectName("pageTitle")
        self.template_box = QComboBox()
        self.template_box.addItems(["Choose template", "Clean data", "Group analysis", "Regression", "Chart", "Machine learning"])
        self.template_box.currentTextChanged.connect(self.load_python_template)
        run = QPushButton("Run all"); run.setObjectName("primary"); run.clicked.connect(self.run_python)
        run_selection = QPushButton("Run selection"); run_selection.clicked.connect(self.run_selection)
        reset = QPushButton("Reset df"); reset.clicked.connect(self.reset_python_df)
        save_script = QPushButton("Save script"); save_script.clicked.connect(self.save_python_script)
        head.addWidget(intro); head.addStretch(); head.addWidget(self.template_box); head.addWidget(run_selection); head.addWidget(run); head.addWidget(reset); head.addWidget(save_script)
        layout.addLayout(head)
        main = QSplitter()
        editor_side = QWidget(); editor_layout = QVBoxLayout(editor_side); editor_layout.setContentsMargins(0,0,0,0)
        self.editor = QPlainTextEdit(); self.editor.setFont(QFont("Consolas", 11)); self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.setPlainText("# Active visual table is df\nimport pandas as pd\nimport numpy as np\n\ndf['temperature'] = df['temperature'].fillna(df['temperature'].median())\ndf['efficiency'] = (df['output'] / df['pressure']).round(1)\ndf")
        self.console = QTextEdit(); self.console.setReadOnly(True); self.console.setFont(QFont("Consolas", 10))
        vertical = QSplitter(Qt.Vertical); vertical.addWidget(self.editor); vertical.addWidget(self.console); vertical.setSizes([520,180])
        editor_layout.addWidget(vertical)
        inspector = QWidget(); il = QVBoxLayout(inspector)
        il.addWidget(QLabel("VARIABLE EXPLORER"))
        self.variable_list = QListWidget(); il.addWidget(self.variable_list)
        il.addWidget(QLabel("EXECUTION HISTORY"))
        self.execution_history = QListWidget(); il.addWidget(self.execution_history)
        self.auto_return = QCheckBox("Return updated df to grid"); self.auto_return.setChecked(True); il.addWidget(self.auto_return)
        main.addWidget(editor_side); main.addWidget(inspector); main.setSizes([900,260])
        layout.addWidget(main,1)
        self.python_base_df = self.model.dataframe.copy()
        self.update_variable_explorer({"df": self.model.dataframe})
        return page

    def refresh_chart_columns(self):
        if not hasattr(self, "chart_x"):
            return
        df = self.model.dataframe
        current_x, current_y = self.chart_x.currentText(), self.chart_y.currentText()
        self.chart_x.clear(); self.chart_y.clear(); self.chart_group.clear(); self.chart_group.addItem("No grouping")
        self.chart_x.addItems([str(c) for c in df.columns])
        self.chart_y.addItems([str(c) for c in df.select_dtypes(include=np.number).columns])
        self.chart_group.addItems([str(c) for c in df.select_dtypes(exclude=np.number).columns])
        if current_x in list(df.columns): self.chart_x.setCurrentText(current_x)
        if current_y in list(df.columns): self.chart_y.setCurrentText(current_y)

    def create_chart(self):
        df = self.model.dataframe.copy(); kind = self.chart_type.currentText(); x = self.chart_x.currentText(); y = self.chart_y.currentText(); group = self.chart_group.currentText()
        self.figure.clear(); ax = self.figure.add_subplot(111)
        try:
            if kind == "Correlation heatmap":
                corr = df.select_dtypes(include=np.number).corr()
                image = ax.imshow(corr, cmap="coolwarm", vmin=-1, vmax=1)
                ax.set_xticks(range(len(corr.columns)), corr.columns, rotation=45, ha="right")
                ax.set_yticks(range(len(corr.columns)), corr.columns)
                self.figure.colorbar(image, ax=ax, label="Correlation")
            elif kind == "Histogram":
                ax.hist(df[y].dropna(), bins=min(20, max(5, int(len(df) ** .5))), color="#2563eb", edgecolor="white")
                ax.set_xlabel(y); ax.set_ylabel("Frequency")
            elif kind == "Box":
                if group != "No grouping":
                    labels=[]; values=[]
                    for name, part in df.groupby(group): labels.append(str(name)); values.append(part[y].dropna())
                    ax.boxplot(values, labels=labels); ax.set_xlabel(group)
                else: ax.boxplot(df[y].dropna(), labels=[y])
                ax.set_ylabel(y)
            else:
                plot_groups = [(None, df)] if group == "No grouping" else list(df.groupby(group))
                for name, part in plot_groups:
                    label = None if name is None else str(name)
                    if kind == "Line": ax.plot(part[x], part[y], marker="o", label=label)
                    elif kind == "Bar": ax.bar(part[x].astype(str), part[y], label=label, alpha=.8)
                    else: ax.scatter(part[x], part[y], label=label, alpha=.8)
                if group != "No grouping": ax.legend(title=group)
                ax.set_xlabel(x); ax.set_ylabel(y)
            ax.set_title(f"{kind}: {y}" if kind != "Correlation heatmap" else "Correlation heatmap")
            ax.grid(True, alpha=.2); self.canvas.draw(); self.tabs.setCurrentWidget(self.chart_page)
            self.workflow.addItem(f"{self.workflow.count()+1}. Create {kind.lower()}")
        except Exception as exc: QMessageBox.critical(self, "Chart failed", str(exc))

    def export_chart(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export chart", "chart.png", "PNG (*.png);;SVG (*.svg);;PDF (*.pdf)")
        if path: self.figure.savefig(path, dpi=180, bbox_inches="tight")

    def load_python_template(self, name):
        templates = {
            "Clean data": "# Clean missing values and duplicates\ndf = df.drop_duplicates().copy()\nfor col in df.select_dtypes(include='number'):\n    df[col] = df[col].fillna(df[col].median())\ndf",
            "Group analysis": "# Change the columns below\nresult = df.groupby('machine')['output'].agg(['count','mean','sum']).reset_index()\ndf = result\ndf",
            "Regression": "from sklearn.linear_model import LinearRegression\nfeatures = ['temperature','pressure','vibration']\ntarget = 'output'\ntrain = df[features + [target]].dropna()\nmodel = LinearRegression().fit(train[features], train[target])\ndf.loc[train.index, 'prediction'] = model.predict(train[features])\ndf",
            "Chart": "import matplotlib.pyplot as plt\ndf.plot(x='time', y='output', kind='line', marker='o')\nplt.tight_layout()\nplt.show()",
            "Machine learning": "from sklearn.ensemble import RandomForestRegressor\nfeatures = ['temperature','pressure','vibration']\ntarget = 'output'\ntrain = df[features + [target]].dropna()\nmodel = RandomForestRegressor(n_estimators=100, random_state=42)\nmodel.fit(train[features], train[target])\ndf.loc[train.index, 'ml_prediction'] = model.predict(train[features])\ndf"
        }
        if name in templates: self.editor.setPlainText(templates[name])

    def execute_python_code(self, code, label):
        namespace = {"df": self.model.dataframe.copy(), "pd": pd, "np": np}
        try:
            import matplotlib.pyplot as plt
            namespace["plt"] = plt
            exec(code, {"__builtins__": __builtins__}, namespace)
            result = namespace.get("df")
            if self.auto_return.isChecked() and not isinstance(result, pd.DataFrame): raise TypeError("Variable 'df' must remain a pandas DataFrame")
            if self.auto_return.isChecked():
                self.push_history(); self.model.set_dataframe(result); self.datasets[self.active_dataset] = result.copy(); self.workflow.addItem(f"{self.workflow.count()+1}. {label}")
            visible = {k:v for k,v in namespace.items() if not k.startswith("__")}
            self.update_variable_explorer(visible)
            preview = result.head(20).to_string() if isinstance(result, pd.DataFrame) else repr(result)
            self.console.setPlainText(f"Success\n\n{preview}")
            self.execution_history.insertItem(0, f"Success: {label}")
        except Exception:
            self.console.setPlainText(traceback.format_exc()); self.execution_history.insertItem(0, f"Failed: {label}")
        self.refresh_status()

    def run_selection(self):
        selected = self.editor.textCursor().selectedText().replace("\u2029", "\n")
        if not selected.strip(): QMessageBox.information(self, "Run selection", "Select Python code in the editor first."); return
        self.execute_python_code(selected, "Run Python selection")

    def reset_python_df(self):
        self.model.set_dataframe(self.python_base_df.copy()); self.console.setPlainText("df reset to the dataset captured when Python Lab opened."); self.refresh_status()

    def save_python_script(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Python script", "analysis.py", "Python (*.py)")
        if path: Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")

    def update_variable_explorer(self, values):
        if not hasattr(self, "variable_list"): return
        self.variable_list.clear()
        for name, value in values.items():
            if callable(value): continue
            if isinstance(value, pd.DataFrame): detail=f"DataFrame {value.shape[0]}x{value.shape[1]}"
            elif isinstance(value, pd.Series): detail=f"Series {len(value)}"
            elif isinstance(value, np.ndarray): detail=f"ndarray {value.shape}"
            else: detail=type(value).__name__
            self.variable_list.addItem(f"{name}  |  {detail}")

    def select_tool(self, name):
        handlers = {
            "KPI summary": self.kpi_summary,
            "Define KPI": self.define_kpi,
            "Save pipeline": self.save_pipeline_definition,
            "Run pipeline": self.run_saved_pipeline,
            "Dashboard designer": self.open_dashboard_designer,
            "SQL analysis": lambda: self.tabs.setCurrentWidget(self.sql_page),
            "Segment analysis": self.segment_analysis,
            "Trend analysis": self.trend_analysis,
            "Pareto analysis": self.pareto_analysis,
            "Cohort table": self.cohort_table,
            "Executive summary": self.executive_summary,
            "Missing values": self.fill_missing,
            "Duplicates": self.remove_duplicates,
            "Data types": self.infer_types,
            "Text cleanup": self.clean_text,
            "Data profile": self.show_profile,
            "Distributions": self.show_profile,
            "Correlation": self.show_correlation,
            "Outliers": self.detect_outliers,
            "Descriptive statistics": self.show_profile,
            "Feature engineering": self.feature_engineering,
            "Matrix operations": self.matrix_summary,
            "Join tables": self.join_tables,
            "Pivot / reshape": self.pivot_table,
            "Group analysis": self.group_analysis,
            "Time patterns": self.time_patterns,
            "Hypothesis tests": self.hypothesis_test,
            "Regression": self.regression,
            "Quality control": self.quality_control,
            "Train model": self.train_model_dialog,
            "Compare models": self.compare_models,
            "Forecast": self.forecast,
            "Cluster": self.cluster_data,
            "Anomaly detection": self.ml_anomaly,
            "Save model": self.save_trained_model,
            "Batch predict": self.batch_predict,
            "Signal processing": self.signal_processing,
            "Optimization": self.optimize_process,
            "Simulation": self.monte_carlo,
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
        self.datasets[self.active_dataset] = df.copy()
        self.pipeline_steps.append({"dataset": self.active_dataset, "step": step})
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
        self.execute_python_code(self.editor.toPlainText(), "Run Python Lab")

    def train_model_dialog(self):
        try:
            from sklearn.compose import ColumnTransformer
            from sklearn.pipeline import Pipeline
            from sklearn.preprocessing import OneHotEncoder, StandardScaler
            from sklearn.impute import SimpleImputer
            from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import r2_score, mean_absolute_error, accuracy_score, classification_report
            target = self.choose_column("Train model - target")
            if not target: return
            df = self.model.dataframe.copy(); X = df.drop(columns=[target]); y = df[target]
            valid = y.notna(); X=X.loc[valid]; y=y.loc[valid]
            numeric=list(X.select_dtypes(include=np.number).columns); categorical=[c for c in X.columns if c not in numeric]
            pre=ColumnTransformer([("num",Pipeline([("impute",SimpleImputer(strategy="median")),("scale",StandardScaler())]),numeric),("cat",Pipeline([("impute",SimpleImputer(strategy="most_frequent")),("encode",OneHotEncoder(handle_unknown="ignore"))]),categorical)])
            classification = y.dtype == object or y.nunique() <= max(10, int(len(y)*.1))
            estimator = RandomForestClassifier(n_estimators=200,random_state=42) if classification else RandomForestRegressor(n_estimators=200,random_state=42)
            model=Pipeline([("preprocess",pre),("model",estimator)])
            Xtr,Xte,ytr,yte=train_test_split(X,y,test_size=.25,random_state=42,stratify=y if classification and y.value_counts().min()>=2 else None)
            model.fit(Xtr,ytr); pred=model.predict(Xte)
            self.trained_models[target] = {"model": model, "features": list(X.columns), "target": target, "classification": classification}
            if classification: report=f"Accuracy: {accuracy_score(yte,pred):.4f}\n\n{classification_report(yte,pred,zero_division=0)}"
            else: report=f"R-squared: {r2_score(yte,pred):.4f}\nMAE: {mean_absolute_error(yte,pred):.4f}"
            df[f"ml_prediction_{target}"]=model.predict(df.drop(columns=[target]))
            self.apply_dataframe(df,f"Train Random Forest for {target}"); self.console.setPlainText(report); self.tabs.setCurrentWidget(self.python_page)
        except Exception as exc: QMessageBox.critical(self,"Model training failed",str(exc))

    def compare_models(self):
        try:
            from sklearn.model_selection import cross_val_score
            from sklearn.impute import SimpleImputer
            from sklearn.pipeline import make_pipeline
            from sklearn.preprocessing import StandardScaler
            from sklearn.linear_model import LinearRegression, Ridge
            from sklearn.ensemble import RandomForestRegressor, ExtraTreesRegressor
            target=self.choose_column("Compare models - numeric target",True)
            if not target:return
            df=self.model.dataframe.select_dtypes(include=np.number).dropna(subset=[target]); X=df.drop(columns=[target]); y=df[target]
            if len(df)<6 or X.shape[1]<1: raise ValueError("At least six rows and one numeric feature are required.")
            cv=min(5,max(2,len(df)//2)); models={"Linear Regression":LinearRegression(),"Ridge":Ridge(),"Random Forest":RandomForestRegressor(n_estimators=150,random_state=42),"Extra Trees":ExtraTreesRegressor(n_estimators=150,random_state=42)}
            rows=[]
            for name,est in models.items():
                pipe=make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),est); scores=cross_val_score(pipe,X,y,cv=cv,scoring="neg_mean_absolute_error"); rows.append({"model":name,"MAE_mean":-scores.mean(),"MAE_std":scores.std()})
            result=pd.DataFrame(rows).sort_values("MAE_mean").reset_index(drop=True); self.apply_dataframe(result,f"Compare regression models for {target}")
        except Exception as exc: QMessageBox.critical(self,"Model comparison failed",str(exc))

    def choose_column(self, title, numeric=False):
        df = self.model.dataframe
        columns = list(df.select_dtypes(include=np.number).columns) if numeric else list(df.columns)
        if not columns:
            QMessageBox.warning(self, title, "No compatible columns are available.")
            return None
        value, ok = QInputDialog.getItem(self, title, "Column", columns, 0, False)
        return value if ok else None

    def choose_two_numeric(self, title):
        first = self.choose_column(title + " - X", True)
        if not first:
            return None, None
        second = self.choose_column(title + " - Y", True)
        return (first, second) if second else (None, None)

    def join_tables(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select table to join", "", "Data files (*.csv *.xlsx *.json *.parquet)")
        if not path:
            return
        try:
            suffix = Path(path).suffix.lower()
            other = pd.read_csv(path) if suffix == ".csv" else pd.read_excel(path) if suffix in (".xlsx", ".xls") else pd.read_json(path) if suffix == ".json" else pd.read_parquet(path)
            common = [c for c in self.model.dataframe.columns if c in other.columns]
            if not common:
                raise ValueError("The datasets have no common column for a visual join.")
            key, ok = QInputDialog.getItem(self, "Join tables", "Join key", common, 0, False)
            if ok:
                result = self.model.dataframe.merge(other, on=key, how="left", suffixes=("", "_joined"))
                self.apply_dataframe(result, f"Left join on {key}")
        except Exception as exc:
            QMessageBox.critical(self, "Join failed", str(exc))

    def pivot_table(self):
        df = self.model.dataframe
        category = self.choose_column("Pivot - group column")
        value = self.choose_column("Pivot - value column", True)
        if category and value:
            result = df.pivot_table(index=category, values=value, aggfunc=["count", "mean", "sum"]).reset_index()
            result.columns = ["_".join(str(x) for x in c if x).strip("_") if isinstance(c, tuple) else str(c) for c in result.columns]
            self.apply_dataframe(result, f"Pivot {value} by {category}")

    def group_analysis(self):
        category = self.choose_column("Group analysis - category")
        value = self.choose_column("Group analysis - numeric measure", True)
        if category and value:
            result = self.model.dataframe.groupby(category, dropna=False)[value].agg(["count", "mean", "median", "min", "max", "sum"]).reset_index()
            self.apply_dataframe(result, f"Group {value} by {category}")

    def time_patterns(self):
        df = self.model.dataframe.copy()
        time_col = self.choose_column("Time patterns - date/time")
        value = self.choose_column("Time patterns - numeric measure", True)
        if time_col and value:
            df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
            result = df.dropna(subset=[time_col]).set_index(time_col)[value].resample("h").mean().interpolate().to_frame("mean").reset_index()
            result["rolling_3"] = result["mean"].rolling(3, min_periods=1).mean()
            self.apply_dataframe(result, f"Hourly time pattern for {value}")

    def hypothesis_test(self):
        try:
            from scipy import stats
            group = self.choose_column("Hypothesis test - group")
            value = self.choose_column("Hypothesis test - measure", True)
            if not group or not value:
                return
            levels = list(self.model.dataframe[group].dropna().unique())
            if len(levels) < 2:
                raise ValueError("The group column needs at least two values.")
            a = self.model.dataframe.loc[self.model.dataframe[group] == levels[0], value].dropna()
            b = self.model.dataframe.loc[self.model.dataframe[group] == levels[1], value].dropna()
            stat, p = stats.ttest_ind(a, b, equal_var=False)
            self.console.setPlainText(f"Welch two-sample t-test\nMeasure: {value}\nGroups: {levels[0]} vs {levels[1]}\nStatistic: {stat:.5f}\np-value: {p:.6g}\nDecision at 0.05: {'significant' if p < .05 else 'not significant'}")
            self.tabs.setCurrentWidget(self.python_page)
        except Exception as exc:
            QMessageBox.critical(self, "Test failed", str(exc))

    def regression(self):
        try:
            from scipy import stats
            x, y = self.choose_two_numeric("Linear regression")
            if not x:
                return
            clean = self.model.dataframe[[x, y]].dropna()
            fit = stats.linregress(clean[x], clean[y])
            result = self.model.dataframe.copy()
            result[f"predicted_{y}"] = fit.intercept + fit.slope * result[x]
            self.apply_dataframe(result, f"Regression {y} on {x}")
            self.console.setPlainText(f"Linear regression\n{y} = {fit.intercept:.4f} + {fit.slope:.4f} * {x}\nR-squared: {fit.rvalue**2:.4f}\np-value: {fit.pvalue:.6g}")
        except Exception as exc:
            QMessageBox.critical(self, "Regression failed", str(exc))

    def quality_control(self):
        col = self.choose_column("Quality control", True)
        if col:
            df = self.model.dataframe.copy()
            mean, std = df[col].mean(), df[col].std()
            df[f"{col}_zscore"] = (df[col] - mean) / std
            df[f"{col}_outside_3sigma"] = df[f"{col}_zscore"].abs() > 3
            self.apply_dataframe(df, f"3-sigma control limits for {col}")

    def feature_engineering(self):
        x, y = self.choose_two_numeric("Feature engineering")
        if x:
            df = self.model.dataframe.copy()
            df[f"{x}_plus_{y}"] = df[x] + df[y]
            df[f"{x}_div_{y}"] = df[x] / df[y].replace(0, np.nan)
            df[f"{x}_zscore"] = (df[x] - df[x].mean()) / df[x].std()
            self.apply_dataframe(df, f"Engineer features from {x} and {y}")

    def predict_model(self):
        try:
            from sklearn.ensemble import RandomForestRegressor
            target = self.choose_column("Prediction target", True)
            if not target:
                return
            df = self.model.dataframe.copy()
            features = [c for c in df.select_dtypes(include=np.number).columns if c != target]
            clean = df[features + [target]].dropna()
            if len(clean) < 4 or not features:
                raise ValueError("Prediction needs at least four complete rows and one numeric feature.")
            model = RandomForestRegressor(n_estimators=100, random_state=42).fit(clean[features], clean[target])
            valid = df[features].notna().all(axis=1)
            df[f"predicted_{target}"] = np.nan
            df.loc[valid, f"predicted_{target}"] = model.predict(df.loc[valid, features])
            self.apply_dataframe(df, f"Random Forest prediction for {target}")
        except Exception as exc:
            QMessageBox.critical(self, "Prediction failed", str(exc))

    def forecast(self):
        value = self.choose_column("Forecast measure", True)
        if value:
            series = self.model.dataframe[value].dropna().reset_index(drop=True)
            window = min(3, max(1, len(series)))
            prediction = float(series.tail(window).mean())
            df = self.model.dataframe.copy()
            row = {c: np.nan for c in df.columns}
            row[value] = prediction
            if "status" in row: row["status"] = "Forecast"
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
            self.apply_dataframe(df, f"Moving-average forecast for {value}")

    def cluster_data(self):
        try:
            from sklearn.cluster import KMeans
            df = self.model.dataframe.copy()
            cols = list(df.select_dtypes(include=np.number).columns)
            clean = df[cols].fillna(df[cols].median())
            if len(clean) < 3 or len(cols) < 2:
                raise ValueError("Clustering needs at least three rows and two numeric columns.")
            k = min(3, len(clean))
            df["cluster"] = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(clean)
            self.apply_dataframe(df, f"K-means clustering ({k} clusters)")
        except Exception as exc:
            QMessageBox.critical(self, "Clustering failed", str(exc))

    def ml_anomaly(self):
        try:
            from sklearn.ensemble import IsolationForest
            df = self.model.dataframe.copy()
            cols = list(df.select_dtypes(include=np.number).columns)
            clean = df[cols].fillna(df[cols].median())
            df["ml_anomaly"] = IsolationForest(contamination="auto", random_state=42).fit_predict(clean) == -1
            self.apply_dataframe(df, "Isolation Forest anomalies")
        except Exception as exc:
            QMessageBox.critical(self, "Anomaly detection failed", str(exc))

    def signal_processing(self):
        try:
            from scipy.signal import savgol_filter
            col = self.choose_column("Signal processing", True)
            if not col:
                return
            df = self.model.dataframe.copy()
            values = df[col].interpolate().bfill().ffill()
            window = min(len(values) if len(values) % 2 else len(values)-1, 5)
            if window < 3:
                raise ValueError("At least three readings are required.")
            df[f"{col}_smoothed"] = savgol_filter(values, window_length=window, polyorder=min(2, window-1))
            self.apply_dataframe(df, f"Savitzky-Golay smoothing for {col}")
        except Exception as exc:
            QMessageBox.critical(self, "Signal processing failed", str(exc))

    def optimize_process(self):
        try:
            from scipy.optimize import minimize_scalar
            x, y = self.choose_two_numeric("Optimize quadratic response")
            if not x:
                return
            clean = self.model.dataframe[[x, y]].dropna()
            coeff = np.polyfit(clean[x], clean[y], 2)
            low, high = clean[x].min(), clean[x].max()
            optimum = minimize_scalar(lambda z: -np.polyval(coeff, z), bounds=(low, high), method="bounded")
            self.console.setPlainText(f"Quadratic response optimization\nInput: {x}\nResponse: {y}\nBest {x}: {optimum.x:.5f}\nEstimated {y}: {-optimum.fun:.5f}\nObserved range: {low:.5f} to {high:.5f}")
            self.tabs.setCurrentWidget(self.python_page)
        except Exception as exc:
            QMessageBox.critical(self, "Optimization failed", str(exc))

    def monte_carlo(self):
        col = self.choose_column("Monte Carlo simulation", True)
        if col:
            values = self.model.dataframe[col].dropna()
            rng = np.random.default_rng(42)
            simulations = rng.normal(values.mean(), values.std(ddof=1), 10000)
            summary = pd.DataFrame({"metric": ["mean", "std", "p05", "p50", "p95"], "value": [simulations.mean(), simulations.std(), *np.quantile(simulations, [.05, .5, .95])]})
            self.apply_dataframe(summary, f"Monte Carlo simulation for {col}")

    def unique_dataset_name(self, base):
        name = base or "dataset"
        counter = 2
        while name in self.datasets:
            name = f"{base}_{counter}"; counter += 1
        return name

    def refresh_dataset_list(self, select=None):
        if not hasattr(self, "dataset_list"): return
        target = select or self.active_dataset
        self.dataset_list.blockSignals(True)
        self.dataset_list.clear()
        for name, df in self.datasets.items():
            item = QListWidgetItem(f"{name}  ({len(df):,} × {len(df.columns)})")
            item.setData(Qt.UserRole, name)
            self.dataset_list.addItem(item)
            if name == target: self.dataset_list.setCurrentItem(item)
        self.dataset_list.blockSignals(False)

    def switch_dataset(self):
        item = self.dataset_list.currentItem() if hasattr(self, "dataset_list") else None
        if not item: return
        name = item.data(Qt.UserRole)
        if name == self.active_dataset: return
        self.datasets[self.active_dataset] = self.model.dataframe.copy()
        self.active_dataset = name
        self.model.set_dataframe(self.datasets[name])
        self.python_base_df = self.model.dataframe.copy()
        self.refresh_sql_tables()
        self.refresh_status()

    def remove_dataset(self):
        if len(self.datasets) <= 1:
            QMessageBox.warning(self, "Remove dataset", "A project must retain at least one dataset."); return
        name = self.active_dataset
        answer = QMessageBox.question(self, "Remove dataset", f"Remove {name} from this project?")
        if answer != QMessageBox.Yes: return
        del self.datasets[name]
        self.active_dataset = next(iter(self.datasets))
        self.model.set_dataframe(self.datasets[self.active_dataset])
        self.refresh_dataset_list(); self.refresh_status()

    def save_project(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save project", "workbench_project.pdw", "Python Data Workbench (*.pdw)")
        if not path: return
        if not path.lower().endswith(".pdw"): path += ".pdw"
        try:
            self.datasets[self.active_dataset] = self.model.dataframe.copy()
            payload = {"version": "0.4", "active_dataset": self.active_dataset, "datasets": self.datasets, "pipeline_steps": self.pipeline_steps, "python_code": self.editor.toPlainText(), "trained_models": self.trained_models, "metric_definitions": self.metric_definitions, "dashboard_cards": self.dashboard_cards, "saved_pipelines": self.saved_pipelines, "kpi_definitions": self.kpi_definitions, "dashboard_filters": self.dashboard_filters}
            with open(path, "wb") as handle: pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
            self.statusBar().showMessage(f"Project saved: {path}", 5000)
        except Exception as exc: QMessageBox.critical(self, "Save project failed", str(exc))

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Python Data Workbench (*.pdw)")
        if not path: return
        try:
            with open(path, "rb") as handle: payload = pickle.load(handle)
            datasets = payload.get("datasets", {})
            if not datasets: raise ValueError("Project contains no datasets.")
            self.datasets = datasets
            self.active_dataset = payload.get("active_dataset") if payload.get("active_dataset") in datasets else next(iter(datasets))
            self.pipeline_steps = payload.get("pipeline_steps", [])
            self.trained_models = payload.get("trained_models", {})
            self.metric_definitions = payload.get("metric_definitions", [])
            self.dashboard_cards = payload.get("dashboard_cards", [])
            self.saved_pipelines = payload.get("saved_pipelines", {})
            self.kpi_definitions = payload.get("kpi_definitions", [])
            self.dashboard_filters = payload.get("dashboard_filters", {})
            self.editor.setPlainText(payload.get("python_code", self.editor.toPlainText()))
            self.model.set_dataframe(self.datasets[self.active_dataset])
            self.workflow.clear()
            for i, step in enumerate(self.pipeline_steps, 1): self.workflow.addItem(f"{i}. [{step.get('dataset','')}] {step.get('step','')}")
            self.refresh_dataset_list(); self.refresh_sql_tables(); self.refresh_status()
        except Exception as exc: QMessageBox.critical(self, "Open project failed", str(exc))

    def save_trained_model(self):
        if not self.trained_models:
            QMessageBox.information(self, "Save model", "Train a model first."); return
        target, ok = QInputDialog.getItem(self, "Save model", "Model target", list(self.trained_models), 0, False)
        if not ok: return
        path, _ = QFileDialog.getSaveFileName(self, "Save model", f"{target}_model.pkl", "Pickle model (*.pkl)")
        if path:
            with open(path, "wb") as handle: pickle.dump(self.trained_models[target], handle, protocol=pickle.HIGHEST_PROTOCOL)
            self.statusBar().showMessage(f"Model saved: {path}", 5000)

    def batch_predict(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose saved model", "", "Pickle model (*.pkl)")
        if not path: return
        try:
            with open(path, "rb") as handle: artifact = pickle.load(handle)
            features = artifact["features"]; target = artifact["target"]; model = artifact["model"]
            missing = [c for c in features if c not in self.model.dataframe.columns]
            if missing: raise ValueError("Active dataset is missing model features: " + ", ".join(missing))
            df = self.model.dataframe.copy()
            df[f"prediction_{target}"] = model.predict(df[features])
            self.apply_dataframe(df, f"Batch predict {target}")
        except Exception as exc: QMessageBox.critical(self, "Batch prediction failed", str(exc))

    def export_pipeline(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export workflow", "workflow.json", "JSON (*.json)")
        if path: Path(path).write_text(json.dumps(self.pipeline_steps, indent=2), encoding="utf-8")

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
            name = self.unique_dataset_name(Path(path).stem)
            self.datasets[name] = df.copy()
            self.active_dataset = name
            self.model.set_dataframe(df)
            self.refresh_dataset_list(select=name)
            self.refresh_sql_tables()
            self.workflow.addItem(f"{self.workflow.count()+1}. Import {name}")
            self.pipeline_steps.append({"dataset": name, "step": "Import data"})
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
            self.datasets[self.active_dataset] = self.model.dataframe.copy()
            self.workflow.addItem(f"{self.workflow.count()+1}. Undo")

    def refresh_status(self):
        df = self.model.dataframe
        missing = int(df.isna().sum().sum()) if not df.empty else 0
        cells = max(1, df.size)
        score = int(100 * (1 - missing / cells))
        self.dataset_name.setText(self.active_dataset)
        self.dataset_size.setText(f"{len(df):,} rows × {len(df.columns)} columns")
        if self.active_dataset in self.datasets:
            self.datasets[self.active_dataset] = df.copy()
        self.quality.setValue(score)
        self.quality_note.setText(f"{missing} missing cells · {df.duplicated().sum()} duplicate rows")
        self.refresh_chart_columns()
        if hasattr(self, "dashboard_summary"): self.refresh_dashboard()
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
