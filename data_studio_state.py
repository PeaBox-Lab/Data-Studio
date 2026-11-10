"""Versioned Data Studio project-state defaults, validation, and migration."""
from __future__ import annotations
from copy import deepcopy
from typing import Any

CURRENT_VERSION = "0.38"

DEFAULTS: dict[str, Any] = {
    "active_dataset": None, "datasets": {}, "pipeline_steps": [], "trained_models": {},
    "metric_definitions": [], "dashboard_cards": [], "saved_pipelines": {},
    "kpi_definitions": [], "dashboard_filters": {}, "compute_engine": "Auto",
    "engine_threshold_rows": 1_000_000, "relationships": [], "semantic_measures": [],
    "calculated_columns": [], "pipeline_definitions": {}, "active_pipeline": None,
    "pipeline_step_counter": 0, "lazy_sources": {}, "preview_limit": 10_000,
    "materialization_limit": 500_000, "dashboard_definitions": {}, "active_dashboard": None,
    "dashboard_tile_counter": 0, "analytics_pack_results": {}, "statistics_results": {},
    "engineering_results": {}, "automl_experiments": {}, "active_experiment": None,
    "model_registry": {}, "prediction_history": [], "model_version_counter": 0,
    "monitoring_baselines": {}, "monitoring_runs": [], "monitoring_alerts": [],
    "drift_thresholds": {"warning": 0.10, "critical": 0.25}, "api_deployments": {},
    "api_request_history": [], "scheduled_jobs": {}, "job_runs": [], "job_counter": 0,
    "ui_preferences": {"theme":"Light","density":"Comfortable","show_guidance":True},
    "recent_actions": [], "smart_recommendations": [], "contract_test_suites": {},
    "contract_test_runs": [], "cicd_profiles": {}, "cicd_runs": [], "release_gate_runs": [],
    "environments": {"DEV":{"protected":False,"current_release":"v0.38-dev","previous_release":None},"TEST":{"protected":False,"current_release":None,"previous_release":None},"PROD":{"protected":True,"current_release":None,"previous_release":None}},
    "promotion_history": [], "validation_rules": {}, "data_contracts": {},
    "lineage_graph": {"nodes":[],"edges":[]}, "cleaning_plans": {},
    "deployment_thresholds": {"healthy":85,"degraded":65,"api_error_rate":0.05,"drift_alerts":2},
    "deployment_observations": [], "rollback_history": [],
    "diagnostic_events": [], "project_schema_version": 1,
}


def migrate_project(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(payload, dict): raise ValueError("Project payload must be a dictionary.")
    migrated = dict(payload); changes: list[str] = []
    source_version = str(migrated.get("version", "legacy"))
    for key, value in DEFAULTS.items():
        if key not in migrated:
            migrated[key] = deepcopy(value); changes.append(f"initialized {key}")
    if migrated.get("datasets"):
        active=migrated.get("active_dataset")
        if active not in migrated["datasets"]:
            migrated["active_dataset"] = next(iter(migrated["datasets"])); changes.append("repaired active_dataset")
    for name in ("DEV","TEST","PROD"):
        env=migrated["environments"].setdefault(name,deepcopy(DEFAULTS["environments"][name]))
        env.setdefault("protected",name=="PROD"); env.setdefault("current_release",None); env.setdefault("previous_release",None)
    migrated["version"] = CURRENT_VERSION
    migrated["project_schema_version"] = 1
    if source_version != CURRENT_VERSION: changes.insert(0,f"migrated project {source_version} to {CURRENT_VERSION}")
    return migrated, changes


def validate_project(payload: dict[str, Any], require_datasets: bool = True) -> list[str]:
    errors=[]
    if not isinstance(payload,dict): return ["payload is not a dictionary"]
    datasets=payload.get("datasets")
    if require_datasets and not datasets: errors.append("project contains no datasets")
    if datasets and payload.get("active_dataset") not in datasets: errors.append("active_dataset is not present in datasets")
    if not isinstance(payload.get("scheduled_jobs",{}),dict): errors.append("scheduled_jobs must be a dictionary")
    if not isinstance(payload.get("job_runs",[]),list): errors.append("job_runs must be a list")
    if not isinstance(payload.get("environments",{}),dict): errors.append("environments must be a dictionary")
    return errors
