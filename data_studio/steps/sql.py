"""Shared SQL construction helpers for built-in steps."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from ..engine.duckdb_engine import (
    DuckDBTable,
    quote_identifier,
    role_for_column,
    sql_literal,
)
from ..kernel.schema import Column, DataType, TableSchema
from .registry import StepContext, StepError

CAST_TYPES = (
    "boolean",
    "integer",
    "float",
    "decimal",
    "string",
    "date",
    "datetime",
    "time",
    "category",
)

_SQL_TYPES = {
    "boolean": "BOOLEAN",
    "integer": "BIGINT",
    "float": "DOUBLE",
    "decimal": "DECIMAL(18,4)",
    "string": "VARCHAR",
    "category": "VARCHAR",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "time": "TIME",
    "json": "JSON",
}

AGGREGATES = {
    "sum": "SUM",
    "mean": "AVG",
    "avg": "AVG",
    "median": "MEDIAN",
    "min": "MIN",
    "max": "MAX",
    "count": "COUNT",
    "count_distinct": "COUNT_DISTINCT",
    "stddev": "STDDEV_SAMP",
    "std": "STDDEV_SAMP",
    "variance": "VAR_SAMP",
    "var": "VAR_SAMP",
    "first": "FIRST",
    "last": "LAST",
    "min_by": "MIN_BY",
    "max_by": "MAX_BY",
    "quantile": "QUANTILE_CONT",
    "q": "QUANTILE_CONT",
}

_TYPE_ALIASES: Mapping[str, str] = {
    "bool": "BOOLEAN",
    "int": "BIGINT",
    "bigint": "BIGINT",
    "double": "DOUBLE",
    "real": "DOUBLE",
    "varchar": "VARCHAR",
    "text": "VARCHAR",
    "timestamp": "TIMESTAMP",
    "datetime": "TIMESTAMP",
}


def q(name: str) -> str:
    return quote_identifier(name)


def wrap(table: DuckDBTable) -> str:
    return f"({table.sql})"


def literal(value: Any) -> str:
    return sql_literal(value)


def require_columns(
    table: DuckDBTable,
    columns: Sequence[str],
    *,
    label: str = "column",
) -> tuple[str, ...]:
    if isinstance(columns, str):
        columns = (columns,)
    resolved = tuple(columns)
    if not resolved:
        raise StepError(f"At least one {label} is required.")
    for column in resolved:
        if column not in table.schema:
            available = ", ".join(table.schema.names) or "(none)"
            raise StepError(f"Unknown {label} {column!r}. Available columns: {available}.")
    if len(set(resolved)) != len(resolved):
        raise StepError(f"Duplicate {label} in {list(resolved)!r}.")
    return resolved


def optional_columns(
    table: DuckDBTable,
    columns: Sequence[str] | None,
    *,
    label: str = "column",
) -> tuple[str, ...]:
    if not columns:
        return table.schema.names
    return require_columns(table, columns, label=label)


def sql_type(name: str) -> str:
    normalised = str(name).strip().casefold()
    if normalised in _SQL_TYPES:
        return _SQL_TYPES[normalised]
    if normalised in _TYPE_ALIASES:
        return _TYPE_ALIASES[normalised]
    raise StepError(f"Unsupported cast type {name!r}. Use one of: {', '.join(CAST_TYPES)}.")


def cast_expression(expression: str, dtype: str, *, try_cast: bool = False) -> str:
    function = "TRY_CAST" if try_cast else "CAST"
    return f"{function}({expression} AS {sql_type(dtype)})"


def as_table(
    context: StepContext,
    sql: str,
    source: DuckDBTable | None = None,
    *,
    base_schema: TableSchema | None = None,
    sources: Sequence[Any] | None = None,
    name: str | None = None,
) -> DuckDBTable:
    effective_sources = (
        tuple(sources) if sources is not None else (source.sources if source is not None else ())
    )
    effective_schema = (
        base_schema if base_schema is not None else (source.schema if source is not None else None)
    )
    return context.engine.table(
        sql,
        name=name or (source.name if source is not None else None),
        sources=effective_sources,
        base_schema=effective_schema,
    )


def retable(table: DuckDBTable, schema: TableSchema) -> DuckDBTable:
    return DuckDBTable(table.engine, table.sql, schema, table.sources, table.name)


def replace_column_role(schema: TableSchema, name: str) -> TableSchema:
    """Refresh a column's role after its logical type changed (e.g. cast)."""

    columns: list[Column] = []
    for column in schema.columns:
        if column.name == name:
            dtype = column.dtype
            columns.append(replace(column, role=role_for_column(column.name, dtype)))
        else:
            columns.append(column)
    return schema.with_columns(columns)


def output_name(prefix: str, column: str | None = None) -> str:
    base = f"{prefix}_{column}" if column else prefix
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", base)
    if not cleaned or cleaned[0].isdigit():
        cleaned = "col_" + cleaned
    return cleaned


def aggregation_expression(aggregation: Mapping[str, Any]) -> tuple[str, str]:
    function = str(aggregation.get("function", "")).strip().casefold()
    if function not in AGGREGATES:
        raise StepError(
            f"Unsupported aggregate {function!r}. Use one of: {', '.join(sorted(AGGREGATES))}."
        )
    column = aggregation.get("column")
    if column is not None and not isinstance(column, str):
        raise StepError("Aggregation column must be a string or null.")
    column = column.strip() if isinstance(column, str) else None
    if column == "*":
        column = None
    output = aggregation.get("output")
    if output is not None and (not isinstance(output, str) or not output.strip()):
        raise StepError("Aggregation output must be a non-empty string when provided.")

    if function == "count" and column is None:
        expression = "COUNT(*)"
        default_name = "count"
    elif function == "count_distinct":
        if column is None:
            raise StepError("count_distinct requires a column.")
        expression = f"COUNT(DISTINCT {q(column)})"
        default_name = output_name("count_distinct", column)
    elif function in {"min_by", "max_by"}:
        if column is None:
            raise StepError(f"{function} requires a column.")
        by = aggregation.get("by")
        if not isinstance(by, str) or not by.strip():
            raise StepError(f"{function} requires a 'by' column.")
        expression = f"{AGGREGATES[function]}({q(column)}, {q(by.strip())})"
        default_name = output_name(function, column)
    elif function == "quantile" or function == "q":
        if column is None:
            raise StepError("quantile requires a column.")
        probability = aggregation.get("p", 0.5)
        try:
            probability = float(probability)
        except (TypeError, ValueError) as exc:
            raise StepError("quantile 'p' must be a number between 0 and 1.") from exc
        if not 0.0 <= probability <= 1.0:
            raise StepError("quantile 'p' must be between 0 and 1.")
        expression = f"QUANTILE_CONT({q(column)}, {probability})"
        default_name = output_name("quantile", column)
    else:
        if column is None:
            raise StepError(f"Aggregate {function!r} requires a column.")
        expression = f"{AGGREGATES[function]}({q(column)})"
        default_name = output_name(function, column)
    return expression, str(output or default_name).strip()


def sort_clause(
    table: DuckDBTable,
    columns: Sequence[str],
    *,
    descending: bool = False,
) -> str:
    if not columns:
        return ""
    require_columns(table, columns)
    direction = " DESC" if descending else ""
    return " ORDER BY " + ", ".join(f"{q(column)}{direction}" for column in columns)


def passthrough_select(table: DuckDBTable, exclude: Sequence[str] = ()) -> list[str]:
    excluded = set(exclude)
    return [q(column) for column in table.schema.names if column not in excluded]


def combined_schema(
    left: TableSchema, right: TableSchema, renames: Mapping[str, str]
) -> TableSchema:
    columns: list[Column] = list(left.columns)
    for column in right.columns:
        name = renames.get(column.name, column.name)
        dtype = column.dtype if isinstance(column.dtype, DataType) else DataType(str(column.dtype))
        columns.append(
            Column(
                name=name,
                dtype=dtype,
                nullable=column.nullable,
                role=column.role,
                unit=column.unit,
                description=column.description,
                metadata=dict(column.metadata),
            )
        )
    return TableSchema(columns=tuple(columns))


__all__ = [
    "AGGREGATES",
    "CAST_TYPES",
    "aggregation_expression",
    "as_table",
    "cast_expression",
    "combined_schema",
    "literal",
    "optional_columns",
    "output_name",
    "passthrough_select",
    "q",
    "replace_column_role",
    "require_columns",
    "retable",
    "sort_clause",
    "sql_type",
    "wrap",
]
