"""Typed analysis results.

This module is the heart of the redesign. An analysis does not "print a report"; it
returns an `AnalysisResult` containing:

* the parameters actually used,
* one or more result tables,
* scalar metrics (p-values, R-squared, sample sizes, ...),
* diagnostics and assumption checks,
* a plain-language narrative,
* chart specifications,
* provenance for reproducibility.

Every surface (desktop panel, web client, REST API, HTML report, notebook, test)
renders that same object.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from ..version import RESULT_SCHEMA_VERSION
from .provenance import Provenance
from .spec import ChartSpec

Scalar = float | int | str | bool | None


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


def _canonical(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _is_cell(value: Any) -> bool:
    return value is None or isinstance(
        value, (bool, int, float, str, Decimal, date, datetime)
    )


def _is_scalar(value: Any) -> bool:
    return _is_cell(value)


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A machine-readable note attached to a result."""

    code: str
    message: str
    severity: Severity = Severity.INFO
    details: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        code = self.code.strip()
        message = self.message.strip()
        if not code:
            raise ValueError("Diagnostic code must not be empty.")
        if not message:
            raise ValueError("Diagnostic message must not be empty.")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", message)
        severity = (
            self.severity
            if isinstance(self.severity, Severity)
            else Severity(str(self.severity))
        )
        object.__setattr__(self, "severity", severity)
        if not isinstance(self.details, Mapping):
            raise TypeError("Diagnostic details must be a mapping.")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    @classmethod
    def info(cls, code: str, message: str, **details: Any) -> Diagnostic:
        return cls(code, message, Severity.INFO, details)

    @classmethod
    def warning(cls, code: str, message: str, **details: Any) -> Diagnostic:
        return cls(code, message, Severity.WARNING, details)

    @classmethod
    def error(cls, code: str, message: str, **details: Any) -> Diagnostic:
        return cls(code, message, Severity.ERROR, details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Diagnostic:
        return cls(
            code=str(payload["code"]),
            message=str(payload["message"]),
            severity=Severity(str(payload.get("severity", Severity.INFO.value))),
            details=dict(payload.get("details") or {}),
        )


@dataclass(frozen=True, slots=True)
class Estimate:
    """A point estimate with optional uncertainty and a p-value."""

    name: str
    value: float | None
    label: str | None = None
    se: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    p_value: float | None = None

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("Estimate name must not be empty.")
        object.__setattr__(self, "name", name)
        if self.se is not None and self.se < 0:
            raise ValueError("Estimate standard error must be >= 0.")
        if self.p_value is not None and not 0.0 <= self.p_value <= 1.0:
            raise ValueError("Estimate p-value must be between 0 and 1.")
        if (self.ci_low is None) != (self.ci_high is None):
            raise ValueError("Estimate confidence bounds must be provided together.")
        if (
            self.ci_low is not None
            and self.ci_high is not None
            and self.ci_low > self.ci_high
        ):
            raise ValueError("Estimate ci_low must be <= ci_high.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "label": self.label,
            "se": self.se,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "p_value": self.p_value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Estimate:
        return cls(
            name=str(payload["name"]),
            value=payload.get("value"),
            label=payload.get("label"),
            se=payload.get("se"),
            ci_low=payload.get("ci_low"),
            ci_high=payload.get("ci_high"),
            p_value=payload.get("p_value"),
        )


@dataclass(frozen=True, slots=True)
class ResultTable:
    """A small, display-ready table of results (not raw source data)."""

    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        title = self.title.strip()
        if not title:
            raise ValueError("ResultTable title must not be empty.")
        object.__setattr__(self, "title", title)

        columns = tuple(str(column).strip() for column in self.columns)
        if not columns:
            raise ValueError("ResultTable must have at least one column.")
        if any(not column for column in columns):
            raise ValueError("ResultTable column names must not be empty.")
        lowered = [column.casefold() for column in columns]
        if len(set(lowered)) != len(lowered):
            raise ValueError("ResultTable column names must be unique case-insensitively.")
        object.__setattr__(self, "columns", columns)

        rows = tuple(tuple(row) for row in self.rows)
        for row_number, row in enumerate(rows):
            if len(row) != len(columns):
                raise ValueError(
                    f"ResultTable row {row_number} has {len(row)} values; "
                    f"expected {len(columns)}."
                )
            if not all(_is_cell(cell) for cell in row):
                raise TypeError(
                    f"ResultTable row {row_number} contains a non-scalar value."
                )
        object.__setattr__(self, "rows", rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ResultTable:
        return cls(
            title=str(payload["title"]),
            columns=tuple(payload.get("columns", [])),
            rows=tuple(tuple(row) for row in payload.get("rows", [])),
            note=payload.get("note"),
        )


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """The only way an analysis communicates its output."""

    kind: str
    title: str
    provenance: Provenance
    params: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)
    tables: tuple[ResultTable, ...] = ()
    metrics: Mapping[str, Scalar] = field(default_factory=dict, compare=False, hash=False)
    diagnostics: tuple[Diagnostic, ...] = ()
    narrative: str = ""
    charts: tuple[ChartSpec, ...] = ()
    schema_version: str = RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        kind = self.kind.strip()
        if not kind:
            raise ValueError("AnalysisResult kind must not be empty.")
        if not kind.replace("_", "").replace(".", "").isalnum():
            raise ValueError("AnalysisResult kind must be snake_case-ish: letters/digits/_/.")
        object.__setattr__(self, "kind", kind)

        title = self.title.strip()
        if not title:
            raise ValueError("AnalysisResult title must not be empty.")
        object.__setattr__(self, "title", title)

        if not isinstance(self.provenance, Provenance):
            raise TypeError("AnalysisResult provenance must be a Provenance instance.")

        if not isinstance(self.params, Mapping):
            raise TypeError("AnalysisResult params must be a mapping.")
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

        tables = tuple(self.tables)
        if any(not isinstance(table, ResultTable) for table in tables):
            raise TypeError("AnalysisResult tables must be ResultTable instances.")
        object.__setattr__(self, "tables", tables)

        if not isinstance(self.metrics, Mapping):
            raise TypeError("AnalysisResult metrics must be a mapping.")
        metrics: dict[str, Scalar] = {}
        for name, value in self.metrics.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Metric names must be non-empty strings.")
            if not _is_scalar(value):
                raise TypeError(f"Metric {name!r} must be a scalar.")
            metrics[name] = value
        object.__setattr__(self, "metrics", MappingProxyType(metrics))

        diagnostics = tuple(self.diagnostics)
        if any(not isinstance(item, Diagnostic) for item in diagnostics):
            raise TypeError("AnalysisResult diagnostics must be Diagnostic instances.")
        object.__setattr__(self, "diagnostics", diagnostics)

        if not isinstance(self.narrative, str):
            raise TypeError("AnalysisResult narrative must be a string.")

        charts = tuple(self.charts)
        if any(not isinstance(chart, ChartSpec) for chart in charts):
            raise TypeError("AnalysisResult charts must be ChartSpec instances.")
        object.__setattr__(self, "charts", charts)

        if not str(self.schema_version).strip():
            raise ValueError("AnalysisResult schema_version must not be empty.")

    @property
    def result_id(self) -> str:
        payload = {
            "kind": self.kind,
            "params": dict(self.params),
            "dataset": self.provenance.dataset,
            "dataset_fingerprint": self.provenance.dataset_fingerprint,
            "step_ids": list(self.provenance.step_ids),
            "parameters_hash": self.provenance.parameters_hash,
            "schema_version": self.schema_version,
        }
        digest = sha256(_canonical(payload).encode("utf-8")).hexdigest()
        return f"res_{digest[:32]}"

    @property
    def has_warnings(self) -> bool:
        return any(
            diagnostic.severity in (Severity.WARNING, Severity.ERROR)
            for diagnostic in self.diagnostics
        )

    @property
    def primary_table(self) -> ResultTable | None:
        return self.tables[0] if self.tables else None

    def metric(self, name: str, default: Scalar = None) -> Scalar:
        return self.metrics.get(name, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "result_id": self.result_id,
            "kind": self.kind,
            "title": self.title,
            "params": dict(self.params),
            "tables": [table.to_dict() for table in self.tables],
            "metrics": dict(self.metrics),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "narrative": self.narrative,
            "charts": [chart.to_dict() for chart in self.charts],
            "provenance": self.provenance.to_dict(),
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AnalysisResult:
        return cls(
            kind=str(payload["kind"]),
            title=str(payload["title"]),
            provenance=Provenance.from_dict(payload["provenance"]),
            params=dict(payload.get("params") or {}),
            tables=tuple(
                ResultTable.from_dict(table) for table in payload.get("tables", [])
            ),
            metrics=dict(payload.get("metrics") or {}),
            diagnostics=tuple(
                Diagnostic.from_dict(item) for item in payload.get("diagnostics", [])
            ),
            narrative=str(payload.get("narrative", "")),
            charts=tuple(
                ChartSpec.from_dict(chart) for chart in payload.get("charts", [])
            ),
            schema_version=str(payload.get("schema_version", RESULT_SCHEMA_VERSION)),
        )
