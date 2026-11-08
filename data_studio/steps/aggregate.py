"""Aggregation steps."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..engine.duckdb_engine import DuckDBTable
from .registry import StepContext, StepDefinition, StepError, StepParam
from .sql import aggregation_expression, as_table, q, require_columns, wrap


def _group_aggregate(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    group_by = tuple(params["group_by"] or ())
    if group_by:
        group_by = require_columns(table, group_by)
    aggregations = params["aggregations"]
    if not aggregations:
        raise StepError("At least one aggregation is required.")

    parts = [q(column) for column in group_by]
    outputs: set[str] = {column.casefold() for column in group_by}
    for aggregation in aggregations:
        column = aggregation.get("column")
        if isinstance(column, str) and column != "*" and column not in table.schema:
            raise StepError(f"Unknown aggregation column {column!r}.")
        by = aggregation.get("by")
        if isinstance(by, str) and by not in table.schema:
            raise StepError(f"Unknown aggregation 'by' column {by!r}.")
        expression, name = aggregation_expression(aggregation)
        if name.casefold() in outputs:
            raise StepError(f"Duplicate aggregation output {name!r}.")
        outputs.add(name.casefold())
        parts.append(f"{expression} AS {q(name)}")

    sql = f"SELECT {', '.join(parts)} FROM {wrap(table)} AS _src"
    if group_by:
        sql += " GROUP BY " + ", ".join(q(column) for column in group_by)
        base_schema = table.schema.select(*group_by)
    else:
        base_schema = table.schema.select()
    return as_table(context, sql, table, base_schema=base_schema)


def _value_counts(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    columns = require_columns(table, params["columns"])
    reserved = {"count", "percent"}
    if any(column.casefold() in reserved for column in columns):
        raise StepError("Value counts cannot group by columns named 'count' or 'percent'.")
    group = ", ".join(q(column) for column in columns)
    inner = f"SELECT {group}, COUNT(*) AS {q('count')} FROM {wrap(table)} AS _src GROUP BY {group}"
    sql = (
        f"SELECT {group}, {q('count')}, "
        f"{q('count')} * 1.0 / SUM({q('count')}) OVER () AS {q('percent')} "
        f"FROM ({inner}) AS _counts ORDER BY {q('count')} DESC"
    )
    return as_table(context, sql, table, base_schema=table.schema.select(*columns))


STEPS = (
    StepDefinition(
        name="group_aggregate",
        title="Group and aggregate",
        category="aggregate",
        description="Summarise rows by group with one or more aggregate functions.",
        params=(
            StepParam(
                "group_by",
                "columns",
                required=False,
                default=(),
                description="Grouping columns; empty aggregates the whole table.",
            ),
            StepParam(
                "aggregations",
                "aggregations",
                description="List of {column, function, output?} objects.",
            ),
        ),
        compile=_group_aggregate,
    ),
    StepDefinition(
        name="value_counts",
        title="Value counts",
        category="aggregate",
        description="Count and percentage of rows per key combination.",
        params=(StepParam("columns", "columns", description="Columns to count."),),
        compile=_value_counts,
    ),
)

__all__ = ["STEPS"]
