"""Join and union steps across project datasets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..engine.duckdb_engine import DuckDBTable
from .registry import StepContext, StepDefinition, StepError, StepParam
from .sql import (
    as_table,
    combined_schema,
    q,
    require_columns,
    wrap,
)

_JOIN_TYPES = {
    "inner": "INNER JOIN",
    "left": "LEFT JOIN",
    "right": "RIGHT JOIN",
    "full": "FULL OUTER JOIN",
}


def _qualified(alias: str, column: str) -> str:
    return f"{alias}.{q(column)}"


def _assert_unique(context: StepContext, table: DuckDBTable, columns: tuple[str, ...]) -> None:
    group = ", ".join(q(column) for column in columns)
    sql = (
        f"SELECT 1 FROM (SELECT {group} FROM {wrap(table)} AS _u "
        f"GROUP BY {group} HAVING COUNT(*) > 1 LIMIT 1) AS _dupes"
    )
    if context.engine.query(sql):
        raise StepError(
            f"Join cardinality check failed: {columns!r} are not unique in dataset {table.name!r}."
        )


def _join(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    right = context.dataset(params["dataset"])
    left_on = require_columns(table, params["left_on"])
    right_on = tuple(params["right_on"] or left_on)
    right_on = require_columns(right, right_on)
    if len(left_on) != len(right_on):
        raise StepError("left_on and right_on must have the same number of columns.")

    cardinality = params["cardinality"]
    if cardinality in {"one_to_one", "one_to_many"}:
        _assert_unique(context, table, left_on)
    if cardinality in {"one_to_one", "many_to_one"}:
        _assert_unique(context, right, right_on)

    left_names = {name.casefold() for name in table.schema.names}
    suffix = params["suffix"] or "_right"
    excluded = set(right_on)
    renames: dict[str, str] = {}
    used = {name.casefold() for name in table.schema.names}
    for column in right.schema.names:
        if column in excluded:
            continue
        candidate = column
        if candidate.casefold() in left_names or candidate.casefold() in used:
            candidate = f"{column}{suffix}"
            counter = 2
            while candidate.casefold() in used:
                candidate = f"{column}{suffix}{counter}"
                counter += 1
        renames[column] = candidate
        used.add(candidate.casefold())

    select_parts = [_qualified("l", column) for column in table.schema.names]
    select_parts.extend(
        f"{_qualified('r', column)} AS {q(new_name)}" for column, new_name in renames.items()
    )
    on_clause = " AND ".join(
        f"{_qualified('l', left)} = {_qualified('r', right_name)}"
        for left, right_name in zip(left_on, right_on, strict=True)
    )
    sql = (
        f"SELECT {', '.join(select_parts)} "
        f"FROM {wrap(table)} AS l {_JOIN_TYPES[params['how']]} {wrap(right)} AS r "
        f"ON {on_clause}"
    )

    right_schema = right.schema.with_columns(
        tuple(column for column in right.schema.columns if column.name not in excluded)
    )
    schema = combined_schema(table.schema, right_schema, renames)
    sources = tuple(dict.fromkeys((*table.sources, *right.sources)))
    return as_table(
        context,
        sql,
        table,
        base_schema=schema,
        sources=sources,
    )


def _union(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    names = params["datasets"]
    if not names:
        raise StepError("Union needs at least one additional dataset.")
    tables = [table, *(context.dataset(name) for name in names)]
    columns = table.schema.names
    for other in tables[1:]:
        if set(other.schema.names) != set(columns):
            raise StepError(
                f"Union column mismatch with dataset {other.name!r}: "
                f"{other.schema.names!r} versus {columns!r}."
            )
    operator = "UNION ALL" if params["all"] else "UNION"
    select = ", ".join(q(column) for column in columns)
    parts = [f"SELECT {select} FROM {wrap(other)} AS _u" for other in tables]
    sql = f" {operator} ".join(parts)
    sources = tuple(dict.fromkeys(source for other in tables for source in other.sources))
    return as_table(context, sql, table, base_schema=table.schema, sources=sources)


STEPS = (
    StepDefinition(
        name="join",
        title="Join another dataset",
        category="join",
        description=(
            "Join the current table to a project dataset with an explicit cardinality check."
        ),
        params=(
            StepParam("dataset", "dataset", description="Dataset id to join."),
            StepParam(
                "how",
                "enum",
                required=False,
                default="left",
                choices=("inner", "left", "right", "full"),
                description="Join type.",
            ),
            StepParam("left_on", "columns", description="Key columns in the current table."),
            StepParam(
                "right_on",
                "columns",
                required=False,
                default=(),
                description="Key columns in the other table; defaults to left_on.",
            ),
            StepParam(
                "suffix",
                "string",
                required=False,
                default="_right",
                description="Suffix for name collisions.",
            ),
            StepParam(
                "cardinality",
                "enum",
                required=False,
                default="many_to_many",
                choices=("many_to_many", "many_to_one", "one_to_many", "one_to_one"),
                description="Expected key cardinality; validated before joining.",
            ),
        ),
        compile=_join,
    ),
    StepDefinition(
        name="union",
        title="Union datasets",
        category="join",
        description="Stack rows from other datasets with matching columns.",
        params=(
            StepParam("datasets", "columns", description="Dataset ids to append."),
            StepParam(
                "all", "boolean", required=False, default=True, description="Keep duplicates."
            ),
        ),
        compile=_union,
    ),
)

__all__ = ["STEPS"]
