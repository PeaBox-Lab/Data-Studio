import sys
import traceback
import json
import pickle
import re
import shutil
from datetime import datetime
from pathlib import Path
from data_studio_core_v0_38 import (calculate_deployment_health, promotion_readiness, infer_validation_rules, validate_frame, workspace_health, safe_math_eval, validate_dataframe_expression)
from data_studio_state_v0_38 import migrate_project, validate_project
from data_studio_diagnostics_v0_38 import DiagnosticLog
from data_studio_jobs_v0_38 import execute_with_retry
from data_studio_tasks_v0_38 import TaskManager
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
  from matplotlib import pyplot as plt
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
      self.setWindowTitle("Data Studio v0.38 Deployment Observability & Rollback Studio")
      self.resize(1450, 900)
      self.current_project_path = None
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
      self.compute_engine = "Auto"
      self.spark_session = None
      self.engine_threshold_rows = 1000000
      self.relationships = []
      self.semantic_measures = []
      self.calculated_columns = []
      self.pipeline_definitions = {}
      self.active_pipeline = None
      self.pipeline_step_counter = 0
      self.lazy_sources = {}
      self.preview_limit = 10000
      self.materialization_limit = 500000
      self.dashboard_definitions = {}
      self.active_dashboard = None
      self.dashboard_tile_counter = 0
      self.dashboard_cross_filter = None
      self.analytics_pack_results = {}
      self.statistics_results = {}
      self.engineering_results = {}
      self.automl_experiments = {}
      self.active_experiment = None
      self.model_registry = {}
      self.prediction_history = []
      self.model_version_counter = 0
      self.monitoring_baselines = {}
      self.monitoring_runs = []
      self.monitoring_alerts = []
      self.drift_thresholds = {"warning": 0.10, "critical": 0.25}
      self.api_deployments = {}
      self.api_request_history = []
      self.scheduled_jobs = {}
      self.job_runs = []
      self.job_counter = 0
      self.recent_actions = []
      self.ui_compact_mode = False
      self.ui_preferences = {"theme":"Light", "density":"Comfortable", "show_guidance":True}
      self.smart_recommendations = []
      self.contract_test_suites = {}
      self.contract_test_runs = []
      self.cicd_profiles = {}
      self.cicd_runs = []
      self.release_gate_runs = []
      self.environments = {"DEV":{"protected":False,"current_release":"v0.38-dev","previous_release":None}, "TEST":{"protected":False,"current_release":None,"previous_release":None}, "PROD":{"protected":True,"current_release":None,"previous_release":None}}
      self.promotion_history = []
      self.validation_rules = {}
      self.data_contracts = {}
      self.lineage_graph = {"nodes":[],"edges":[]}
      self.cleaning_plans = {}
      self.deployment_thresholds = {"healthy":85,"degraded":65,"api_error_rate":0.05,"drift_alerts":2}
      self.deployment_observations = []
      self.rollback_history = []
      self.diagnostic_events = []
      self.diagnostics = DiagnosticLog(self.diagnostic_events)
      self.task_manager = TaskManager(max_workers=2)
      self.project_migration_history = []
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
      for text, callback, shortcut in [("Import", self.import_data, "Ctrl+I"), ("Export", self.export_data, "Ctrl+E"), ("Save project", self.save_project, "Ctrl+S"), ("Open project", self.open_project, "Ctrl+O"), ("Save snapshot", self.snapshot, None), ("Undo", self.undo, "Ctrl+Z")]:
          action = QAction(text, self)
          if shortcut: action.setShortcut(shortcut)
          action.triggered.connect(callback)
          toolbar.addAction(action)
      toolbar.addSeparator()
      python_action = QAction("Python Lab", self)
      python_action.triggered.connect(lambda: self.tabs.setCurrentWidget(self.python_page))
      toolbar.addAction(python_action)
      quick_action = QAction("Quick actions", self)
      quick_action.setShortcut("Ctrl+K")
      quick_action.triggered.connect(self.open_command_palette)
      toolbar.addAction(quick_action)
      focus_action = QAction("Focus mode", self)
      focus_action.setShortcut("F11")
      focus_action.triggered.connect(self.toggle_focus_mode)
      toolbar.addAction(focus_action)
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
      logo = QLabel("DS")
      logo.setObjectName("logo")
      title_box = QVBoxLayout()
      title = QLabel("Data Studio")
      title.setObjectName("appTitle")
      subtitle = QLabel("Import → Clean → Analyze → Visualize → Model → Automate")
      subtitle.setObjectName("muted")
      title_box.addWidget(title)
      title_box.addWidget(subtitle)
      hl.addWidget(logo)
      hl.addLayout(title_box)
      hl.addStretch()
      hl.addWidget(QLabel("Engine"))
      self.engine_selector = QComboBox(); self.engine_selector.addItems(["Auto", "pandas", "DuckDB", "PySpark"]); self.engine_selector.currentTextChanged.connect(self.change_engine)
      hl.addWidget(self.engine_selector)
      self.engine_badge = QLabel("Auto: pandas"); self.engine_badge.setObjectName("muted"); hl.addWidget(self.engine_badge)
      self.status_label = QLabel()
      hl.addWidget(self.status_label)
      root.addWidget(header)
      splitter = QSplitter()
      splitter.addWidget(self.build_project_panel())
      splitter.addWidget(self.build_workspace())
      splitter.setSizes([270, 1180])
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
      hint = QLabel("Home covers common tasks. Press Ctrl+K for Quick Actions. Advanced is for SQL, Spark and Python.")
      hint.setWordWrap(True); hint.setObjectName("muted"); layout.addWidget(hint)
      layout.addWidget(QLabel("Workflow"))
      self.workflow = QListWidget()
      self.workflow.addItems(["1. Import sample data", "2. Profile dataset"])
      layout.addWidget(self.workflow, 1)
      return panel
  def build_workspace(self):
      self.tabs = QTabWidget()
      self.tabs.setDocumentMode(True)
      self.tabs.setUsesScrollButtons(True)
      definitions = {
          "Home": [("Getting started", "Choose a guided path"), ("Open data", "Import CSV, Excel, JSON or Parquet"), ("Recent actions", "Resume recent work"), ("Workspace overview", "Project health and next steps"), ("Customize workspace", "Theme, density and guidance"), ("Quick actions", "Search common actions"), ("Smart recommendations", "Context-aware next best actions"), ("Workspace health", "Audit project readiness and recovery risks")],
          "Data": [("Missing values", "Clean missing values"), ("Duplicates", "Remove duplicates"), ("Data types", "Infer and convert types"), ("Join tables", "Merge datasets"), ("Pivot / reshape", "Pivot and aggregate"), ("Attach large file", "DuckDB CSV or Parquet")],
          "Transform": [("New pipeline", "Create workflow"), ("Add cleaning step", "Reusable cleaning"), ("Add filter step", "Saved row filters"), ("Add calculated step", "Derived columns"), ("Pipeline editor", "Preview and execute"), ("Import / export pipeline", "Share pipeline JSON")],
          "Analyze": [("KPI summary", "Executive KPIs"), ("Segment analysis", "Compare segments"), ("Trend analysis", "Growth and moving averages"), ("Guided test", "Guided statistics"), ("Monte Carlo Pro", "Simulation and risk"), ("Engineering report", "Scientific results")],
          "Visualize": [("New dashboard", "Create dashboard"), ("Add KPI tile", "Metric card"), ("Add chart tile", "Chart visual"), ("Add table tile", "Detail table"), ("Dashboard layout", "Arrange visuals"), ("Dashboard package", "Import/export dashboard")],
          "Model": [("Relationship manager", "Dataset relationships"), ("Semantic measure", "Reusable measures"), ("New AutoML experiment", "Guided machine learning"), ("Run model leaderboard", "Compare models"), ("Model registry", "Model versions"), ("Create baseline", "Monitoring baseline")],
          "Automate": [("Create job", "Schedule work"), ("Run job now", "Execute job"), ("Job manager", "Manage schedules"), ("Create API deployment", "REST deployment"), ("Export API package", "FastAPI package"), ("Deployment report", "Review endpoints")],
          "Govern": [("Data quality profile", "Profile quality and structural risks"), ("Validation rules", "Infer and execute data rules"), ("Data contracts", "Create governed schema contracts"), ("Contract test automation", "Run repeatable contract suites"), ("Release gate", "Evaluate release readiness"), ("Environment promotion", "Promote DEV to TEST to PROD"), ("Deployment observability", "Score environment health"), ("Rollback center", "Review and execute rollback"), ("Diagnostics center", "Review structured application events")],
          "Advanced": [("SQL analysis", "DuckDB SQL"), ("Engine status", "Compute engine"), ("Large-data SQL", "Lazy-source SQL"), ("Convert to Spark", "PySpark"), ("Explain plan", "Execution plan"), ("Python Lab", "Full Python access")],
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
  def _latest_pass(self, records):
      return bool(records and str(records[-1].get("status", "")).upper() in {"PASS", "PASSED", "SUCCESS"})
  def _project_health_payload(self):
      return {"datasets":self.datasets,"pipeline_definitions":self.pipeline_definitions,"contract_test_runs":self.contract_test_runs,"cicd_runs":self.cicd_runs,"release_gate_runs":self.release_gate_runs,"current_project_path":self.current_project_path}
  def show_smart_recommendations(self):
      recommendations=[]; df=self.model.dataframe
      if df.isna().sum().sum(): recommendations.append("Review missing values or generate a cleaning plan.")
      if int(df.duplicated().sum()): recommendations.append("Remove duplicate rows before analysis or modeling.")
      if not self.pipeline_definitions: recommendations.append("Create a reusable pipeline so transformations are reproducible.")
      if not self.contract_test_runs: recommendations.append("Generate and run a data contract before release.")
      if not self.cicd_runs: recommendations.append("Run the CI/CD readiness assessment.")
      if not self.deployment_observations: recommendations.append("Record a deployment-health snapshot for DEV, TEST, or PROD.")
      if not recommendations: recommendations.append("Workspace foundations are complete; review the release gate and promote to the next environment.")
      self.smart_recommendations=recommendations
      self.console.setPlainText("SMART RECOMMENDATIONS\n"+"="*80+"\n"+"\n".join(f"{i}. {x}" for i,x in enumerate(recommendations,1))); self.tabs.setCurrentWidget(self.python_page)
  def show_workspace_health(self):
      result=workspace_health(self._project_health_payload()); lines=["WORKSPACE HEALTH","="*80,f"Score: {result['score']}/100",f"Status: {result['status']}",""]
      lines.extend(f"- {x['finding']} (-{x['deduction']})" for x in result["findings"]); lines.append("\nNo deductions detected." if not result["findings"] else "\nResolve the listed findings before PROD promotion.")
      self.console.setPlainText("\n".join(lines)); self.tabs.setCurrentWidget(self.python_page)
  def show_data_quality_profile(self):
      df=self.model.dataframe; rows=max(1,len(df)); profile=[]
      for column in df.columns:
          series=df[column]; profile.append({"column":str(column),"dtype":str(series.dtype),"missing":int(series.isna().sum()),"missing_pct":round(float(series.isna().mean()*100),2),"unique":int(series.nunique(dropna=True)),"constant":bool(series.nunique(dropna=True)<=1),"high_cardinality":bool(series.nunique(dropna=True)>max(50,rows*.8))})
      completeness=round(100*(1-df.isna().sum().sum()/max(1,df.size)),2); quality=max(0,round(completeness-min(20,df.duplicated().sum()/rows*100),2))
      self.console.setPlainText(f"DATA QUALITY PROFILE\n{'='*80}\nDataset: {self.active_dataset}\nRows: {len(df):,}\nColumns: {len(df.columns)}\nQuality score: {quality:.2f}\nDuplicate rows: {int(df.duplicated().sum())}\n\n"+pd.DataFrame(profile).to_string(index=False)); self.tabs.setCurrentWidget(self.python_page)
  def show_cleaning_plan(self):
      df=self.model.dataframe; plan=[]
      duplicates=int(df.duplicated().sum())
      if duplicates: plan.append({"action":"remove_duplicates","scope":"all columns","reason":f"{duplicates} duplicate rows"})
      for column in df.columns:
          missing=int(df[column].isna().sum())
          if missing:
              strategy="median" if pd.api.types.is_numeric_dtype(df[column]) else "mode"
              plan.append({"action":"fill_missing","scope":str(column),"strategy":strategy,"reason":f"{missing} missing values"})
      self.cleaning_plans[self.active_dataset]=plan
      text="No remediation is currently required." if not plan else pd.DataFrame(plan).to_string(index=False)
      answer=QMessageBox.question(self,"Cleaning plan",f"Reviewable plan for {self.active_dataset}:\n\n{text}\n\nApply this conservative plan now?")
      if answer==QMessageBox.Yes and plan:
          result=df.copy()
          if duplicates: result=result.drop_duplicates().reset_index(drop=True)
          for item in plan:
              if item["action"]!="fill_missing": continue
              column=item["scope"]
              if item["strategy"]=="median": result[column]=result[column].fillna(result[column].median())
              else:
                  mode=result[column].mode(dropna=True)
                  if not mode.empty: result[column]=result[column].fillna(mode.iloc[0])
          self.apply_dataframe(result,"Apply reviewed cleaning plan")
  def show_validation_rules(self):
      rules=infer_validation_rules(self.model.dataframe); self.validation_rules[self.active_dataset]=rules
      results=validate_frame(self.model.dataframe,rules)
      self.console.setPlainText(f"VALIDATION RULES\n{'='*80}\nDataset: {self.active_dataset}\nRules: {len(rules)}\nFailures: {sum(x['status']=='FAIL' for x in results)}\n\n"+pd.DataFrame(results).to_string(index=False)); self.tabs.setCurrentWidget(self.python_page)
  def show_data_contracts(self):
      rules=self.validation_rules.get(self.active_dataset) or infer_validation_rules(self.model.dataframe); self.validation_rules[self.active_dataset]=rules
      contract={"name":f"{self.active_dataset}_contract","version":1,"owner":"Unassigned","dataset":self.active_dataset,"columns":[{"name":str(c),"dtype":str(self.model.dataframe[c].dtype),"nullable":bool(self.model.dataframe[c].isna().any())} for c in self.model.dataframe.columns],"rules":rules,"created_utc":pd.Timestamp.utcnow().isoformat()}
      self.data_contracts[self.active_dataset]=contract; self.console.setPlainText("DATA CONTRACT\n"+"="*80+"\n"+json.dumps(contract,indent=2)); self.tabs.setCurrentWidget(self.python_page)
  def _lineage_edges(self):
      edges=[]
      for name,pipe in self.pipeline_definitions.items():
          source=pipe.get("source_dataset") or pipe.get("dataset") or self.active_dataset; edges.append((f"dataset:{source}",f"pipeline:{name}"))
      for relation in self.relationships:
          left=relation.get("left_dataset") or relation.get("left"); right=relation.get("right_dataset") or relation.get("right")
          if left and right: edges.append((f"dataset:{left}",f"dataset:{right}"))
      for job_id,job in self.scheduled_jobs.items():
          edges.append((f"asset:{job.get('config',{}).get('dataset') or job.get('config',{}).get('pipeline') or self.active_dataset}",f"job:{job_id}"))
      return edges
  def show_data_lineage(self):
      edges=self._lineage_edges(); nodes=sorted({x for edge in edges for x in edge}|{f"dataset:{x}" for x in self.datasets})
      self.lineage_graph={"nodes":nodes,"edges":[{"source":a,"target":b} for a,b in edges]}; text="\n".join(f"{a} -> {b}" for a,b in edges) or "No derived lineage relationships yet."
      self.console.setPlainText(f"DATA LINEAGE\n{'='*80}\nAssets: {len(nodes)}\nRelationships: {len(edges)}\n\n{text}"); self.tabs.setCurrentWidget(self.python_page)
  def show_lineage_visualization(self):
      edges=self._lineage_edges(); nodes=sorted({x for edge in edges for x in edge}|{f"dataset:{x}" for x in self.datasets})
      self.figure.clear(); ax=self.figure.add_subplot(111); ax.axis("off")
      if not nodes: ax.text(.5,.5,"No lineage assets",ha="center",va="center")
      else:
          angles=np.linspace(0,2*np.pi,len(nodes),endpoint=False); pos={n:(np.cos(a),np.sin(a)) for n,a in zip(nodes,angles)}
          for node,(x,y) in pos.items(): ax.scatter([x],[y],s=900,color="#dbeafe",edgecolor="#2563eb"); ax.text(x,y,node,ha="center",va="center",fontsize=7,wrap=True)
          for source,target in edges:
              if source in pos and target in pos: ax.annotate("",xy=pos[target],xytext=pos[source],arrowprops={"arrowstyle":"->","color":"#64748b"})
      ax.set_title("Data Studio lineage"); self.canvas.draw(); self.tabs.setCurrentWidget(self.chart_page)
  def export_lineage_dialog(self):
      path,_=QFileDialog.getSaveFileName(self,"Export lineage","lineage_v0.38.json","JSON (*.json);;PNG (*.png);;SVG (*.svg)")
      if not path:return
      if Path(path).suffix.lower() in {".png",".svg"}: self.show_lineage_visualization(); self.figure.savefig(path,bbox_inches="tight")
      else:
          self.show_data_lineage(); Path(path).write_text(json.dumps(self.lineage_graph,indent=2),encoding="utf-8")
      self.statusBar().showMessage(f"Lineage exported: {path}",5000)
  def show_contract_test_automation(self):
      rules=self.validation_rules.get(self.active_dataset) or infer_validation_rules(self.model.dataframe); suite={"name":f"{self.active_dataset}_contract_suite","dataset":self.active_dataset,"rules":rules}
      self.contract_test_suites[suite["name"]]=suite; results=validate_frame(self.model.dataframe,rules); failed=sum(x["status"]=="FAIL" for x in results); record={"suite":suite["name"],"dataset":self.active_dataset,"status":"PASS" if failed==0 else "FAIL","rules":len(results),"failed":failed,"run_utc":pd.Timestamp.utcnow().isoformat(),"results":results}; self.contract_test_runs.append(record)
      self.console.setPlainText("CONTRACT TEST RUN\n"+"="*80+"\n"+json.dumps(record,indent=2)); self.tabs.setCurrentWidget(self.python_page)
  def show_cicd_integration(self):
      checks={"project_saved":bool(self.current_project_path),"source_compiles":True,"datasets_available":bool(self.datasets),"contract_tests_passed":self._latest_pass(self.contract_test_runs)}; status="PASS" if all(checks.values()) else "FAIL"; record={"profile":"Data Studio local quality gate","status":status,"checks":checks,"run_utc":pd.Timestamp.utcnow().isoformat()}; self.cicd_profiles["default"]={"python":["3.11","3.12"],"provider":"GitHub Actions / Azure Pipelines"}; self.cicd_runs.append(record)
      self.console.setPlainText("CI/CD READINESS\n"+"="*80+"\n"+json.dumps(record,indent=2)); self.tabs.setCurrentWidget(self.python_page)
  def export_cicd_bundle_dialog(self):
      folder=QFileDialog.getExistingDirectory(self,"Export CI/CD bundle")
      if not folder:return
      out=Path(folder); (out/"github-actions-data-studio.yml").write_text("name: Data Studio CI\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    strategy:\n      matrix:\n        python: ['3.11', '3.12']\n    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-python@v5\n        with: {python-version: '${{ matrix.python }}'}\n      - run: pip install -r requirements_v0.38.txt\n      - run: python data_studio_ci_v0.38.py --project .\n",encoding="utf-8"); (out/"azure-pipelines-data-studio.yml").write_text("trigger:\n- main\npool:\n  vmImage: ubuntu-latest\nsteps:\n- task: UsePythonVersion@0\n  inputs: {versionSpec: '3.11'}\n- script: pip install -r requirements_v0.38.txt\n- script: python data_studio_ci_v0.38.py --project .\n",encoding="utf-8"); QMessageBox.information(self,"CI/CD export",f"Templates exported to {out}")
  def show_release_gate(self):
      checks={"project_saved":bool(self.current_project_path),"contract_tests":self._latest_pass(self.contract_test_runs),"ci":self._latest_pass(self.cicd_runs),"datasets":bool(self.datasets),"pipelines":bool(self.pipeline_definitions)}; status="PASS" if all(checks.values()) else "FAIL"; record={"status":status,"checks":checks,"run_utc":pd.Timestamp.utcnow().isoformat()}; self.release_gate_runs.append(record)
      self.console.setPlainText("RELEASE GATE\n"+"="*80+"\n"+json.dumps(record,indent=2)); self.tabs.setCurrentWidget(self.python_page)
  def export_release_automation_dialog(self):
      folder=QFileDialog.getExistingDirectory(self,"Export release automation")
      if not folder:return
      Path(folder,"release-data-studio.yml").write_text("name: Data Studio Release\non:\n  push:\n    tags: ['v*']\njobs:\n  package:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - run: python data_studio_ci_v0.38.py --project .\n      - uses: actions/upload-artifact@v4\n        with: {name: data-studio-release, path: .}\n",encoding="utf-8"); QMessageBox.information(self,"Release automation",f"Release workflow exported to {folder}")
  def show_environment_promotion(self):
      source,ok=QInputDialog.getItem(self,"Environment promotion","Source environment",["DEV","TEST"],0,False)
      if not ok:return
      targets=["TEST"] if source=="DEV" else ["PROD"]; target,ok=QInputDialog.getItem(self,"Environment promotion","Target environment",targets,0,False)
      if not ok:return
      release,ok=QInputDialog.getText(self,"Environment promotion","Release identifier",text="v0.38")
      if not ok or not release.strip():return
      ready,blockers=promotion_readiness(target,self._latest_pass(self.cicd_runs),self._latest_pass(self.contract_test_runs),self._latest_pass(self.release_gate_runs))
      record={"source":source,"target":target,"release":release.strip(),"status":"PROMOTED" if ready else "BLOCKED","blockers":blockers,"timestamp_utc":pd.Timestamp.utcnow().isoformat()}; self.promotion_history.append(record)
      if not ready: QMessageBox.warning(self,"Promotion blocked","\n".join(blockers)); return
      env=self.environments.setdefault(target,{"protected":target=="PROD"}); env["previous_release"]=env.get("current_release"); env["current_release"]=release.strip(); env["promoted_utc"]=record["timestamp_utc"]; QMessageBox.information(self,"Promotion complete",f"{release.strip()} promoted from {source} to {target}.")
  def export_deployment_automation_dialog(self):
      folder=QFileDialog.getExistingDirectory(self,"Export deployment automation")
      if not folder:return
      Path(folder,"deploy-data-studio.yml").write_text("name: Data Studio Deploy\non: workflow_dispatch\njobs:\n  deploy-test:\n    environment: TEST\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - run: python data_studio_ci_v0.38.py --project .\n  deploy-prod:\n    needs: deploy-test\n    environment: PROD\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo 'Deploy approved Data Studio release'\n",encoding="utf-8"); QMessageBox.information(self,"Deployment automation",f"Deployment workflow exported to {folder}")
  def record_deployment_observation(self):
      environment,ok=QInputDialog.getItem(self,"Deployment observation","Environment",["DEV","TEST","PROD"],1,False)
      if not ok:return
      error_pct,ok=QInputDialog.getDouble(self,"Deployment observation","API error rate (%)",0.0,0.0,100.0,2)
      if not ok:return
      drift,ok=QInputDialog.getInt(self,"Deployment observation","Drift alerts",0,0,10000,1)
      if not ok:return
      critical,ok=QInputDialog.getInt(self,"Deployment observation","Critical drift alerts",0,0,10000,1)
      if not ok:return
      failed_jobs,ok=QInputDialog.getInt(self,"Deployment observation","Failed jobs",0,0,10000,1)
      if not ok:return
      observation={"environment":environment,"release":self.environments.get(environment,{}).get("current_release"),"api_error_rate":error_pct/100.0,"drift_alerts":drift,"critical_drift_alerts":critical,"failed_jobs":failed_jobs,"ci_passed":self._latest_pass(self.cicd_runs),"contracts_passed":self._latest_pass(self.contract_test_runs),"observed_utc":pd.Timestamp.utcnow().isoformat()}; health=calculate_deployment_health(observation,self.deployment_thresholds).to_dict(); observation["health"]=health; self.deployment_observations.append(observation); return observation
  def show_deployment_observability(self):
      answer=QMessageBox.question(self,"Deployment Observability","Record a new deployment-health snapshot?")
      if answer==QMessageBox.Yes:self.record_deployment_observation()
      rows=[]
      for item in self.deployment_observations[-100:]:
          health=item.get("health",{}); rows.append({"observed_utc":item.get("observed_utc"),"environment":item.get("environment"),"release":item.get("release"),"score":health.get("score"),"status":health.get("status"),"api_error_rate":item.get("api_error_rate"),"drift_alerts":item.get("drift_alerts"),"failed_jobs":item.get("failed_jobs"),"rollback":health.get("rollback_recommended")})
      text="No deployment observations recorded." if not rows else pd.DataFrame(rows).to_string(index=False)
      self.console.setPlainText("DEPLOYMENT OBSERVABILITY\n"+"="*100+"\nThresholds: HEALTHY >= 85; DEGRADED 65-84; UNHEALTHY < 65; rollback review at API errors >= 5% or drift alerts >= 2.\n\n"+text); self.tabs.setCurrentWidget(self.python_page)
  def show_rollback_center(self):
      candidates=[x for x in self.deployment_observations if x.get("health",{}).get("rollback_recommended")]
      lines=["ROLLBACK CENTER","="*100,f"Recommendations: {len(candidates)}",f"Rollback history: {len(self.rollback_history)}",""]
      for item in candidates[-20:]: lines.append(f"{item.get('environment')} | {item.get('release')} | score={item.get('health',{}).get('score')} | "+"; ".join(item.get("health",{}).get("reasons",[])))
      if self.rollback_history: lines.extend(["","ROLLBACK HISTORY",pd.DataFrame(self.rollback_history[-50:]).to_string(index=False)])
      self.console.setPlainText("\n".join(lines)); self.tabs.setCurrentWidget(self.python_page)
      if candidates and QMessageBox.question(self,"Rollback Center","Execute a governance-state rollback for a recommended environment?")==QMessageBox.Yes:self.execute_rollback()
  def execute_rollback(self):
      options=[name for name,state in self.environments.items() if state.get("previous_release")]
      if not options: QMessageBox.information(self,"Rollback","No environment has a previous release recorded."); return
      environment,ok=QInputDialog.getItem(self,"Rollback","Environment",options,0,False)
      if not ok:return
      state=self.environments[environment]; current=state.get("current_release"); previous=state.get("previous_release")
      if QMessageBox.question(self,"Confirm rollback",f"Rollback {environment} from {current} to {previous}?")!=QMessageBox.Yes:return
      state["current_release"]=previous; state["previous_release"]=current; record={"environment":environment,"from_release":current,"to_release":previous,"status":"COMPLETED","timestamp_utc":pd.Timestamp.utcnow().isoformat()}; self.rollback_history.append(record); QMessageBox.information(self,"Rollback complete",f"{environment} now points to {previous}. Deployment execution remains controlled by the exported environment workflow.")
  def show_diagnostics_center(self):
      rows=self.diagnostic_events[-500:]
      text="No structured diagnostic events recorded." if not rows else pd.DataFrame([{"timestamp_utc":x.get("timestamp_utc"),"level":x.get("level"),"component":x.get("component"),"action":x.get("action"),"message":x.get("message"),"correlation_id":x.get("correlation_id")} for x in rows]).to_string(index=False)
      self.console.setPlainText("DIAGNOSTICS CENTER\n"+"="*100+f"\nEvents: {len(self.diagnostic_events)}\n\n"+text); self.tabs.setCurrentWidget(self.python_page)
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
  def jobs_create(self):
      kinds = ["Pipeline", "Model batch score", "Dataset export", "Monitoring drift check"]
      kind, ok = QInputDialog.getItem(self, "Create job", "Job type", kinds, 0, False)
      if not ok: return
      name, ok = QInputDialog.getText(self, "Create job", "Job name", text=f"{kind} job")
      if not ok or not name.strip(): return
      schedule, ok = QInputDialog.getItem(self, "Create job", "Schedule", ["Manual", "Hourly", "Daily", "Weekly", "Monthly"], 1, False)
      if not ok: return
      config = {}
      if kind == "Pipeline":
          if not self.pipeline_definitions: QMessageBox.information(self, "Job", "Create a pipeline first."); return
          value, ok = QInputDialog.getItem(self, "Pipeline job", "Pipeline", list(self.pipeline_definitions), 0, False)
          if not ok: return
          config = {"pipeline": value}
      elif kind == "Model batch score":
          if not self.api_deployments: QMessageBox.information(self, "Job", "Create an API deployment first."); return
          value, ok = QInputDialog.getItem(self, "Scoring job", "Deployment", list(self.api_deployments), 0, False)
          if not ok: return
          config = {"deployment": value, "dataset": self.active_dataset}
      elif kind == "Dataset export":
          fmt, ok = QInputDialog.getItem(self, "Export job", "Format", ["CSV", "Excel", "Parquet"], 0, False)
          if not ok: return
          extension = {"CSV":"csv", "Excel":"xlsx", "Parquet":"parquet"}[fmt]
          path, ok = QInputDialog.getText(self, "Export job", "Output path", text=str(Path.home() / f"{self.active_dataset}_scheduled.{extension}"))
          if not ok: return
          config = {"dataset": self.active_dataset, "format": fmt, "path": path}
      else:
          if not self.monitoring_baselines: QMessageBox.information(self, "Job", "Create a baseline first."); return
          value, ok = QInputDialog.getItem(self, "Drift job", "Baseline", list(self.monitoring_baselines), 0, False)
          if not ok: return
          config = {"baseline": value, "dataset": self.active_dataset}
      retries, ok = QInputDialog.getInt(self, "Reliability policy", "Retry attempts after failure", 0, 0, 10, 1)
      if not ok: return
      dependency_text = ""
      if self.scheduled_jobs:
          dependency_text, ok = QInputDialog.getText(
              self,
              "Job dependencies",
              "Run only after these job IDs succeed (comma-separated; optional)",
              text="",
          )
          if not ok: return
      dependencies = [item.strip() for item in dependency_text.split(",") if item.strip()]
      invalid = [item for item in dependencies if item not in self.scheduled_jobs]
      if invalid:
          QMessageBox.warning(self, "Job dependencies", "Unknown job IDs: " + ", ".join(invalid))
          return
      self.job_counter += 1
      job_id = f"job_{self.job_counter:04d}"
      self.scheduled_jobs[job_id] = {
          "id":job_id, "name":name.strip(), "type":kind, "schedule":schedule,
          "enabled":True, "config":config, "run_count":0, "last_status":"Never run",
          "created_utc":pd.Timestamp.utcnow().isoformat(), "next_run_utc":None,
          "depends_on":dependencies, "max_retries":retries, "retry_delay_seconds":5,
      }
      QMessageBox.information(
          self,
          "Job created",
          f"Created {job_id} with {schedule} schedule.\nRetries: {retries}\n"
          f"Dependencies: {', '.join(dependencies) if dependencies else 'None'}",
      )
  def jobs_execute(self, job_id):
      import time
      job = self.scheduled_jobs[job_id]; started = time.time(); status = "success"; error = None; details = {}
      correlation=self.diagnostics.info("jobs","execute","Job execution started",job_id=job_id,job_type=job.get("type"))["correlation_id"]
      try:
          if job["type"] == "Pipeline":
              result, audit = self.execute_pipeline(job["config"]["pipeline"])
              target = self.unique_dataset_name(job["config"]["pipeline"] + "_job_output")
              self.datasets[target] = result; details = {"output_dataset":target, "rows":len(result), "steps":len(audit)}
          elif job["type"] == "Model batch score":
              deployment = self.api_deployments[job["config"]["deployment"]]; artifact = self.trained_models.get(deployment["model_key"])
              if artifact is None: raise ValueError("Model artifact unavailable")
              frame = self.datasets[job["config"]["dataset"]].copy(); missing = [c for c in artifact["features"] if c not in frame]
              if missing: raise ValueError("Missing features: " + ", ".join(missing))
              frame[f"prediction_{artifact['target']}"] = artifact["model"].predict(frame[artifact["features"]])
              target = self.unique_dataset_name(job["config"]["dataset"] + "_scored"); self.datasets[target] = frame; details = {"output_dataset":target, "rows":len(frame)}
          elif job["type"] == "Dataset export":
              cfg = job["config"]; frame = self.datasets[cfg["dataset"]]; Path(cfg["path"]).parent.mkdir(parents=True, exist_ok=True)
              if cfg["format"] == "CSV": frame.to_csv(cfg["path"], index=False)
              elif cfg["format"] == "Excel": frame.to_excel(cfg["path"], index=False)
              else: frame.to_parquet(cfg["path"], index=False)
              details = {"path":cfg["path"], "rows":len(frame)}
          else:
              cfg = job["config"]; base = self.monitoring_baselines[cfg["baseline"]]; frame = self.datasets[cfg["dataset"]]; summary = []
              for column in base["features"]:
                  if column not in frame.columns: summary.append({"feature":column, "psi":None, "status":"CRITICAL"}); continue
                  profile = base["profiles"][column]
                  current = self.monitor_numeric_profile(frame[column], profile["edges"]) if profile["type"] == "numeric" else self.monitor_categorical_profile(frame[column], profile["categories"])
                  psi = self.monitor_psi(profile["proportions"], current["proportions"])
                  state = "CRITICAL" if psi >= self.drift_thresholds["critical"] else "WARNING" if psi >= self.drift_thresholds["warning"] else "STABLE"
                  summary.append({"feature":column, "psi":psi, "status":state})
              critical = sum(x["status"] == "CRITICAL" for x in summary); warning = sum(x["status"] == "WARNING" for x in summary)
              self.monitoring_runs.append({"baseline":cfg["baseline"], "dataset":cfg["dataset"], "rows":len(frame), "summary":summary, "critical":critical, "warning":warning}); details = {"critical":critical, "warning":warning}
      except Exception as exc:
          status = "failed"; error = str(exc)
      record = {"job_id":job_id, "job_name":job["name"], "type":job["type"], "status":status, "duration_seconds":round(time.time()-started, 3), "error":error, "details":details}
      self.job_runs.append(record); job["run_count"] += 1; job["last_status"] = status; job["last_error"] = error
      (self.diagnostics.info if status=="success" else self.diagnostics.error)("jobs","execute","Job execution finished",correlation_id=correlation,job_id=job_id,status=status,error=error,duration_seconds=record["duration_seconds"])
      self.refresh_dataset_list(); return record
  def jobs_run_now(self):
      options = [(k,v) for k,v in self.scheduled_jobs.items() if v.get("enabled", True)]
      if not options: QMessageBox.information(self, "Run job", "No enabled jobs exist."); return
      labels = [f"{k} | {v['name']} | {v['type']}" for k,v in options]
      choice, ok = QInputDialog.getItem(self, "Run job now", "Job", labels, 0, False)
      if ok:
          result = self.jobs_execute(options[labels.index(choice)][0]); QMessageBox.information(self, "Job finished", f"Status: {result['status']}\nDuration: {result['duration_seconds']} sec" + (f"\nError: {result['error']}" if result['error'] else ""))
  def jobs_manager(self):
      if not self.scheduled_jobs: QMessageBox.information(self, "Job manager", "No jobs exist."); return
      dialog = QDialog(self); dialog.setWindowTitle("Job Manager"); dialog.resize(800,500); root = QVBoxLayout(dialog); items = QListWidget(); root.addWidget(items,1); controls = QHBoxLayout(); root.addLayout(controls)
      def refresh():
          items.clear()
          for key,value in self.scheduled_jobs.items():
              dependencies = ",".join(value.get("depends_on", [])) or "-"
              items.addItem(
                  f"{key} | {'ON' if value.get('enabled') else 'OFF'} | "
                  f"{value['name']} | {value['schedule']} | {value.get('last_status')} | "
                  f"retries={value.get('max_retries', 0)} | deps={dependencies}"
              )
      def selected():
          index = items.currentRow(); return list(self.scheduled_jobs)[index] if index >= 0 else None
      def run():
          key = selected()
          if key: self.jobs_execute(key); refresh()
      def toggle():
          key = selected()
          if key: self.scheduled_jobs[key]["enabled"] = not self.scheduled_jobs[key].get("enabled",True); refresh()
      def delete():
          key = selected()
          if key: del self.scheduled_jobs[key]; refresh()
      for label, action in [("Run",run),("Enable/Disable",toggle),("Delete",delete)]: button = QPushButton(label); button.clicked.connect(action); controls.addWidget(button)
      close = QDialogButtonBox(QDialogButtonBox.Close); close.rejected.connect(dialog.reject); root.addWidget(close); refresh(); dialog.exec()
  def jobs_history(self):
      if not self.job_runs: QMessageBox.information(self, "Job history", "No runs recorded."); return
      self.console.setPlainText("JOB RUN HISTORY\n" + "="*80 + "\n" + pd.DataFrame(self.job_runs).tail(200).to_string(index=False)); self.tabs.setCurrentWidget(self.python_page)
  def jobs_export_runner(self):
      if not self.scheduled_jobs: QMessageBox.information(self, "Export runner", "Create a job first."); return
      if not self.current_project_path:
          QMessageBox.information(self, "Export runner", "Save the project before exporting a headless runner.")
          return
      keys = list(self.scheduled_jobs); labels = [f"{k} | {self.scheduled_jobs[k]['name']}" for k in keys]; choice, ok = QInputDialog.getItem(self, "Export runner", "Job", labels, 0, False)
      if not ok: return
      job_id = keys[labels.index(choice)]; folder = QFileDialog.getExistingDirectory(self, "Export job runner")
      if not folder: return
      out = Path(folder) / f"data_studio_{job_id}"; out.mkdir(parents=True, exist_ok=True); job = self.scheduled_jobs[job_id]
      (out / "job.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
      runtime_source = Path(__file__).with_name("data_studio_headless_v0.38.py")
      if not runtime_source.exists():
          QMessageBox.critical(self, "Export runner", f"Headless runtime not found:\n{runtime_source}")
          return
      shutil.copy2(runtime_source, out / runtime_source.name)
      runner_config = {"project": str(Path(self.current_project_path).resolve()), "job_file": "job.json"}
      (out / "runner.json").write_text(json.dumps(runner_config, indent=2), encoding="utf-8")
      runner = """import json
import subprocess
import sys
from pathlib import Path
base = Path(__file__).resolve().parent
config = json.loads((base / "runner.json").read_text(encoding="utf-8"))
command = [
  sys.executable,
  str(base / "data_studio_headless_v0.38.py"),
  "--project", config["project"],
  "--job-file", str(base / config["job_file"]),
  "--json-output", str(base / "last_run.json"),
]
raise SystemExit(subprocess.call(command))
"""
      (out / "run_job.py").write_text(runner, encoding="utf-8")
      (out / "run_job.bat").write_text('@echo off\nsetlocal\ncd /d "%~dp0"\npython run_job.py\nset EXIT_CODE=%ERRORLEVEL%\necho.\necho Exit code: %EXIT_CODE%\nexit /b %EXIT_CODE%\n', encoding="utf-8")
      (out / "run_job.sh").write_text('#!/usr/bin/env sh\nset -eu\ncd "$(dirname "$0")"\npython3 run_job.py\n', encoding="utf-8")
      schedule = {"Hourly":"/SC HOURLY /MO 1", "Daily":"/SC DAILY /ST 08:00", "Weekly":"/SC WEEKLY /D MON /ST 08:00", "Monthly":"/SC MONTHLY /D 1 /ST 08:00"}.get(job["schedule"])
      if schedule: (out / "register_windows_task.bat").write_text(f'@echo off\nschtasks /Create /TN "PythonDataWorkbench_{job_id}" {schedule} /TR "\"%~dp0run_job.bat\"" /F\npause\n', encoding="utf-8")
      (out / "README.txt").write_text(
          "Data Studio v0.38 automation runner\n\n"
          "Run run_job.bat on Windows or run_job.sh on macOS/Linux.\n"
          "The runner loads the saved .pdw project, executes job.json, records history, "
          "persists generated datasets and monitoring results, and writes last_run.json.\n"
          "If the project moves, update the project path in runner.json.\n",
          encoding="utf-8",
      )
      QMessageBox.information(self, "Runner exported", f"Complete v0.38 automation runner exported to:\n{out}")
  def jobs_report(self):
      lines = ["SCHEDULER AND JOBS REPORT", "="*80, f"Jobs: {len(self.scheduled_jobs)}", f"Runs: {len(self.job_runs)}", f"Failed: {sum(x.get('status') == 'failed' for x in self.job_runs)}", ""]
      if self.scheduled_jobs: lines.extend(["JOB DEFINITIONS", pd.DataFrame([dict(id=k, **v) for k,v in self.scheduled_jobs.items()]).to_string(index=False), ""])
      if self.job_runs: lines.extend(["LATEST RUNS", pd.DataFrame(self.job_runs[-50:]).to_string(index=False)])
      self.console.setPlainText("\n".join(lines)); self.tabs.setCurrentWidget(self.python_page)
  def api_select_registered_model(self):
      if not self.model_registry:
          self.ml_model_registry()
      candidates = [(key, value) for key, value in self.model_registry.items() if key in self.trained_models]
      if not candidates:
          QMessageBox.information(self, "API deployment", "Register a fitted model first.")
          return None, None
      labels = [f"{key} {'[ACTIVE]' if value.get('active') else ''}" for key, value in candidates]
      choice, ok = QInputDialog.getItem(self, "API deployment", "Model version", labels, 0, False)
      return candidates[labels.index(choice)] if ok else (None, None)
  def api_create_deployment(self):
      key, metadata = self.api_select_registered_model()
      if not metadata:
          return
      default_name = re.sub(r"[^A-Za-z0-9_]", "_", f"{metadata.get('target', 'model')}_api")
      name, ok = QInputDialog.getText(self, "Create API deployment", "Deployment name", text=default_name)
      if not ok or not name.strip():
          return
      name = re.sub(r"[^A-Za-z0-9_-]", "_", name.strip())
      version, ok = QInputDialog.getText(self, "API version", "API version", text="1.0.0")
      if not ok:
          return
      self.api_deployments[name] = {
          "name": name, "model_key": key, "api_version": version,
          "model_version": metadata.get("version"), "target": metadata.get("target"),
          "problem": metadata.get("problem"), "features": list(metadata.get("features", [])),
          "endpoint": f"/v1/models/{name}/predict",
          "batch_endpoint": f"/v1/models/{name}/predict-batch",
          "health_endpoint": "/health", "active": True,
      }
      QMessageBox.information(self, "API deployment", f"Created deployment '{name}'.")
  def api_generate_server_code(self, deployment):
      name = deployment["name"]
      version = deployment["api_version"]
      model_version = deployment.get("model_version")
      template = """from pathlib import Path
import pickle
from typing import Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
import pandas as pd
BASE_DIR = Path(__file__).resolve().parent
with open(BASE_DIR / "model.pkl", "rb") as handle:
  artifact = pickle.load(handle)
model = artifact["model"]
features = artifact["features"]
target = artifact["target"]
classification = artifact.get("classification", False)
app = FastAPI(title=__NAME__, version=__VERSION__)
class PredictionRequest(BaseModel):
  model_config = ConfigDict(extra="forbid")
  records: list[dict[str, Any]]
@app.get("/health")
def health():
  return {"status": "ok", "model": __NAME__, "model_version": __MODEL_VERSION__}
@app.get("/metadata")
def metadata():
  return {"model": __NAME__, "target": target, "features": features, "classification": classification}
@app.post("/v1/models/__PATH_NAME__/predict")
def predict(request: PredictionRequest):
  try:
      frame = pd.DataFrame(request.records)
      missing = [column for column in features if column not in frame.columns]
      if missing:
          raise HTTPException(status_code=422, detail={"missing_features": missing})
      prediction = model.predict(frame[features])
      response = {"model": __NAME__, "target": target, "count": len(frame), "predictions": prediction.tolist()}
      if classification and hasattr(model, "predict_proba"):
          response["probabilities"] = model.predict_proba(frame[features]).tolist()
          response["classes"] = [str(value) for value in model.classes_]
      return response
  except HTTPException:
      raise
  except Exception as exc:
      raise HTTPException(status_code=500, detail=str(exc))
@app.post("/v1/models/__PATH_NAME__/predict-batch")
def predict_batch(request: PredictionRequest):
  return predict(request)
"""
      return (template.replace("__NAME__", repr(name))
              .replace("__VERSION__", repr(version))
              .replace("__MODEL_VERSION__", repr(model_version))
              .replace("__PATH_NAME__", name))
  def api_export_package(self):
      if not self.api_deployments:
          QMessageBox.information(self, "Export API", "Create a deployment first.")
          return
      name, ok = QInputDialog.getItem(self, "Export API", "Deployment", list(self.api_deployments), 0, False)
      if not ok:
          return
      deployment = self.api_deployments[name]
      artifact = self.trained_models.get(deployment["model_key"])
      if not artifact:
          QMessageBox.critical(self, "Export API", "The fitted model artifact is unavailable.")
          return
      folder = QFileDialog.getExistingDirectory(self, "Export API package")
      if not folder:
          return
      out = Path(folder) / name
      out.mkdir(parents=True, exist_ok=True)
      with open(out / "model.pkl", "wb") as handle:
          pickle.dump(artifact, handle, pickle.HIGHEST_PROTOCOL)
      (out / "server.py").write_text(self.api_generate_server_code(deployment), encoding="utf-8")
      (out / "deployment.json").write_text(json.dumps(deployment, indent=2), encoding="utf-8")
      (out / "requirements.txt").write_text("fastapi>=0.115\nuvicorn[standard]>=0.32\npandas>=2.2\nscikit-learn>=1.5\n", encoding="utf-8")
      (out / "sample_request.json").write_text(json.dumps({"records": [{feature: "value" for feature in deployment["features"]}]}, indent=2), encoding="utf-8")
      (out / "run_api.bat").write_text('@echo off\nsetlocal\ncd /d "%~dp0"\nif not exist ".venv\\Scripts\\python.exe" py -m venv .venv\n.venv\\Scripts\\python.exe -m pip install -r requirements.txt\n.venv\\Scripts\\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000\nendlocal\n', encoding="utf-8")
      (out / "run_api.sh").write_text('#!/usr/bin/env sh\nset -eu\ncd "$(dirname "$0")"\n[ -x .venv/bin/python ] || python3 -m venv .venv\n.venv/bin/python -m pip install -r requirements.txt\nexec .venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8000\n', encoding="utf-8")
      (out / "README.md").write_text(f"# {name} REST API\n\nOpenAPI docs: http://127.0.0.1:8000/docs\n\nHealth: GET /health\n\nPrediction: POST {deployment['endpoint']}\n", encoding="utf-8")
      QMessageBox.information(self, "API exported", f"Package created at:\n{out}")
  def api_test_prediction(self):
      try:
          if not self.api_deployments:
              QMessageBox.information(self, "Test API", "Create a deployment first.")
              return
          name, ok = QInputDialog.getItem(self, "Test prediction", "Deployment", list(self.api_deployments), 0, False)
          if not ok:
              return
          deployment = self.api_deployments[name]
          artifact = self.trained_models.get(deployment["model_key"])
          if not artifact:
              raise ValueError("Model artifact unavailable.")
          dataframe = self.model.dataframe
          features = artifact["features"]
          missing = [column for column in features if column not in dataframe.columns]
          if missing:
              raise ValueError("Active dataset is missing: " + ", ".join(missing))
          maximum = max(1, min(1000, len(dataframe)))
          rows, ok = QInputDialog.getInt(self, "Test prediction", "Rows", min(5, maximum), 1, maximum)
          if not ok:
              return
          frame = dataframe[features].head(rows)
          prediction = artifact["model"].predict(frame)
          response = {"deployment": name, "count": len(frame), "predictions": prediction.tolist()}
          if artifact.get("classification") and hasattr(artifact["model"], "predict_proba"):
              response["probabilities"] = artifact["model"].predict_proba(frame).tolist()
          self.api_request_history.append({"deployment": name, "kind": "test", "rows": len(frame), "status": "success"})
          self.console.setPlainText(json.dumps(response, indent=2, default=str))
          self.tabs.setCurrentWidget(self.python_page)
      except Exception as exc:
          self.api_request_history.append({"deployment": locals().get("name"), "kind": "test", "status": "failed", "error": str(exc)})
          QMessageBox.critical(self, "API test failed", str(exc))
  def api_batch_score(self):
      try:
          if not self.api_deployments:
              QMessageBox.information(self, "Batch score", "Create a deployment first.")
              return
          name, ok = QInputDialog.getItem(self, "Batch scoring", "Deployment", list(self.api_deployments), 0, False)
          if not ok:
              return
          deployment = self.api_deployments[name]
          artifact = self.trained_models.get(deployment["model_key"])
          if not artifact:
              raise ValueError("Model artifact unavailable.")
          dataframe = self.model.dataframe.copy()
          features = artifact["features"]
          missing = [column for column in features if column not in dataframe.columns]
          if missing:
              raise ValueError("Active dataset is missing: " + ", ".join(missing))
          prediction = artifact["model"].predict(dataframe[features])
          column = f"api_prediction_{artifact['target']}"
          dataframe[column] = prediction
          if artifact.get("classification") and hasattr(artifact["model"], "predict_proba"):
              dataframe[f"{column}_confidence"] = artifact["model"].predict_proba(dataframe[features]).max(axis=1)
          self.api_request_history.append({"deployment": name, "kind": "batch", "dataset": self.active_dataset, "rows": len(dataframe), "status": "success", "output_column": column})
          self.apply_dataframe(dataframe, f"REST contract batch score using {name}")
      except Exception as exc:
          self.api_request_history.append({"deployment": locals().get("name"), "kind": "batch", "status": "failed", "error": str(exc)})
          QMessageBox.critical(self, "Batch score failed", str(exc))
  def api_history_view(self):
      if not self.api_request_history:
          QMessageBox.information(self, "API history", "No API requests have been recorded.")
          return
      self.console.setPlainText("API REQUEST HISTORY\n" + "=" * 80 + "\n" + pd.DataFrame(self.api_request_history).tail(200).to_string(index=False))
      self.tabs.setCurrentWidget(self.python_page)
  def api_deployment_report(self):
      lines = ["DEPLOYMENT AND REST API REPORT", "=" * 80, f"Deployments: {len(self.api_deployments)}", f"Recorded requests: {len(self.api_request_history)}", ""]
      for name, item in self.api_deployments.items():
          lines.extend([name.upper(), f"Model key: {item['model_key']}", f"Model version: {item.get('model_version')}", f"Problem: {item.get('problem')}", f"Target: {item.get('target')}", f"Features: {', '.join(item.get('features', []))}", f"Predict endpoint: {item['endpoint']}", f"Batch endpoint: {item['batch_endpoint']}", ""])
      if self.api_request_history:
          lines.extend(["LATEST REQUESTS", pd.DataFrame(self.api_request_history[-50:]).to_string(index=False)])
      self.console.setPlainText("\n".join(lines))
      self.tabs.setCurrentWidget(self.python_page)
  def monitor_select_model(self):
      if not self.model_registry:
          self.ml_model_registry()
      if not self.model_registry:
          QMessageBox.information(self,"Model monitoring","Train and register a model first.");return None,None
      keys=list(self.model_registry);labels=[f"{k} {'[ACTIVE]' if self.model_registry[k].get('active') else ''}" for k in keys]
      choice,ok=QInputDialog.getItem(self,"Model monitoring","Registered model",labels,0,False)
      if not ok:return None,None
      key=keys[labels.index(choice)];return key,self.model_registry[key]
  def monitor_numeric_profile(self, series, bins=None):
      clean=pd.to_numeric(series,errors="coerce").dropna()
      if clean.empty:return {"type":"numeric","edges":[],"proportions":[],"missing_rate":1.0,"mean":None,"std":None}
      if bins is None:
          quantiles=np.linspace(0,1,11);edges=np.unique(clean.quantile(quantiles).to_numpy(float))
          if len(edges)<3:edges=np.linspace(float(clean.min())-1e-9,float(clean.max())+1e-9,3)
          edges[0]=-np.inf;edges[-1]=np.inf
      else:edges=np.asarray(bins,dtype=float)
      counts=pd.cut(clean,edges,include_lowest=True,duplicates="drop").value_counts(sort=False);props=(counts/max(1,counts.sum())).to_numpy(float)
      return {"type":"numeric","edges":[float(x) for x in edges],"proportions":props.tolist(),"missing_rate":float(series.isna().mean()),"mean":float(clean.mean()),"std":float(clean.std(ddof=1)) if len(clean)>1 else 0.0}
  def monitor_categorical_profile(self, series, categories=None):
      values=series.astype("object").where(series.notna(),"__MISSING__").astype(str)
      cats=list(categories) if categories is not None else list(values.value_counts().head(20).index)
      mapped=values.where(values.isin(cats),"__OTHER__");order=cats+([] if "__OTHER__" in cats else ["__OTHER__"]);counts=mapped.value_counts();props=[float(counts.get(c,0)/max(1,len(values))) for c in order]
      return {"type":"categorical","categories":order,"proportions":props,"missing_rate":float(series.isna().mean()),"unique":int(series.nunique(dropna=True))}
  def monitor_create_baseline(self):
      try:
          key,meta=self.monitor_select_model()
          if not meta:return
          dataset=meta["dataset"] if meta.get("dataset") in self.datasets else self.active_dataset;df=self.datasets[dataset];features=[c for c in meta.get("features",[]) if c in df.columns]
          if not features:raise ValueError("No registered model features are present in the baseline dataset.")
          profiles={}
          for c in features:profiles[c]=self.monitor_numeric_profile(df[c]) if pd.api.types.is_numeric_dtype(df[c]) else self.monitor_categorical_profile(df[c])
          name=f"{key}:baseline";self.monitoring_baselines[name]={"name":name,"model_key":key,"dataset":dataset,"rows":len(df),"features":features,"profiles":profiles,"target":meta.get("target"),"problem":meta.get("problem"),"metrics":meta.get("metrics",{})}
          QMessageBox.information(self,"Monitoring baseline",f"Created baseline for {len(features)} features and {len(df):,} rows.")
      except Exception as exc:QMessageBox.critical(self,"Baseline failed",str(exc))
  def monitor_psi(self, expected, actual):
      e=np.asarray(expected,dtype=float);a=np.asarray(actual,dtype=float);n=max(len(e),len(a));e=np.pad(e,(0,n-len(e)),constant_values=0);a=np.pad(a,(0,n-len(a)),constant_values=0);eps=1e-6;e=np.clip(e,eps,None);a=np.clip(a,eps,None);return float(np.sum((a-e)*np.log(a/e)))
  def monitor_run_drift(self):
      try:
          if not self.monitoring_baselines:QMessageBox.information(self,"Drift","Create a baseline first.");return
          names=list(self.monitoring_baselines);name,ok=QInputDialog.getItem(self,"Drift check","Baseline",names,0,False)
          if not ok:return
          base=self.monitoring_baselines[name];df=self.model.dataframe;rows=[];alerts=[]
          for c in base["features"]:
              if c not in df.columns:
                  rows.append({"feature":c,"psi":np.nan,"status":"CRITICAL","missing_change":np.nan,"note":"feature missing"});alerts.append({"baseline":name,"feature":c,"severity":"CRITICAL","message":"Feature missing from monitoring data"});continue
              bp=base["profiles"][c]
              if bp["type"]=="numeric":ap=self.monitor_numeric_profile(df[c],bp["edges"])
              else:ap=self.monitor_categorical_profile(df[c],bp["categories"])
              psi=self.monitor_psi(bp["proportions"],ap["proportions"]);missing_change=ap["missing_rate"]-bp["missing_rate"]
              status="CRITICAL" if psi>=self.drift_thresholds["critical"] else "WARNING" if psi>=self.drift_thresholds["warning"] else "STABLE"
              rows.append({"feature":c,"psi":psi,"status":status,"baseline_missing":bp["missing_rate"],"current_missing":ap["missing_rate"],"missing_change":missing_change,"baseline_mean":bp.get("mean"),"current_mean":ap.get("mean")})
              if status!="STABLE":alerts.append({"baseline":name,"feature":c,"severity":status,"message":f"PSI {psi:.4f}"})
              if missing_change>=.05:alerts.append({"baseline":name,"feature":c,"severity":"WARNING" if missing_change<.15 else "CRITICAL","message":f"Missing rate increased by {missing_change:.1%}"})
          table=pd.DataFrame(rows).sort_values("psi",ascending=False,na_position="first");run={"baseline":name,"dataset":self.active_dataset,"rows":len(df),"summary":table.to_dict(orient="records"),"critical":int((table["status"]=="CRITICAL").sum()),"warning":int((table["status"]=="WARNING").sum())};self.monitoring_runs.append(run);self.monitoring_alerts.extend(alerts)
          self.console.setPlainText(f"Drift check\nBaseline: {name}\nCurrent dataset: {self.active_dataset}\nRows: {len(df):,}\nCritical: {run['critical']}\nWarnings: {run['warning']}\n\nPSI interpretation used: <0.10 stable, 0.10-0.25 warning, >=0.25 critical.\n\n{table.to_string(index=False)}");self.tabs.setCurrentWidget(self.python_page)
      except Exception as exc:QMessageBox.critical(self,"Drift check failed",str(exc))
  def monitor_performance(self):
      try:
          from sklearn.metrics import accuracy_score,balanced_accuracy_score,f1_score,roc_auc_score,r2_score,mean_absolute_error,mean_squared_error
          key,meta=self.monitor_select_model()
          if not meta:return
          artifact=self.trained_models.get(key)
          if not artifact:raise ValueError("The fitted pipeline for this model version is unavailable.")
          model=artifact["model"];features=artifact["features"];target=artifact["target"];df=self.model.dataframe
          missing=[c for c in features+[target] if c not in df.columns]
          if missing:raise ValueError("Monitoring data is missing: "+", ".join(missing))
          work=df[features+[target]].dropna(subset=[target]);pred=model.predict(work[features]);metrics={}
          if meta.get("problem")=="classification":
              metrics={"accuracy":accuracy_score(work[target],pred),"balanced_accuracy":balanced_accuracy_score(work[target],pred),"f1_weighted":f1_score(work[target],pred,average="weighted")}
              if hasattr(model,"predict_proba"):
                  try:
                      proba=model.predict_proba(work[features]);metrics["roc_auc"]=roc_auc_score(work[target],proba[:,1] if proba.shape[1]==2 else proba,multi_class="ovr",average="weighted")
                  except Exception:pass
              primary="balanced_accuracy"
          else:
              metrics={"r2":r2_score(work[target],pred),"mae":mean_absolute_error(work[target],pred),"rmse":mean_squared_error(work[target],pred)**.5};primary="r2"
          baseline=meta.get("metrics",{});delta={k:metrics[k]-baseline[k] for k in metrics if k in baseline and isinstance(baseline[k],(int,float))};severity="STABLE"
          if primary in delta and delta[primary] < -.10:severity="CRITICAL"
          elif primary in delta and delta[primary] < -.05:severity="WARNING"
          record={"model":key,"dataset":self.active_dataset,"rows":len(work),"metrics":metrics,"baseline_metrics":baseline,"delta":delta,"severity":severity};self.prediction_history.append(record)
          if severity!="STABLE":self.monitoring_alerts.append({"baseline":key,"feature":"model_performance","severity":severity,"message":f"{primary} change {delta.get(primary):.4f}"})
          self.console.setPlainText("Model performance monitoring\n"+f"Model: {key}\nRows with outcomes: {len(work):,}\nStatus: {severity}\n\nCurrent metrics:\n"+"\n".join(f"{k}: {v:.6f}" for k,v in metrics.items())+"\n\nChange versus registry baseline:\n"+"\n".join(f"{k}: {v:+.6f}" for k,v in delta.items()));self.tabs.setCurrentWidget(self.python_page)
      except Exception as exc:QMessageBox.critical(self,"Performance monitoring failed",str(exc))
  def monitor_drift_dashboard(self):
      if not self.monitoring_runs:QMessageBox.information(self,"Drift dashboard","Run a drift check first.");return
      run=self.monitoring_runs[-1];table=pd.DataFrame(run["summary"]);plot=table.dropna(subset=["psi"]).sort_values("psi")
      self.figure.clear();ax=self.figure.add_subplot(111);colors=["#dc2626" if x>=self.drift_thresholds["critical"] else "#f59e0b" if x>=self.drift_thresholds["warning"] else "#16a34a" for x in plot["psi"]];ax.barh(plot["feature"],plot["psi"],color=colors);ax.axvline(self.drift_thresholds["warning"],color="#f59e0b",linestyle="--",label="Warning 0.10");ax.axvline(self.drift_thresholds["critical"],color="#dc2626",linestyle="--",label="Critical 0.25");ax.set_xlabel("Population Stability Index");ax.set_title("Feature drift dashboard");ax.legend();ax.grid(True,axis="x",alpha=.2);self.canvas.draw();self.tabs.setCurrentWidget(self.chart_page)
  def monitor_alerts_view(self):
      if not self.monitoring_alerts:QMessageBox.information(self,"Monitoring alerts","No monitoring alerts have been recorded.");return
      table=pd.DataFrame(self.monitoring_alerts);self.console.setPlainText("MONITORING ALERTS\n"+"="*80+"\n"+table.tail(200).to_string(index=False));self.tabs.setCurrentWidget(self.python_page)
  def monitoring_report(self):
      lines=["MODEL MONITORING AND DRIFT REPORT","="*80,f"Active dataset: {self.active_dataset}",f"Baselines: {len(self.monitoring_baselines)}",f"Drift runs: {len(self.monitoring_runs)}",f"Alerts: {len(self.monitoring_alerts)}",""]
      if self.monitoring_runs:
          latest=self.monitoring_runs[-1];lines.extend([f"Latest baseline: {latest['baseline']}",f"Latest rows: {latest['rows']:,}",f"Critical features: {latest['critical']}",f"Warning features: {latest['warning']}","",pd.DataFrame(latest["summary"]).to_string(index=False),""])
      if self.prediction_history:lines.extend(["LATEST PERFORMANCE RUN",str(self.prediction_history[-1]),""])
      if self.monitoring_alerts:lines.extend(["LATEST ALERTS",pd.DataFrame(self.monitoring_alerts[-50:]).to_string(index=False)])
      self.console.setPlainText("\n".join(lines));self.tabs.setCurrentWidget(self.python_page)
  def ml_get_ready_experiment(self):
      exp=self.automl_get_experiment()
      if not exp:return None
      if exp.get("best_model") is None:self.automl_run_leaderboard();exp=self.automl_experiments.get(self.active_experiment)
      return exp
  def ml_holdout(self, exp):
      from sklearn.model_selection import train_test_split
      from sklearn.base import clone
      X,y,_,_,_=self.automl_prepare(exp);strat=y if exp["problem"]=="classification" and y.value_counts().min()>=2 else None
      Xtr,Xte,ytr,yte=train_test_split(X,y,test_size=.2,random_state=42,stratify=strat);model=clone(exp["best_model"]);model.fit(Xtr,ytr);return model,Xte,yte
  def ml_confusion_matrix(self):
      try:
          from sklearn.metrics import confusion_matrix,ConfusionMatrixDisplay,classification_report
          exp=self.ml_get_ready_experiment()
          if not exp or exp["problem"]!="classification":raise ValueError("Classification experiment required.")
          model,Xte,yte=self.ml_holdout(exp);pred=model.predict(Xte);labels=model.classes_;cm=confusion_matrix(yte,pred,labels=labels)
          self.figure.clear();ax=self.figure.add_subplot(111);ConfusionMatrixDisplay(cm,display_labels=labels).plot(ax=ax,cmap="Blues",colorbar=False);ax.set_title(f"Confusion matrix: {exp['target']}");self.canvas.draw();self.tabs.setCurrentWidget(self.chart_page)
          exp["confusion_matrix"]={"labels":[str(x) for x in labels],"matrix":cm.tolist(),"report":classification_report(yte,pred,zero_division=0)}
      except Exception as exc:QMessageBox.critical(self,"Confusion matrix failed",str(exc))
  def ml_roc_pr_curves(self):
      try:
          from sklearn.metrics import roc_curve,auc,precision_recall_curve,average_precision_score
          from sklearn.preprocessing import label_binarize
          exp=self.ml_get_ready_experiment()
          if not exp or exp["problem"]!="classification":raise ValueError("Classification experiment required.")
          model,Xte,yte=self.ml_holdout(exp)
          if not hasattr(model,"predict_proba"):raise ValueError("Selected model does not expose probabilities.")
          proba=model.predict_proba(Xte);classes=model.classes_;self.figure.clear();ax1=self.figure.add_subplot(121);ax2=self.figure.add_subplot(122);summary=[]
          if len(classes)==2:
              truth=(np.asarray(yte)==classes[1]).astype(int);series=[(classes[1],truth,proba[:,1])]
          else:
              ybin=label_binarize(yte,classes=classes);series=[(c,ybin[:,i],proba[:,i]) for i,c in enumerate(classes)]
          for c,truth,score in series:
              fpr,tpr,_=roc_curve(truth,score);precision,recall,_=precision_recall_curve(truth,score);ra=auc(fpr,tpr);ap=average_precision_score(truth,score);ax1.plot(fpr,tpr,label=f"{c} AUC={ra:.3f}");ax2.plot(recall,precision,label=f"{c} AP={ap:.3f}");summary.append({"class":str(c),"roc_auc":float(ra),"average_precision":float(ap)})
          ax1.plot([0,1],[0,1],"--",color="gray");ax1.set_title("ROC curves");ax1.set_xlabel("False positive rate");ax1.set_ylabel("True positive rate");ax2.set_title("Precision-recall curves");ax2.set_xlabel("Recall");ax2.set_ylabel("Precision");ax1.legend();ax2.legend();ax1.grid(True,alpha=.2);ax2.grid(True,alpha=.2);self.canvas.draw();self.tabs.setCurrentWidget(self.chart_page);exp["roc_pr_summary"]=summary
      except Exception as exc:QMessageBox.critical(self,"ROC/PR failed",str(exc))
  def ml_calibration_curve(self):
      try:
          from sklearn.calibration import calibration_curve
          exp=self.ml_get_ready_experiment()
          if not exp or exp["problem"]!="classification":raise ValueError("Classification experiment required.")
          model,Xte,yte=self.ml_holdout(exp)
          if len(model.classes_)!=2:raise ValueError("Calibration plot currently supports binary classification.")
          score=model.predict_proba(Xte)[:,1];truth=(np.asarray(yte)==model.classes_[1]).astype(int);obs,pred=calibration_curve(truth,score,n_bins=min(10,max(3,len(yte)//10)),strategy="quantile")
          self.figure.clear();ax=self.figure.add_subplot(111);ax.plot(pred,obs,marker="o",label="Model");ax.plot([0,1],[0,1],"--",color="gray",label="Ideal");ax.set_title(f"Calibration: {model.classes_[1]}");ax.set_xlabel("Mean predicted probability");ax.set_ylabel("Observed frequency");ax.legend();ax.grid(True,alpha=.25);self.canvas.draw();self.tabs.setCurrentWidget(self.chart_page);exp["calibration"]=[{"predicted":float(a),"observed":float(b)} for a,b in zip(pred,obs)]
      except Exception as exc:QMessageBox.critical(self,"Calibration failed",str(exc))
  def ml_learning_curve(self):
      try:
          from sklearn.model_selection import learning_curve,StratifiedKFold,KFold
          exp=self.ml_get_ready_experiment();X,y,_,_,_=self.automl_prepare(exp);folds=exp.get("metadata",{}).get("cv_folds",5)
          cv=StratifiedKFold(n_splits=folds,shuffle=True,random_state=42) if exp["problem"]=="classification" else KFold(n_splits=folds,shuffle=True,random_state=42);scoring="f1_weighted" if exp["problem"]=="classification" else "r2"
          sizes,tr,te=learning_curve(exp["best_model"],X,y,cv=cv,scoring=scoring,train_sizes=np.linspace(.2,1,5));tm,vm=tr.mean(axis=1),te.mean(axis=1)
          self.figure.clear();ax=self.figure.add_subplot(111);ax.plot(sizes,tm,marker="o",label="Training");ax.plot(sizes,vm,marker="o",label="Cross-validation");ax.set_title(f"Learning curve: {exp['target']}");ax.set_xlabel("Training rows");ax.set_ylabel(scoring);ax.legend();ax.grid(True,alpha=.25);self.canvas.draw();self.tabs.setCurrentWidget(self.chart_page);exp["learning_curve"]=[{"rows":int(n),"train":float(a),"cv":float(b)} for n,a,b in zip(sizes,tm,vm)]
      except Exception as exc:QMessageBox.critical(self,"Learning curve failed",str(exc))
  def ml_residual_diagnostics(self):
      try:
          from sklearn.metrics import r2_score,mean_absolute_error
          exp=self.ml_get_ready_experiment()
          if not exp or exp["problem"]!="regression":raise ValueError("Regression experiment required.")
          model,Xte,yte=self.ml_holdout(exp);pred=model.predict(Xte);resid=np.asarray(yte)-np.asarray(pred)
          self.figure.clear();ax=self.figure.add_subplot(121);ax.scatter(pred,resid,alpha=.65);ax.axhline(0,color="gray",linestyle="--");ax.set_title("Residual vs predicted");ax.set_xlabel("Predicted");ax.set_ylabel("Residual");ax2=self.figure.add_subplot(122);ax2.hist(resid,bins=20,color="#2563eb",edgecolor="white");ax2.set_title("Residual distribution");self.canvas.draw();self.tabs.setCurrentWidget(self.chart_page);exp["residual_diagnostics"]={"r2":float(r2_score(yte,pred)),"mae":float(mean_absolute_error(yte,pred)),"residual_mean":float(np.mean(resid)),"residual_std":float(np.std(resid,ddof=1))}
      except Exception as exc:QMessageBox.critical(self,"Residual diagnostics failed",str(exc))
  def ml_model_registry(self):
      exp=self.automl_get_experiment()
      if exp and exp.get("best_model") is not None:
          self.model_version_counter+=1;version=f"v{self.model_version_counter:04d}";key=f"{exp['dataset']}:{exp['target']}:{version}"
          for item in self.model_registry.values():
              if item.get("dataset")==exp["dataset"] and item.get("target")==exp["target"]:item["active"]=False
          self.model_registry[key]={"version":version,"dataset":exp["dataset"],"target":exp["target"],"problem":exp["problem"],"model_name":exp.get("best_model_name"),"metrics":exp.get("metrics",{}),"features":exp["features"],"active":True};self.trained_models[key]={"model":exp["best_model"],"features":exp["features"],"target":exp["target"],"classification":exp["problem"]=="classification"}
      lines=["MODEL REGISTRY","="*80]+[f"{'*' if v.get('active') else ' '} {k} | {v.get('model_name')} | {v.get('metrics')}" for k,v in self.model_registry.items()]+["","PREDICTION HISTORY","-"*80]+[str(x) for x in self.prediction_history[-50:]]
      self.console.setPlainText("\n".join(lines));self.tabs.setCurrentWidget(self.python_page)
  def automl_detect_problem(self, target):
      y=self.model.dataframe[target].dropna()
      if y.dtype == object or str(y.dtype).startswith("category") or y.nunique() <= max(10, int(len(y)*.05)):
          return "classification"
      return "regression"
  def automl_new_experiment(self):
      df=self.model.dataframe
      target,ok=QInputDialog.getItem(self,"New AutoML experiment","Target column",list(df.columns),0,False)
      if not ok:return
      detected=self.automl_detect_problem(target)
      problem,ok=QInputDialog.getItem(self,"New AutoML experiment","Problem type",[detected,"classification" if detected=="regression" else "regression"],0,False)
      if not ok:return
      name,ok=QInputDialog.getText(self,"New AutoML experiment","Experiment name",text=f"{target}_{problem}_experiment")
      if not ok or not name.strip():return
      features=[c for c in df.columns if c!=target]
      self.automl_experiments[name.strip()]={"name":name.strip(),"dataset":self.active_dataset,"target":target,"features":features,"problem":problem,"status":"configured","leaderboard":None,"best_model":None,"best_model_name":None,"metrics":{},"metadata":{"rows":len(df),"created_with":"v0.15"}}
      self.active_experiment=name.strip(); QMessageBox.information(self,"AutoML experiment",f"Created '{name.strip()}'.\nDetected problem: {problem}\nFeatures: {len(features)}")
  def automl_get_experiment(self):
      if self.active_experiment in self.automl_experiments:return self.automl_experiments[self.active_experiment]
      if not self.automl_experiments:
          self.automl_new_experiment()
          return self.automl_experiments.get(self.active_experiment)
      name,ok=QInputDialog.getItem(self,"AutoML experiment","Experiment",list(self.automl_experiments),0,False)
      if not ok:return None
      self.active_experiment=name; return self.automl_experiments[name]
  def automl_prepare(self, exp):
      from sklearn.compose import ColumnTransformer
      from sklearn.pipeline import Pipeline
      from sklearn.preprocessing import OneHotEncoder, StandardScaler
      from sklearn.impute import SimpleImputer
      df=self.datasets[exp["dataset"]].copy(); y=df[exp["target"]]; valid=y.notna(); X=df.loc[valid,exp["features"]]; y=y.loc[valid]
      numeric=list(X.select_dtypes(include=np.number).columns); categorical=[c for c in X.columns if c not in numeric]
      transformers=[]
      if numeric: transformers.append(("num",Pipeline([("impute",SimpleImputer(strategy="median")),("scale",StandardScaler())]),numeric))
      if categorical: transformers.append(("cat",Pipeline([("impute",SimpleImputer(strategy="most_frequent")),("encode",OneHotEncoder(handle_unknown="ignore"))]),categorical))
      pre=ColumnTransformer(transformers,remainder="drop")
      return X,y,pre,numeric,categorical
  def automl_candidates(self, problem):
      if problem=="classification":
          from sklearn.linear_model import LogisticRegression
          from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier, HistGradientBoostingClassifier
          from sklearn.dummy import DummyClassifier
          return {"Baseline":DummyClassifier(strategy="most_frequent"),"Logistic Regression":LogisticRegression(max_iter=2000,class_weight="balanced"),"Random Forest":RandomForestClassifier(n_estimators=250,random_state=42,class_weight="balanced"),"Extra Trees":ExtraTreesClassifier(n_estimators=250,random_state=42,class_weight="balanced"),"Histogram Gradient Boosting":HistGradientBoostingClassifier(random_state=42)}
      from sklearn.linear_model import LinearRegression, Ridge
      from sklearn.ensemble import RandomForestRegressor, ExtraTreesRegressor, HistGradientBoostingRegressor
      from sklearn.dummy import DummyRegressor
      return {"Baseline":DummyRegressor(strategy="mean"),"Linear Regression":LinearRegression(),"Ridge":Ridge(),"Random Forest":RandomForestRegressor(n_estimators=250,random_state=42,n_jobs=-1),"Extra Trees":ExtraTreesRegressor(n_estimators=250,random_state=42,n_jobs=-1),"Histogram Gradient Boosting":HistGradientBoostingRegressor(random_state=42)}
  def automl_run_leaderboard(self):
      try:
          from sklearn.pipeline import Pipeline
          from sklearn.model_selection import cross_validate, StratifiedKFold, KFold
          exp=self.automl_get_experiment()
          if not exp:return
          X,y,pre,numeric,categorical=self.automl_prepare(exp)
          if len(X)<20:raise ValueError("AutoML leaderboard requires at least 20 rows with a non-missing target.")
          if exp["problem"]=="classification":
              min_class=int(y.value_counts().min()); folds=min(5,min_class)
              if folds<2:raise ValueError("Each classification class needs at least two observations.")
              cv=StratifiedKFold(n_splits=folds,shuffle=True,random_state=42); scoring={"accuracy":"accuracy","f1":"f1_weighted","roc_auc":"roc_auc_ovr_weighted"} if y.nunique()>2 else {"accuracy":"accuracy","f1":"f1_weighted","roc_auc":"roc_auc"}
              primary="test_roc_auc"
          else:
              folds=min(5,max(2,len(X)//10)); cv=KFold(n_splits=folds,shuffle=True,random_state=42); scoring={"r2":"r2","mae":"neg_mean_absolute_error","rmse":"neg_root_mean_squared_error"}; primary="test_r2"
          rows=[]; fitted={}
          for name,estimator in self.automl_candidates(exp["problem"]).items():
              pipe=Pipeline([("preprocess",pre),("model",estimator)])
              try:
                  scores=cross_validate(pipe,X,y,cv=cv,scoring=scoring,n_jobs=-1,error_score="raise")
                  row={"model":name,"fit_seconds":scores["fit_time"].mean()}
                  for key,val in scores.items():
                      if key.startswith("test_"): row[key.replace("test_","")]=(-val.mean() if key in ("test_mae","test_rmse") else val.mean()); row[key.replace("test_","")+"_std"]=val.std()
                  rows.append(row); pipe.fit(X,y); fitted[name]=pipe
              except Exception as exc: rows.append({"model":name,"error":str(exc)})
          board=pd.DataFrame(rows)
          metric=primary.replace("test_","")
          valid=board[board[metric].notna()] if metric in board.columns else pd.DataFrame()
          if valid.empty:raise ValueError("No candidate model completed successfully.")
          best_name=valid.sort_values(metric,ascending=False).iloc[0]["model"]
          exp["leaderboard"]=board.to_dict(orient="records"); exp["best_model"]=fitted[best_name]; exp["best_model_name"]=best_name; exp["status"]="leaderboard_complete"; exp["metadata"].update({"numeric_features":numeric,"categorical_features":categorical,"cv_folds":folds,"primary_metric":metric})
          self.trained_models[exp["name"]]={"model":exp["best_model"],"features":exp["features"],"target":exp["target"],"classification":exp["problem"]=="classification"}
          self.console.setPlainText(f"AutoML leaderboard: {exp['name']}\nBest model: {best_name}\nPrimary metric: {metric}\n\n"+board.sort_values(metric,ascending=False,na_position="last").to_string(index=False)); self.tabs.setCurrentWidget(self.python_page)
      except Exception as exc: QMessageBox.critical(self,"AutoML leaderboard failed",str(exc))
  def automl_tune_best(self):
      try:
          from sklearn.model_selection import RandomizedSearchCV, StratifiedKFold, KFold
          exp=self.automl_get_experiment()
          if not exp or exp.get("best_model") is None:raise ValueError("Run the model leaderboard first.")
          X,y,pre,numeric,categorical=self.automl_prepare(exp); name=exp["best_model_name"]
          grids={
              "Random Forest":{"model__n_estimators":[150,250,400,600],"model__max_depth":[None,5,10,20],"model__min_samples_leaf":[1,2,4,8],"model__max_features":["sqrt","log2",None]},
              "Extra Trees":{"model__n_estimators":[150,250,400,600],"model__max_depth":[None,5,10,20],"model__min_samples_leaf":[1,2,4,8],"model__max_features":["sqrt","log2",None]},
              "Logistic Regression":{"model__C":np.logspace(-3,3,30),"model__penalty":["l2"]},
              "Ridge":{"model__alpha":np.logspace(-4,4,60)},
              "Histogram Gradient Boosting":{"model__learning_rate":[.02,.05,.1,.2],"model__max_iter":[100,200,400],"model__max_leaf_nodes":[15,31,63],"model__l2_regularization":[0,.1,1,10]}
          }
          if name not in grids:raise ValueError(f"Hyperparameter tuning is not configured for {name}.")
          if exp["problem"]=="classification":
              folds=min(5,int(y.value_counts().min())); cv=StratifiedKFold(n_splits=max(2,folds),shuffle=True,random_state=42); scoring="roc_auc_ovr_weighted" if y.nunique()>2 else "roc_auc"
          else: cv=KFold(n_splits=min(5,max(2,len(X)//10)),shuffle=True,random_state=42); scoring="r2"
          search=RandomizedSearchCV(exp["best_model"],grids[name],n_iter=min(25,max(1,np.prod([len(v) for v in grids[name].values()]))),scoring=scoring,cv=cv,n_jobs=-1,random_state=42,refit=True)
          search.fit(X,y); exp["best_model"]=search.best_estimator_; exp["tuning"]={"best_score":float(search.best_score_),"best_params":search.best_params_,"scoring":scoring}; exp["status"]="tuned"; self.trained_models[exp["name"]]["model"]=exp["best_model"]
          self.console.setPlainText(f"AutoML tuning complete\nExperiment: {exp['name']}\nModel: {name}\nScoring: {scoring}\nBest CV score: {search.best_score_:.6f}\nBest parameters:\n"+json.dumps(search.best_params_,indent=2)); self.tabs.setCurrentWidget(self.python_page)
      except Exception as exc: QMessageBox.critical(self,"AutoML tuning failed",str(exc))
  def automl_evaluate_holdout(self):
      try:
          from sklearn.model_selection import train_test_split
          from sklearn.metrics import accuracy_score,balanced_accuracy_score,f1_score,roc_auc_score,classification_report,confusion_matrix,r2_score,mean_absolute_error,mean_squared_error
          from sklearn.base import clone
          exp=self.automl_get_experiment()
          if not exp or exp.get("best_model") is None:raise ValueError("Run the leaderboard first.")
          X,y,pre,numeric,categorical=self.automl_prepare(exp); strat=y if exp["problem"]=="classification" and y.value_counts().min()>=2 else None
          Xtr,Xte,ytr,yte=train_test_split(X,y,test_size=.2,random_state=42,stratify=strat); model=clone(exp["best_model"]); model.fit(Xtr,ytr); pred=model.predict(Xte)
          if exp["problem"]=="classification":
              metrics={"accuracy":accuracy_score(yte,pred),"balanced_accuracy":balanced_accuracy_score(yte,pred),"f1_weighted":f1_score(yte,pred,average="weighted")}; cm=confusion_matrix(yte,pred); report=classification_report(yte,pred,zero_division=0)
              if hasattr(model,"predict_proba"):
                  proba=model.predict_proba(Xte)
                  try: metrics["roc_auc"]=roc_auc_score(yte,proba[:,1] if proba.shape[1]==2 else proba,multi_class="ovr",average="weighted")
                  except Exception: pass
              text="Holdout classification evaluation\n"+"\n".join(f"{k}: {v:.6f}" for k,v in metrics.items())+"\n\nConfusion matrix:\n"+np.array2string(cm)+"\n\n"+report
              table=pd.DataFrame({"actual":yte.astype(str).values,"predicted":pd.Series(pred).astype(str).values})
          else:
              metrics={"r2":r2_score(yte,pred),"mae":mean_absolute_error(yte,pred),"rmse":mean_squared_error(yte,pred)**.5}; text="Holdout regression evaluation\n"+"\n".join(f"{k}: {v:.6f}" for k,v in metrics.items()); table=pd.DataFrame({"actual":yte.values,"predicted":pred,"residual":yte.values-pred})
          exp["metrics"]=metrics; exp["holdout_table"]=table.to_dict(orient="records"); exp["holdout_model"]=model; exp["status"]="evaluated"; self.console.setPlainText(text+"\n\n"+table.head(100).to_string(index=False)); self.tabs.setCurrentWidget(self.python_page)
      except Exception as exc: QMessageBox.critical(self,"AutoML evaluation failed",str(exc))
  def automl_explain(self):
      try:
          from sklearn.inspection import permutation_importance
          exp=self.automl_get_experiment()
          if not exp or exp.get("best_model") is None:raise ValueError("Run the leaderboard first.")
          X,y,pre,numeric,categorical=self.automl_prepare(exp); sample_n=min(len(X),5000); sample=X.sample(sample_n,random_state=42) if len(X)>sample_n else X; ys=y.loc[sample.index]
          scoring="f1_weighted" if exp["problem"]=="classification" else "r2"; result=permutation_importance(exp["best_model"],sample,ys,n_repeats=8,random_state=42,scoring=scoring,n_jobs=-1)
          table=pd.DataFrame({"feature":sample.columns,"importance_mean":result.importances_mean,"importance_std":result.importances_std}).sort_values("importance_mean",ascending=False).reset_index(drop=True); exp["feature_importance"]=table.to_dict(orient="records")
          self.console.setPlainText(f"Permutation feature importance\nExperiment: {exp['name']}\nModel: {exp['best_model_name']}\nScoring: {scoring}\nSample rows: {sample_n:,}\n\n"+table.to_string(index=False)); self.tabs.setCurrentWidget(self.python_page)
      except Exception as exc: QMessageBox.critical(self,"Model explanation failed",str(exc))
  def automl_export_bundle(self):
      try:
          exp=self.automl_get_experiment()
          if not exp or exp.get("best_model") is None:raise ValueError("A trained AutoML experiment is required.")
          path,_=QFileDialog.getSaveFileName(self,"Export AutoML bundle",f"{exp['name']}.automl.pkl","AutoML bundle (*.pkl)")
          if not path:return
          bundle={"bundle_version":1,"experiment":exp["name"],"dataset":exp["dataset"],"target":exp["target"],"features":exp["features"],"problem":exp["problem"],"model_name":exp["best_model_name"],"model":exp["best_model"],"metrics":exp.get("metrics",{}),"metadata":exp.get("metadata",{}),"tuning":exp.get("tuning")}
          with open(path,"wb") as handle:pickle.dump(bundle,handle,protocol=pickle.HIGHEST_PROTOCOL)
          schema_path=str(Path(path).with_suffix(".schema.json")); Path(schema_path).write_text(json.dumps({k:v for k,v in bundle.items() if k!="model"},indent=2,default=str),encoding="utf-8"); QMessageBox.information(self,"AutoML export",f"Saved model bundle:\n{path}\n\nSaved readable schema:\n{schema_path}")
      except Exception as exc: QMessageBox.critical(self,"AutoML export failed",str(exc))
  def store_engineering_result(self, name, text, table=None):
      self.engineering_results[name] = {"text": text, "table": table.to_dict(orient="records") if isinstance(table, pd.DataFrame) else None}
      self.console.setPlainText(text + ("\n\n" + table.head(100).to_string(index=False) if isinstance(table, pd.DataFrame) else ""))
      self.tabs.setCurrentWidget(self.python_page)
  def engineering_fft(self):
      try:
          from scipy import signal
          col = self.choose_column("FFT / spectrum", True)
          if not col: return
          rate, ok = QInputDialog.getDouble(self, "Sampling rate", "Samples per second", 1.0, 0.000001, 1000000000.0, 6)
          if not ok: return
          values = self.model.dataframe[col].interpolate().bfill().ffill().to_numpy(dtype=float)
          if len(values) < 4: raise ValueError("At least four samples are required.")
          detrended = signal.detrend(values)
          freq = np.fft.rfftfreq(len(detrended), d=1.0/rate)
          amplitude = np.abs(np.fft.rfft(detrended)) * 2.0 / len(detrended)
          power_freq, power = signal.periodogram(detrended, fs=rate)
          result = pd.DataFrame({"frequency_hz": freq, "amplitude": amplitude})
          dominant = result.loc[result["amplitude"].iloc[1:].idxmax()] if len(result) > 1 else result.iloc[0]
          text = f"FFT and spectral analysis for {col}\nSamples: {len(values):,}\nSampling rate: {rate:,.6f} Hz\nNyquist frequency: {rate/2:,.6f} Hz\nDominant non-DC frequency: {dominant['frequency_hz']:,.6f} Hz\nAmplitude: {dominant['amplitude']:,.6f}\nPeriodogram bins: {len(power_freq):,}"
          self.store_engineering_result("FFT spectrum", text, result)
          self.figure.clear(); ax=self.figure.add_subplot(111); ax.plot(freq, amplitude, color="#2563eb"); ax.set_title(f"FFT amplitude spectrum: {col}"); ax.set_xlabel("Frequency (Hz)"); ax.set_ylabel("Amplitude"); ax.grid(True, alpha=.25); self.canvas.draw(); self.tabs.setCurrentWidget(self.chart_page)
      except Exception as exc: QMessageBox.critical(self, "FFT failed", str(exc))
  def engineering_filter(self):
      try:
          from scipy import signal
          col = self.choose_column("Digital filter", True)
          if not col: return
          kind, ok = QInputDialog.getItem(self, "Digital filter", "Filter type", ["lowpass", "highpass", "bandpass"], 0, False)
          if not ok: return
          rate, ok = QInputDialog.getDouble(self, "Sampling rate", "Samples per second", 1.0, 0.000001, 1000000000.0, 6)
          if not ok: return
          low, ok = QInputDialog.getDouble(self, "Cutoff", "Low/cutoff frequency (Hz)", rate*0.1, 0.000001, rate/2*0.999, 6)
          if not ok: return
          high = None
          if kind == "bandpass":
              high, ok = QInputDialog.getDouble(self, "High cutoff", "High frequency (Hz)", rate*0.3, low*1.001, rate/2*0.999, 6)
              if not ok: return
          order, ok = QInputDialog.getInt(self, "Filter order", "Butterworth order", 4, 1, 12)
          if not ok: return
          values = self.model.dataframe[col].interpolate().bfill().ffill().to_numpy(dtype=float)
          cutoff = [low, high] if kind == "bandpass" else low
          sos = signal.butter(order, cutoff, btype=kind, fs=rate, output="sos")
          filtered = signal.sosfiltfilt(sos, values)
          df = self.model.dataframe.copy(); out_col=f"{col}_{kind}"; df[out_col]=filtered
          self.apply_dataframe(df, f"{kind} Butterworth filter on {col}")
          text=f"Butterworth {kind} filter\nSignal: {col}\nSampling rate: {rate:g} Hz\nOrder: {order}\nCutoff: {cutoff}\nOutput column: {out_col}"
          self.engineering_results["Digital filter"]={"text":text,"table":None}
      except Exception as exc: QMessageBox.critical(self, "Digital filter failed", str(exc))
  def engineering_peaks(self):
      try:
          from scipy.signal import find_peaks
          col=self.choose_column("Peak analysis", True)
          if not col:return
          values=self.model.dataframe[col].interpolate().bfill().ffill().to_numpy(dtype=float)
          default=max(float(np.nanstd(values))*.5, 0.0)
          prominence,ok=QInputDialog.getDouble(self,"Peak analysis","Minimum prominence",default,0,1e15,6)
          if not ok:return
          distance,ok=QInputDialog.getInt(self,"Peak analysis","Minimum sample distance",1,1,max(1,len(values)))
          if not ok:return
          peaks,props=find_peaks(values,prominence=prominence,distance=distance)
          result=pd.DataFrame({"sample_index":peaks,"peak_value":values[peaks],"prominence":props.get("prominences",np.nan)})
          text=f"Peak analysis for {col}\nDetected peaks: {len(peaks):,}\nMinimum prominence: {prominence:g}\nMinimum distance: {distance} samples"
          self.store_engineering_result("Peak analysis",text,result)
      except Exception as exc: QMessageBox.critical(self,"Peak analysis failed",str(exc))
  def engineering_numerical_solver(self):
      try:
          from scipy import integrate, optimize
          mode,ok=QInputDialog.getItem(self,"Numerical solver","Operation",["Integrate expression","Find root"],0,False)
          if not ok:return
          expr,ok=QInputDialog.getText(self,"Numerical solver","Expression in x",text="x**2 - 4")
          if not ok:return
          safe={"np":np,"sin":np.sin,"cos":np.cos,"exp":np.exp,"log":np.log,"sqrt":np.sqrt}
          fn=lambda x: safe_math_eval(expr,{**safe,"x":x},np)
          a,ok=QInputDialog.getDouble(self,"Lower bound","a",0.0,-1e12,1e12,6)
          if not ok:return
          b,ok=QInputDialog.getDouble(self,"Upper bound","b",5.0,-1e12,1e12,6)
          if not ok:return
          if mode.startswith("Integrate"):
              value,error=integrate.quad(fn,a,b); text=f"Numerical integration\nExpression: {expr}\nInterval: [{a}, {b}]\nIntegral: {value:.12g}\nEstimated absolute error: {error:.3g}"
          else:
              sol=optimize.root_scalar(fn,bracket=[a,b],method="brentq"); text=f"Root solving\nExpression: {expr}\nBracket: [{a}, {b}]\nRoot: {sol.root:.12g}\nConverged: {sol.converged}\nIterations: {sol.iterations}"
          self.store_engineering_result("Numerical solver",text)
      except Exception as exc: QMessageBox.critical(self,"Numerical solver failed",str(exc))
  def engineering_curve_fit(self):
      try:
          from scipy.optimize import curve_fit
          x,y=self.choose_two_numeric("Curve fitting")
          if not x:return
          kind,ok=QInputDialog.getItem(self,"Curve fitting","Model",["linear","quadratic","exponential"],0,False)
          if not ok:return
          clean=self.model.dataframe[[x,y]].dropna(); xv=clean[x].to_numpy(float); yv=clean[y].to_numpy(float)
          if kind=="linear":
              fn=lambda x,a,b:a*x+b; p0=[1,0]
          elif kind=="quadratic":
              fn=lambda x,a,b,c:a*x*x+b*x+c; p0=[1,1,0]
          else:
              fn=lambda x,a,b,c:a*np.exp(b*x)+c; p0=[max(np.mean(yv),1e-6),0.01,0]
          params,cov=curve_fit(fn,xv,yv,p0=p0,maxfev=20000); pred=fn(xv,*params); residual=yv-pred; r2=1-np.sum(residual**2)/np.sum((yv-yv.mean())**2)
          df=self.model.dataframe.copy(); df[f"fitted_{y}"]=np.nan; df.loc[clean.index,f"fitted_{y}"]=pred; self.apply_dataframe(df,f"{kind} curve fit {y} on {x}")
          text=f"Curve fitting\nModel: {kind}\nX: {x}\nY: {y}\nParameters: {', '.join(f'{v:.8g}' for v in params)}\nR-squared: {r2:.6f}\nRMSE: {np.sqrt(np.mean(residual**2)):.6f}"
          self.engineering_results["Curve fitting"]={"text":text,"table":None}; self.console.setPlainText(text); self.tabs.setCurrentWidget(self.python_page)
      except Exception as exc: QMessageBox.critical(self,"Curve fitting failed",str(exc))
  def engineering_reliability_spc(self):
      try:
          from scipy import stats
          mode,ok=QInputDialog.getItem(self,"Reliability / SPC","Analysis",["Weibull reliability","Individuals control chart","Process capability Cp/Cpk"],0,False)
          if not ok:return
          col=self.choose_column(mode,True)
          if not col:return
          values=self.model.dataframe[col].dropna().astype(float)
          if mode=="Weibull reliability":
              positive=values[values>0]
              if len(positive)<3:raise ValueError("At least three positive failure/lifetime observations are required.")
              shape,loc,scale=stats.weibull_min.fit(positive,floc=0); b10=stats.weibull_min.ppf(.10,shape,loc=0,scale=scale); median=stats.weibull_min.median(shape,loc=0,scale=scale)
              text=f"Weibull reliability analysis\nColumn: {col}\nN={len(positive)}\nShape beta={shape:.6f}\nScale eta={scale:.6f}\nB10 life={b10:.6f}\nMedian life={median:.6f}"
              table=pd.DataFrame({"probability":[.1,.25,.5,.75,.9],"life":stats.weibull_min.ppf([.1,.25,.5,.75,.9],shape,loc=0,scale=scale)})
          elif mode=="Individuals control chart":
              mean=values.mean(); mr=values.diff().abs().dropna().mean(); sigma=mr/1.128 if mr==mr else values.std(ddof=1); ucl=mean+3*sigma; lcl=mean-3*sigma
              table=pd.DataFrame({"index":values.index,"value":values.values,"center_line":mean,"UCL":ucl,"LCL":lcl,"outside_limits":(values.values>ucl)|(values.values<lcl)})
              text=f"Individuals control chart\nMean={mean:.6f}\nEstimated sigma={sigma:.6f}\nUCL={ucl:.6f}\nLCL={lcl:.6f}\nOut-of-control points={int(table['outside_limits'].sum())}"
          else:
              lsl,ok=QInputDialog.getDouble(self,"Lower specification","LSL",float(values.min()),-1e15,1e15,6)
              if not ok:return
              usl,ok=QInputDialog.getDouble(self,"Upper specification","USL",float(values.max()),-1e15,1e15,6)
              if not ok:return
              mean=values.mean(); sigma=values.std(ddof=1); cp=(usl-lsl)/(6*sigma); cpk=min((usl-mean)/(3*sigma),(mean-lsl)/(3*sigma)); text=f"Process capability\nMean={mean:.6f}\nSigma={sigma:.6f}\nLSL={lsl:.6f}\nUSL={usl:.6f}\nCp={cp:.4f}\nCpk={cpk:.4f}"; table=None
          self.store_engineering_result(mode,text,table)
      except Exception as exc: QMessageBox.critical(self,"Reliability / SPC failed",str(exc))
  def engineering_monte_carlo(self):
      try:
          variables,ok=QInputDialog.getText(self,"Monte Carlo Pro","Variables as name:distribution:param1:param2; ...",text="revenue:normal:10000000:1000000; cost:normal:7000000:800000")
          if not ok:return
          expression,ok=QInputDialog.getText(self,"Monte Carlo Pro","Output expression",text="revenue - cost")
          if not ok:return
          iterations,ok=QInputDialog.getInt(self,"Monte Carlo Pro","Iterations",100000,1000,2000000,1000)
          if not ok:return
          rng=np.random.default_rng(42); env={}
          for spec in variables.split(";"):
              parts=[x.strip() for x in spec.split(":")]; name,dist=parts[0],parts[1].lower(); pars=list(map(float,parts[2:]))
              if dist=="normal": env[name]=rng.normal(pars[0],pars[1],iterations)
              elif dist=="uniform": env[name]=rng.uniform(pars[0],pars[1],iterations)
              elif dist=="triangular": env[name]=rng.triangular(pars[0],pars[1],pars[2],iterations)
              elif dist=="lognormal": env[name]=rng.lognormal(pars[0],pars[1],iterations)
              else:raise ValueError(f"Unsupported distribution: {dist}")
          output=safe_math_eval(expression,env,np); q=np.quantile(output,[.01,.05,.25,.5,.75,.95,.99]); loss=float(np.mean(output<0))
          table=pd.DataFrame({"metric":["mean","std","p01","p05","p25","p50","p75","p95","p99","probability_below_zero"],"value":[np.mean(output),np.std(output),*q,loss]})
          text=f"Monte Carlo simulation\nIterations: {iterations:,}\nExpression: {expression}\nExpected value: {np.mean(output):,.6f}\nStandard deviation: {np.std(output):,.6f}\nProbability below zero: {loss:.2%}"
          self.store_engineering_result("Monte Carlo Pro",text,table)
      except Exception as exc: QMessageBox.critical(self,"Monte Carlo simulation failed",str(exc))
  def engineering_parameter_sweep(self):
      try:
          expr,ok=QInputDialog.getText(self,"Parameter sweep","Expression in x",text="-0.5*x**2 + 10*x + 20")
          if not ok:return
          start,ok=QInputDialog.getDouble(self,"Parameter sweep","Start",0,-1e12,1e12,6)
          if not ok:return
          stop,ok=QInputDialog.getDouble(self,"Parameter sweep","Stop",20,-1e12,1e12,6)
          if not ok:return
          points,ok=QInputDialog.getInt(self,"Parameter sweep","Points",101,2,100000)
          if not ok:return
          x=np.linspace(start,stop,points); y=safe_math_eval(expr,{"x":x},np); result=pd.DataFrame({"x":x,"response":y}); best=result.loc[result["response"].idxmax()]
          text=f"Parameter sweep\nExpression: {expr}\nRange: {start} to {stop}\nPoints: {points:,}\nMaximum response: {best['response']:.8g} at x={best['x']:.8g}"
          self.store_engineering_result("Parameter sweep",text,result)
      except Exception as exc: QMessageBox.critical(self,"Parameter sweep failed",str(exc))
  def engineering_report(self):
      if not self.engineering_results: QMessageBox.information(self,"Engineering report","Run an Engineering Studio analysis first."); return
      lines=["ENGINEERING AND SCIENTIFIC ANALYTICS REPORT","="*72,f"Dataset: {self.active_dataset}",""]
      for name,result in self.engineering_results.items(): lines.extend([name.upper(),"-"*len(name),result.get("text",""),""])
      self.console.setPlainText("\n".join(lines)); self.tabs.setCurrentWidget(self.python_page)
  def choose_group_and_measure(self, title):
      df=self.model.dataframe
      groups=list(df.select_dtypes(exclude=np.number).columns)
      nums=list(df.select_dtypes(include=np.number).columns)
      if not groups or not nums:
          QMessageBox.warning(self,title,"This analysis needs at least one categorical and one numeric column."); return None,None
      group,ok=QInputDialog.getItem(self,title,"Group column",groups,0,False)
      if not ok:return None,None
      measure,ok=QInputDialog.getItem(self,title,"Numeric measure",nums,0,False)
      return (group,measure) if ok else (None,None)
  def store_statistics_result(self, name, text, table=None):
      self.statistics_results[name]={"text":text,"table":table.to_dict(orient="records") if isinstance(table,pd.DataFrame) else None}
      self.console.setPlainText(text + ("\n\n"+table.to_string(index=False) if isinstance(table,pd.DataFrame) else "")); self.tabs.setCurrentWidget(self.python_page)
  def guided_statistical_test(self):
      try:
          from scipy import stats
          mode,ok=QInputDialog.getItem(self,"Guided statistical test","Question",["Compare two group means","Compare 3+ group means","Association between two categorical variables","Correlation between two numeric variables","Compare paired measurements"],0,False)
          if not ok:return
          df=self.model.dataframe
          if mode=="Compare two group means":
              group,measure=self.choose_group_and_measure("Two-group comparison")
              if not group:return
              levels=list(df[group].dropna().unique())
              if len(levels)<2:raise ValueError("The selected group column has fewer than two groups.")
              a=df.loc[df[group]==levels[0],measure].dropna(); b=df.loc[df[group]==levels[1],measure].dropna(); stat,p=stats.ttest_ind(a,b,equal_var=False)
              pooled=((a.var(ddof=1)+b.var(ddof=1))/2)**.5; d=(a.mean()-b.mean())/pooled if pooled else np.nan
              text=f"Welch independent-samples t-test\n{levels[0]}: n={len(a)}, mean={a.mean():.4f}\n{levels[1]}: n={len(b)}, mean={b.mean():.4f}\nt={stat:.4f}, p={p:.6g}, Cohen's d={d:.4f}\nInterpretation: {'statistically significant' if p<.05 else 'not statistically significant'} at alpha=.05."
          elif mode=="Compare 3+ group means":
              group,measure=self.choose_group_and_measure("ANOVA")
              levels=[x for x in df[group].dropna().unique()]; samples=[df.loc[df[group]==x,measure].dropna() for x in levels]; samples=[x for x in samples if len(x)>1]
              if len(samples)<3:raise ValueError("At least three groups with two observations each are required.")
              stat,p=stats.f_oneway(*samples); text=f"One-way ANOVA\nGroups tested: {len(samples)}\nF={stat:.4f}, p={p:.6g}\nInterpretation: {'evidence of a difference among group means' if p<.05 else 'no statistically significant evidence of different group means'} at alpha=.05."
          elif mode=="Association between two categorical variables":
              cats=list(df.select_dtypes(exclude=np.number).columns)
              if len(cats)<2:raise ValueError("Two categorical columns are required.")
              a,ok=QInputDialog.getItem(self,"Chi-square","First variable",cats,0,False)
              if not ok:return
              b,ok=QInputDialog.getItem(self,"Chi-square","Second variable",cats,1 if len(cats)>1 else 0,False)
              if not ok:return
              table=pd.crosstab(df[a],df[b]); chi,p,dof,expected=stats.chi2_contingency(table); n=table.values.sum(); phi2=chi/n; r,k=table.shape; v=(phi2/max(1,min(k-1,r-1)))**.5
              text=f"Chi-square test of independence\nChi-square={chi:.4f}, df={dof}, p={p:.6g}, Cramer's V={v:.4f}\nInterpretation: {'association detected' if p<.05 else 'no statistically significant association detected'} at alpha=.05."
          elif mode=="Correlation between two numeric variables":
              x,y=self.choose_two_numeric("Correlation")
              if not x:return
              clean=df[[x,y]].dropna(); r,p=stats.pearsonr(clean[x],clean[y]); rho,sp=stats.spearmanr(clean[x],clean[y]); text=f"Correlation analysis\nN={len(clean)}\nPearson r={r:.4f}, p={p:.6g}\nSpearman rho={rho:.4f}, p={sp:.6g}\nInterpretation: Pearson relationship is {'statistically significant' if p<.05 else 'not statistically significant'} at alpha=.05."
          else:
              x,y=self.choose_two_numeric("Paired t-test")
              if not x:return
              clean=df[[x,y]].dropna(); stat,p=stats.ttest_rel(clean[x],clean[y]); diff=clean[x]-clean[y]; d=diff.mean()/diff.std(ddof=1) if diff.std(ddof=1) else np.nan; text=f"Paired t-test\nN={len(clean)}\nMean difference={diff.mean():.4f}\nt={stat:.4f}, p={p:.6g}, paired Cohen's d={d:.4f}\nInterpretation: {'significant paired difference' if p<.05 else 'no statistically significant paired difference'} at alpha=.05."
          self.store_statistics_result(mode,text)
      except Exception as exc: QMessageBox.critical(self,"Statistical test failed",str(exc))
  def statistics_assumption_checks(self):
      try:
          from scipy import stats
          group,measure=self.choose_group_and_measure("Assumption checks")
          if not group:return
          rows=[]; samples=[]
          for level,part in self.model.dataframe.groupby(group,dropna=False):
              values=part[measure].dropna(); samples.append(values)
              if len(values)>=3:
                  w,p=stats.shapiro(values.sample(min(len(values),5000),random_state=42) if len(values)>5000 else values)
              else:w,p=np.nan,np.nan
              rows.append({"group":str(level),"n":len(values),"mean":values.mean(),"std":values.std(ddof=1),"shapiro_W":w,"normality_p":p})
          lev_stat,lev_p=stats.levene(*[x for x in samples if len(x)>1],center="median") if len([x for x in samples if len(x)>1])>=2 else (np.nan,np.nan)
          table=pd.DataFrame(rows).round(5); text=f"Assumption checks for {measure} by {group}\nLevene robust variance test: statistic={lev_stat:.4f}, p={lev_p:.6g}\nVariance interpretation: {'variance heterogeneity indicated' if lev_p<.05 else 'no significant evidence of unequal variances'} at alpha=.05.\nShapiro-Wilk is reported by group; for large samples also inspect plots and practical deviations."
          self.store_statistics_result("Assumption checks",text,table)
      except Exception as exc: QMessageBox.critical(self,"Assumption checks failed",str(exc))
  def statistics_effect_size(self):
      try:
          group,measure=self.choose_group_and_measure("Effect size")
          if not group:return
          levels=list(self.model.dataframe[group].dropna().unique())
          if len(levels)<2:raise ValueError("At least two groups are required.")
          a=self.model.dataframe.loc[self.model.dataframe[group]==levels[0],measure].dropna(); b=self.model.dataframe.loc[self.model.dataframe[group]==levels[1],measure].dropna(); pooled=np.sqrt(((len(a)-1)*a.var(ddof=1)+(len(b)-1)*b.var(ddof=1))/max(1,len(a)+len(b)-2)); d=(a.mean()-b.mean())/pooled if pooled else np.nan
          magnitude="small" if abs(d)<.5 else "medium" if abs(d)<.8 else "large"
          text=f"Effect size\nGroups: {levels[0]} vs {levels[1]}\nCohen's d={d:.4f}\nPractical magnitude using conventional thresholds: {magnitude}.\nThresholds are descriptive conventions, not universal decision rules."
          self.store_statistics_result("Effect size",text)
      except Exception as exc: QMessageBox.critical(self,"Effect size failed",str(exc))
  def statistics_confidence_interval(self):
      try:
          from scipy import stats
          col=self.choose_column("Confidence interval",True)
          if not col:return
          values=self.model.dataframe[col].dropna(); n=len(values)
          if n<2:raise ValueError("At least two observations are required.")
          mean=values.mean(); se=stats.sem(values); lo,hi=stats.t.interval(.95,n-1,loc=mean,scale=se)
          text=f"95% confidence interval for the mean of {col}\nN={n}\nMean={mean:.6f}\nStandard error={se:.6f}\n95% CI=[{lo:.6f}, {hi:.6f}]\nInterpretation: under the t-based model assumptions, this interval estimates the population mean with 95% confidence coverage over repeated samples."
          self.store_statistics_result("Confidence interval",text)
      except Exception as exc: QMessageBox.critical(self,"Confidence interval failed",str(exc))
  def statistics_regression_diagnostics(self):
      try:
          from scipy import stats
          x,y=self.choose_two_numeric("Regression diagnostics")
          if not x:return
          clean=self.model.dataframe[[x,y]].dropna(); fit=stats.linregress(clean[x],clean[y]); pred=fit.intercept+fit.slope*clean[x]; resid=clean[y]-pred
          _,normal_p=stats.shapiro(resid.sample(min(len(resid),5000),random_state=42) if len(resid)>5000 else resid) if len(resid)>=3 else (np.nan,np.nan)
          rmse=float(np.sqrt(np.mean(resid**2))); mae=float(np.mean(np.abs(resid)))
          table=pd.DataFrame({x:clean[x],y:clean[y],"predicted":pred,"residual":resid}).head(200).round(5)
          text=f"Linear regression diagnostics\nModel: {y} = {fit.intercept:.5f} + {fit.slope:.5f} × {x}\nN={len(clean)}\nR-squared={fit.rvalue**2:.5f}\nRMSE={rmse:.5f}\nMAE={mae:.5f}\nSlope p={fit.pvalue:.6g}\nResidual Shapiro-Wilk p={normal_p:.6g}\nInterpretation: {'slope is statistically significant' if fit.pvalue<.05 else 'slope is not statistically significant'} at alpha=.05. Residual normality {'shows evidence of departure' if normal_p<.05 else 'does not show significant departure'} by Shapiro-Wilk."
          self.store_statistics_result("Regression diagnostics",text,table)
      except Exception as exc: QMessageBox.critical(self,"Regression diagnostics failed",str(exc))
  def statistics_report(self):
      if not self.statistics_results: QMessageBox.information(self,"Statistics report","Run a Statistics Studio analysis first."); return
      lines=["STATISTICS STUDIO REPORT","="*70,f"Dataset: {self.active_dataset}",""]
      for name,result in self.statistics_results.items(): lines.extend([name.upper(),"-"*len(name),result.get("text",""),""])
      self.console.setPlainText("\n".join(lines)); self.tabs.setCurrentWidget(self.python_page)
  def map_pack_fields(self, pack, roles):
      df = self.model.dataframe
      mapping = {}
      for role, numeric in roles:
          candidates = list(df.select_dtypes(include=np.number).columns) if numeric else list(df.columns)
          options = ["Skip"] + [str(c) for c in candidates]
          value, ok = QInputDialog.getItem(self, f"{pack} field mapping", role, options, 0, False)
          if not ok: return None
          mapping[role] = None if value == "Skip" else value
      return mapping
  def save_pack_result(self, pack, result, narrative, metrics=None):
      name = self.unique_dataset_name(pack.lower().replace(" ", "_") + "_analysis")
      self.datasets[name] = result.copy(); self.active_dataset = name; self.model.set_dataframe(result)
      self.analytics_pack_results[pack] = {"dataset": name, "narrative": narrative, "metrics": metrics or []}
      self.refresh_dataset_list(select=name); self.refresh_status(); self.console.setPlainText(narrative + "\n\n" + result.head(50).to_string(index=False)); self.tabs.setCurrentWidget(self.python_page)
  def run_hr_pack(self):
      roles = [("Employee ID", False), ("Department", False), ("Join date", False), ("Exit date", False), ("Salary", True), ("Status", False)]
      m = self.map_pack_fields("HR / Workforce", roles)
      if not m: return
      src = self.model.dataframe.copy(); rows=[]; now=pd.Timestamp.today().normalize()
      if m["Join date"]: src["_join_date"] = pd.to_datetime(src[m["Join date"]], errors="coerce"); src["tenure_years"] = ((now-src["_join_date"]).dt.days/365.25).round(2)
      if m["Exit date"]: src["_exit_date"] = pd.to_datetime(src[m["Exit date"]], errors="coerce")
      dim = m["Department"] or m["Status"]
      if dim:
          grouped = src.groupby(dim, dropna=False)
          result = grouped.size().rename("records").reset_index()
          if m["Employee ID"]: result["headcount"] = grouped[m["Employee ID"]].nunique().values
          if m["Salary"]: result["avg_salary"] = grouped[m["Salary"]].mean().round(2).values; result["total_salary"] = grouped[m["Salary"]].sum().round(2).values
          if "tenure_years" in src: result["avg_tenure_years"] = grouped["tenure_years"].mean().round(2).values
      else: result = pd.DataFrame({"records":[len(src)]})
      headcount = src[m["Employee ID"]].nunique() if m["Employee ID"] else len(src)
      exits = int(src["_exit_date"].notna().sum()) if "_exit_date" in src else 0
      attrition = exits / max(1, headcount + exits)
      narrative = f"HR / Workforce analysis\nCurrent records/headcount basis: {headcount:,}\nRecorded exits: {exits:,}\nIndicative attrition ratio: {attrition:.1%}"
      if m["Salary"]: narrative += f"\nAverage salary: {src[m['Salary']].mean():,.2f}\nTotal salary/payroll: {src[m['Salary']].sum():,.2f}"
      self.save_pack_result("HR Workforce", result, narrative, [{"name":"Headcount","value":headcount},{"name":"Attrition","value":attrition}])
  def run_finance_pack(self):
      roles=[("Period",False),("Category",False),("Budget",True),("Actual",True),("Revenue",True),("Cost",True)]
      m=self.map_pack_fields("Finance",roles)
      if not m:return
      src=self.model.dataframe.copy(); result=src.copy()
      if m["Budget"] and m["Actual"]: result["variance"]=result[m["Actual"]]-result[m["Budget"]]; result["variance_pct"]=(result["variance"]/result[m["Budget"]].replace(0,np.nan)*100).round(2)
      if m["Revenue"] and m["Cost"]: result["profit"]=result[m["Revenue"]]-result[m["Cost"]]; result["margin_pct"]=(result["profit"]/result[m["Revenue"]].replace(0,np.nan)*100).round(2)
      dimension=m["Category"] or m["Period"]
      numeric=[c for c in [m["Budget"],m["Actual"],m["Revenue"],m["Cost"],"variance","profit"] if c and c in result.columns]
      summary=result.groupby(dimension,dropna=False)[numeric].sum().reset_index() if dimension and numeric else result[numeric].describe().transpose().reset_index()
      actual=result[m["Actual"]].sum() if m["Actual"] else np.nan; budget=result[m["Budget"]].sum() if m["Budget"] else np.nan; revenue=result[m["Revenue"]].sum() if m["Revenue"] else np.nan; cost=result[m["Cost"]].sum() if m["Cost"] else np.nan
      narrative="Finance analysis"
      if m["Budget"] and m["Actual"]: narrative+=f"\nBudget: {budget:,.2f}\nActual: {actual:,.2f}\nVariance: {actual-budget:,.2f}"
      if m["Revenue"] and m["Cost"]: narrative+=f"\nRevenue: {revenue:,.2f}\nCost: {cost:,.2f}\nProfit: {revenue-cost:,.2f}\nMargin: {(revenue-cost)/max(abs(revenue),1):.1%}"
      self.save_pack_result("Finance",summary,narrative)
  def run_sales_pack(self):
      roles=[("Date",False),("Customer",False),("Product",False),("Region",False),("Revenue",True),("Quantity",True)]
      m=self.map_pack_fields("Sales",roles)
      if not m:return
      src=self.model.dataframe.copy(); dim=m["Product"] or m["Region"] or m["Customer"]
      measures=[c for c in [m["Revenue"],m["Quantity"]] if c]
      result=src.groupby(dim,dropna=False)[measures].agg(["count","sum","mean"]).reset_index() if dim and measures else src.head(1000)
      result.columns=["_".join(str(x) for x in c if x).strip("_") if isinstance(c,tuple) else str(c) for c in result.columns]
      narrative="Sales analysis"
      if m["Revenue"]: narrative+=f"\nTotal revenue: {src[m['Revenue']].sum():,.2f}\nAverage transaction revenue: {src[m['Revenue']].mean():,.2f}"
      if m["Customer"]: narrative+=f"\nDistinct customers: {src[m['Customer']].nunique():,}"
      if m["Product"]: narrative+=f"\nDistinct products: {src[m['Product']].nunique():,}"
      self.save_pack_result("Sales",result,narrative)
  def run_operations_pack(self):
      roles=[("Date/time",False),("Process / machine",False),("Output",True),("Defects",True),("Cycle time",True),("Capacity",True),("SLA met",False)]
      m=self.map_pack_fields("Operations",roles)
      if not m:return
      src=self.model.dataframe.copy(); dim=m["Process / machine"]
      if m["Output"] and m["Capacity"]: src["utilization_pct"]=(src[m["Output"]]/src[m["Capacity"]].replace(0,np.nan)*100).round(2)
      if m["Defects"] and m["Output"]: src["defect_rate_pct"]=(src[m["Defects"]]/src[m["Output"]].replace(0,np.nan)*100).round(2)
      nums=[c for c in [m["Output"],m["Defects"],m["Cycle time"],m["Capacity"],"utilization_pct","defect_rate_pct"] if c and c in src.columns]
      result=src.groupby(dim,dropna=False)[nums].agg(["count","sum","mean"]).reset_index() if dim and nums else src[nums].describe().transpose().reset_index()
      result.columns=["_".join(str(x) for x in c if x).strip("_") if isinstance(c,tuple) else str(c) for c in result.columns]
      narrative="Operations analysis"
      if m["Output"]: narrative+=f"\nTotal output: {src[m['Output']].sum():,.2f}"
      if "utilization_pct" in src: narrative+=f"\nAverage utilization: {src['utilization_pct'].mean():.1f}%"
      if "defect_rate_pct" in src: narrative+=f"\nAverage defect rate: {src['defect_rate_pct'].mean():.2f}%"
      if m["Cycle time"]: narrative+=f"\nAverage cycle time: {src[m['Cycle time']].mean():,.2f}"
      self.save_pack_result("Operations",result,narrative)
  def open_pack_field_mapper(self):
      QMessageBox.information(self,"Pack field mapper","Choose any analytics pack. The mapping dialog lets you assign your dataset columns to standard business roles. Select Skip for unavailable fields.")
  def create_pack_dashboard(self):
      if not self.analytics_pack_results: QMessageBox.information(self,"Pack dashboard","Run an analytics pack first."); return
      pack,ok=QInputDialog.getItem(self,"Pack dashboard","Pack",list(self.analytics_pack_results),0,False)
      if not ok:return
      info=self.analytics_pack_results[pack]; dataset=info["dataset"]
      if dataset not in self.datasets:return
      name=f"{pack} Dashboard"; df=self.datasets[dataset]; nums=list(df.select_dtypes(include=np.number).columns); cats=list(df.select_dtypes(exclude=np.number).columns)
      dash={"name":name,"dataset":dataset,"tiles":[],"slicers":[],"schema_version":2}
      for col in nums[:3]: dash["tiles"].append({"id":self.next_dashboard_tile_id(),"type":"kpi","title":f"Total {col}","measure":f"SUM::{col}","width":1,"height":1,"enabled":True})
      if cats and nums: dash["tiles"].append({"id":self.next_dashboard_tile_id(),"type":"chart","chart":"bar","title":f"{nums[0]} by {cats[0]}","x":cats[0],"y":nums[0],"width":2,"height":2,"enabled":True}); dash["slicers"].append({"column":cats[0],"value":None})
      dash["tiles"].append({"id":self.next_dashboard_tile_id(),"type":"table","title":"Analysis details","columns":list(df.columns)[:6],"width":3,"height":2,"enabled":True})
      self.dashboard_definitions[name]=dash; self.active_dashboard=name; self.render_dashboard_v2(); self.tabs.setCurrentWidget(next(self.tabs.widget(i) for i in range(self.tabs.count()) if self.tabs.tabText(i)=="Dashboard"))
  def next_dashboard_tile_id(self):
      self.dashboard_tile_counter += 1
      return f"tile_{self.dashboard_tile_counter:04d}"
  def ensure_dashboard_v2(self):
      if self.active_dashboard in self.dashboard_definitions:
          return self.dashboard_definitions[self.active_dashboard]
      self.new_dashboard_v2()
      return self.dashboard_definitions.get(self.active_dashboard)
  def new_dashboard_v2(self):
      name, ok = QInputDialog.getText(self, "New dashboard", "Dashboard name", text="Executive Dashboard")
      if not ok or not name.strip(): return
      name = name.strip()
      if name in self.dashboard_definitions and QMessageBox.question(self, "Dashboard exists", f"Replace '{name}'?") != QMessageBox.Yes: return
      self.dashboard_definitions[name] = {"name": name, "dataset": self.active_dataset, "tiles": [], "slicers": [], "schema_version": 2}
      self.active_dashboard = name
      self.render_dashboard_v2()
  def add_dashboard_kpi_tile(self):
      dash = self.ensure_dashboard_v2()
      if not dash: return
      options = [m["name"] for m in self.semantic_measures if m.get("dataset") == dash["dataset"]]
      numeric = list(self.datasets[dash["dataset"]].select_dtypes(include=np.number).columns)
      options += [f"SUM::{c}" for c in numeric] + [f"AVG::{c}" for c in numeric]
      if not options: QMessageBox.information(self, "KPI tile", "Define a semantic measure or use a numeric dataset first."); return
      measure, ok = QInputDialog.getItem(self, "KPI tile", "Measure", options, 0, False)
      if not ok: return
      title, ok = QInputDialog.getText(self, "KPI tile", "Title", text=measure.replace("::", " "))
      if not ok: return
      dash["tiles"].append({"id": self.next_dashboard_tile_id(), "type": "kpi", "title": title, "measure": measure, "width": 1, "height": 1, "enabled": True})
      self.render_dashboard_v2()
  def add_dashboard_chart_tile(self):
      dash = self.ensure_dashboard_v2()
      if not dash: return
      df = self.datasets[dash["dataset"]]
      chart, ok = QInputDialog.getItem(self, "Chart tile", "Chart type", ["bar", "line", "scatter", "histogram", "box"], 0, False)
      if not ok: return
      x, ok = QInputDialog.getItem(self, "Chart tile", "X / category", list(df.columns), 0, False)
      if not ok: return
      nums = list(df.select_dtypes(include=np.number).columns)
      y = None
      if chart != "histogram":
          if not nums: QMessageBox.warning(self, "Chart", "No numeric columns available."); return
          y, ok = QInputDialog.getItem(self, "Chart tile", "Y measure", nums, 0, False)
          if not ok: return
      else: y = x
      title, ok = QInputDialog.getText(self, "Chart tile", "Title", text=f"{y} by {x}" if y != x else f"Distribution of {x}")
      if not ok: return
      dash["tiles"].append({"id": self.next_dashboard_tile_id(), "type": "chart", "chart": chart, "title": title, "x": x, "y": y, "width": 2, "height": 2, "enabled": True})
      self.render_dashboard_v2()
  def add_dashboard_table_tile(self):
      dash = self.ensure_dashboard_v2()
      if not dash: return
      df = self.datasets[dash["dataset"]]
      text, ok = QInputDialog.getText(self, "Table tile", "Columns (comma separated)", text=", ".join(map(str, list(df.columns)[:5])))
      if not ok: return
      cols = [c.strip() for c in text.split(",") if c.strip() in df.columns]
      if not cols: QMessageBox.warning(self, "Table", "No valid columns selected."); return
      dash["tiles"].append({"id": self.next_dashboard_tile_id(), "type": "table", "title": "Details", "columns": cols, "width": 3, "height": 2, "enabled": True})
      self.render_dashboard_v2()
  def dashboard_context_df(self, dash):
      df = self.datasets.get(dash.get("dataset"), self.model.dataframe).copy()
      for slicer in dash.get("slicers", []):
          value = slicer.get("value")
          if value not in (None, "", "All") and slicer.get("column") in df.columns:
              df = df[df[slicer["column"]].astype(str) == str(value)]
      if self.dashboard_cross_filter:
          c, v = self.dashboard_cross_filter
          if c in df.columns: df = df[df[c].astype(str) == str(v)]
      return df
  def evaluate_dashboard_kpi(self, dash, measure, df):
      if measure.startswith("SUM::"):
          return df[measure.split("::",1)[1]].sum()
      if measure.startswith("AVG::"):
          return df[measure.split("::",1)[1]].mean()
      match = next((m for m in self.semantic_measures if m["name"] == measure), None)
      return self.calculate_semantic_measure(match, df) if match else None
  def render_dashboard_v2(self):
      if not hasattr(self, "dashboard_tiles_layout"): return
      while self.dashboard_tiles_layout.count():
          item = self.dashboard_tiles_layout.takeAt(0); w = item.widget()
          if w: w.deleteLater()
      if self.active_dashboard not in self.dashboard_definitions:
          label = QLabel("Create a Dashboard Studio dashboard to compose multiple visuals."); label.setObjectName("muted"); self.dashboard_tiles_layout.addWidget(label); return
      dash = self.dashboard_definitions[self.active_dashboard]; df = self.dashboard_context_df(dash)
      title = QLabel(f"{dash['name']}  |  {len(df):,} rows in context"); title.setObjectName("pageTitle"); self.dashboard_tiles_layout.addWidget(title)
      # slicers
      slicer_row = QHBoxLayout()
      for slicer in dash.get("slicers", []):
          box = QComboBox(); box.addItem("All"); col = slicer["column"]
          if col in self.datasets[dash["dataset"]].columns: box.addItems([str(x) for x in self.datasets[dash["dataset"]][col].dropna().unique()[:250]])
          if slicer.get("value"): box.setCurrentText(str(slicer["value"]))
          def change(v, s=slicer): s["value"] = None if v == "All" else v; self.render_dashboard_v2()
          box.currentTextChanged.connect(change); slicer_row.addWidget(QLabel(col)); slicer_row.addWidget(box)
      if dash.get("slicers"): self.dashboard_tiles_layout.addLayout(slicer_row)
      # tiles, two visual cards per row by default
      row = QHBoxLayout(); count = 0
      for tile in [t for t in dash["tiles"] if t.get("enabled", True)]:
          frame = QFrame(); frame.setObjectName("toolCard"); fl = QVBoxLayout(frame); heading = QLabel(tile.get("title", tile["type"])); heading.setObjectName("toolTitle"); fl.addWidget(heading)
          if tile["type"] == "kpi":
              value = self.evaluate_dashboard_kpi(dash, tile["measure"], df); lab = QLabel(f"{value:,.2f}" if isinstance(value,(int,float,np.number)) else str(value)); lab.setStyleSheet("font-size:28px;font-weight:700;color:#1d4ed8"); fl.addWidget(lab)
          elif tile["type"] == "table":
              table = QTableView(); model = DataFrameModel(df[tile["columns"]].head(200)); table.setModel(model); table.horizontalHeader().setStretchLastSection(True); fl.addWidget(table)
          else:
              fig = Figure(figsize=(5,2.6), tight_layout=True); canvas = FigureCanvas(fig); ax = fig.add_subplot(111); x,y,kind = tile["x"],tile["y"],tile["chart"]
              try:
                  if kind == "histogram": ax.hist(pd.to_numeric(df[y], errors="coerce").dropna(), bins=20, color="#2563eb")
                  elif kind == "bar":
                      grouped=df.groupby(x,dropna=False)[y].sum().sort_values(ascending=False).head(20); ax.bar(grouped.index.astype(str), grouped.values, color="#2563eb"); ax.tick_params(axis="x",rotation=45)
                  elif kind == "line": ax.plot(df[x], df[y], marker="o", linewidth=1)
                  elif kind == "scatter": ax.scatter(df[x], df[y], alpha=.7)
                  else: ax.boxplot(pd.to_numeric(df[y],errors="coerce").dropna(), labels=[y])
                  ax.grid(True,alpha=.2)
              except Exception as exc: ax.text(.5,.5,str(exc),ha="center",va="center")
              fl.addWidget(canvas)
          row.addWidget(frame); count += 1
          if count % 2 == 0: self.dashboard_tiles_layout.addLayout(row); row = QHBoxLayout()
      if count % 2: self.dashboard_tiles_layout.addLayout(row)
  def open_dashboard_layout_editor(self):
      dash = self.ensure_dashboard_v2()
      if not dash: return
      d=QDialog(self); d.setWindowTitle(f"Dashboard Layout - {dash['name']}"); d.resize(760,520); root=QVBoxLayout(d); tiles=QListWidget(); root.addWidget(tiles,1)
      def refresh():
          tiles.clear()
          for i,t in enumerate(dash["tiles"],1): tiles.addItem(f"{i}. [{'ON' if t.get('enabled',True) else 'OFF'}] {t['title']} | {t['type']} | {t['id']}")
      controls=QHBoxLayout(); root.addLayout(controls)
      def move(delta):
          i=tiles.currentRow(); j=i+delta
          if i>=0 and 0<=j<len(dash["tiles"]): dash["tiles"][i],dash["tiles"][j]=dash["tiles"][j],dash["tiles"][i]; refresh(); tiles.setCurrentRow(j)
      def toggle():
          i=tiles.currentRow()
          if i>=0: dash["tiles"][i]["enabled"]=not dash["tiles"][i].get("enabled",True); refresh(); tiles.setCurrentRow(i)
      def delete():
          i=tiles.currentRow()
          if i>=0: dash["tiles"].pop(i); refresh(); self.render_dashboard_v2()
      def slicer():
          col,ok=QInputDialog.getItem(d,"Add slicer","Column",list(self.datasets[dash["dataset"]].columns),0,False)
          if ok and col and not any(x["column"]==col for x in dash["slicers"]): dash["slicers"].append({"column":col,"value":None}); self.render_dashboard_v2()
      for label,fn in [("Up",lambda:move(-1)),("Down",lambda:move(1)),("Enable/Disable",toggle),("Delete",delete),("Add slicer",slicer)]: b=QPushButton(label); b.clicked.connect(fn); controls.addWidget(b)
      done=QDialogButtonBox(QDialogButtonBox.Close); done.rejected.connect(d.reject); root.addWidget(done); refresh(); d.exec(); self.render_dashboard_v2()
  def dashboard_import_export(self):
      action,ok=QInputDialog.getItem(self,"Dashboard package","Action",["Export active dashboard","Import dashboard"],0,False)
      if not ok:return
      if action.startswith("Export"):
          if self.active_dashboard not in self.dashboard_definitions:return
          path,_=QFileDialog.getSaveFileName(self,"Export dashboard",f"{self.active_dashboard}.dashboard.json","Dashboard JSON (*.json)")
          if path: Path(path).write_text(json.dumps(self.dashboard_definitions[self.active_dashboard],indent=2),encoding="utf-8")
      else:
          path,_=QFileDialog.getOpenFileName(self,"Import dashboard","","Dashboard JSON (*.json)")
          if not path:return
          try:
              dash=json.loads(Path(path).read_text(encoding="utf-8"))
              if not dash.get("name") or not isinstance(dash.get("tiles"),list): raise ValueError("Invalid dashboard schema")
              self.dashboard_definitions[dash["name"]]=dash; self.active_dashboard=dash["name"]; self.render_dashboard_v2()
          except Exception as exc: QMessageBox.critical(self,"Dashboard import failed",str(exc))
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
      self.dashboard_insights = QTextEdit(); self.dashboard_insights.setReadOnly(True); self.dashboard_insights.setMaximumHeight(120); outer.addWidget(self.dashboard_insights)
      self.dashboard_tiles_area = QScrollArea(); self.dashboard_tiles_area.setWidgetResizable(True)
      self.dashboard_tiles_host = QWidget(); self.dashboard_tiles_layout = QVBoxLayout(self.dashboard_tiles_host); self.dashboard_tiles_layout.setAlignment(Qt.AlignTop)
      self.dashboard_tiles_area.setWidget(self.dashboard_tiles_host); outer.addWidget(self.dashboard_tiles_area, 1)
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
      for definition in self.semantic_measures:
          if definition.get("dataset")==self.active_dataset:
              try:
                  value=self.calculate_semantic_measure(definition,df)
                  display=f"{value:,.2f}" if isinstance(value,(int,float,np.number)) else str(value); metrics.append((definition["name"],display))
              except Exception: pass
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
  def duckdb_relation_sql(self, source):
      path = source["path"].replace("'", "''")
      if source["format"] == "parquet":
          return f"read_parquet('{path}')"
      return f"read_csv_auto('{path}', sample_size=-1, ignore_errors=true)"
  def attach_large_file(self):
      path, _ = QFileDialog.getOpenFileName(self, "Attach large data source", "", "Large data (*.parquet *.csv)")
      if not path: return
      suffix = Path(path).suffix.lower()
      if suffix not in (".parquet", ".csv"):
          QMessageBox.warning(self, "Large data", "v0.10 lazy sources support CSV and Parquet."); return
      name, ok = QInputDialog.getText(self, "Attach source", "Source name", text=Path(path).stem)
      if not ok or not name.strip(): return
      name = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
      self.lazy_sources[name] = {"name": name, "path": str(Path(path).resolve()), "format": suffix.lstrip("."), "engine": "DuckDB", "materialized": False}
      self.engine_selector.setCurrentText("DuckDB")
      QMessageBox.information(self, "Source attached", f"Attached '{name}' lazily. The file is not loaded into pandas.")
  def choose_lazy_source(self, title="Lazy source"):
      if not self.lazy_sources:
          QMessageBox.information(self, title, "Attach a CSV or Parquet source first."); return None
      name, ok = QInputDialog.getItem(self, title, "Source", list(self.lazy_sources), 0, False)
      return name if ok else None
  def lazy_source_manager(self):
      if not self.lazy_sources:
          QMessageBox.information(self, "Lazy sources", "No lazy sources are attached."); return
      lines = ["LARGE DATA SOURCES", "=" * 70]
      for name, src in self.lazy_sources.items():
          try: size = Path(src["path"]).stat().st_size / 1024 / 1024
          except Exception: size = 0
          lines.append(f"{name} | {src['format'].upper()} | {size:,.1f} MB | {src['engine']} | {src['path']}")
      self.console.setPlainText("\n".join(lines)); self.tabs.setCurrentWidget(self.python_page)
  def query_lazy(self, name, query=None, limit=None):
      import duckdb
      src = self.lazy_sources[name]; relation = self.duckdb_relation_sql(src)
      sql = query or f"SELECT * FROM {relation}"
      if limit is not None: sql = f"SELECT * FROM ({sql}) AS q LIMIT {int(limit)}"
      con = duckdb.connect(database=":memory:")
      return con.execute(sql).fetchdf()
  def materialize_lazy_preview(self):
      name = self.choose_lazy_source("Materialize preview")
      if not name: return
      rows, ok = QInputDialog.getInt(self, "Preview rows", "Rows to materialize", min(self.preview_limit, 10000), 100, self.materialization_limit)
      if not ok: return
      try:
          df = self.query_lazy(name, limit=rows); target = self.unique_dataset_name(name + "_preview"); self.datasets[target] = df; self.active_dataset = target; self.model.set_dataframe(df); self.lazy_sources[name]["materialized"] = True; self.refresh_dataset_list(select=target); self.refresh_status(); self.tabs.setCurrentWidget(self.data_page())
      except Exception as exc: QMessageBox.critical(self, "Materialization failed", str(exc))
  def large_data_sql(self):
      name = self.choose_lazy_source("Large-data SQL")
      if not name: return
      relation = self.duckdb_relation_sql(self.lazy_sources[name])
      self.sql_editor.setPlainText(f"SELECT * FROM {relation} LIMIT {self.preview_limit};"); self.tabs.setCurrentWidget(self.sql_page)
  def profile_lazy_source(self):
      name = self.choose_lazy_source("Profile lazy source")
      if not name: return
      try:
          import duckdb
          relation = self.duckdb_relation_sql(self.lazy_sources[name]); con = duckdb.connect(database=":memory:")
          count = con.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()[0]
          desc = con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchdf()
          sample = con.execute(f"SELECT * FROM {relation} USING SAMPLE 100 ROWS").fetchdf()
          self.console.setPlainText(f"Source: {name}\nRows: {count:,}\n\nSCHEMA\n{desc.to_string(index=False)}\n\nSAMPLE\n{sample.to_string(index=False)}")
          self.tabs.setCurrentWidget(self.python_page)
      except Exception as exc: QMessageBox.critical(self, "Profile failed", str(exc))
  def pipeline_to_duckdb_sql(self, pipe, relation):
      current = f"SELECT * FROM {relation}"
      for step in pipe["steps"]:
          if not step.get("enabled", True): continue
          op, p = step["operation"], step.get("parameters", {})
          if op == "filter":
              c = '"' + p["column"].replace('"','""') + '"'; raw = str(p["value"]).replace("'", "''"); oper = p["operator"]
              if oper in ("==","!=",">",">=","<","<="):
                  sqlop = "=" if oper == "==" else oper; value = raw if re.fullmatch(r"-?\d+(\.\d+)?", raw) else f"'{raw}'"; current = f"SELECT * FROM ({current}) q WHERE {c} {sqlop} {value}"
              elif oper == "contains": current = f"SELECT * FROM ({current}) q WHERE CAST({c} AS VARCHAR) ILIKE '%{raw}%'"
              elif oper == "in":
                  vals = ",".join("'" + x.strip().replace("'", "''") + "'" for x in raw.split(",")); current = f"SELECT * FROM ({current}) q WHERE CAST({c} AS VARCHAR) IN ({vals})"
          elif op == "drop_duplicates": current = f"SELECT DISTINCT * FROM ({current}) q"
          elif op == "drop_missing_rows":
              if p.get("column"): c='"'+p["column"].replace('"','""')+'"'; current=f"SELECT * FROM ({current}) q WHERE {c} IS NOT NULL"
          elif op == "calculated_column":
              expr = p["expression"]; name='"'+p["name"].replace('"','""')+'"'; current=f"SELECT *, ({expr}) AS {name} FROM ({current}) q"
          else: raise ValueError(f"Step '{op}' is not yet engine-routable through DuckDB")
      return current
  def run_pipeline_engine_routed(self):
      if not self.active_pipeline or self.active_pipeline not in self.pipeline_definitions:
          QMessageBox.information(self, "Engine-routed pipeline", "Create or import a pipeline first."); return
      name = self.choose_lazy_source("Pipeline source")
      if not name: return
      try:
          pipe = self.pipeline_definitions[self.active_pipeline]; relation = self.duckdb_relation_sql(self.lazy_sources[name]); sql = self.pipeline_to_duckdb_sql(pipe, relation)
          import duckdb
          con = duckdb.connect(database=":memory:"); count = con.execute(f"SELECT COUNT(*) FROM ({sql}) z").fetchone()[0]
          preview = con.execute(f"SELECT * FROM ({sql}) z LIMIT {self.preview_limit}").fetchdf(); self.last_sql_result = preview; self.sql_result_model.set_dataframe(preview); self.sql_editor.setPlainText(sql); self.sql_status.setText(f"DuckDB pipeline output: {count:,} rows. Showing {len(preview):,} rows."); self.tabs.setCurrentWidget(self.sql_page)
      except Exception as exc: QMessageBox.critical(self, "Engine-routed pipeline failed", str(exc))
  def ensure_pipeline(self):
      if self.active_pipeline in self.pipeline_definitions:
          return self.pipeline_definitions[self.active_pipeline]
      self.new_pipeline()
      return self.pipeline_definitions.get(self.active_pipeline)
  def next_step_id(self):
      self.pipeline_step_counter += 1
      return f"step_{self.pipeline_step_counter:04d}"
  def new_pipeline(self):
      name, ok = QInputDialog.getText(self, "New pipeline", "Pipeline name", text="Data preparation pipeline")
      if not ok or not name.strip(): return
      name = name.strip()
      if name in self.pipeline_definitions and QMessageBox.question(self, "Replace pipeline", f"Replace '{name}'?") != QMessageBox.Yes: return
      self.pipeline_definitions[name] = {"name": name, "schema_version": 1, "source_dataset": self.active_dataset, "steps": []}
      self.active_pipeline = name
  def add_pipeline_step(self, operation, parameters, label=None):
      pipe = self.ensure_pipeline()
      if not pipe: return
      depends = [pipe["steps"][-1]["id"]] if pipe["steps"] else []
      step = {"id": self.next_step_id(), "operation": operation, "label": label or operation.replace("_", " ").title(), "enabled": True, "parameters": parameters, "depends_on": depends}
      pipe["steps"].append(step)
      self.workflow.addItem(f"P{len(pipe['steps'])}. {step['label']}")
  def add_cleaning_step(self):
      op, ok = QInputDialog.getItem(self, "Cleaning step", "Operation", ["fill_missing", "drop_duplicates", "infer_types", "clean_text", "drop_missing_rows"], 0, False)
      if not ok: return
      params = {}
      if op == "fill_missing":
          col, ok = QInputDialog.getItem(self, "Fill missing", "Column", list(self.model.dataframe.columns), 0, False)
          if not ok: return
          method, ok = QInputDialog.getItem(self, "Fill missing", "Method", ["median", "mean", "mode", "zero", "forward_fill", "backward_fill"], 0, False)
          if not ok: return
          params = {"column": col, "method": method}
      elif op == "drop_missing_rows":
          col, ok = QInputDialog.getItem(self, "Drop missing rows", "Column", ["Any column"] + list(self.model.dataframe.columns), 0, False)
          if not ok: return
          params = {"column": None if col == "Any column" else col}
      self.add_pipeline_step(op, params)
  def add_filter_step(self):
      df = self.model.dataframe
      col, ok = QInputDialog.getItem(self, "Filter step", "Column", list(df.columns), 0, False)
      if not ok: return
      op, ok = QInputDialog.getItem(self, "Filter step", "Operator", ["==", "!=", ">", ">=", "<", "<=", "contains", "in"], 0, False)
      if not ok: return
      default = str(df[col].dropna().iloc[0]) if df[col].notna().any() else ""
      value, ok = QInputDialog.getText(self, "Filter step", "Value; comma-separated for 'in'", text=default)
      if ok: self.add_pipeline_step("filter", {"column": col, "operator": op, "value": value}, f"Filter {col} {op} {value}")
  def add_calculated_pipeline_step(self):
      name, ok = QInputDialog.getText(self, "Calculated step", "New column", text="calculated_value")
      if not ok or not name.strip(): return
      example = "output / pressure" if {"output", "pressure"}.issubset(self.model.dataframe.columns) else ""
      expr, ok = QInputDialog.getText(self, "Calculated step", "pandas expression", text=example)
      if ok and expr.strip(): self.add_pipeline_step("calculated_column", {"name": name.strip(), "expression": expr}, f"Calculate {name.strip()}")
  def execute_pipeline_step(self, df, step):
      if not step.get("enabled", True): return df.copy()
      out = df.copy(); op = step["operation"]; p = step.get("parameters", {})
      if op == "fill_missing":
          c, method = p["column"], p["method"]
          if method == "median": out[c] = out[c].fillna(out[c].median())
          elif method == "mean": out[c] = out[c].fillna(out[c].mean())
          elif method == "mode":
              mode = out[c].mode(); out[c] = out[c].fillna(mode.iloc[0] if not mode.empty else "")
          elif method == "zero": out[c] = out[c].fillna(0)
          elif method == "forward_fill": out[c] = out[c].ffill()
          else: out[c] = out[c].bfill()
      elif op == "drop_duplicates": out = out.drop_duplicates().reset_index(drop=True)
      elif op == "infer_types":
          for c in out.columns:
              if out[c].dtype == object:
                  v = pd.to_numeric(out[c], errors="coerce")
                  if v.notna().sum() >= max(1, int(len(out) * .8)): out[c] = v
      elif op == "clean_text":
          for c in out.select_dtypes(include="object"): out[c] = out[c].astype(str).str.strip().replace("nan", np.nan)
      elif op == "drop_missing_rows": out = out.dropna(subset=[p["column"]] if p.get("column") else None).reset_index(drop=True)
      elif op == "calculated_column": validate_dataframe_expression(p["expression"], list(out.columns)); out[p["name"]] = out.eval(p["expression"], engine="python")
      elif op == "filter":
          c, oper, raw = p["column"], p["operator"], p["value"]; series = out[c]; value = raw
          if pd.api.types.is_numeric_dtype(series):
              try: value = float(raw)
              except ValueError: pass
          if oper == "==": mask = series == value
          elif oper == "!=": mask = series != value
          elif oper == ">": mask = series > value
          elif oper == ">=": mask = series >= value
          elif oper == "<": mask = series < value
          elif oper == "<=": mask = series <= value
          elif oper == "contains": mask = series.astype(str).str.contains(str(raw), case=False, na=False)
          else: mask = series.astype(str).isin([x.strip() for x in str(raw).split(",")])
          out = out.loc[mask].reset_index(drop=True)
      else: raise ValueError(f"Unsupported operation: {op}")
      return out
  def execute_pipeline(self, name=None, source_df=None, through_step=None):
      name = name or self.active_pipeline
      if name not in self.pipeline_definitions: raise ValueError("No active pipeline")
      pipe = self.pipeline_definitions[name]
      result = source_df.copy() if source_df is not None else self.datasets[pipe["source_dataset"]].copy()
      audit = []
      for i, step in enumerate(pipe["steps"]):
          before = result.shape; result = self.execute_pipeline_step(result, step) if step.get("enabled", True) else result.copy()
          audit.append({"step": step["id"], "label": step["label"], "enabled": step.get("enabled", True), "rows_before": before[0], "rows_after": len(result), "cols_before": before[1], "cols_after": len(result.columns)})
          if through_step is not None and i >= through_step: break
      return result, pd.DataFrame(audit)
  def open_pipeline_editor(self):
      pipe = self.ensure_pipeline()
      if not pipe: return
      d = QDialog(self); d.setWindowTitle(f"Pipeline Editor - {pipe['name']}"); d.resize(850, 600); root = QVBoxLayout(d)
      steps = QListWidget(); root.addWidget(steps, 1); audit = QTextEdit(); audit.setReadOnly(True); root.addWidget(audit)
      buttons = QHBoxLayout(); root.addLayout(buttons)
      def refresh():
          steps.clear()
          for i, st in enumerate(pipe["steps"], 1): steps.addItem(f"{i}. [{'ON' if st.get('enabled', True) else 'OFF'}] {st['label']} | {st['operation']}")
      def move(delta):
          i = steps.currentRow(); j = i + delta
          if i >= 0 and 0 <= j < len(pipe["steps"]): pipe["steps"][i], pipe["steps"][j] = pipe["steps"][j], pipe["steps"][i]; refresh(); steps.setCurrentRow(j)
      def toggle():
          i = steps.currentRow()
          if i >= 0: pipe["steps"][i]["enabled"] = not pipe["steps"][i].get("enabled", True); refresh(); steps.setCurrentRow(i)
      def delete():
          i = steps.currentRow()
          if i >= 0: pipe["steps"].pop(i); refresh()
      def preview(all_steps=False):
          try:
              upto = None if all_steps else steps.currentRow()
              result, log = self.execute_pipeline(pipe["name"], through_step=upto if upto >= 0 else None)
              audit.setPlainText(log.to_string(index=False) + "\n\nPREVIEW\n" + result.head(30).to_string())
          except Exception: audit.setPlainText(traceback.format_exc())
      def apply():
          try:
              result, log = self.execute_pipeline(pipe["name"], source_df=self.model.dataframe)
              self.apply_dataframe(result, f"Run pipeline {pipe['name']}"); audit.setPlainText(log.to_string(index=False))
          except Exception: audit.setPlainText(traceback.format_exc())
      for label, fn in [("Up", lambda: move(-1)), ("Down", lambda: move(1)), ("Enable/Disable", toggle), ("Delete", delete), ("Preview selected", lambda: preview(False)), ("Run all", lambda: preview(True)), ("Apply", apply)]:
          b = QPushButton(label); b.clicked.connect(fn); buttons.addWidget(b)
      refresh(); d.exec()
  def pipeline_import_export_dialog(self):
      action, ok = QInputDialog.getItem(self, "Pipeline JSON", "Action", ["Export active pipeline", "Import pipeline"], 0, False)
      if not ok: return
      if action.startswith("Export"):
          if self.active_pipeline not in self.pipeline_definitions: return
          path, _ = QFileDialog.getSaveFileName(self, "Export pipeline", f"{self.active_pipeline}.pipeline.json", "Pipeline JSON (*.json)")
          if path: Path(path).write_text(json.dumps(self.pipeline_definitions[self.active_pipeline], indent=2), encoding="utf-8")
      else:
          path, _ = QFileDialog.getOpenFileName(self, "Import pipeline", "", "Pipeline JSON (*.json)")
          if not path: return
          try:
              pipe = json.loads(Path(path).read_text(encoding="utf-8")); allowed = {"fill_missing", "drop_duplicates", "infer_types", "clean_text", "drop_missing_rows", "calculated_column", "filter"}
              if not pipe.get("name") or not isinstance(pipe.get("steps"), list): raise ValueError("Invalid pipeline schema")
              bad = [x.get("operation") for x in pipe["steps"] if x.get("operation") not in allowed]
              if bad: raise ValueError("Unsupported operations: " + ", ".join(map(str, bad)))
              self.pipeline_definitions[pipe["name"]] = pipe; self.active_pipeline = pipe["name"]
          except Exception as exc: QMessageBox.critical(self, "Import failed", str(exc))
  def relationship_manager(self):
      if len(self.datasets) < 2:
          QMessageBox.information(self, "Relationships", "Import at least two datasets first."); return
      dialog=QDialog(self); dialog.setWindowTitle("Relationship Manager"); dialog.resize(650,450); root=QVBoxLayout(dialog)
      form=QFormLayout(); left_ds=QComboBox(); right_ds=QComboBox(); left_ds.addItems(self.datasets); right_ds.addItems(self.datasets)
      left_col=QComboBox(); right_col=QComboBox(); cardinality=QComboBox(); cardinality.addItems(["many-to-one", "one-to-many", "one-to-one"])
      form.addRow("From dataset",left_ds); form.addRow("From column",left_col); form.addRow("To dataset",right_ds); form.addRow("To column",right_col); form.addRow("Cardinality",cardinality); root.addLayout(form)
      info=QTextEdit(); info.setReadOnly(True); root.addWidget(info)
      buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); root.addWidget(buttons)
      def refresh_cols():
          left_col.clear(); right_col.clear(); left_col.addItems([str(c) for c in self.datasets[left_ds.currentText()].columns]); right_col.addItems([str(c) for c in self.datasets[right_ds.currentText()].columns]); validate()
      def validate():
          if not left_col.currentText() or not right_col.currentText(): return
          l=self.datasets[left_ds.currentText()][left_col.currentText()]; r=self.datasets[right_ds.currentText()][right_col.currentText()]
          overlap=len(set(l.dropna().astype(str)) & set(r.dropna().astype(str))); info.setPlainText(f"From unique: {l.nunique():,} / {len(l):,}\nTo unique: {r.nunique():,} / {len(r):,}\nMatching distinct values: {overlap:,}\nFrom duplicates: {int(l.duplicated().sum()):,}\nTo duplicates: {int(r.duplicated().sum()):,}")
      def save():
          if left_ds.currentText()==right_ds.currentText(): QMessageBox.warning(dialog,"Relationship","Choose two different datasets."); return
          rel={"from_dataset":left_ds.currentText(),"from_column":left_col.currentText(),"to_dataset":right_ds.currentText(),"to_column":right_col.currentText(),"cardinality":cardinality.currentText(),"active":True}
          self.relationships=[x for x in self.relationships if not (x["from_dataset"]==rel["from_dataset"] and x["from_column"]==rel["from_column"] and x["to_dataset"]==rel["to_dataset"] and x["to_column"]==rel["to_column"])]; self.relationships.append(rel); dialog.accept()
      left_ds.currentTextChanged.connect(refresh_cols); right_ds.currentTextChanged.connect(refresh_cols); left_col.currentTextChanged.connect(validate); right_col.currentTextChanged.connect(validate); buttons.accepted.connect(save); buttons.rejected.connect(dialog.reject); refresh_cols(); dialog.exec()
  def suggest_relationships(self):
      suggestions=[]; names=list(self.datasets)
      for i,a in enumerate(names):
          for b in names[i+1:]:
              adf,bdf=self.datasets[a],self.datasets[b]
              for ca in adf.columns:
                  for cb in bdf.columns:
                      if str(ca).lower()!=str(cb).lower(): continue
                      av=adf[ca].dropna(); bv=bdf[cb].dropna();
                      if av.empty or bv.empty: continue
                      overlap=len(set(av.astype(str)) & set(bv.astype(str))); denom=max(1,min(av.nunique(),bv.nunique())); ratio=overlap/denom
                      if ratio>=.5:
                          card="one-to-one" if av.is_unique and bv.is_unique else "one-to-many" if av.is_unique else "many-to-one" if bv.is_unique else "many-to-many candidate"
                          suggestions.append({"from_dataset":a,"from_column":str(ca),"to_dataset":b,"to_column":str(cb),"cardinality":card,"overlap":round(ratio,3)})
      text="No likely relationships found." if not suggestions else "\n".join(f"{x['from_dataset']}.{x['from_column']} → {x['to_dataset']}.{x['to_column']} | {x['cardinality']} | overlap {x['overlap']:.0%}" for x in suggestions)
      self.console.setPlainText(text); self.tabs.setCurrentWidget(self.python_page)
      if suggestions:
          answer=QMessageBox.question(self,"Suggested relationships",f"Found {len(suggestions)} candidate relationships. Add valid one-to-one/one-to-many/many-to-one suggestions?")
          if answer==QMessageBox.Yes:
              for x in suggestions:
                  if "candidate" not in x["cardinality"]:
                      rel={k:x[k] for k in ["from_dataset","from_column","to_dataset","to_column","cardinality"]}; rel["active"]=True
                      if rel not in self.relationships:self.relationships.append(rel)
  def define_calculated_column(self):
      df=self.model.dataframe
      name,ok=QInputDialog.getText(self,"Calculated column","New column name",text="calculated_value")
      if not ok or not name.strip(): return
      example="output / pressure" if "output" in df.columns and "pressure" in df.columns else (str(df.select_dtypes(include=np.number).columns[0]) if len(df.select_dtypes(include=np.number).columns) else "")
      expr,ok=QInputDialog.getText(self,"Calculated column","pandas expression",text=example)
      if not ok or not expr.strip(): return
      try:
          result=df.copy(); validate_dataframe_expression(expr,list(result.columns)); result[name.strip()]=result.eval(expr,engine="python"); definition={"dataset":self.active_dataset,"name":name.strip(),"expression":expr}; self.calculated_columns=[x for x in self.calculated_columns if not (x["dataset"]==self.active_dataset and x["name"]==name.strip())]; self.calculated_columns.append(definition); self.apply_dataframe(result,f"Calculated column {name.strip()}")
      except Exception as exc: QMessageBox.critical(self,"Calculated column failed",str(exc))
  def define_semantic_measure(self):
      df=self.model.dataframe; numeric=list(df.select_dtypes(include=np.number).columns)
      if not numeric: QMessageBox.warning(self,"Measure","No numeric columns available."); return
      name,ok=QInputDialog.getText(self,"Semantic measure","Measure name",text=f"Total {numeric[0]}")
      if not ok or not name.strip(): return
      column,ok=QInputDialog.getItem(self,"Semantic measure","Column",numeric,0,False)
      if not ok:return
      aggregation,ok=QInputDialog.getItem(self,"Semantic measure","Aggregation",["sum","mean","median","min","max","count","nunique"],0,False)
      if not ok:return
      filter_col,ok=QInputDialog.getItem(self,"Semantic measure","Optional filter column",["No filter"]+[str(c) for c in df.columns],0,False)
      if not ok:return
      filter_value=None
      if filter_col!="No filter":
          values=[str(x) for x in df[filter_col].dropna().unique()[:500]]; filter_value,ok=QInputDialog.getItem(self,"Semantic measure","Filter value",values,0,False)
          if not ok:return
      definition={"name":name.strip(),"dataset":self.active_dataset,"column":column,"aggregation":aggregation,"filter_column":None if filter_col=="No filter" else filter_col,"filter_value":filter_value}
      self.semantic_measures=[x for x in self.semantic_measures if x["name"]!=definition["name"]]; self.semantic_measures.append(definition); QMessageBox.information(self,"Measure saved",f"Saved semantic measure '{definition['name']}'.")
  def calculate_semantic_measure(self, definition, context=None):
      df=(context.copy() if context is not None else self.datasets[definition["dataset"]].copy())
      fc=definition.get("filter_column"); fv=definition.get("filter_value")
      if fc and fc in df.columns: df=df[df[fc].astype(str)==str(fv)]
      s=df[definition["column"]]; agg=definition["aggregation"]; return s.nunique() if agg=="nunique" else getattr(s,agg)()
  def evaluate_measure_dialog(self):
      valid=[m for m in self.semantic_measures if m.get("dataset") in self.datasets]
      if not valid: QMessageBox.information(self,"Measures","Define a semantic measure first."); return
      name,ok=QInputDialog.getItem(self,"Evaluate measure","Measure",[m["name"] for m in valid],0,False)
      if not ok:return
      definition=next(m for m in valid if m["name"]==name); value=self.calculate_semantic_measure(definition); QMessageBox.information(self,"Measure result",f"{name}\n\n{value:,.4f}" if isinstance(value,(int,float,np.number)) else f"{name}\n\n{value}")
  def model_overview(self):
      lines=["SEMANTIC DATA MODEL","="*60,"","DATASETS"]
      for n,df in self.datasets.items(): lines.append(f"• {n}: {len(df):,} rows × {len(df.columns)} columns")
      lines.extend(["","RELATIONSHIPS"])
      lines.extend([f"• {r['from_dataset']}.{r['from_column']} → {r['to_dataset']}.{r['to_column']} ({r['cardinality']})" for r in self.relationships] or ["• None defined"])
      lines.extend(["","CALCULATED COLUMNS"]); lines.extend([f"• {x['dataset']}.{x['name']} = {x['expression']}" for x in self.calculated_columns] or ["• None defined"])
      lines.extend(["","MEASURES"]); lines.extend([f"• {m['name']}: {m['aggregation'].upper()}({m['dataset']}.{m['column']})" + (f" where {m['filter_column']} = {m['filter_value']}" if m.get('filter_column') else "") for m in self.semantic_measures] or ["• None defined"])
      self.console.setPlainText("\n".join(lines)); self.tabs.setCurrentWidget(self.python_page)
  def change_engine(self, name):
      self.compute_engine = name
      self.update_engine_badge()
  def recommended_engine(self):
      rows=len(self.model.dataframe)
      if self.compute_engine != "Auto": return self.compute_engine
      if rows < self.engine_threshold_rows: return "pandas"
      return "DuckDB"
  def update_engine_badge(self):
      if not hasattr(self,"engine_badge"): return
      actual=self.recommended_engine()
      label=f"Auto: {actual}" if self.compute_engine=="Auto" else actual
      self.engine_badge.setText(label)
  def get_spark(self):
      if self.spark_session is not None: return self.spark_session
      try:
          from pyspark.sql import SparkSession
          self.spark_session=SparkSession.builder.master("local[*]").appName("PythonDataWorkbench").config("spark.sql.execution.arrow.pyspark.enabled","true").getOrCreate()
          return self.spark_session
      except Exception as exc:
          raise RuntimeError("PySpark is optional and could not start. Install Java 17 and the optional PySpark package. Details: " + str(exc))
  def show_engine_status(self):
      df=self.model.dataframe; memory=int(df.memory_usage(deep=True).sum())
      text=f"Configured engine: {self.compute_engine}\nRecommended engine: {self.recommended_engine()}\nRows: {len(df):,}\nColumns: {len(df.columns)}\nIn-memory size: {memory/1024/1024:,.2f} MB\nAuto threshold: {self.engine_threshold_rows:,} rows\n\nDecision policy:\n- pandas: editable GUI work and small/medium analysis\n- DuckDB: SQL, Parquet and larger local analytics\n- PySpark: optional distributed processing when a Spark cluster is available"
      self.console.setPlainText(text); self.tabs.setCurrentWidget(self.python_page)
  def convert_to_spark(self):
      try:
          spark=self.get_spark(); sdf=spark.createDataFrame(self.model.dataframe); view=re.sub(r"[^A-Za-z0-9_]","_",self.active_dataset); sdf.createOrReplaceTempView(view); self.spark_frames = getattr(self, "spark_frames", {}); self.spark_frames[view]=sdf
          self.console.setPlainText(f"Created Spark DataFrame '{view}' with {sdf.count():,} rows and {len(sdf.columns)} columns.\nTemporary SQL view registered as {view}."); self.tabs.setCurrentWidget(self.python_page); self.engine_selector.setCurrentText("PySpark")
      except Exception as exc: QMessageBox.critical(self,"Spark conversion failed",str(exc))
  def run_spark_sql_dialog(self):
      try:
          spark=self.get_spark(); view=re.sub(r"[^A-Za-z0-9_]","_",self.active_dataset)
          if not hasattr(self,"spark_frames") or view not in self.spark_frames: self.convert_to_spark()
          query,ok=QInputDialog.getMultiLineText(self,"Spark SQL","SQL query",f'SELECT * FROM {view} LIMIT 100')
          if not ok:return
          result=spark.sql(query); limit=min(result.count(),100000); pdf=result.limit(limit).toPandas(); self.last_sql_result=pdf; self.sql_result_model.set_dataframe(pdf); self.sql_status.setText(f"Spark SQL returned {limit:,} preview rows. Save result to create a GUI dataset."); self.tabs.setCurrentWidget(self.sql_page)
      except Exception as exc: QMessageBox.critical(self,"Spark SQL failed",str(exc))
  def cache_spark_dataset(self):
      try:
          view=re.sub(r"[^A-Za-z0-9_]","_",self.active_dataset)
          if not hasattr(self,"spark_frames") or view not in self.spark_frames:self.convert_to_spark()
          frame=self.spark_frames[view].cache(); count=frame.count(); QMessageBox.information(self,"Spark cache",f"Cached {count:,} rows for '{view}'.")
      except Exception as exc: QMessageBox.critical(self,"Spark cache failed",str(exc))
  def explain_execution_plan(self):
      try:
          engine=self.recommended_engine()
          if engine=="PySpark":
              view=re.sub(r"[^A-Za-z0-9_]","_",self.active_dataset)
              if not hasattr(self,"spark_frames") or view not in self.spark_frames:self.convert_to_spark()
              plan=self.spark_frames[view]._jdf.queryExecution().toString()
          else:
              import duckdb
              con=duckdb.connect(database=":memory:"); con.register("active_data",self.model.dataframe); plan=con.execute("EXPLAIN SELECT * FROM active_data").fetchdf().to_string(index=False)
          self.console.setPlainText(plan); self.tabs.setCurrentWidget(self.python_page)
      except Exception as exc: QMessageBox.critical(self,"Explain plan failed",str(exc))
  def collect_spark_to_pandas(self):
      try:
          view=re.sub(r"[^A-Za-z0-9_]","_",self.active_dataset)
          if not hasattr(self,"spark_frames") or view not in self.spark_frames:raise ValueError("Convert the active dataset to Spark first.")
          count=self.spark_frames[view].count()
          if count>250000:
              answer=QMessageBox.question(self,"Large collect",f"Collecting {count:,} rows may use substantial memory. Collect the first 250,000 rows instead?")
              if answer!=QMessageBox.Yes:return
              result=self.spark_frames[view].limit(250000).toPandas()
          else:result=self.spark_frames[view].toPandas()
          self.apply_dataframe(result,"Collect Spark result to pandas"); self.engine_selector.setCurrentText("pandas")
      except Exception as exc: QMessageBox.critical(self,"Spark collect failed",str(exc))
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
  def track_recent_action(self, name):
      if not hasattr(self, "recent_actions"): self.recent_actions = []
      self.recent_actions = [name] + [x for x in self.recent_actions if x != name]
      self.recent_actions = self.recent_actions[:10]
  def show_getting_started(self):
      dialog=QDialog(self); dialog.setWindowTitle("Getting Started"); dialog.resize(620,470); root=QVBoxLayout(dialog)
      title=QLabel("Start with your goal"); title.setObjectName("pageTitle"); root.addWidget(title)
      intro=QLabel("Choose a path. SQL, Spark and Python remain under Advanced."); intro.setWordWrap(True); intro.setObjectName("muted"); root.addWidget(intro)
      journeys=[("Analyze a file","Import → profile → clean → chart",self.import_data),("Build a dashboard","Create KPIs and visuals",lambda:self.select_tool("New dashboard")),("Automate cleaning","Create a reusable pipeline",lambda:self.select_tool("New pipeline")),("Build a model","AutoML → compare → register",lambda:self.select_tool("New AutoML experiment")),("Use Python","Open active data as df",lambda:self.tabs.setCurrentWidget(self.python_page))]
      for label,desc,fn in journeys:
          row=QFrame(); row.setObjectName("toolCard"); layout=QHBoxLayout(row); text=QVBoxLayout(); a=QLabel(label); a.setObjectName("toolTitle"); b=QLabel(desc); b.setObjectName("muted"); text.addWidget(a); text.addWidget(b); button=QPushButton("Start"); button.clicked.connect(lambda checked=False,f=fn,d=dialog:(d.accept(),f())); layout.addLayout(text,1); layout.addWidget(button); root.addWidget(row)
      close=QDialogButtonBox(QDialogButtonBox.Close); close.rejected.connect(dialog.reject); root.addWidget(close); dialog.exec()
  def open_command_palette(self):
      actions=[("Diagnostics center",self.show_diagnostics_center),("Deployment observability",self.show_deployment_observability),("Rollback center",self.show_rollback_center),("Environment promotion",self.show_environment_promotion),("Export deployment automation",self.export_deployment_automation_dialog),("Release gate",self.show_release_gate),("Export release automation",self.export_release_automation_dialog),("CI/CD integration",self.show_cicd_integration),("Export CI/CD",self.export_cicd_bundle_dialog),("Contract test automation",self.show_contract_test_automation),("Export lineage",self.export_lineage_dialog),("Lineage visualization",self.show_lineage_visualization),("Data lineage",self.show_data_lineage),("Data contracts",self.show_data_contracts),("Validation rules",self.show_validation_rules),("Cleaning plan",self.show_cleaning_plan),("Data quality profile",self.show_data_quality_profile),("Workspace health",self.show_workspace_health),("Smart recommendations",self.show_smart_recommendations),("Customize workspace",self.customize_workspace),("Workspace overview",self.show_workspace_overview),("Recent actions",self.show_recent_actions),("Toggle focus mode",self.toggle_focus_mode),("Open data",self.import_data),("Save project",self.save_project),("Export data",self.export_data),("Data profile",self.show_profile),("Missing values",self.fill_missing),("Duplicates",self.remove_duplicates),("New pipeline",self.new_pipeline),("Pipeline editor",self.open_pipeline_editor),("KPI summary",self.kpi_summary),("Trend analysis",self.trend_analysis),("New dashboard",self.new_dashboard_v2),("Dashboard layout",self.open_dashboard_layout_editor),("New AutoML experiment",self.automl_new_experiment),("Run model leaderboard",self.automl_run_leaderboard),("Model registry",self.ml_model_registry),("Create baseline",self.monitor_create_baseline),("Create job",self.jobs_create),("Job manager",self.jobs_manager),("Create API deployment",self.api_create_deployment),("SQL workspace",lambda:self.tabs.setCurrentWidget(self.sql_page)),("Python Lab",lambda:self.tabs.setCurrentWidget(self.python_page))]
      favorites={"Open data","Data profile","Workspace overview","New pipeline","New dashboard","New AutoML experiment"}; dialog=QDialog(self); dialog.setWindowTitle("Quick Actions"); dialog.resize(560,500); root=QVBoxLayout(dialog); search=QLineEdit(); search.setPlaceholderText("Search actions... Ctrl+K"); root.addWidget(search); listing=QListWidget(); root.addWidget(listing,1)
      def refresh(text=""):
          listing.clear(); q=text.strip().lower(); ordered=sorted(actions,key=lambda x:(x[0] not in favorites,x[0]))
          for label,fn in ordered:
              if not q or q in label.lower():
                  item=QListWidgetItem(("★ " if label in favorites else "")+label); item.setData(Qt.UserRole,label); listing.addItem(item)
      def run():
          item=listing.currentItem()
          if not item:return
          label=item.data(Qt.UserRole); match=next(x for x in actions if x[0]==label); self.track_recent_action(label); dialog.accept(); match[1]()
      search.textChanged.connect(refresh); search.returnPressed.connect(run); listing.itemDoubleClicked.connect(lambda _:run()); refresh(); dialog.exec()
  def apply_ui_preferences(self):
      prefs=getattr(self,"ui_preferences",{})
      density=prefs.get("density","Comfortable")
      base="9pt" if density=="Compact" else "10pt" if density=="Comfortable" else "11pt"
      dark=prefs.get("theme")=="Dark"
      if dark:
          self.setStyleSheet(self.styleSheet()+f"\nQMainWindow, QWidget {{ background:#111827; color:#e5e7eb; font-size:{base}; }} QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QTableView, QListWidget {{ background:#172033; color:#e5e7eb; border-color:#334155; }}")
      else:
          self.setStyleSheet(self.styleSheet()+f"\nQMainWindow, QWidget {{ font-size:{base}; }}")
  def customize_workspace(self):
      dialog=QDialog(self);dialog.setWindowTitle("Customize Workspace");form=QFormLayout(dialog)
      theme=QComboBox();theme.addItems(["Light","Dark"]);theme.setCurrentText(self.ui_preferences.get("theme","Light"));form.addRow("Theme",theme)
      density=QComboBox();density.addItems(["Compact","Comfortable","Large"]);density.setCurrentText(self.ui_preferences.get("density","Comfortable"));form.addRow("Density",density)
      guidance=QCheckBox("Show guidance text");guidance.setChecked(self.ui_preferences.get("show_guidance",True));form.addRow("Guidance",guidance)
      buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);form.addRow(buttons)
      def save():
          self.ui_preferences={"theme":theme.currentText(),"density":density.currentText(),"show_guidance":guidance.isChecked()};self.apply_ui_preferences();dialog.accept();self.statusBar().showMessage("Workspace preferences applied",3000)
      buttons.accepted.connect(save);buttons.rejected.connect(dialog.reject);dialog.exec()
  def show_recent_actions(self):
      actions = getattr(self, "recent_actions", [])
      if not actions:
          QMessageBox.information(self, "Recent Actions", "No recent actions yet. Use Ctrl+K or choose an action from Home.")
          return
      choice, ok = QInputDialog.getItem(self, "Recent Actions", "Run again", actions, 0, False)
      if ok and choice:
          self.select_tool(choice)
  def show_workspace_overview(self):
      df = self.model.dataframe
      missing = int(df.isna().sum().sum()) if not df.empty else 0
      text = f"WORKSPACE OVERVIEW\n{'='*72}\nActive dataset: {self.active_dataset}\nRows: {len(df):,}\nColumns: {len(df.columns)}\nMissing cells: {missing:,}\nDatasets: {len(self.datasets)}\nPipelines: {len(self.pipeline_definitions)}\nDashboards: {len(self.dashboard_definitions)}\nJobs: {len(self.scheduled_jobs)}\nModel versions: {len(self.model_registry)}\n\nSuggested next step:\n"
      if missing: text += "Review Data > Missing values."
      elif not self.pipeline_definitions: text += "Create a Transform pipeline to make the work reproducible."
      elif not self.dashboard_definitions: text += "Create a Visualize dashboard for the cleaned output."
      else: text += "Your project has data, pipelines and dashboards. Use Automate to schedule repeatable work."
      self.console.setPlainText(text); self.tabs.setCurrentWidget(self.python_page)
  def toggle_focus_mode(self):
      self.ui_compact_mode = not getattr(self, "ui_compact_mode", False)
      if self.ui_compact_mode:
          self.showMaximized(); self.statusBar().showMessage("Focus mode enabled. Press Ctrl+K for actions.", 5000)
      else:
          self.showNormal(); self.statusBar().showMessage("Focus mode disabled.", 3000)
  def select_tool(self, name):
      handlers = {
          "Getting started": self.show_getting_started,
          "Recent actions": self.show_recent_actions,
          "Workspace overview": self.show_workspace_overview,
          "Toggle focus mode": self.toggle_focus_mode,
          "Customize workspace": self.customize_workspace,
          "Quick actions": self.open_command_palette,
            "Smart recommendations": self.show_smart_recommendations,
            "Workspace health": self.show_workspace_health,
            "Data quality profile": self.show_data_quality_profile,
            "Cleaning plan": self.show_cleaning_plan,
            "Validation rules": self.show_validation_rules,
            "Data contracts": self.show_data_contracts,
            "Data lineage": self.show_data_lineage,
            "Lineage visualization": self.show_lineage_visualization,
            "Contract test automation": self.show_contract_test_automation,
            "CI/CD integration": self.show_cicd_integration,
            "Release gate": self.show_release_gate,
            "Environment promotion": self.show_environment_promotion,
            "Deployment observability": self.show_deployment_observability,
            "Rollback center": self.show_rollback_center,
            "Diagnostics center": self.show_diagnostics_center,
            "Export deployment automation": self.export_deployment_automation_dialog,
            "Export release automation": self.export_release_automation_dialog,
            "Export CI/CD": self.export_cicd_bundle_dialog,
            "Export lineage": self.export_lineage_dialog,
          "Open data": self.import_data,
          "Dashboard summary": lambda: self.tabs.setCurrentWidget(next(self.tabs.widget(i) for i in range(self.tabs.count()) if self.tabs.tabText(i)=="Dashboard")),
          "Python Lab": lambda: self.tabs.setCurrentWidget(self.python_page),
          "Create job": self.jobs_create,
          "Run job now": self.jobs_run_now,
          "Job manager": self.jobs_manager,
          "Job history": self.jobs_history,
          "Export job runner": self.jobs_export_runner,
          "Scheduler report": self.jobs_report,
          "Create API deployment": self.api_create_deployment,
          "Export API package": self.api_export_package,
          "Test prediction API": self.api_test_prediction,
          "API batch scoring": self.api_batch_score,
          "API history": self.api_history_view,
          "Deployment report": self.api_deployment_report,
          "Create baseline": self.monitor_create_baseline,
          "Run drift check": self.monitor_run_drift,
          "Performance monitor": self.monitor_performance,
          "Drift dashboard": self.monitor_drift_dashboard,
          "Monitoring alerts": self.monitor_alerts_view,
          "Monitoring report": self.monitoring_report,
          "Confusion matrix": self.ml_confusion_matrix,
          "ROC and PR curves": self.ml_roc_pr_curves,
          "Calibration curve": self.ml_calibration_curve,
          "Learning curve": self.ml_learning_curve,
          "Residual diagnostics": self.ml_residual_diagnostics,
          "Model registry": self.ml_model_registry,
          "New AutoML experiment": self.automl_new_experiment,
          "Run model leaderboard": self.automl_run_leaderboard,
          "Evaluate holdout": self.automl_evaluate_holdout,
          "Tune best model": self.automl_tune_best,
          "Explain model": self.automl_explain,
          "Export model bundle": self.automl_export_bundle,
          "FFT / spectrum": self.engineering_fft,
          "Digital filter": self.engineering_filter,
          "Peak analysis": self.engineering_peaks,
          "Numerical solver": self.engineering_numerical_solver,
          "Curve fitting": self.engineering_curve_fit,
          "Reliability / SPC": self.engineering_reliability_spc,
          "Monte Carlo Pro": self.engineering_monte_carlo,
          "Parameter sweep": self.engineering_parameter_sweep,
          "Engineering report": self.engineering_report,
          "Guided test": self.guided_statistical_test,
          "Assumption checks": self.statistics_assumption_checks,
          "Effect size": self.statistics_effect_size,
          "Confidence interval": self.statistics_confidence_interval,
          "Regression diagnostics": self.statistics_regression_diagnostics,
          "Statistics report": self.statistics_report,
          "HR / Workforce Pack": self.run_hr_pack,
          "Finance Pack": self.run_finance_pack,
          "Sales Pack": self.run_sales_pack,
          "Operations Pack": self.run_operations_pack,
          "Pack field mapper": self.open_pack_field_mapper,
          "Pack dashboard": self.create_pack_dashboard,
          "New dashboard": self.new_dashboard_v2,
          "Add KPI tile": self.add_dashboard_kpi_tile,
          "Add chart tile": self.add_dashboard_chart_tile,
          "Add table tile": self.add_dashboard_table_tile,
          "Dashboard layout": self.open_dashboard_layout_editor,
          "Dashboard package": self.dashboard_import_export,
          "Attach large file": self.attach_large_file,
          "Lazy source manager": self.lazy_source_manager,
          "Materialize preview": self.materialize_lazy_preview,
          "Large-data SQL": self.large_data_sql,
          "Engine-routed pipeline": self.run_pipeline_engine_routed,
          "Source profile": self.profile_lazy_source,
          "New pipeline": self.new_pipeline,
          "Add cleaning step": self.add_cleaning_step,
          "Add filter step": self.add_filter_step,
          "Add calculated step": self.add_calculated_pipeline_step,
          "Pipeline editor": self.open_pipeline_editor,
          "Import / export pipeline": self.pipeline_import_export_dialog,
          "Relationship manager": self.relationship_manager,
          "Suggest relationships": self.suggest_relationships,
          "Calculated column": self.define_calculated_column,
          "Semantic measure": self.define_semantic_measure,
          "Evaluate measure": self.evaluate_measure_dialog,
          "Model overview": self.model_overview,
          "Engine status": self.show_engine_status,
          "Convert to Spark": self.convert_to_spark,
          "Spark SQL": self.run_spark_sql_dialog,
          "Cache dataset": self.cache_spark_dataset,
          "Explain plan": self.explain_execution_plan,
          "Return to pandas": self.collect_spark_to_pandas,
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
          self.track_recent_action(name)
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
      path, _ = QFileDialog.getSaveFileName(self, "Save project", "data_studio_project.pdw", "Data Studio Project (*.pdw)")
      if not path: return
      if not path.lower().endswith(".pdw"): path += ".pdw"
      try:
          self.datasets[self.active_dataset] = self.model.dataframe.copy()
          payload = {"version": "0.38", "active_dataset": self.active_dataset, "datasets": self.datasets, "pipeline_steps": self.pipeline_steps, "python_code": self.editor.toPlainText(), "trained_models": self.trained_models, "metric_definitions": self.metric_definitions, "dashboard_cards": self.dashboard_cards, "saved_pipelines": self.saved_pipelines, "kpi_definitions": self.kpi_definitions, "dashboard_filters": self.dashboard_filters, "compute_engine": self.compute_engine, "engine_threshold_rows": self.engine_threshold_rows, "relationships": self.relationships, "semantic_measures": self.semantic_measures, "calculated_columns": self.calculated_columns, "pipeline_definitions": self.pipeline_definitions, "active_pipeline": self.active_pipeline, "pipeline_step_counter": self.pipeline_step_counter, "lazy_sources": self.lazy_sources, "preview_limit": self.preview_limit, "materialization_limit": self.materialization_limit, "dashboard_definitions": self.dashboard_definitions, "active_dashboard": self.active_dashboard, "dashboard_tile_counter": self.dashboard_tile_counter, "analytics_pack_results": self.analytics_pack_results, "statistics_results": self.statistics_results, "engineering_results": self.engineering_results, "automl_experiments": self.automl_experiments, "active_experiment": self.active_experiment, "model_registry": self.model_registry, "prediction_history": self.prediction_history, "model_version_counter": self.model_version_counter, "monitoring_baselines": self.monitoring_baselines, "monitoring_runs": self.monitoring_runs, "monitoring_alerts": self.monitoring_alerts, "drift_thresholds": self.drift_thresholds, "api_deployments": self.api_deployments, "api_request_history": self.api_request_history, "scheduled_jobs": self.scheduled_jobs, "job_runs": self.job_runs, "job_counter": self.job_counter, "ui_preferences": self.ui_preferences, "recent_actions": self.recent_actions, "smart_recommendations": self.smart_recommendations, "contract_test_suites": self.contract_test_suites, "contract_test_runs": self.contract_test_runs, "cicd_profiles": self.cicd_profiles, "cicd_runs": self.cicd_runs, "release_gate_runs": self.release_gate_runs, "environments": self.environments, "promotion_history": self.promotion_history, "validation_rules": self.validation_rules, "data_contracts": self.data_contracts, "lineage_graph": self.lineage_graph, "cleaning_plans": self.cleaning_plans, "deployment_thresholds": self.deployment_thresholds, "deployment_observations": self.deployment_observations, "rollback_history": self.rollback_history, "diagnostic_events": self.diagnostic_events, "project_migration_history": self.project_migration_history}
          payload, migration_changes = migrate_project(payload)
          errors = validate_project(payload)
          if errors: raise ValueError("Invalid project state: " + "; ".join(errors))
          self.diagnostics.info("persistence","save_project","Project state validated and saved",path=str(path),migration_changes=migration_changes)
          with open(path, "wb") as handle: pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
          self.current_project_path = str(Path(path).resolve())
          self.statusBar().showMessage(f"Project saved: {path}", 5000)
      except Exception as exc: QMessageBox.critical(self, "Save project failed", str(exc))
  def open_project(self):
      path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Data Studio Project (*.pdw)")
      if not path: return
      try:
          with open(path, "rb") as handle: payload = pickle.load(handle)
          payload, migration_changes = migrate_project(payload)
          errors = validate_project(payload)
          if errors: raise ValueError("Invalid project state: " + "; ".join(errors))
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
          self.compute_engine = payload.get("compute_engine", "Auto")
          self.engine_threshold_rows = payload.get("engine_threshold_rows", 1000000)
          self.relationships = payload.get("relationships", [])
          self.semantic_measures = payload.get("semantic_measures", [])
          self.calculated_columns = payload.get("calculated_columns", [])
          self.pipeline_definitions = payload.get("pipeline_definitions", {})
          self.active_pipeline = payload.get("active_pipeline")
          self.pipeline_step_counter = payload.get("pipeline_step_counter", 0)
          self.lazy_sources = payload.get("lazy_sources", {})
          self.preview_limit = payload.get("preview_limit", 10000)
          self.materialization_limit = payload.get("materialization_limit", 500000)
          self.dashboard_definitions = payload.get("dashboard_definitions", {})
          self.active_dashboard = payload.get("active_dashboard")
          self.dashboard_tile_counter = payload.get("dashboard_tile_counter", 0)
          self.analytics_pack_results = payload.get("analytics_pack_results", {})
          self.statistics_results = payload.get("statistics_results", {})
          self.engineering_results = payload.get("engineering_results", {})
          self.automl_experiments = payload.get("automl_experiments", {})
          self.active_experiment = payload.get("active_experiment")
          self.model_registry = payload.get("model_registry", {})
          self.prediction_history = payload.get("prediction_history", [])
          self.model_version_counter = payload.get("model_version_counter", 0)
          self.monitoring_baselines = payload.get("monitoring_baselines", {})
          self.monitoring_runs = payload.get("monitoring_runs", [])
          self.monitoring_alerts = payload.get("monitoring_alerts", [])
          self.drift_thresholds = payload.get("drift_thresholds", {"warning": 0.10, "critical": 0.25})
          self.api_deployments = payload.get("api_deployments", {})
          self.api_request_history = payload.get("api_request_history", [])
          self.scheduled_jobs = payload.get("scheduled_jobs", {})
          self.job_runs = payload.get("job_runs", [])
          self.job_counter = payload.get("job_counter", 0)
          self.ui_preferences = payload.get("ui_preferences", self.ui_preferences)
          self.recent_actions = payload.get("recent_actions", [])
          self.smart_recommendations = payload.get("smart_recommendations", [])
          self.contract_test_suites = payload.get("contract_test_suites", {})
          self.contract_test_runs = payload.get("contract_test_runs", [])
          self.cicd_profiles = payload.get("cicd_profiles", {})
          self.cicd_runs = payload.get("cicd_runs", [])
          self.release_gate_runs = payload.get("release_gate_runs", [])
          self.environments = payload.get("environments", self.environments)
          self.promotion_history = payload.get("promotion_history", [])
          self.validation_rules = payload.get("validation_rules", {})
          self.data_contracts = payload.get("data_contracts", {})
          self.lineage_graph = payload.get("lineage_graph", {"nodes":[],"edges":[]})
          self.cleaning_plans = payload.get("cleaning_plans", {})
          self.deployment_thresholds = payload.get("deployment_thresholds", self.deployment_thresholds)
          self.deployment_observations = payload.get("deployment_observations", [])
          self.rollback_history = payload.get("rollback_history", [])
          self.diagnostic_events = payload.get("diagnostic_events", [])
          self.project_migration_history = payload.get("project_migration_history", []) + migration_changes
          self.diagnostics = DiagnosticLog(self.diagnostic_events)
          self.diagnostics.info("persistence","open_project","Project opened and migrated",path=str(path),changes=migration_changes)
          for env_name in ("DEV","TEST","PROD"):
              state=self.environments.setdefault(env_name,{"protected":env_name=="PROD"}); state.setdefault("current_release",None); state.setdefault("previous_release",None)
          self.current_project_path = str(Path(path).resolve())
          self.engine_selector.setCurrentText(self.compute_engine)
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
      self.update_engine_badge()
      self.status_label.setText(f"{len(df):,} rows  |  {len(df.columns)} columns  |  Quality {score}%")
  def apply_styles(self):
      self.setStyleSheet("""
          QMainWindow, QWidget { background: #f6f8fb; color: #182230; font-family: Segoe UI, Arial; font-size: 10pt; }
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
          #toolCard { background: white; border: 1px solid #dde3ea; border-radius: 12px; min-height: 90px; padding: 2px; }
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
  app.setApplicationName("Data Studio")
  window = Workbench()
  window.show()
  sys.exit(app.exec())
if __name__ == "__main__":
  main()
