"""Shared helpers for built-in analyses.

Analyses read through the engine, validate columns once and return typed results. These
helpers keep that behaviour identical across descriptive and inferential analyses without
duplicating validation or JSON-safety rules.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import pandas as pd

from ..engine.duckdb_engine import DuckDBTable
from ..kernel.results import AnalysisResult, Diagnostic, ResultTable
from ..kernel.schema import DataType
from ..kernel.spec import ChartSpec
from ..steps.sql import q, wrap
from .registry import AnalysisContext, AnalysisError

NUMERIC_TYPES = frozenset({DataType.INTEGER, DataType.FLOAT, DataType.DECIMAL})
UNORDERED_TYPES = frozenset({DataType.JSON, DataType.BINARY})


def require_columns(
    table: DuckDBTable,
    columns: Sequence[str] | None,
    *,
    label: str = "column",
) -> tuple[str, ...]:
    """Validate that every requested column exists and no name repeats."""

    names = tuple(columns or ())
    if not names:
        raise AnalysisError(f"At least one {label} is required.")
    for name in names:
        if name not in table.schema:
            available = ", ".join(table.schema.names) or "(none)"
            raise AnalysisError(
                f"Unknown {label} {name!r}. Available columns: {available}."
            )
    if len(set(names)) != len(names):
        raise AnalysisError(f"Duplicate {label} in {list(names)!r}.")
    return names


def numeric_columns(
    table: DuckDBTable,
    columns: Sequence[str] | None,
) -> tuple[str, ...]:
    """Resolve requested columns, defaulting to every numeric column."""

    if columns:
        names = require_columns(table, columns)
        non_numeric = [
            name for name in names if table.schema.column(name).dtype not in NUMERIC_TYPES
        ]
        if non_numeric:
            raise AnalysisError(
                "These columns are not numeric: " + ", ".join(non_numeric) + "."
            )
        return names
    names = tuple(
        column.name for column in table.schema if column.dtype in NUMERIC_TYPES
    )
    if not names:
        raise AnalysisError("The table has no numeric columns to analyse.")
    return names


def cell(value: Any) -> Any:
    """Convert engine values to JSON-safe table cells."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item") and callable(value.item):
        # numpy scalars from pandas/arrow-backed frames
        return cell(value.item())
    return value


def num(value: Any, digits: int = 6) -> float | None:
    """Round a numeric value to a finite float, or return None."""

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def p_value(value: Any) -> float | None:
    """Return a p-value as a finite float clamped to [0, 1]."""

    number = num(value, digits=15)
    if number is None:
        return None
    return min(1.0, max(0.0, number))


def p_text(value: Any) -> str:
    """Format a p-value for humans, keeping very small values readable."""

    number = p_value(value)
    if number is None:
        return "n/a"
    if number <= 0.0:
        return "<1e-15"
    if number < 0.0001:
        return f"{number:.2e}"
    return f"{number:.4f}"


def label(value: Any) -> str:
    """Render a cell value as a stable display label."""

    if value is None:
        return "(missing)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def sort_key(value: Any) -> tuple[int, Any]:
    """Sort numbers before text, matching the descriptive crosstab order."""

    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return (0, float(value))
    return (1, label(value))


def unique_labels(values: Sequence[Any], *, reserved: Sequence[str] = ()) -> list[str]:
    """Turn values into unique, case-insensitive display labels."""

    taken = {str(item).casefold() for item in reserved}
    labels: list[str] = []
    for value in values:
        base = label(value)
        candidate = base
        counter = 2
        while candidate.casefold() in taken:
            candidate = f"{base} ({counter})"
            counter += 1
        taken.add(candidate.casefold())
        labels.append(candidate)
    return labels


def result(
    context: AnalysisContext,
    name: str,
    title: str,
    params: Mapping[str, Any],
    *,
    tables: Sequence[ResultTable] = (),
    metrics: Mapping[str, Any] | None = None,
    diagnostics: Sequence[Diagnostic] = (),
    narrative: str = "",
    charts: Sequence[ChartSpec] = (),
) -> AnalysisResult:
    """Build a result with provenance derived from the analysis context."""

    return AnalysisResult(
        kind=name,
        title=title,
        provenance=context.provenance(params),
        params=params,
        tables=tuple(tables),
        metrics=dict(metrics or {}),
        diagnostics=tuple(diagnostics),
        narrative=narrative,
        charts=tuple(charts),
    )


def frame(
    table: DuckDBTable,
    columns: Sequence[str],
    *,
    numeric: bool = False,
) -> pd.DataFrame:
    """Materialise selected columns as a DataFrame without full-table copies."""

    if numeric:
        selection = ", ".join(
            f"CAST({q(name)} AS DOUBLE) AS {q(name)}" for name in columns
        )
    else:
        selection = ", ".join(f"{q(name)} AS {q(name)}" for name in columns)
    rows = table.engine.query(f"SELECT {selection} FROM {wrap(table)} AS _analysis")
    return pd.DataFrame(rows, columns=list(columns))


__all__ = [
    "NUMERIC_TYPES",
    "UNORDERED_TYPES",
    "cell",
    "frame",
    "label",
    "num",
    "numeric_columns",
    "p_text",
    "p_value",
    "require_columns",
    "result",
    "sort_key",
    "unique_labels",
]
