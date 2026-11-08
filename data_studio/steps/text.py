"""Text cleaning and extraction steps."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..engine.duckdb_engine import DuckDBTable
from .registry import StepContext, StepDefinition, StepError, StepParam
from .sql import as_table, literal, q, require_columns, wrap


def _replace_select(table: DuckDBTable, replacements: Mapping[str, str]) -> str:
    return ", ".join(
        f"{replacements[name]} AS {q(name)}" if name in replacements else q(name)
        for name in table.schema.names
    )


def _title_expression(column: str) -> str:
    return (
        "array_to_string(list_transform("
        f"string_split(lower({q(column)}), ' '), "
        "lambda x: upper(substr(x, 1, 1)) || substr(x, 2)), ' ')"
    )


def _trim_text(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    columns = require_columns(table, params["columns"])
    function = {"both": "TRIM", "left": "LTRIM", "right": "RTRIM"}[params["side"]]
    replacements = {column: f"{function}({q(column)})" for column in columns}
    sql = f"SELECT {_replace_select(table, replacements)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


def _replace_text(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    column = params["column"]
    require_columns(table, (column,))
    expression = (
        f"REGEXP_REPLACE({q(column)}, {literal(params['pattern'])}, "
        f"{literal(params['replacement'])}, 'g')"
        if params["regex"]
        else f"REPLACE({q(column)}, {literal(params['pattern'])}, {literal(params['replacement'])})"
    )
    sql = f"SELECT {_replace_select(table, {column: expression})} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


def _normalize_text(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    columns = require_columns(table, params["columns"])
    replacements: dict[str, str] = {}
    for column in columns:
        if params["case"] == "lower":
            replacements[column] = f"LOWER({q(column)})"
        elif params["case"] == "upper":
            replacements[column] = f"UPPER({q(column)})"
        else:
            replacements[column] = _title_expression(column)
    sql = f"SELECT {_replace_select(table, replacements)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


def _extract_text(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    column = params["column"]
    require_columns(table, (column,))
    output = params["output"] or f"{column}_extract"
    if output in table.schema:
        raise StepError(f"Output column {output!r} already exists.")
    expression = (
        f"REGEXP_EXTRACT({q(column)}, {literal(params['pattern'])}, {int(params['group'])})"
    )
    sql = f"SELECT *, {expression} AS {q(output)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


def _split_text(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    column = params["column"]
    require_columns(table, (column,))
    output = params["output"] or f"{column}_part"
    if output in table.schema:
        raise StepError(f"Output column {output!r} already exists.")
    index = int(params["index"])
    if index == 0:
        raise StepError("Split index is 1-based and must not be 0.")
    expression = f"STRING_SPLIT({q(column)}, {literal(params['delimiter'])})[{index}]"
    sql = f"SELECT *, {expression} AS {q(output)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


def _concat_columns(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    columns = require_columns(table, params["columns"])
    output = params["column"]
    if output in table.schema:
        raise StepError(f"Output column {output!r} already exists.")
    expression = (
        f"CONCAT_WS({literal(params['separator'])}, {', '.join(q(name) for name in columns)})"
    )
    sql = f"SELECT *, {expression} AS {q(output)} FROM {wrap(table)} AS _src"
    return as_table(context, sql, table)


STEPS = (
    StepDefinition(
        name="trim_text",
        title="Trim whitespace",
        category="text",
        description="Trim leading and/or trailing whitespace.",
        params=(
            StepParam("columns", "columns", description="Text columns to trim."),
            StepParam(
                "side",
                "enum",
                required=False,
                default="both",
                choices=("both", "left", "right"),
                description="Which side to trim.",
            ),
        ),
        compile=_trim_text,
    ),
    StepDefinition(
        name="replace_text",
        title="Replace text",
        category="text",
        description="Replace literal or regular-expression matches.",
        params=(
            StepParam("column", "column", description="Text column."),
            StepParam("pattern", "string", description="Text or regular expression to find."),
            StepParam("replacement", "string", description="Replacement text."),
            StepParam(
                "regex",
                "boolean",
                required=False,
                default=True,
                description="Treat pattern as a regular expression.",
            ),
        ),
        compile=_replace_text,
    ),
    StepDefinition(
        name="normalize_text",
        title="Normalize case",
        category="text",
        description="Lower-case, upper-case or title-case text columns.",
        params=(
            StepParam("columns", "columns", description="Text columns."),
            StepParam(
                "case", "enum", choices=("lower", "upper", "title"), description="Target case."
            ),
        ),
        compile=_normalize_text,
    ),
    StepDefinition(
        name="extract_text",
        title="Extract text pattern",
        category="text",
        description="Extract a regular-expression group into a new column.",
        params=(
            StepParam("column", "column", description="Text column."),
            StepParam("pattern", "string", description="Regular expression."),
            StepParam("group", "integer", required=False, default=1, description="Capture group."),
            StepParam(
                "output", "column", required=False, default=None, description="Output column name."
            ),
        ),
        compile=_extract_text,
    ),
    StepDefinition(
        name="split_text",
        title="Split text",
        category="text",
        description="Take one field from a delimited string.",
        params=(
            StepParam("column", "column", description="Text column."),
            StepParam("delimiter", "string", description="Delimiter."),
            StepParam(
                "index", "integer", required=False, default=1, description="1-based field index."
            ),
            StepParam(
                "output", "column", required=False, default=None, description="Output column name."
            ),
        ),
        compile=_split_text,
    ),
    StepDefinition(
        name="concat_columns",
        title="Concatenate columns",
        category="text",
        description="Join several columns into a new text column.",
        params=(
            StepParam("columns", "columns", description="Columns to join."),
            StepParam("column", "column", description="Output column name."),
            StepParam("separator", "string", required=False, default=" ", description="Separator."),
        ),
        compile=_concat_columns,
    ),
)

__all__ = ["STEPS"]
