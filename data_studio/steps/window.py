"""Window and analytic steps."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..engine.duckdb_engine import DuckDBTable
from .registry import StepContext, StepDefinition, StepError, StepParam
from .sql import as_table, q, require_columns, wrap

_FUNCTIONS = (
    "lag",
    "lead",
    "row_number",
    "rank",
    "dense_rank",
    "cumsum",
    "rolling_sum",
    "rolling_mean",
    "rolling_min",
    "rolling_max",
)
_NEEDS_COLUMN = {
    "lag",
    "lead",
    "cumsum",
    "rolling_sum",
    "rolling_mean",
    "rolling_min",
    "rolling_max",
}


def _window(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    function = params["function"]
    order_by = tuple(params["order_by"] or ())
    partition_by = tuple(params["partition_by"] or ())
    if not order_by:
        raise StepError("Window steps require order_by so results are deterministic.")
    order_by = require_columns(table, order_by)
    if partition_by:
        partition_by = require_columns(table, partition_by)

    column = params["column"]
    if function in _NEEDS_COLUMN:
        if not column:
            raise StepError(f"{function} requires a column.")
        require_columns(table, (column,))
    output = params["output"] or (f"{function}_{column}" if column else function)
    if output in table.schema:
        raise StepError(f"Output column {output!r} already exists.")

    over_parts: list[str] = []
    if partition_by:
        over_parts.append("PARTITION BY " + ", ".join(q(name) for name in partition_by))
    over_parts.append("ORDER BY " + ", ".join(q(name) for name in order_by))
    over = " ".join(over_parts)

    if function in {"lag", "lead"}:
        offset = int(params["offset"])
        if offset < 0:
            raise StepError("Window offset must be >= 0.")
        expression = f"{function.upper()}({q(column)}, {offset}) OVER ({over})"
    elif function == "row_number":
        expression = f"ROW_NUMBER() OVER ({over})"
    elif function == "rank":
        expression = f"RANK() OVER ({over})"
    elif function == "dense_rank":
        expression = f"DENSE_RANK() OVER ({over})"
    else:
        size = int(params["window"])
        if size < 1:
            raise StepError("Window size must be >= 1.")
        aggregate = {
            "cumsum": "SUM",
            "rolling_sum": "SUM",
            "rolling_mean": "AVG",
            "rolling_min": "MIN",
            "rolling_max": "MAX",
        }[function]
        if function == "cumsum":
            frame = "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
        else:
            frame = f"ROWS BETWEEN {size - 1} PRECEDING AND CURRENT ROW"
        expression = f"{aggregate}({q(column)}) OVER ({over} {frame})"

    sql = f"SELECT *, {expression} AS {q(output)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


STEPS = (
    StepDefinition(
        name="window",
        title="Window function",
        category="window",
        description="Add lag/lead, ranking, cumulative or rolling values.",
        params=(
            StepParam("function", "enum", choices=_FUNCTIONS, description="Window function."),
            StepParam(
                "column",
                "column",
                required=False,
                default=None,
                description="Input column where required.",
            ),
            StepParam(
                "output", "column", required=False, default=None, description="Output column name."
            ),
            StepParam(
                "partition_by",
                "columns",
                required=False,
                default=(),
                description="Partition columns.",
            ),
            StepParam("order_by", "columns", description="Order columns."),
            StepParam(
                "offset", "integer", required=False, default=1, description="lag/lead offset."
            ),
            StepParam(
                "window",
                "integer",
                required=False,
                default=3,
                description="Rolling window size in rows.",
            ),
        ),
        compile=_window,
    ),
)

__all__ = ["STEPS"]
