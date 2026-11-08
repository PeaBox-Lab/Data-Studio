"""Column and row transformation steps."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from ..engine.duckdb_engine import DuckDBTable
from ..kernel.schema import TableSchema
from .expr import compile_expression
from .registry import StepContext, StepDefinition, StepError, StepParam
from .sql import (
    CAST_TYPES,
    as_table,
    cast_expression,
    q,
    replace_column_role,
    require_columns,
    retable,
    sort_clause,
    wrap,
)


def _select(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    columns = require_columns(table, params["columns"])
    sql = f"SELECT {', '.join(q(column) for column in columns)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table, base_schema=table.schema.select(*columns))


def _drop(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    drop = set(require_columns(table, params["columns"]))
    remaining = tuple(column for column in table.schema.names if column not in drop)
    if not remaining:
        raise StepError("Dropping these columns would leave the table empty.")
    sql = f"SELECT {', '.join(q(column) for column in remaining)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table, base_schema=table.schema.select(*remaining))


def _rename(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    mapping = params["columns"]
    if not mapping:
        raise StepError("Rename needs at least one column mapping.")
    require_columns(table, tuple(mapping))
    new_names = [str(value).strip() for value in mapping.values()]
    if any(not name for name in new_names):
        raise StepError("New column names must not be empty.")
    final_names = [mapping.get(column, column) for column in table.schema.names]
    lowered = [name.casefold() for name in final_names]
    if len(set(lowered)) != len(lowered):
        raise StepError("Renaming would create duplicate column names.")

    parts = [
        q(column) if column not in mapping else f"{q(column)} AS {q(mapping[column])}"
        for column in table.schema.names
    ]
    renamed_columns = tuple(
        replace(column, name=mapping.get(column.name, column.name))
        for column in table.schema.columns
    )
    schema = TableSchema(columns=renamed_columns, primary_key=())
    sql = f"SELECT {', '.join(parts)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table, base_schema=schema)


def _cast(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    column = params["column"]
    require_columns(table, (column,))
    output = params["output"] or column
    if output != column and output in table.schema:
        raise StepError(f"Output column {output!r} already exists.")
    expression = cast_expression(q(column), params["dtype"], try_cast=params["try_cast"])
    parts = [
        f"{expression} AS {q(output)}" if name == column else q(name) for name in table.schema.names
    ]
    sql = f"SELECT {', '.join(parts)} FROM {wrap(table)} AS _src"
    result = as_table(context, sql, table)
    if output in result.schema:
        result = retable(result, replace_column_role(result.schema, output))
    return result


def _derive(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    output = params["column"]
    if output in table.schema and not params["replace"]:
        raise StepError(f"Column {output!r} already exists; set replace=true to overwrite it.")
    expression = compile_expression(params["expression"], table.schema)
    parts = [
        f"{expression} AS {q(output)}" if name == output else q(name)
        for name in table.schema.names
        if name != output or params["replace"]
    ]
    if output not in table.schema:
        parts.append(f"{expression} AS {q(output)}")
    sql = f"SELECT {', '.join(parts)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


def _filter(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    expression = compile_expression(params["expression"], table.schema)
    sql = f"SELECT * FROM {wrap(table)} AS _src WHERE {expression}"
    return as_table(context, sql, table)


def _sort(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    columns = require_columns(table, params["columns"])
    order = sort_clause(table, columns, descending=params["descending"])
    sql = f"SELECT * FROM {wrap(table)} AS _src{order}"
    return as_table(context, sql, table)


def _distinct(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    columns = params["columns"] or table.schema.names
    columns = require_columns(table, columns)
    sql = f"SELECT DISTINCT {', '.join(q(column) for column in columns)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table, base_schema=table.schema.select(*columns))


STEPS = (
    StepDefinition(
        name="select",
        title="Select columns",
        category="transform",
        description="Keep only the named columns, in the given order.",
        params=(StepParam("columns", "columns", description="Columns to keep."),),
        compile=_select,
    ),
    StepDefinition(
        name="drop",
        title="Drop columns",
        category="transform",
        description="Remove the named columns.",
        params=(StepParam("columns", "columns", description="Columns to drop."),),
        compile=_drop,
    ),
    StepDefinition(
        name="rename",
        title="Rename columns",
        category="transform",
        description="Rename columns with an old-name to new-name mapping.",
        params=(StepParam("columns", "mapping", description="Mapping of old name to new name."),),
        compile=_rename,
    ),
    StepDefinition(
        name="cast",
        title="Change column type",
        category="transform",
        description="Cast a column to a logical type; try_cast yields nulls instead of failing.",
        params=(
            StepParam("column", "column", description="Column to cast."),
            StepParam("dtype", "enum", choices=CAST_TYPES, description="Target logical type."),
            StepParam(
                "output",
                "column",
                required=False,
                default=None,
                description="Optional output column.",
            ),
            StepParam(
                "try_cast",
                "boolean",
                required=False,
                default=False,
                description="Return null instead of failing.",
            ),
        ),
        compile=_cast,
    ),
    StepDefinition(
        name="derive",
        title="Add calculated column",
        category="transform",
        description="Add or replace a column from a checked expression.",
        params=(
            StepParam("column", "column", description="New column name."),
            StepParam("expression", "expression", description="Expression, e.g. amount * 1.1."),
            StepParam(
                "replace",
                "boolean",
                required=False,
                default=False,
                description="Overwrite an existing column.",
            ),
        ),
        compile=_derive,
    ),
    StepDefinition(
        name="filter",
        title="Filter rows",
        category="transform",
        description="Keep rows where an expression is true.",
        params=(StepParam("expression", "expression", description="Boolean expression."),),
        compile=_filter,
    ),
    StepDefinition(
        name="sort",
        title="Sort rows",
        category="transform",
        description="Order rows by one or more columns.",
        params=(
            StepParam("columns", "columns", description="Sort columns."),
            StepParam(
                "descending",
                "boolean",
                required=False,
                default=False,
                description="Sort descending.",
            ),
        ),
        compile=_sort,
    ),
    StepDefinition(
        name="distinct",
        title="Distinct rows",
        category="transform",
        description="Remove duplicate rows across all or selected columns.",
        params=(
            StepParam(
                "columns",
                "columns",
                required=False,
                default=(),
                description="Columns to compare; all when empty.",
            ),
        ),
        compile=_distinct,
    ),
)

__all__ = ["STEPS"]
