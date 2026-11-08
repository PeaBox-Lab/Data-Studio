"""Date parsing and calendar extraction steps."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..engine.duckdb_engine import DuckDBTable
from ..kernel.schema import DataType
from .registry import StepContext, StepDefinition, StepError, StepParam
from .sql import as_table, literal, q, require_columns, wrap


def _new_column(
    table: DuckDBTable,
    output: str | None,
    default: str,
) -> str:
    name = output or default
    if name in table.schema:
        raise StepError(f"Output column {name!r} already exists.")
    return name


def _date_parse(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    column = params["column"]
    require_columns(table, (column,))
    output = _new_column(table, params["output"], f"{column}_date")
    dtype = table.schema.column(column).dtype
    if dtype in {DataType.DATE, DataType.DATETIME}:
        # CSV/Parquet auto-detection may already have produced a temporal column.
        expression = f"CAST({q(column)} AS TIMESTAMP)"
    elif params["format"]:
        expression = f"TRY_STRPTIME({q(column)}, {literal(params['format'])})"
    else:
        expression = f"TRY_CAST({q(column)} AS TIMESTAMP)"
    sql = f"SELECT *, {expression} AS {q(output)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


def _date_extract(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    column = params["column"]
    require_columns(table, (column,))
    part = params["part"]
    output = _new_column(table, params["output"], f"{column}_{part}")
    if part == "weekday":
        expression = f"DAYNAME({q(column)})"
    elif part == "dow":
        expression = f"DAYOFWEEK({q(column)})"
    elif part == "doy":
        expression = f"DAYOFYEAR({q(column)})"
    else:
        expression = f"DATE_PART({literal(part)}, {q(column)})"
    sql = f"SELECT *, {expression} AS {q(output)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


def _date_trunc(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    column = params["column"]
    require_columns(table, (column,))
    unit = params["unit"]
    output = _new_column(table, params["output"], f"{column}_{unit}")
    expression = f"DATE_TRUNC({literal(unit)}, {q(column)})"
    sql = f"SELECT *, {expression} AS {q(output)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


def _date_shift(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    column = params["column"]
    require_columns(table, (column,))
    unit = params["unit"]
    output = _new_column(table, params["output"], f"{column}_shifted")
    expression = f"{q(column)} + INTERVAL {literal(str(int(params['amount'])))} {unit.upper()}"
    sql = f"SELECT *, {expression} AS {q(output)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


_EXTRACT_PARTS = (
    "year",
    "quarter",
    "month",
    "week",
    "day",
    "weekday",
    "dow",
    "doy",
    "hour",
    "minute",
)
_TRUNC_UNITS = ("year", "quarter", "month", "week", "day", "hour")
_SHIFT_UNITS = ("year", "quarter", "month", "week", "day", "hour", "minute")

STEPS = (
    StepDefinition(
        name="date_parse",
        title="Parse dates",
        category="datetime",
        description="Parse text into timestamps with an optional format string.",
        params=(
            StepParam("column", "column", description="Text column to parse."),
            StepParam(
                "format",
                "string",
                required=False,
                default=None,
                description="strptime format, e.g. %Y-%m-%d.",
            ),
            StepParam(
                "output", "column", required=False, default=None, description="Output column name."
            ),
        ),
        compile=_date_parse,
    ),
    StepDefinition(
        name="date_extract",
        title="Extract date part",
        category="datetime",
        description="Add a calendar part such as year, month, weekday or hour.",
        params=(
            StepParam("column", "column", description="Date or timestamp column."),
            StepParam("part", "enum", choices=_EXTRACT_PARTS, description="Calendar part."),
            StepParam(
                "output", "column", required=False, default=None, description="Output column name."
            ),
        ),
        compile=_date_extract,
    ),
    StepDefinition(
        name="date_trunc",
        title="Truncate dates",
        category="datetime",
        description="Truncate a timestamp to a calendar unit.",
        params=(
            StepParam("column", "column", description="Date or timestamp column."),
            StepParam("unit", "enum", choices=_TRUNC_UNITS, description="Truncation unit."),
            StepParam(
                "output", "column", required=False, default=None, description="Output column name."
            ),
        ),
        compile=_date_trunc,
    ),
    StepDefinition(
        name="date_shift",
        title="Shift dates",
        category="datetime",
        description="Add or subtract a calendar interval.",
        params=(
            StepParam("column", "column", description="Date or timestamp column."),
            StepParam("amount", "integer", description="Number of units; negative subtracts."),
            StepParam("unit", "enum", choices=_SHIFT_UNITS, description="Interval unit."),
            StepParam(
                "output", "column", required=False, default=None, description="Output column name."
            ),
        ),
        compile=_date_shift,
    ),
)

__all__ = ["STEPS"]
