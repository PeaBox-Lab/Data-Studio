"""Query execution over a semantic model.

The engine turns a `SemanticQuery` (dimensions, measures, filters, ordering, limit) into
one DuckDB SQL statement. It resolves the joins needed by the referenced dimensions and
measures along the model's relationships, compiles measure expressions through the safe
DSL, and returns a typed `SemanticResult`.

Only model metadata crosses into SQL: identifiers are quoted, literals are escaped and
measure expressions are compiled from the DSL, so project JSON can never inject SQL.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as datetime_time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..engine.duckdb_engine import sql_literal
from ..kernel.schema import TableSchema
from .dsl import CompiledMeasure, MeasureError
from .model import GRAIN_PERIODS, SemanticError, SemanticModel

if TYPE_CHECKING:
    from ..project.store import Project

_FILTER_OPERATORS = {
    "=",
    "!=",
    "<>",
    "<",
    "<=",
    ">",
    ">=",
    "in",
    "not_in",
    "between",
    "contains",
    "starts_with",
    "ends_with",
    "is_null",
    "not_null",
}
_OPERATOR_ALIASES = {
    "==": "=",
    "eq": "=",
    "ne": "!=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "notin": "not_in",
    "isnull": "is_null",
    "notnull": "not_null",
    "startswith": "starts_with",
    "endswith": "ends_with",
}


def _normalise_operator(value: str) -> str:
    operator = str(value).strip().lower().replace(" ", "_")
    operator = _OPERATOR_ALIASES.get(operator, operator)
    if operator not in _FILTER_OPERATORS:
        raise SemanticError(
            "Filter operator must be one of: "
            + ", ".join(sorted(_FILTER_OPERATORS))
            + "."
        )
    return operator


def _values(value: Any, operator: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SemanticError(f"The {operator!r} filter needs a list of values.")
    values = tuple(value)
    if operator == "between" and len(values) != 2:
        raise SemanticError("The 'between' filter needs exactly two values.")
    return values


@dataclass(frozen=True, slots=True)
class QueryFilter:
    """One predicate on a dimension, a measure or a qualified column."""

    field: str
    operator: str = "="
    value: Any = None

    def __post_init__(self) -> None:
        field = str(self.field).strip()
        if not field:
            raise SemanticError("Filter field must not be empty.")
        object.__setattr__(self, "field", field)
        operator = _normalise_operator(self.operator)
        object.__setattr__(self, "operator", operator)
        if operator in {"in", "not_in", "between"}:
            object.__setattr__(self, "value", _values(self.value, operator))
        if operator in {"is_null", "not_null"}:
            object.__setattr__(self, "value", None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "operator": self.operator,
            "value": _json_value(self.value),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> QueryFilter:
        return _filter_from(payload)


@dataclass(frozen=True, slots=True)
class QueryOrder:
    field: str
    direction: str = "asc"

    def __post_init__(self) -> None:
        field = str(self.field).strip()
        if not field:
            raise SemanticError("Order field must not be empty.")
        object.__setattr__(self, "field", field)
        direction = str(self.direction).strip().lower()
        if direction in {"ascending", "up"}:
            direction = "asc"
        elif direction in {"descending", "down"}:
            direction = "desc"
        if direction not in {"asc", "desc"}:
            raise SemanticError("Order direction must be 'asc' or 'desc'.")
        object.__setattr__(self, "direction", direction)

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "direction": self.direction}


@dataclass(frozen=True, slots=True)
class SemanticQuery:
    """A grouped, filtered query over one semantic model."""

    dimensions: tuple[str, ...] = ()
    measures: tuple[str, ...] = ()
    filters: tuple[QueryFilter, ...] = ()
    order_by: tuple[QueryOrder, ...] = ()
    limit: int | None = None
    table: str | None = None

    def __post_init__(self) -> None:
        for name, values in (
            ("dimensions", self.dimensions),
            ("measures", self.measures),
        ):
            cleaned: list[str] = []
            for value in values:
                text = str(value).strip()
                if not text:
                    raise SemanticError(f"Query {name} must not contain empty names.")
                if text in cleaned:
                    raise SemanticError(f"Query {name} contains {text!r} twice.")
                cleaned.append(text)
            object.__setattr__(self, name, tuple(cleaned))
        overlap = set(self.dimensions) & set(self.measures)
        if overlap:
            raise SemanticError(
                "A dimension and a measure share the name(s): "
                + ", ".join(sorted(overlap))
                + "."
            )
        object.__setattr__(self, "filters", tuple(self.filters))
        object.__setattr__(self, "order_by", tuple(self.order_by))
        if self.limit is not None:
            limit = int(self.limit)
            if limit < 1:
                raise SemanticError("Query limit must be at least 1.")
            object.__setattr__(self, "limit", limit)
        if self.table is not None:
            table = str(self.table).strip()
            if not table:
                raise SemanticError("Query table must be a name or null.")
            object.__setattr__(self, "table", table)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimensions": list(self.dimensions),
            "measures": list(self.measures),
            "filters": [item.to_dict() for item in self.filters],
            "order_by": [item.to_dict() for item in self.order_by],
            "limit": self.limit,
            "table": self.table,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SemanticQuery:
        return cls(
            dimensions=tuple(payload.get("dimensions", ())),
            measures=tuple(payload.get("measures", ())),
            filters=tuple(
                item if isinstance(item, QueryFilter) else QueryFilter.from_dict(item)
                for item in payload.get("filters", ())
            ),
            order_by=tuple(_order_from(item) for item in payload.get("order_by", ())),
            limit=payload.get("limit"),
            table=payload.get("table"),
        )


def _order_from(value: Any) -> QueryOrder:
    if isinstance(value, QueryOrder):
        return value
    if isinstance(value, str):
        parts = value.replace(":", " ").split()
        return QueryOrder(parts[0], parts[1] if len(parts) > 1 else "asc")
    if isinstance(value, Mapping):
        return QueryOrder(str(value.get("field", "")), str(value.get("direction", "asc")))
    raise SemanticError("Order entries must be objects, 'field asc' strings or QueryOrder.")


def _filter_from(value: Any) -> QueryFilter:
    if isinstance(value, QueryFilter):
        return value
    if isinstance(value, Mapping):
        return QueryFilter(
            str(value.get("field", "")),
            str(value.get("operator", "=")),
            value.get("value"),
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = list(value)
        if len(items) not in {2, 3}:
            raise SemanticError("Filter lists need [field, operator] or [field, operator, value].")
        return QueryFilter(str(items[0]), str(items[1]), items[2] if len(items) == 3 else None)
    raise SemanticError("Filters must be objects or [field, operator, value] lists.")


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if hasattr(value, "item") and callable(value.item):
        return _json_value(value.item())
    return str(value)


def _literal(value: Any) -> str:
    if isinstance(value, (datetime, date, datetime_time)):
        return sql_literal(value.isoformat())
    if isinstance(value, Decimal):
        return sql_literal(float(value))
    return sql_literal(value)


@dataclass(frozen=True, slots=True)
class SemanticColumn:
    name: str
    title: str
    kind: str
    format: str = "number"

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "title": self.title,
            "kind": self.kind,
            "format": self.format,
        }


@dataclass(frozen=True, slots=True)
class SemanticResult:
    query: SemanticQuery
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


def _alias(table_name: str) -> str:
    return f"t_{table_name}"


class SemanticEngine:
    """Execute semantic queries against one open project."""

    def __init__(
        self,
        project: Project,
        model: SemanticModel,
        *,
        use_pipeline: bool = True,
    ) -> None:
        if not isinstance(model, SemanticModel):
            raise TypeError("SemanticEngine expects a SemanticModel.")
        self.project = project
        self.model = model
        self.use_pipeline = bool(use_pipeline)
        self._schemas: dict[str, TableSchema] = {}
        for table in model.tables:
            try:
                self._schemas[table.name] = project.dataset_schema(table.dataset)
            except KeyError as exc:
                raise SemanticError(
                    f"Table {table.name!r} references unknown dataset "
                    f"{table.dataset!r}."
                ) from exc
        model.validate_or_raise(self._schemas)
        self._measure_cache: dict[str, CompiledMeasure] = {}
        self._table_sql_cache: dict[str, str] = {}
        self._schema_cache: dict[str, TableSchema] = {}

    # -- metadata ----------------------------------------------------------

    @property
    def schemas(self) -> Mapping[str, TableSchema]:
        return self._schemas

    def compiled_measure(
        self,
        name: str,
        time_axis: tuple[str, int] | None = None,
    ) -> CompiledMeasure:
        key = f"{name}|{time_axis[0] if time_axis else ''}"
        if key not in self._measure_cache:
            try:
                self._measure_cache[key] = self.model.compiled_measure(
                    name, self._schemas, time_axis=time_axis
                )
            except MeasureError as exc:
                raise SemanticError(f"Measure {name!r}: {exc}") from exc
        return self._measure_cache[key]

    def _table_sql(self, table_name: str) -> str:
        if table_name in self._table_sql_cache:
            return self._table_sql_cache[table_name]
        table = self.model.table(table_name)
        if self.use_pipeline and table_name == self.model.default_table:
            context = self.project.analysis_context(
                dataset_id=table.dataset, use_pipeline=True
            )
            sql = context.table.sql
        else:
            sql = self.project.dataset_table(table.dataset).sql
        self._table_sql_cache[table_name] = sql
        return sql

    # -- join planner ------------------------------------------------------

    def _relationship(self, left: str, right: str):
        for relationship in self.model.relationships:
            if {relationship.from_table, relationship.to_table} == {left, right}:
                return relationship
        raise SemanticError(
            f"Tables {left!r} and {right!r} are not related; add a relationship "
            "to the semantic model."
        )

    def _join_plan(self, base: str, required: set[str]) -> list[str]:
        required = set(required) | {base}
        adjacency = self.model.relationship_tables()
        parents: dict[str, tuple[str, Any]] = {}
        queue = [base]
        seen = {base}
        while queue:
            current = queue.pop(0)
            for neighbour in sorted(adjacency.get(current, ())):
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                parents[neighbour] = (current, self._relationship(current, neighbour))
                queue.append(neighbour)

        missing = required - seen
        if missing:
            raise SemanticError(
                "Table(s) "
                + ", ".join(repr(name) for name in sorted(missing))
                + f" cannot be joined to the query table {base!r}; add a relationship."
            )

        needed: set[str] = set()
        for name in required:
            current = name
            while current != base:
                needed.add(current)
                current = parents[current][0]

        joins: list[str] = []
        for name, (parent, relationship) in parents.items():
            if name not in needed:
                continue
            if relationship.from_table == parent:
                left_column, right_column = (
                    relationship.from_column,
                    relationship.to_column,
                )
            else:
                left_column, right_column = (
                    relationship.to_column,
                    relationship.from_column,
                )
            joins.append(
                f'LEFT JOIN ({self._table_sql(name)}) AS "{_alias(name)}" '
                f'ON "{_alias(parent)}"."{left_column}" = '
                f'"{_alias(name)}"."{right_column}"'
            )
        return joins

    # -- filters -----------------------------------------------------------

    def _filter_target(self, field: str) -> tuple[str, set[str], bool]:
        """Return ``(expression, referenced_tables, is_measure)``."""

        try:
            measure = self.model.measure(field)
        except KeyError:
            measure = None
        if measure is not None:
            compiled = self.compiled_measure(field)
            if compiled.needs_time_axis:
                raise SemanticError(
                    f"Measure {field!r} uses time intelligence and cannot be "
                    "used as a filter."
                )
            return compiled.sql, set(compiled.tables), True
        try:
            dimension = self.model.dimension(field)
        except KeyError:
            dimension = None
        if dimension is not None:
            return (
                f'"{_alias(dimension.table)}"."{dimension.column}"',
                {dimension.table},
                False,
            )
        column = self.model.column_resolver(self._schemas)(field)
        return column.sql, {column.table}, False

    @staticmethod
    def _condition(expression: str, item: QueryFilter) -> str:
        operator = item.operator
        value = item.value
        if operator == "is_null" or (operator == "=" and value is None):
            return f"{expression} IS NULL"
        if operator == "not_null" or (operator == "!=" and value is None):
            return f"{expression} IS NOT NULL"
        if operator in {"in", "not_in"}:
            values = tuple(value)
            if not values:
                return "FALSE" if operator == "in" else "TRUE"
            literals = ", ".join(_literal(entry) for entry in values)
            keyword = "IN" if operator == "in" else "NOT IN"
            return f"{expression} {keyword} ({literals})"
        if operator == "between":
            low, high = tuple(value)
            return f"{expression} BETWEEN {_literal(low)} AND {_literal(high)}"
        if operator == "contains":
            return (
                f"contains(lower(CAST({expression} AS VARCHAR)), "
                f"lower({_literal(str(value))}))"
            )
        if operator == "starts_with":
            return (
                f"starts_with(lower(CAST({expression} AS VARCHAR)), "
                f"lower({_literal(str(value))}))"
            )
        if operator == "ends_with":
            return (
                f"ends_with(lower(CAST({expression} AS VARCHAR)), "
                f"lower({_literal(str(value))}))"
            )
        return f"{expression} {operator} {_literal(value)}"

    # -- query -------------------------------------------------------------

    def _build(self, query: SemanticQuery) -> tuple[str, list[str], list[str]]:
        base = query.table or self.model.default_table
        if base is None:
            raise SemanticError("The semantic model has no default table.")
        self.model.table(base)

        dimension_sql: list[str] = []
        group_sql: list[str] = []
        referenced: set[str] = set()
        for name in query.dimensions:
            try:
                dimension = self.model.dimension(name)
            except KeyError as exc:
                raise SemanticError(f"Unknown dimension {name!r}.") from exc
            expression = f'"{_alias(dimension.table)}"."{dimension.column}"'
            dimension_sql.append(f'{expression} AS "{name}"')
            group_sql.append(expression)
            referenced.add(dimension.table)

        time_axis: tuple[str, int] | None = None
        for name in query.dimensions:
            dimension = self.model.dimension(name)
            if dimension.grain:
                time_axis = (
                    f'"{_alias(dimension.table)}"."{dimension.column}"',
                    GRAIN_PERIODS[dimension.grain],
                )
                break

        measure_sql: list[str] = []
        for name in query.measures:
            try:
                self.model.measure(name)
            except KeyError as exc:
                raise SemanticError(f"Unknown measure {name!r}.") from exc
            compiled = self.compiled_measure(name, time_axis)
            if compiled.needs_time_axis and time_axis is None:
                raise SemanticError(
                    f"Measure {name!r} uses time intelligence; group the query "
                    "by a date dimension with a grain (set one on the dimension "
                    "in the model, for example the calendar month)."
                )
            measure_sql.append(f'{compiled.sql} AS "{name}"')
            referenced.update(compiled.tables)

        if not dimension_sql and not measure_sql:
            raise SemanticError(
                "A semantic query needs at least one dimension or one measure."
            )

        where: list[str] = []
        having: list[str] = []
        for item in query.filters:
            expression, tables, is_measure = self._filter_target(item.field)
            referenced.update(tables)
            condition = self._condition(expression, item)
            (having if is_measure else where).append(condition)

        select_sql = ", ".join(dimension_sql + measure_sql)
        from_sql = [
            f'FROM ({self._table_sql(base)}) AS "{_alias(base)}"',
            *self._join_plan(base, referenced),
        ]
        sql = "SELECT " + select_sql + " " + " ".join(from_sql)
        if where:
            sql += " WHERE " + " AND ".join(where)
        if group_sql:
            sql += " GROUP BY " + ", ".join(group_sql)
        if having:
            sql += " HAVING " + " AND ".join(having)

        output_names = list(query.dimensions) + list(query.measures)
        order_sql: list[str] = []
        for order in query.order_by:
            if order.field not in output_names:
                raise SemanticError(
                    f"Order field {order.field!r} is not part of the query output."
                )
            order_sql.append(f'"{order.field}" {order.direction.upper()}')
        if not order_sql and query.dimensions:
            order_sql = [f'"{name}" ASC' for name in query.dimensions]
        if order_sql:
            sql += " ORDER BY " + ", ".join(order_sql)
        if query.limit is not None:
            sql += f" LIMIT {int(query.limit)}"
        return sql, output_names, group_sql

    def query(self, query: SemanticQuery) -> SemanticResult:
        if not isinstance(query, SemanticQuery):
            raise TypeError("query expects a SemanticQuery.")
        sql, output_names, _ = self._build(query)
        started = time.perf_counter()
        try:
            raw_rows = self.project.engine.query(sql)
        except Exception as exc:  # EngineError carries the database message
            raise SemanticError(f"Semantic query failed: {exc}") from exc
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)

        rows = tuple(
            {
                name: _json_value(value)
                for name, value in zip(output_names, row, strict=False)
            }
            for row in raw_rows
        )
        columns: list[SemanticColumn] = []
        for name in query.dimensions:
            dimension = self.model.dimension(name)
            columns.append(
                SemanticColumn(name, dimension.title or name, "dimension", "text")
            )
        for name in query.measures:
            measure = self.model.measure(name)
            columns.append(
                SemanticColumn(
                    name, measure.title or name, "measure", measure.format
                )
            )
        truncated = query.limit is not None and len(rows) >= query.limit
        return SemanticResult(
            query=query,
            columns=tuple(columns),
            rows=rows,
            sql=sql,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed_ms,
        )

    # -- slicers -----------------------------------------------------------

    def dimension_values(
        self,
        dimension: str,
        *,
        limit: int = 100,
        search: str | None = None,
        filters: Sequence[QueryFilter] = (),
    ) -> dict[str, Any]:
        if limit < 1:
            raise SemanticError("dimension_values limit must be at least 1.")
        item = self.model.dimension(dimension)
        predicates = list(filters)
        if search and str(search).strip():
            predicates.append(QueryFilter(dimension, "contains", str(search).strip()))
        query = SemanticQuery(
            dimensions=(dimension,),
            filters=tuple(predicates),
            order_by=(QueryOrder(dimension, "asc"),),
            limit=limit,
        )
        result = self.query(query)
        return {
            "dimension": dimension,
            "title": item.title or dimension,
            "values": [row[dimension] for row in result.rows],
            "truncated": result.truncated,
        }


__all__ = [
    "QueryFilter",
    "QueryOrder",
    "SemanticColumn",
    "SemanticEngine",
    "SemanticQuery",
    "SemanticResult",
]
