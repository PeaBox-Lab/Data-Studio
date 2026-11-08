"""Cleaning steps: missing values, duplicates and outliers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..engine.duckdb_engine import DuckDBTable
from ..kernel.schema import NUMERIC_TYPES
from .registry import StepContext, StepDefinition, StepError, StepParam
from .sql import (
    as_table,
    literal,
    optional_columns,
    q,
    require_columns,
    wrap,
)


def _replace_select(
    table: DuckDBTable,
    replacements: Mapping[str, str],
    *,
    exclude: tuple[str, ...] = (),
) -> str:
    parts: list[str] = []
    for name in table.schema.names:
        if name in replacements:
            parts.append(f"{replacements[name]} AS {q(name)}")
        elif name not in exclude:
            parts.append(q(name))
    return ", ".join(parts)


def _drop_missing(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    columns = optional_columns(table, params["columns"])
    if params["how"] == "any":
        predicate = " AND ".join(f"{q(column)} IS NOT NULL" for column in columns)
    else:
        predicate = " OR ".join(f"{q(column)} IS NOT NULL" for column in columns)
    sql = f"SELECT * FROM {wrap(table)} AS _src WHERE {predicate}"
    return as_table(context, sql, table)


def _fill_missing(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    columns = params["columns"]
    if columns:
        columns = require_columns(table, columns)
    else:
        columns = tuple(column.name for column in table.schema.columns if column.nullable)
    if not columns:
        raise StepError("No nullable columns to fill.")

    method = params["method"]
    replacements: dict[str, str] = {}

    if method == "ffill" or method == "bfill":
        order_by = require_columns(table, params["order_by"]) if params["order_by"] else ()
        if order_by:
            order = ", ".join(q(column) for column in order_by)
            row_number = f"ROW_NUMBER() OVER (ORDER BY {order})"
        else:
            row_number = "ROW_NUMBER() OVER ()"
        inner = ", ".join(
            [q(column) for column in table.schema.names] + [f"{row_number} AS __ds_rn"]
        )
        direction = "DESC" if method == "bfill" else "ASC"
        for column in table.schema.names:
            if column in columns:
                replacements[column] = (
                    f"LAST_VALUE({q(column)} IGNORE NULLS) OVER ("
                    f"ORDER BY __ds_rn {direction} "
                    "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
                )
        select = _replace_select(table, replacements, exclude=())
        sql = f"SELECT {select} FROM (SELECT {inner} FROM {wrap(table)} AS _src) AS _ordered"
        return as_table(context, sql, table)

    for column in columns:
        original = q(column)
        if method == "constant":
            replacement = literal(params["value"])
        elif method == "zero":
            replacement = "0"
        elif method == "mean":
            replacement = f"AVG({original}) OVER ()"
        elif method == "median":
            replacement = f"QUANTILE_CONT({original}, 0.5) OVER ()"
        elif method == "mode":
            replacement = (
                f"(SELECT {original} FROM {wrap(table)} AS _mode "
                f"WHERE {original} IS NOT NULL GROUP BY {original} "
                f"ORDER BY COUNT(*) DESC, {original} LIMIT 1)"
            )
        else:  # pragma: no cover - enum validation prevents this
            raise StepError(f"Unsupported fill method {method!r}.")
        replacements[column] = f"COALESCE({original}, {replacement})"

    sql = f"SELECT {_replace_select(table, replacements)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


def _deduplicate(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    columns = optional_columns(table, params["columns"])
    order_by = tuple(params["order_by"] or ())
    if order_by:
        require_columns(table, order_by)
    partition = ", ".join(q(column) for column in columns)
    if order_by:
        direction = "ASC" if params["keep"] == "first" else "DESC"
        order = " ORDER BY " + ", ".join(f"{q(column)} {direction}" for column in order_by)
    else:
        order = ""
    row_number = f"ROW_NUMBER() OVER (PARTITION BY {partition}{order})"
    inner = ", ".join([q(column) for column in table.schema.names] + [f"{row_number} AS __ds_rn"])
    outer = ", ".join(q(column) for column in table.schema.names)
    sql = (
        f"SELECT {outer} FROM (SELECT {inner} FROM {wrap(table)} AS _src) AS _dedup "
        "WHERE __ds_rn = 1"
    )
    return as_table(context, sql, table)


def _flag_outliers(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    column = params["column"]
    require_columns(table, (column,))
    dtype = table.schema.column(column).dtype
    if dtype not in NUMERIC_TYPES:
        raise StepError(f"Column {column!r} must be numeric to flag outliers.")
    output = params["output"] or f"{column}_outlier"
    if output in table.schema:
        raise StepError(f"Output column {output!r} already exists.")

    original = q(column)
    if params["method"] == "iqr":
        q1 = f"QUANTILE_CONT({original}, 0.25) OVER ()"
        q3 = f"QUANTILE_CONT({original}, 0.75) OVER ()"
        threshold = float(params["threshold"] if params["threshold"] is not None else 1.5)
        spread = f"({q3} - {q1})"
        expression = (
            f"COALESCE(({original} < {q1} - {threshold} * {spread}) OR "
            f"({original} > {q3} + {threshold} * {spread}), FALSE)"
        )
    else:
        threshold = float(params["threshold"] if params["threshold"] is not None else 3.0)
        mean = f"AVG({original}) OVER ()"
        deviation = f"STDDEV_SAMP({original}) OVER ()"
        expression = f"COALESCE(ABS({original} - {mean}) > {threshold} * {deviation}, FALSE)"

    sql = f"SELECT *, {expression} AS {q(output)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


STEPS = (
    StepDefinition(
        name="drop_missing",
        title="Drop rows with missing values",
        category="clean",
        description="Remove rows where selected columns are null.",
        params=(
            StepParam(
                "columns",
                "columns",
                required=False,
                default=(),
                description="Columns to check; all when empty.",
            ),
            StepParam(
                "how",
                "enum",
                required=False,
                default="any",
                choices=("any", "all"),
                description="Drop when any or all selected values are null.",
            ),
        ),
        compile=_drop_missing,
    ),
    StepDefinition(
        name="fill_missing",
        title="Fill missing values",
        category="clean",
        description="Replace nulls with a constant, statistic, or adjacent value.",
        params=(
            StepParam(
                "columns",
                "columns",
                required=False,
                default=(),
                description="Columns to fill; all nullable columns when empty.",
            ),
            StepParam(
                "method",
                "enum",
                required=False,
                default="constant",
                choices=("constant", "zero", "mean", "median", "mode", "ffill", "bfill"),
                description="Fill strategy.",
            ),
            StepParam(
                "value",
                "any",
                required=False,
                default=None,
                description="Value for the constant strategy.",
            ),
            StepParam(
                "order_by",
                "columns",
                required=False,
                default=(),
                description="Row order for ffill/bfill.",
            ),
        ),
        compile=_fill_missing,
    ),
    StepDefinition(
        name="deduplicate",
        title="Remove duplicate rows",
        category="clean",
        description="Keep the first or last row per key combination.",
        params=(
            StepParam(
                "columns",
                "columns",
                required=False,
                default=(),
                description="Key columns; all columns when empty.",
            ),
            StepParam(
                "keep",
                "enum",
                required=False,
                default="first",
                choices=("first", "last"),
                description="Keep first or last.",
            ),
            StepParam(
                "order_by",
                "columns",
                required=False,
                default=(),
                description="Order that defines first/last.",
            ),
        ),
        compile=_deduplicate,
    ),
    StepDefinition(
        name="flag_outliers",
        title="Flag outliers",
        category="clean",
        description="Add a boolean column marking IQR or z-score outliers.",
        params=(
            StepParam("column", "column", description="Numeric column to inspect."),
            StepParam(
                "method",
                "enum",
                required=False,
                default="iqr",
                choices=("iqr", "zscore"),
                description="Detection method.",
            ),
            StepParam(
                "output", "column", required=False, default=None, description="Output column name."
            ),
            StepParam(
                "threshold",
                "number",
                required=False,
                default=None,
                description="IQR multiplier or z-score threshold.",
            ),
        ),
        compile=_flag_outliers,
    ),
)

__all__ = ["STEPS"]
