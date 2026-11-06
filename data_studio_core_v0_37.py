"""Pure, testable governance and deployment-health logic for Data Studio v0.37."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any
import ast
import math

VERSION = "0.37"
DEFAULT_THRESHOLDS = {
    "healthy": 85,
    "degraded": 65,
    "api_error_rate": 0.05,
    "drift_alerts": 2,
}

_ALLOWED_EXPRESSION_NODES = {
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call,
    ast.Name, ast.Load, ast.Constant, ast.Attribute, ast.Subscript, ast.List,
    ast.Tuple, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Pow, ast.USub, ast.UAdd, ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt,
    ast.LtE, ast.Gt, ast.GtE, ast.BitAnd, ast.BitOr,
}
_ALLOWED_NUMPY_FUNCTIONS = {
    "abs", "sqrt", "log", "log10", "exp", "sin", "cos", "tan",
    "minimum", "maximum", "where", "clip", "mean", "std", "sum",
}


def validate_expression(expression: str, allowed_names: set[str], allow_numpy: bool = True) -> None:
    """Reject statements, unknown names, and arbitrary attribute/call access."""
    tree = ast.parse(expression, mode="eval")
    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_EXPRESSION_NODES:
            raise ValueError(f"Unsupported expression element: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in allowed_names and node.id != "np":
            raise ValueError(f"Unknown expression name: {node.id}")
        if isinstance(node, ast.Attribute):
            if not allow_numpy or not isinstance(node.value, ast.Name) or node.value.id != "np" or node.attr not in _ALLOWED_NUMPY_FUNCTIONS:
                raise ValueError("Only approved NumPy functions may be called.")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id not in allowed_names:
                raise ValueError(f"Function is not allowed: {node.func.id}")
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                raise ValueError("Indirect function calls are not allowed.")


def safe_math_eval(expression: str, variables: dict[str, Any], numpy_module) -> Any:
    validate_expression(expression, set(variables), allow_numpy=True)
    return eval(compile(ast.parse(expression, mode="eval"), "<data-studio-expression>", "eval"), {"__builtins__": {}, "np": numpy_module}, dict(variables))


def validate_dataframe_expression(expression: str, columns: list[str]) -> None:
    validate_expression(expression, set(map(str, columns)), allow_numpy=False)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class DeploymentHealth:
    score: int
    status: str
    rollback_recommended: bool
    reasons: tuple[str, ...]
    components: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["reasons"] = list(self.reasons)
        return result


def calculate_deployment_health(observation: dict[str, Any], thresholds: dict[str, Any] | None = None) -> DeploymentHealth:
    """Return a deterministic 0-100 deployment score and rollback decision."""
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    api_error_rate = max(0.0, min(1.0, _finite(observation.get("api_error_rate"))))
    drift_alerts = max(0, int(_finite(observation.get("drift_alerts"))))
    critical_drift = max(0, int(_finite(observation.get("critical_drift_alerts"))))
    failed_jobs = max(0, int(_finite(observation.get("failed_jobs"))))
    ci_passed = bool(observation.get("ci_passed", True))
    contracts_passed = bool(observation.get("contracts_passed", True))

    components = {
        "api": min(40, round(api_error_rate * 400)),
        "drift": min(30, drift_alerts * 6 + critical_drift * 9),
        "jobs": min(20, failed_jobs * 5),
        "ci": 0 if ci_passed else 25,
        "contracts": 0 if contracts_passed else 20,
    }
    score = max(0, 100 - sum(components.values()))
    status = "HEALTHY" if score >= int(limits["healthy"]) else "DEGRADED" if score >= int(limits["degraded"]) else "UNHEALTHY"
    reasons: list[str] = []
    if score < int(limits["degraded"]): reasons.append(f"health score {score} is below {int(limits['degraded'])}")
    if api_error_rate >= float(limits["api_error_rate"]): reasons.append(f"API error rate {api_error_rate:.1%} meets rollback threshold")
    if drift_alerts >= int(limits["drift_alerts"]): reasons.append(f"drift alerts {drift_alerts} meet rollback threshold")
    if not ci_passed: reasons.append("latest CI evidence failed")
    if not contracts_passed: reasons.append("latest contract-test evidence failed")
    return DeploymentHealth(score, status, bool(reasons), tuple(reasons), components)


def promotion_readiness(target: str, ci_passed: bool, contracts_passed: bool, release_gate_passed: bool) -> tuple[bool, list[str]]:
    target = str(target).upper()
    blockers: list[str] = []
    if target == "PROD":
        if not ci_passed: blockers.append("successful CI evidence is required")
        if not contracts_passed: blockers.append("successful contract-test evidence is required")
        if not release_gate_passed: blockers.append("a passing release gate is required")
    return not blockers, blockers


def infer_validation_rules(frame) -> list[dict[str, Any]]:
    """Infer conservative, reviewable rules from a pandas-like frame."""
    rules: list[dict[str, Any]] = []
    rows = max(1, len(frame))
    for column in frame.columns:
        series = frame[column]
        missing_rate = float(series.isna().mean())
        if missing_rate == 0:
            rules.append({"column": str(column), "type": "not_null"})
        if bool(series.is_unique) and len(series) == rows:
            rules.append({"column": str(column), "type": "unique"})
        if getattr(series.dtype, "kind", "") in "iufc" and series.notna().any():
            rules.append({"column": str(column), "type": "range", "min": float(series.min()), "max": float(series.max())})
        elif 0 < int(series.nunique(dropna=True)) <= 20:
            rules.append({"column": str(column), "type": "domain", "values": [str(x) for x in series.dropna().unique().tolist()]})
    return rules


def validate_frame(frame, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for rule in rules:
        column = rule.get("column")
        kind = rule.get("type")
        if column not in frame.columns:
            results.append({"column": column, "rule": kind, "status": "FAIL", "violations": len(frame), "message": "column missing"})
            continue
        series = frame[column]
        if kind == "not_null": violations = int(series.isna().sum())
        elif kind == "unique": violations = int(series.duplicated(keep=False).sum())
        elif kind == "range": violations = int(((series < rule["min"]) | (series > rule["max"])).fillna(False).sum())
        elif kind == "domain": violations = int((~series.astype(str).isin(set(rule.get("values", []))) & series.notna()).sum())
        else:
            results.append({"column": column, "rule": kind, "status": "SKIP", "violations": 0, "message": "unsupported rule"})
            continue
        results.append({"column": column, "rule": kind, "status": "PASS" if violations == 0 else "FAIL", "violations": violations, "message": ""})
    return results


def workspace_health(payload: dict[str, Any]) -> dict[str, Any]:
    """Score project readiness with explicit, explainable deductions."""
    deductions: list[tuple[str, int]] = []
    if not payload.get("datasets"): deductions.append(("No datasets", 40))
    if not payload.get("pipeline_definitions"): deductions.append(("No reusable pipeline", 10))
    if not payload.get("contract_test_runs"): deductions.append(("No contract-test evidence", 10))
    if not payload.get("cicd_runs"): deductions.append(("No CI evidence", 10))
    if not payload.get("release_gate_runs"): deductions.append(("No release-gate evidence", 10))
    if not payload.get("current_project_path"): deductions.append(("Project has not been saved", 10))
    score = max(0, 100 - sum(points for _, points in deductions))
    status = "READY" if score >= 85 else "REVIEW" if score >= 65 else "AT RISK"
    return {"score": score, "status": status, "findings": [{"finding": name, "deduction": points} for name, points in deductions]}
