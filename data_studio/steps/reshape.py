"""Pivot and unpivot steps."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..engine.duckdb_engine import DuckDBTable
from .registry import StepContext, StepDefinition, StepError, StepParam
from .sql import as_table, q, require_columns, wrap

_PIVOT_AGGREGATES = {
    "sum": "SUM",
    "mean": "AVG",
    "count": "COUNT",
    "min": "MIN",
    "max": "MAX",
}


def _pivot(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    index = require_columns(table, params["index"], label="index column")
    column = require_columns(table, (params["columns"],), label="pivot column")[0]
    value = require_columns(table, (params["values"],), label="value column")[0]
    aggregate = _PIVOT_AGGREGATES[params["aggregate"]]
    sql = (
        f"SELECT * FROM (PIVOT {wrap(table)} ON {q(column)} "
        f"USING {aggregate}({q(value)}) "
        f"GROUP BY {', '.join(q(name) for name in index)}) AS _pivot"
    )
    return as_table(context, sql, table, base_schema=table.schema.select(*index))


def _unpivot(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    keep = tuple(params["keep"] or ())
    if keep:
        keep = require_columns(table, keep)
    columns = tuple(params["columns"] or ())
    if columns:
        columns = require_columns(table, columns)
    else:
        columns = tuple(name for name in table.schema.names if name not in set(keep))
    if not columns:
        raise StepError("No columns selected to unpivot.")

    name_column = params["name_column"] or "variable"
    value_column = params["value_column"] or "value"
    if name_column == value_column:
        raise StepError("Unpivot name and value columns must differ.")
    for column in (name_column, value_column):
        if column in keep:
            raise StepError(f"Output column {column!r} collides with a kept column.")

    sql = (
        f"SELECT * FROM (UNPIVOT {wrap(table)} ON "
        f"{', '.join(q(column) for column in columns)} "
        f"INTO NAME {q(name_column)} VALUE {q(value_column)}) AS _unpivot"
    )
    return as_table(context, sql, table)


STEPS = (
    StepDefinition(
        name="pivot",
        title="Pivot wider",
        category="reshape",
        description="Turn distinct values of one column into new columns.",
        params=(
            StepParam("index", "columns", description="Columns that identify a row."),
            StepParam("columns", "column", description="Column whose values become headers."),
            StepParam("values", "column", description="Column to aggregate."),
            StepParam(
                "aggregate",
                "enum",
                required=False,
                default="sum",
                choices=tuple(_PIVOT_AGGREGATES),
                description="Aggregate function.",
            ),
        ),
        compile=_pivot,
    ),
    StepDefinition(
        name="unpivot",
        title="Unpivot longer",
        category="reshape",
        description="Turn several columns into name/value pairs.",
        params=(
            StepParam(
                "columns",
                "columns",
                required=False,
                default=(),
                description="Columns to unpivot; all non-kept when empty.",
            ),
            StepParam(
                "keep",
                "columns",
                required=False,
                default=(),
                description="Columns to keep as identifiers.",
            ),
            StepParam(
                "name_column",
                "column",
                required=False,
                default="variable",
                description="Name output column.",
            ),
            StepParam(
                "value_column",
                "column",
                required=False,
                default="value",
                description="Value output column.",
            ),
        ),
        compile=_unpivot,
    ),
)

__all__ = ["STEPS"]
