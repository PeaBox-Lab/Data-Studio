"""Automation orchestrator for Data Studio v0.37.
Loads a .pdw project, executes saved or due jobs, applies dependency and retry
policies, records each run, and atomically persists changes without the GUI.
"""
from __future__ import annotations
import argparse
import json
import os
import pickle
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from data_studio_core_v0_37 import validate_dataframe_expression
import numpy as np
import pandas as pd
RUNTIME_VERSION = "0.37"
def utc_now() -> str:
  return datetime.now(timezone.utc).isoformat()
def parse_utc(value: str | None) -> datetime | None:
  if not value:
      return None
  parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
  if parsed.tzinfo is None:
      parsed = parsed.replace(tzinfo=timezone.utc)
  return parsed.astimezone(timezone.utc)
def next_schedule_time(schedule: str, anchor: datetime) -> datetime | None:
  schedule = str(schedule or "Manual")
  if schedule == "Hourly":
      return anchor + timedelta(hours=1)
  if schedule == "Daily":
      return anchor + timedelta(days=1)
  if schedule == "Weekly":
      return anchor + timedelta(days=7)
  if schedule == "Monthly":
      year = anchor.year + (1 if anchor.month == 12 else 0)
      month = 1 if anchor.month == 12 else anchor.month + 1
      month_after = month + 1
      year_after = year
      if month_after == 13:
          month_after = 1
          year_after += 1
      first_after = datetime(year_after, month_after, 1, tzinfo=timezone.utc)
      last_day = (first_after - timedelta(days=1)).day
      return anchor.replace(year=year, month=month, day=min(anchor.day, last_day))
  return None
def json_safe(value: Any) -> Any:
  if value is None or isinstance(value, (str, int, bool)):
      return value
  if isinstance(value, float):
      return value if np.isfinite(value) else None
  if isinstance(value, np.generic):
      return json_safe(value.item())
  if isinstance(value, Path):
      return str(value)
  if isinstance(value, dict):
      return {str(key): json_safe(item) for key, item in value.items()}
  if isinstance(value, (list, tuple, set)):
      return [json_safe(item) for item in value]
  return str(value)
def read_project(path: Path) -> dict[str, Any]:
  if not path.exists():
      raise FileNotFoundError(f"Project not found: {path}")
  with path.open("rb") as handle:
      payload = pickle.load(handle)
  if not isinstance(payload, dict):
      raise ValueError("Project payload must be a dictionary.")
  if not payload.get("datasets"):
      raise ValueError("Project contains no datasets.")
  return payload
def atomic_write_project(path: Path, payload: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary = path.with_suffix(path.suffix + ".tmp")
  with temporary.open("wb") as handle:
      pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
      handle.flush()
      os.fsync(handle.fileno())
  os.replace(temporary, path)
@contextmanager
def project_lock(project_path: Path, timeout: float = 30.0):
  lock_path = project_path.with_suffix(project_path.suffix + ".lock")
  deadline = time.monotonic() + max(0.0, timeout)
  while True:
      try:
          descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
          with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
              json.dump(
                  {"pid": os.getpid(), "created_utc": utc_now(), "project": str(project_path)},
                  handle,
                  indent=2,
              )
          break
      except FileExistsError:
          if time.monotonic() >= deadline:
              raise TimeoutError(f"Project is locked by another process: {lock_path}")
          time.sleep(0.25)
  try:
      yield
  finally:
      try:
          lock_path.unlink()
      except FileNotFoundError:
          pass
class HeadlessRuntime:
  def __init__(self, payload: dict[str, Any], project_path: Path):
      self.payload = payload
      self.project_path = project_path
      self.datasets = payload.setdefault("datasets", {})
      self.pipeline_definitions = payload.setdefault("pipeline_definitions", {})
      self.trained_models = payload.setdefault("trained_models", {})
      self.api_deployments = payload.setdefault("api_deployments", {})
      self.monitoring_baselines = payload.setdefault("monitoring_baselines", {})
      self.monitoring_runs = payload.setdefault("monitoring_runs", [])
      self.monitoring_alerts = payload.setdefault("monitoring_alerts", [])
      self.drift_thresholds = payload.setdefault(
          "drift_thresholds", {"warning": 0.10, "critical": 0.25}
      )
      self.scheduled_jobs = payload.setdefault("scheduled_jobs", {})
      self.job_runs = payload.setdefault("job_runs", [])
  def unique_dataset_name(self, base: str) -> str:
      name = base
      counter = 2
      while name in self.datasets:
          name = f"{base}_{counter}"
          counter += 1
      return name
  @staticmethod
  def execute_pipeline_step(frame: pd.DataFrame, step: dict[str, Any]) -> pd.DataFrame:
      if not step.get("enabled", True):
          return frame.copy()
      output = frame.copy()
      operation = step["operation"]
      parameters = step.get("parameters", {})
      if operation == "fill_missing":
          column = parameters["column"]
          method = parameters["method"]
          if method == "median":
              output[column] = output[column].fillna(output[column].median())
          elif method == "mean":
              output[column] = output[column].fillna(output[column].mean())
          elif method == "mode":
              mode = output[column].mode()
              output[column] = output[column].fillna(mode.iloc[0] if not mode.empty else "")
          elif method == "zero":
              output[column] = output[column].fillna(0)
          elif method == "forward_fill":
              output[column] = output[column].ffill()
          elif method == "backward_fill":
              output[column] = output[column].bfill()
          else:
              raise ValueError(f"Unsupported fill method: {method}")
      elif operation == "drop_duplicates":
          output = output.drop_duplicates().reset_index(drop=True)
      elif operation == "infer_types":
          for column in output.columns:
              if output[column].dtype == object:
                  converted = pd.to_numeric(output[column], errors="coerce")
                  if converted.notna().sum() >= max(1, int(len(output) * 0.8)):
                      output[column] = converted
      elif operation == "clean_text":
          for column in output.select_dtypes(include="object"):
              output[column] = output[column].astype(str).str.strip().replace("nan", np.nan)
      elif operation == "drop_missing_rows":
          subset = [parameters["column"]] if parameters.get("column") else None
          output = output.dropna(subset=subset).reset_index(drop=True)
      elif operation == "calculated_column":
          validate_dataframe_expression(parameters["expression"], list(output.columns))
          output[parameters["name"]] = output.eval(parameters["expression"], engine="python")
      elif operation == "filter":
          column = parameters["column"]
          operator = parameters["operator"]
          raw_value = parameters["value"]
          series = output[column]
          value: Any = raw_value
          if pd.api.types.is_numeric_dtype(series):
              try:
                  value = float(raw_value)
              except (TypeError, ValueError):
                  pass
          if operator == "==":
              mask = series == value
          elif operator == "!=":
              mask = series != value
          elif operator == ">":
              mask = series > value
          elif operator == ">=":
              mask = series >= value
          elif operator == "<":
              mask = series < value
          elif operator == "<=":
              mask = series <= value
          elif operator == "contains":
              mask = series.astype(str).str.contains(str(raw_value), case=False, na=False)
          elif operator == "in":
              accepted = [item.strip() for item in str(raw_value).split(",")]
              mask = series.astype(str).isin(accepted)
          else:
              raise ValueError(f"Unsupported filter operator: {operator}")
          output = output.loc[mask].reset_index(drop=True)
      else:
          raise ValueError(f"Unsupported pipeline operation: {operation}")
      return output
  def execute_pipeline(self, name: str) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
      if name not in self.pipeline_definitions:
          raise KeyError(f"Pipeline not found: {name}")
      pipeline = self.pipeline_definitions[name]
      source_name = pipeline["source_dataset"]
      if source_name not in self.datasets:
          raise KeyError(f"Pipeline source dataset not found: {source_name}")
      result = self.datasets[source_name].copy()
      audit: list[dict[str, Any]] = []
      for step in pipeline.get("steps", []):
          before = result.shape
          result = self.execute_pipeline_step(result, step)
          audit.append(
              {
                  "step": step.get("id"),
                  "label": step.get("label"),
                  "enabled": step.get("enabled", True),
                  "rows_before": before[0],
                  "rows_after": len(result),
                  "cols_before": before[1],
                  "cols_after": len(result.columns),
              }
          )
      return result, audit
  @staticmethod
  def numeric_profile(series: pd.Series, bins: list[float]) -> dict[str, Any]:
      clean = pd.to_numeric(series, errors="coerce").dropna()
      edges = np.asarray(bins, dtype=float)
      counts = pd.cut(
          clean, edges, include_lowest=True, duplicates="drop"
      ).value_counts(sort=False)
      proportions = (counts / max(1, counts.sum())).to_numpy(float)
      return {
          "proportions": proportions.tolist(),
          "missing_rate": float(series.isna().mean()),
      }
  @staticmethod
  def categorical_profile(series: pd.Series, categories: list[str]) -> dict[str, Any]:
      values = series.astype("object").where(series.notna(), "__MISSING__").astype(str)
      mapped = values.where(values.isin(categories), "__OTHER__")
      order = categories + ([] if "__OTHER__" in categories else ["__OTHER__"])
      counts = mapped.value_counts()
      return {
          "proportions": [
              float(counts.get(category, 0) / max(1, len(values))) for category in order
          ],
          "missing_rate": float(series.isna().mean()),
      }
  @staticmethod
  def psi(expected: list[float], actual: list[float]) -> float:
      expected_values = np.asarray(expected, dtype=float)
      actual_values = np.asarray(actual, dtype=float)
      length = max(len(expected_values), len(actual_values))
      expected_values = np.pad(expected_values, (0, length - len(expected_values)))
      actual_values = np.pad(actual_values, (0, length - len(actual_values)))
      epsilon = 1e-6
      expected_values = np.clip(expected_values, epsilon, None)
      actual_values = np.clip(actual_values, epsilon, None)
      return float(
          np.sum((actual_values - expected_values) * np.log(actual_values / expected_values))
      )
  def run_pipeline_job(self, job: dict[str, Any]) -> dict[str, Any]:
      pipeline_name = job["config"]["pipeline"]
      result, audit = self.execute_pipeline(pipeline_name)
      target = self.unique_dataset_name(f"{pipeline_name}_job_output")
      self.datasets[target] = result
      return {"output_dataset": target, "rows": len(result), "steps": len(audit), "audit": audit}
  def run_model_score_job(self, job: dict[str, Any]) -> dict[str, Any]:
      config = job["config"]
      deployment_name = config["deployment"]
      if deployment_name not in self.api_deployments:
          raise KeyError(f"Deployment not found: {deployment_name}")
      deployment = self.api_deployments[deployment_name]
      model_key = deployment["model_key"]
      artifact = self.trained_models.get(model_key)
      if artifact is None:
          raise ValueError(f"Model artifact unavailable: {model_key}")
      dataset_name = config["dataset"]
      if dataset_name not in self.datasets:
          raise KeyError(f"Dataset not found: {dataset_name}")
      frame = self.datasets[dataset_name].copy()
      features = artifact["features"]
      missing = [column for column in features if column not in frame]
      if missing:
          raise ValueError("Missing features: " + ", ".join(missing))
      frame[f"prediction_{artifact['target']}"] = artifact["model"].predict(frame[features])
      target = self.unique_dataset_name(f"{dataset_name}_scored")
      self.datasets[target] = frame
      return {"output_dataset": target, "rows": len(frame), "deployment": deployment_name}
  def run_export_job(self, job: dict[str, Any]) -> dict[str, Any]:
      config = job["config"]
      dataset_name = config["dataset"]
      if dataset_name not in self.datasets:
          raise KeyError(f"Dataset not found: {dataset_name}")
      frame = self.datasets[dataset_name]
      destination = Path(config["path"]).expanduser()
      destination.parent.mkdir(parents=True, exist_ok=True)
      export_format = config["format"]
      if export_format == "CSV":
          frame.to_csv(destination, index=False)
      elif export_format == "Excel":
          frame.to_excel(destination, index=False)
      elif export_format == "Parquet":
          frame.to_parquet(destination, index=False)
      else:
          raise ValueError(f"Unsupported export format: {export_format}")
      return {"path": str(destination), "rows": len(frame), "format": export_format}
  def run_drift_job(self, job: dict[str, Any]) -> dict[str, Any]:
      config = job["config"]
      baseline_name = config["baseline"]
      dataset_name = config["dataset"]
      if baseline_name not in self.monitoring_baselines:
          raise KeyError(f"Monitoring baseline not found: {baseline_name}")
      if dataset_name not in self.datasets:
          raise KeyError(f"Dataset not found: {dataset_name}")
      baseline = self.monitoring_baselines[baseline_name]
      frame = self.datasets[dataset_name]
      summary: list[dict[str, Any]] = []
      alerts: list[dict[str, Any]] = []
      for column in baseline["features"]:
          if column not in frame.columns:
              summary.append(
                  {"feature": column, "psi": None, "status": "CRITICAL", "note": "feature missing"}
              )
              alerts.append(
                  {
                      "baseline": baseline_name,
                      "feature": column,
                      "severity": "CRITICAL",
                      "message": "Feature missing from monitoring data",
                      "created_utc": utc_now(),
                  }
              )
              continue
          profile = baseline["profiles"][column]
          if profile["type"] == "numeric":
              current = self.numeric_profile(frame[column], profile["edges"])
          else:
              current = self.categorical_profile(frame[column], profile["categories"])
          score = self.psi(profile["proportions"], current["proportions"])
          status = (
              "CRITICAL"
              if score >= self.drift_thresholds["critical"]
              else "WARNING"
              if score >= self.drift_thresholds["warning"]
              else "STABLE"
          )
          missing_change = current["missing_rate"] - profile.get("missing_rate", 0.0)
          row = {
              "feature": column,
              "psi": score,
              "status": status,
              "baseline_missing": profile.get("missing_rate"),
              "current_missing": current["missing_rate"],
              "missing_change": missing_change,
          }
          summary.append(row)
          if status != "STABLE":
              alerts.append(
                  {
                      "baseline": baseline_name,
                      "feature": column,
                      "severity": status,
                      "message": f"PSI {score:.4f}",
                      "created_utc": utc_now(),
                  }
              )
      critical = sum(item["status"] == "CRITICAL" for item in summary)
      warning = sum(item["status"] == "WARNING" for item in summary)
      run = {
          "baseline": baseline_name,
          "dataset": dataset_name,
          "rows": len(frame),
          "summary": summary,
          "critical": critical,
          "warning": warning,
          "executed_utc": utc_now(),
      }
      self.monitoring_runs.append(run)
      self.monitoring_alerts.extend(alerts)
      return {"critical": critical, "warning": warning, "features": len(summary)}
  def job_due(self, job: dict[str, Any], now: datetime | None = None) -> bool:
      now = now or datetime.now(timezone.utc)
      if not job.get("enabled", True) or job.get("schedule", "Manual") == "Manual":
          return False
      explicit = parse_utc(job.get("next_run_utc"))
      if explicit is not None:
          return explicit <= now
      anchor = parse_utc(job.get("last_run_utc") or job.get("created_utc"))
      if anchor is None:
          return True
      due_at = next_schedule_time(job.get("schedule", "Manual"), anchor)
      return due_at is not None and due_at <= now
  def dependency_status(self, job: dict[str, Any]) -> tuple[bool, list[str]]:
      blocked: list[str] = []
      for dependency_id in job.get("depends_on", []):
          dependency = self.scheduled_jobs.get(dependency_id)
          if dependency is None:
              blocked.append(f"{dependency_id}: missing")
          elif dependency.get("last_status") != "success":
              blocked.append(
                  f"{dependency_id}: {dependency.get('last_status', 'never run')}"
              )
      return not blocked, blocked
  def _execute_job_action(self, job: dict[str, Any]) -> dict[str, Any]:
      job_type = job["type"]
      if job_type == "Pipeline":
          return self.run_pipeline_job(job)
      if job_type == "Model batch score":
          return self.run_model_score_job(job)
      if job_type == "Dataset export":
          return self.run_export_job(job)
      if job_type == "Monitoring drift check":
          return self.run_drift_job(job)
      raise ValueError(f"Unsupported job type: {job_type}")
  def execute_job(self, job_id: str, job_override: dict[str, Any] | None = None) -> dict[str, Any]:
      if job_override is None:
          if job_id not in self.scheduled_jobs:
              raise KeyError(f"Job not found: {job_id}")
          job = self.scheduled_jobs[job_id]
      else:
          job = job_override
      if not job.get("enabled", True):
          raise ValueError(f"Job is disabled: {job_id}")
      dependencies_ok, blocked_dependencies = self.dependency_status(job)
      if not dependencies_ok:
          record = {
              "job_id": job_id,
              "job_name": job.get("name", job_id),
              "type": job.get("type"),
              "status": "blocked",
              "started_utc": utc_now(),
              "finished_utc": utc_now(),
              "duration_seconds": 0.0,
              "error": "Dependencies are not successful.",
              "details": {"blocked_dependencies": blocked_dependencies},
              "runtime_version": RUNTIME_VERSION,
              "mode": "headless",
              "attempts": 0,
          }
          self.job_runs.append(record)
          saved_job = self.scheduled_jobs.get(job_id)
          if saved_job is not None:
              saved_job["last_status"] = "blocked"
              saved_job["last_error"] = record["error"]
              saved_job["last_run_utc"] = record["finished_utc"]
              next_run = next_schedule_time(
                  saved_job.get("schedule", "Manual"),
                  parse_utc(record["finished_utc"]) or datetime.now(timezone.utc),
              )
              saved_job["next_run_utc"] = next_run.isoformat() if next_run else None
          self.payload["version"] = RUNTIME_VERSION
          return record
      started = time.time()
      started_utc = utc_now()
      status = "success"
      error = None
      details: dict[str, Any] = {}
      max_retries = max(0, int(job.get("max_retries", 0)))
      retry_delay = max(0.0, float(job.get("retry_delay_seconds", 5.0)))
      attempts: list[dict[str, Any]] = []
      for attempt_number in range(1, max_retries + 2):
          attempt_started = time.time()
          try:
              details = self._execute_job_action(job)
              attempts.append(
                  {
                      "attempt": attempt_number,
                      "status": "success",
                      "duration_seconds": round(time.time() - attempt_started, 3),
                  }
              )
              status = "success"
              error = None
              break
          except Exception as exc:
              status = "failed"
              error = str(exc)
              details = {"traceback": traceback.format_exc(limit=8)}
              attempts.append(
                  {
                      "attempt": attempt_number,
                      "status": "failed",
                      "error": error,
                      "duration_seconds": round(time.time() - attempt_started, 3),
                  }
              )
              if attempt_number <= max_retries and retry_delay:
                  time.sleep(retry_delay)
      record = {
          "job_id": job_id,
          "job_name": job.get("name", job_id),
          "type": job.get("type"),
          "status": status,
          "started_utc": started_utc,
          "finished_utc": utc_now(),
          "duration_seconds": round(time.time() - started, 3),
          "error": error,
          "details": details,
          "runtime_version": RUNTIME_VERSION,
          "mode": "headless",
          "attempts": len(attempts),
          "attempt_history": attempts,
      }
      self.job_runs.append(record)
      saved_job = self.scheduled_jobs.get(job_id)
      if saved_job is not None:
          saved_job["run_count"] = int(saved_job.get("run_count", 0)) + 1
          saved_job["last_status"] = status
          saved_job["last_error"] = error
          saved_job["last_run_utc"] = record["finished_utc"]
          next_run = next_schedule_time(
              saved_job.get("schedule", "Manual"),
              parse_utc(record["finished_utc"]) or datetime.now(timezone.utc),
          )
          saved_job["next_run_utc"] = next_run.isoformat() if next_run else None
      self.payload["active_dataset"] = self.payload.get(
          "active_dataset", next(iter(self.datasets))
      )
      self.payload["version"] = RUNTIME_VERSION
      return record
  def run_due_jobs(self, now: datetime | None = None) -> dict[str, Any]:
      now = now or datetime.now(timezone.utc)
      pending = [
          job_id
          for job_id, job in self.scheduled_jobs.items()
          if self.job_due(job, now)
      ]
      records: list[dict[str, Any]] = []
      remaining = list(pending)
      while remaining:
          progressed = False
          for job_id in list(remaining):
              job = self.scheduled_jobs[job_id]
              unresolved = [
                  dependency
                  for dependency in job.get("depends_on", [])
                  if dependency in remaining
              ]
              if unresolved:
                  continue
              records.append(self.execute_job(job_id))
              remaining.remove(job_id)
              progressed = True
          if not progressed:
              for job_id in remaining:
                  record = {
                      "job_id": job_id,
                      "job_name": self.scheduled_jobs[job_id].get("name", job_id),
                      "status": "blocked",
                      "error": "Circular or unresolved due-job dependency.",
                      "runtime_version": RUNTIME_VERSION,
                      "mode": "orchestrator",
                      "started_utc": utc_now(),
                      "finished_utc": utc_now(),
                      "duration_seconds": 0.0,
                  }
                  records.append(record)
                  self.job_runs.append(record)
                  job = self.scheduled_jobs[job_id]
                  job["last_status"] = "blocked"
                  job["last_error"] = record["error"]
                  job["last_run_utc"] = record["finished_utc"]
                  next_run = next_schedule_time(
                      job.get("schedule", "Manual"),
                      parse_utc(record["finished_utc"]) or datetime.now(timezone.utc),
                  )
                  job["next_run_utc"] = next_run.isoformat() if next_run else None
              break
      return {
          "status": "completed",
          "mode": "run_due",
          "runtime_version": RUNTIME_VERSION,
          "evaluated_utc": now.isoformat(),
          "due_jobs": len(pending),
          "success": sum(record.get("status") == "success" for record in records),
          "failed": sum(record.get("status") == "failed" for record in records),
          "blocked": sum(record.get("status") == "blocked" for record in records),
          "records": records,
      }
def append_log(project_path: Path, record: dict[str, Any]) -> None:
  log_path = project_path.with_suffix(project_path.suffix + ".jobs.jsonl")
  with log_path.open("a", encoding="utf-8") as handle:
      handle.write(json.dumps(json_safe(record), ensure_ascii=False) + "\n")
def load_job_file(path: Path) -> dict[str, Any]:
  job = json.loads(path.read_text(encoding="utf-8"))
  if not isinstance(job, dict) or not job.get("type"):
      raise ValueError("Job contract must be a JSON object with a job type.")
  return job
def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
      description="Run and orchestrate Data Studio jobs without the GUI."
  )
  parser.add_argument("--project", required=True, help="Path to the .pdw project.")
  selection = parser.add_mutually_exclusive_group()
  selection.add_argument("--job-id", help="Saved project job identifier.")
  selection.add_argument("--job-file", help="Exported job.json contract.")
  selection.add_argument("--list-jobs", action="store_true", help="List jobs and exit.")
  selection.add_argument("--run-due", action="store_true", help="Run all jobs that are due.")
  selection.add_argument(
      "--daemon", action="store_true", help="Continuously scan and run due jobs."
  )
  parser.add_argument("--save-as", help="Write project changes to another .pdw file.")
  parser.add_argument("--no-save", action="store_true", help="Do not persist project changes.")
  parser.add_argument("--json-output", help="Write the execution result to a JSON file.")
  parser.add_argument("--lock-timeout", type=float, default=30.0)
  parser.add_argument(
      "--poll-seconds", type=float, default=60.0, help="Daemon scan interval."
  )
  parser.add_argument(
      "--max-cycles",
      type=int,
      default=0,
      help="Stop daemon after this many cycles; zero runs until interrupted.",
  )
  return parser
def save_json_output(path: str | None, result: dict[str, Any]) -> None:
  if not path:
      return
  output_path = Path(path).expanduser().resolve()
  output_path.parent.mkdir(parents=True, exist_ok=True)
  output_path.write_text(
      json.dumps(json_safe(result), indent=2, ensure_ascii=False) + "\n",
      encoding="utf-8",
  )
def run_cycle(
  arguments: argparse.Namespace, project_path: Path, destination: Path
) -> tuple[dict[str, Any], int]:
  with project_lock(project_path, arguments.lock_timeout):
      payload = read_project(project_path)
      runtime = HeadlessRuntime(payload, project_path)
      if arguments.list_jobs:
          result = {
              "project": str(project_path),
              "runtime_version": RUNTIME_VERSION,
              "jobs": [
                  {
                      "id": job_id,
                      "name": job.get("name"),
                      "type": job.get("type"),
                      "schedule": job.get("schedule"),
                      "enabled": job.get("enabled", True),
                      "depends_on": job.get("depends_on", []),
                      "max_retries": job.get("max_retries", 0),
                      "last_status": job.get("last_status"),
                      "last_run_utc": job.get("last_run_utc"),
                      "next_run_utc": job.get("next_run_utc"),
                      "due": runtime.job_due(job),
                  }
                  for job_id, job in runtime.scheduled_jobs.items()
              ],
          }
          return result, 0
      if arguments.run_due or arguments.daemon:
          result = runtime.run_due_jobs()
          if not arguments.no_save:
              atomic_write_project(destination, payload)
          for record in result["records"]:
              append_log(destination, record)
          exit_code = 1 if result["failed"] or result["blocked"] else 0
          return result, exit_code
      if not arguments.job_id and not arguments.job_file:
          raise ValueError(
              "Use --job-id, --job-file, --list-jobs, --run-due, or --daemon."
          )
      job_override = None
      if arguments.job_file:
          job_override = load_job_file(Path(arguments.job_file).expanduser().resolve())
          job_id = str(job_override.get("id") or "external_job")
      else:
          job_id = arguments.job_id
      record = runtime.execute_job(job_id, job_override)
      if not arguments.no_save:
          atomic_write_project(destination, payload)
      append_log(destination, record)
      return record, 0 if record["status"] == "success" else 1
def main(argv: list[str] | None = None) -> int:
  arguments = build_parser().parse_args(argv)
  project_path = Path(arguments.project).expanduser().resolve()
  destination = (
      Path(arguments.save_as).expanduser().resolve() if arguments.save_as else project_path
  )
  try:
      if arguments.daemon:
          cycles = 0
          last_exit_code = 0
          while True:
              result, last_exit_code = run_cycle(arguments, project_path, destination)
              cycles += 1
              envelope = {
                  "status": "daemon_cycle_complete",
                  "cycle": cycles,
                  "result": result,
              }
              print(json.dumps(json_safe(envelope), ensure_ascii=False), flush=True)
              save_json_output(arguments.json_output, envelope)
              if arguments.max_cycles and cycles >= arguments.max_cycles:
                  return last_exit_code
              time.sleep(max(1.0, arguments.poll_seconds))
      result, exit_code = run_cycle(arguments, project_path, destination)
      save_json_output(arguments.json_output, result)
      print(json.dumps(json_safe(result), indent=2, ensure_ascii=False))
      return exit_code
  except KeyboardInterrupt:
      print(
          json.dumps(
              {
                  "status": "stopped",
                  "runtime_version": RUNTIME_VERSION,
                  "finished_utc": utc_now(),
              }
          )
      )
      return 0
  except Exception as exc:
      failure = {
          "status": "runtime_error",
          "runtime_version": RUNTIME_VERSION,
          "error": str(exc),
          "finished_utc": utc_now(),
      }
      print(json.dumps(failure, indent=2), file=sys.stderr)
      return 2
if __name__ == "__main__":
  raise SystemExit(main())
