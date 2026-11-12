"""Conditional-aggregate pivot tables.

One pivot (column) dimension, any number of row dimensions and any number of
value aggregations — more flexible than the kernel ``pivot`` step, which is single
column / single aggregate. Built as plain ``GROUP BY`` with ``CASE WHEN`` columns,
so it compiles to exactly one DuckDB query.

* Rows identify a line of the pivot (like Excel's Rows area).
* The pivot column's values become column headers (Excel's Columns area).
* Each value is an aggregation of one column (or ``count`` of rows); several
  aggregations may be declared per value (Excel's Values area).
* Output column headers are ``"<label> · <value>"``; labels default to
  ``<aggregation>_<column>`` (or just the aggregation for ``count``).
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .engine.duckdb_engine import sql_literal
from .kernel.schema import NUMERIC_TYPES
from .semantic.engine import QueryFilter, QueryOrder, SemanticColumn, _json_value
from .steps.sql import q

PIVOT_AGGREGATES = {
    "sum": "SUM",
    "avg": "AVG",
    "mean": "AVG",
    "count": "COUNT",
    "count_distinct": "COUNT",  # emitted as COUNT(DISTINCT col) explicitly
    "min": "MIN",
    "max": "MAX",
    "median": "MEDIAN",
    "stddev": "STDDEV_SAMP",
}
_MAX_PIVOT_VALUES = 40
_MAX_PIVOT_OUTPUT_COLUMNS = 240


class PivotError(ValueError):
    """A pivot request was invalid or could not be executed."""


@dataclass(frozen=True, slots=True)
class PivotValue:
    """One aggregation to place on the pivot's values axis."""

    column: str | None
    aggregation: str = "sum"
    label: str | None = None

    def __post_init__(self) -> None:
        aggregation = str(self.aggregation).strip().lower()
        if aggregation not in PIVOT_AGGREGATES:
            raise PivotError(
                f"Aggregation must be one of: {', '.join(sorted(PIVOT_AGGREGATES))}."
            )
        column = self.column
        if column is not None:
            column = str(column).strip()
            if not column:
                column = None
        if aggregation != "count" and column is None:
            raise PivotError(f"Aggregation {aggregation!r} needs a column.")
        label = str(self.label).strip() if isinstance(self.label, str) else ""
        object.__setattr__(self, "column", column)
        object.__setattr__(self, "aggregation", aggregation)
        object.__setattr__(
            self,
            "label",
            label or (f"{aggregation}_{column}" if column else aggregation),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "aggregation": self.aggregation,
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class PivotQuery:
    """A pivot request: rows, one pivot column, values and row-level filters."""

    rows: tuple[str, ...] = ()
    columns: str | None = None
    values: tuple[PivotValue, ...] = ()
    filters: tuple[QueryFilter, ...] = ()
    order_by: tuple[QueryOrder, ...] = ()
    limit: int | None = None

    def __post_init__(self) -> None:
        rows = tuple(dict.fromkeys(str(item).strip() for item in self.rows))
        if any(not item for item in rows):
            raise PivotError("Row dimensions must not be empty names.")
        object.__setattr__(self, "rows", rows)
        if self.columns is not None:
            column = str(self.columns).strip()
            object.__setattr__(self, "columns", column or None)
        values = tuple(self.values)
        if not values:
            raise PivotError("A pivot needs at least one value aggregation.")
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "filters", tuple(self.filters))
        object.__setattr__(self, "order_by", tuple(self.order_by))
        if self.limit is not None:
            limit = int(self.limit)
            if limit < 1:
                raise PivotError("Pivot limit must be at least 1.")
            object.__setattr__(self, "limit", limit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": list(self.rows),
            "columns": self.columns,
            "values": [item.to_dict() for item in self.values],
            "filters": [item.to_dict() for item in self.filters],
            "order_by": [item.to_dict() for item in self.order_by],
            "limit": self.limit,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PivotQuery:
        if not isinstance(payload, Mapping):
            raise PivotError("A pivot request must be an object.")
        rows = payload.get("rows", ())
        if isinstance(rows, str):
            rows = [item.strip() for item in rows.split(",") if item.strip()]
        raw_values = payload.get("values", ())
        if not isinstance(raw_values, Sequence) or isinstance(raw_values, (str, bytes)):
            raise PivotError("'values' must be a list of aggregation objects.")
        values = tuple(
            item if isinstance(item, PivotValue) else PivotValue(
                column=item.get("column"),
                aggregation=(
                    str(item.get("aggregation", "sum"))
                    if isinstance(item, Mapping)
                    else "sum"
                ),
                label=item.get("label") if isinstance(item, Mapping) else None,
            )
            for item in raw_values
        )
        filters = tuple(
            item if isinstance(item, QueryFilter) else QueryFilter.from_dict(item)
            for item in payload.get("filters", ())
        )
        order_by = tuple(
            item if isinstance(item, QueryOrder) else QueryOrder(
                str(item.get("field", "")), str(item.get("direction", "asc"))
            )
            for item in payload.get("order_by", ())
        )
        return cls(
            rows=tuple(rows),
            columns=payload.get("columns"),
            values=values,
            filters=filters,
            order_by=order_by,
            limit=payload.get("limit"),
        )


@dataclass(frozen=True, slots=True)
class PivotResult:
    query: PivotQuery
    columns: tuple[SemanticColumn, ...]
    rows: tuple[dict[str, Any], ...]
    sql: str
    row_count: int
    truncated: bool = False
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.to_dict(),
            "columns": [column.to_dict() for column in self.columns],
            "rows": [dict(row) for row in self.rows],
            "row_count": self.row_count,
            "truncated": self.truncated,
            "elapsed_ms": self.elapsed_ms,
            "sql": self.sql,
        }


def _condition(expression: str, item: QueryFilter) -> str:
    operator = item.operator
    value = item.value
    if operator == "is_null" or (operator == "=" and value is None):
        return f"{expression} IS NULL"
    if operator == "not_null" or (operator == "!=" and value is None):
        return f"{expression} IS NOT NULL"
    if operator in {"in", "not_in"}:
        values = tuple(value or ())
        if not values:
            return "FALSE" if operator == "in" else "TRUE"
        literals = ", ".join(sql_literal(entry) for entry in values)
        keyword = "IN" if operator == "in" else "NOT IN"
        return f"{expression} {keyword} ({literals})"
    if operator == "between":
        low, high = tuple(value)
        return f"{expression} BETWEEN {sql_literal(low)} AND {sql_literal(high)}"
    if operator == "contains":
        return (
            f"contains(lower(CAST({expression} AS VARCHAR)), "
            f"lower({sql_literal(str(value))}))"
        )
    if operator == "starts_with":
        return (
            f"starts_with(lower(CAST({expression} AS VARCHAR)), "
            f"lower({sql_literal(str(value))}))"
        )
    if operator == "ends_with":
        return (
            f"ends_with(lower(CAST({expression} AS VARCHAR)), "
            f"lower({sql_literal(str(value))}))"
        )
    return f"{expression} {operator} {sql_literal(value)}"


def _distinct(engine: Any, table_sql: str, column: str) -> list[Any]:
    rows = engine.query(
        f"SELECT DISTINCT {q(column)} FROM ({table_sql}) AS _distinct_src "
        f"ORDER BY 1 NULLS LAST LIMIT {_MAX_PIVOT_VALUES + 1}"
    )
    values = [row[0] for row in rows]
    if len(values) > _MAX_PIVOT_VALUES:
        raise PivotError(
            f"Pivot column {column!r} has more than {_MAX_PIVOT_VALUES} distinct "
            "values; narrow the data with a filter first."
        )
    return values


def _alias(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in name)
    return cleaned or "value"


def pivot_query(
    engine: Any,
    table: Any,
    query: PivotQuery,
) -> PivotResult:
    """Execute a pivot against a DuckDB table and return typed rows."""

    if not isinstance(query, PivotQuery):
        raise TypeError("pivot_query expects a PivotQuery.")
    schema = table.schema
    names = {column.name for column in schema.columns}
    numeric = {column.name for column in schema.columns if column.dtype in NUMERIC_TYPES}

    def require(column: str, label: str) -> str:
        if column not in names:
            raise PivotError(f"{label} column {column!r} does not exist.")
        return column

    rows = tuple(require(name, "Row") for name in query.rows)
    pivot_column = require(query.columns, "Pivot") if query.columns else None
    if pivot_column and pivot_column in rows:
        raise PivotError("The pivot column cannot also be a row dimension.")

    values: list[PivotValue] = []
    for value in query.values:
        column = value.column
        if column is not None:
            require(column, "Value")
            if (
                value.aggregation not in {"count", "count_distinct"}
                and column not in numeric
            ):
                raise PivotError(
                    f"Aggregation {value.aggregation!r} needs a numeric column; "
                    f"{column!r} is not numeric."
                )
        values.append(value)

    if not rows and not pivot_column:
        raise PivotError("A pivot needs at least one row dimension or a pivot column.")

    where = ""
    for item in query.filters:
        field_name = str(item.field).strip()
        if field_name not in names:
            raise PivotError(f"Filter column {field_name!r} does not exist.")
        where += (" AND " if where else " WHERE ") + _condition(q(field_name), item)

    started = time.perf_counter()
    pivot_values = (
        _distinct(engine, table.sql, pivot_column) if pivot_column else [None]
    )

    output: list[tuple[str, str, int]] = []  # (alias, expression, kind)
    for name in rows:
        output.append((name, q(name), 0))
    for value in values:
        for pivot_value in pivot_values:
            function = PIVOT_AGGREGATES[value.aggregation]
            if value.column is None:
                inner = "COUNT(*)"
            elif value.aggregation == "count_distinct":
                inner = f"COUNT(DISTINCT {q(value.column)})"
            else:
                inner = f"{function}({q(value.column)})"
            if pivot_column:
                inner = (
                    f"{inner} FILTER (WHERE {q(pivot_column)} "
                    f"= {sql_literal(pivot_value)})"
                )
            alias = _alias(value.label)
            if pivot_value is not None:
                alias = f"{alias} | {_alias(str(pivot_value))}"
            output.append((alias, inner, 1))
    aliases = [alias for alias, _, _kind in output]
    duplicates = sorted({alias for alias in aliases if aliases.count(alias) > 1})
    if duplicates:
        raise PivotError(
            "Value labels produce duplicate column names: "
            + ", ".join(duplicates)
            + "; give the values distinct labels."
        )
    if len(output) > _MAX_PIVOT_OUTPUT_COLUMNS:
        raise PivotError(
            f"The pivot would produce more than {_MAX_PIVOT_OUTPUT_COLUMNS} columns; "
            "reduce values or filters."
        )

    select = ", ".join(f'{expression} AS "{alias}"' for alias, expression, _ in output)
    group_by = ", ".join(q(name) for name in rows)
    sql = f"SELECT {select} FROM ({table.sql}) AS _src{where}"
    if group_by:
        sql += f" GROUP BY {group_by}"
    if query.order_by:
        unknown = [order.field for order in query.order_by if order.field not in aliases]
        if unknown:
            raise PivotError(
                "Order field(s) not in the pivot output: "
                + ", ".join(unknown)
                + "."
            )
        sql += " ORDER BY " + ", ".join(
            f'"{order.field}" {order.direction.upper()}' for order in query.order_by
        )
    elif group_by:
        sql += " ORDER BY " + ", ".join(f'"{name}" ASC' for name in rows)
    if query.limit is not None:
        sql += f" LIMIT {int(query.limit)}"

    try:
        raw_rows = engine.query(sql)
    except Exception as exc:  # EngineError carries the database message
        raise PivotError(f"Pivot query failed: {exc}") from exc
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)

    rows_out = tuple(
        {alias: _json_value(value) for alias, value in zip(aliases, row, strict=False)}
        for row in raw_rows
    )
    columns: list[SemanticColumn] = []
    for name in rows:
        columns.append(SemanticColumn(name, name, "dimension", "text"))
    for value in values:
        for pivot_value in pivot_values:
            alias = _alias(value.label)
            if pivot_value is not None:
                alias = f"{alias} | {_alias(str(pivot_value))}"
            columns.append(SemanticColumn(alias, alias, "measure"))
    truncated = query.limit is not None and len(rows_out) >= query.limit
    return PivotResult(
        query=query,
        columns=tuple(columns),
        rows=rows_out,
        sql=sql,
        row_count=len(rows_out),
        truncated=truncated,
        elapsed_ms=elapsed_ms,
    )


__all__ = [
    "PIVOT_AGGREGATES",
    "PivotError",
    "PivotQuery",
    "PivotResult",
    "PivotValue",
    "pivot_query",
]
