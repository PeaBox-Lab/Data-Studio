"""The semantic model: tables, relationships, dimensions and typed measures.

A semantic model is the declarative layer between stored datasets and dashboards. It
names the tables a dashboard can see, how they join, which columns are dimensions and
which measures are meaningful. Measures are expressions in the safe
:mod:`data_studio.semantic.dsl` language, never raw SQL.

The model is stored as plain JSON under ``models/semantic.json``. Saving validates every
table, relationship, dimension and measure against the project's recorded schemas, so an
invalid model cannot reach a dashboard query.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..kernel.schema import NUMERIC_TYPES, DataType, Role, TableSchema
from .dsl import CompiledMeasure, MeasureError, ResolvedColumn, compile_measure

MODEL_SCHEMA_VERSION = "1"
MEASURE_FORMATS = ("number", "integer", "currency", "percent", "decimal")
RELATIONSHIP_KINDS = ("many_to_one", "one_to_many", "one_to_one")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_UNSAFE_IDENTIFIER = re.compile(r"[^A-Za-z0-9_]+")


class SemanticError(ValueError):
    """A semantic model or query is invalid."""


def _identifier(value: str, label: str) -> str:
    text = str(value).strip() if isinstance(value, str) else ""
    if not _IDENTIFIER.match(text):
        raise SemanticError(
            f"{label} must be 1-64 letters, digits or underscores and start with a "
            f"letter or underscore; got {value!r}."
        )
    return text


def _column_name(value: str, label: str) -> str:
    text = str(value).strip() if isinstance(value, str) else ""
    if not text:
        raise SemanticError(f"{label} must not be empty.")
    return text


def _title(value: str | None, fallback: str) -> str:
    text = str(value).strip() if isinstance(value, str) else ""
    return text or fallback


def _choose_name(base: str, taken: set[str]) -> str:
    candidate = _UNSAFE_IDENTIFIER.sub("_", base.strip()).strip("_") or "field"
    if candidate[0].isdigit():
        candidate = "f_" + candidate
    candidate = candidate[:64]
    if candidate not in taken:
        return candidate
    counter = 2
    while True:
        suffix = f"_{counter}"
        unique = f"{candidate[: 64 - len(suffix)]}{suffix}"
        if unique not in taken:
            return unique
        counter += 1


def _humanize(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip().title()


@dataclass(frozen=True, slots=True)
class SemanticTable:
    """A dataset exposed to dashboards under a stable name."""

    name: str
    dataset: str
    title: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "Table name"))
        dataset = str(self.dataset).strip() if isinstance(self.dataset, str) else ""
        if not dataset:
            raise SemanticError("Table dataset must not be empty.")
        object.__setattr__(self, "dataset", dataset)
        object.__setattr__(self, "title", _title(self.title, _humanize(self.name)))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "dataset": self.dataset, "title": self.title}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SemanticTable:
        return cls(
            name=str(payload.get("name", "")),
            dataset=str(payload.get("dataset", "")),
            title=payload.get("title"),
        )


@dataclass(frozen=True, slots=True)
class Relationship:
    """A join path between two semantic tables."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    kind: str = "many_to_one"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "from_table", _identifier(self.from_table, "Relationship from_table")
        )
        object.__setattr__(
            self, "to_table", _identifier(self.to_table, "Relationship to_table")
        )
        object.__setattr__(
            self,
            "from_column",
            _column_name(self.from_column, "Relationship from_column"),
        )
        object.__setattr__(
            self, "to_column", _column_name(self.to_column, "Relationship to_column")
        )
        kind = str(self.kind).strip().lower()
        if kind not in RELATIONSHIP_KINDS:
            raise SemanticError(
                f"Relationship kind must be one of: {', '.join(RELATIONSHIP_KINDS)}."
            )
        object.__setattr__(self, "kind", kind)
        if self.from_table == self.to_table:
            raise SemanticError("A relationship must join two different tables.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_table": self.from_table,
            "from_column": self.from_column,
            "to_table": self.to_table,
            "to_column": self.to_column,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Relationship:
        return cls(
            from_table=str(payload.get("from_table", "")),
            from_column=str(payload.get("from_column", "")),
            to_table=str(payload.get("to_table", "")),
            to_column=str(payload.get("to_column", "")),
            kind=str(payload.get("kind", "many_to_one")),
        )


@dataclass(frozen=True, slots=True)
class SemanticDimension:
    """A grouping column exposed to dashboards."""

    name: str
    table: str
    column: str
    title: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "Dimension name"))
        object.__setattr__(self, "table", _identifier(self.table, "Dimension table"))
        object.__setattr__(
            self, "column", _column_name(self.column, "Dimension column")
        )
        object.__setattr__(self, "title", _title(self.title, _humanize(self.name)))
        object.__setattr__(self, "description", str(self.description or ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "table": self.table,
            "column": self.column,
            "title": self.title,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SemanticDimension:
        return cls(
            name=str(payload.get("name", "")),
            table=str(payload.get("table", "")),
            column=str(payload.get("column", "")),
            title=payload.get("title"),
            description=str(payload.get("description", "")),
        )


@dataclass(frozen=True, slots=True)
class SemanticMeasure:
    """A named, typed aggregate expression."""

    name: str
    expression: str
    title: str | None = None
    format: str = "number"
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "Measure name"))
        expression = str(self.expression).strip() if isinstance(self.expression, str) else ""
        if not expression:
            raise SemanticError("Measure expression must not be empty.")
        object.__setattr__(self, "expression", expression)
        object.__setattr__(self, "title", _title(self.title, _humanize(self.name)))
        fmt = str(self.format).strip().lower()
        if fmt not in MEASURE_FORMATS:
            raise SemanticError(
                f"Measure format must be one of: {', '.join(MEASURE_FORMATS)}."
            )
        object.__setattr__(self, "format", fmt)
        object.__setattr__(self, "description", str(self.description or ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "expression": self.expression,
            "title": self.title,
            "format": self.format,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SemanticMeasure:
        return cls(
            name=str(payload.get("name", "")),
            expression=str(payload.get("expression", "")),
            title=payload.get("title"),
            format=str(payload.get("format", "number")),
            description=str(payload.get("description", "")),
        )


@dataclass(frozen=True, slots=True)
class SemanticHierarchy:
    """An ordered drill path through existing dimensions.

    Levels are dimension names from outermost to innermost, for example
    ``("region", "manager", "rep")``. Hierarchies let a chart tile start at the
    coarsest level and drill into the levels below it without storing extra SQL.
    """

    name: str
    levels: tuple[str, ...] = ()
    title: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "Hierarchy name"))
        levels = tuple(str(item).strip() for item in self.levels)
        if not levels:
            raise SemanticError("A hierarchy needs at least one level.")
        if any(not item for item in levels):
            raise SemanticError("Hierarchy levels must not be empty names.")
        if len(set(levels)) != len(levels):
            raise SemanticError("Hierarchy levels must be unique.")
        object.__setattr__(self, "levels", levels)
        object.__setattr__(self, "title", _title(self.title, _humanize(self.name)))
        object.__setattr__(self, "description", str(self.description or ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "levels": list(self.levels),
            "title": self.title,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SemanticHierarchy:
        return cls(
            name=str(payload.get("name", "")),
            levels=tuple(payload.get("levels", ())),
            title=payload.get("title"),
            description=str(payload.get("description", "")),
        )


@dataclass(frozen=True, slots=True)
class SemanticModel:
    """A complete star-schema description for one project."""

    name: str
    tables: tuple[SemanticTable, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    dimensions: tuple[SemanticDimension, ...] = ()
    measures: tuple[SemanticMeasure, ...] = ()
    hierarchies: tuple[SemanticHierarchy, ...] = ()
    default_table: str | None = None
    schema_version: str = MODEL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise SemanticError("Semantic model name must not be empty.")
        object.__setattr__(self, "name", name)

        tables = tuple(self.tables)
        if any(not isinstance(item, SemanticTable) for item in tables):
            raise SemanticError("Semantic model tables must be SemanticTable instances.")
        table_names = [item.name for item in tables]
        if len(set(table_names)) != len(table_names):
            raise SemanticError("Semantic table names must be unique.")
        if not tables:
            raise SemanticError("A semantic model needs at least one table.")
        object.__setattr__(self, "tables", tables)

        relationships = tuple(self.relationships)
        if any(not isinstance(item, Relationship) for item in relationships):
            raise SemanticError(
                "Semantic model relationships must be Relationship instances."
            )
        known_tables = set(table_names)
        for relationship in relationships:
            if relationship.from_table not in known_tables:
                raise SemanticError(
                    f"Relationship references unknown table {relationship.from_table!r}."
                )
            if relationship.to_table not in known_tables:
                raise SemanticError(
                    f"Relationship references unknown table {relationship.to_table!r}."
                )
        object.__setattr__(self, "relationships", relationships)

        dimensions = tuple(self.dimensions)
        if any(not isinstance(item, SemanticDimension) for item in dimensions):
            raise SemanticError(
                "Semantic model dimensions must be SemanticDimension instances."
            )
        dimension_names = [item.name for item in dimensions]
        if len(set(dimension_names)) != len(dimension_names):
            raise SemanticError("Semantic dimension names must be unique.")
        for dimension in dimensions:
            if dimension.table not in known_tables:
                raise SemanticError(
                    f"Dimension {dimension.name!r} references unknown table "
                    f"{dimension.table!r}."
                )
        object.__setattr__(self, "dimensions", dimensions)

        measures = tuple(self.measures)
        if any(not isinstance(item, SemanticMeasure) for item in measures):
            raise SemanticError("Semantic model measures must be SemanticMeasure instances.")
        measure_names = [item.name for item in measures]
        if len(set(measure_names)) != len(measure_names):
            raise SemanticError("Semantic measure names must be unique.")
        object.__setattr__(self, "measures", measures)

        hierarchies = tuple(self.hierarchies)
        if any(not isinstance(item, SemanticHierarchy) for item in hierarchies):
            raise SemanticError(
                "Semantic model hierarchies must be SemanticHierarchy instances."
            )
        hierarchy_names = [item.name for item in hierarchies]
        if len(set(hierarchy_names)) != len(hierarchy_names):
            raise SemanticError("Semantic hierarchy names must be unique.")
        known_dimensions = set(dimension_names)
        for hierarchy in hierarchies:
            unknown = [
                level for level in hierarchy.levels if level not in known_dimensions
            ]
            if unknown:
                raise SemanticError(
                    f"Hierarchy {hierarchy.name!r} references unknown dimensions: "
                    + ", ".join(unknown)
                    + "."
                )
        object.__setattr__(self, "hierarchies", hierarchies)

        default = self.default_table
        if default is not None:
            default = _identifier(default, "default_table")
            if default not in known_tables:
                raise SemanticError(
                    f"default_table {default!r} is not one of the model tables."
                )
        elif tables:
            default = tables[0].name
        object.__setattr__(self, "default_table", default)

        if not str(self.schema_version).strip():
            raise SemanticError("Semantic model schema_version must not be empty.")

    # -- lookup ------------------------------------------------------------

    def table(self, name: str) -> SemanticTable:
        for item in self.tables:
            if item.name == name:
                return item
        raise KeyError(f"Unknown semantic table {name!r}.")

    def dimension(self, name: str) -> SemanticDimension:
        for item in self.dimensions:
            if item.name == name:
                return item
        raise KeyError(f"Unknown semantic dimension {name!r}.")

    def measure(self, name: str) -> SemanticMeasure:
        for item in self.measures:
            if item.name == name:
                return item
        raise KeyError(f"Unknown semantic measure {name!r}.")

    def hierarchy(self, name: str) -> SemanticHierarchy:
        for item in self.hierarchies:
            if item.name == name:
                return item
        raise KeyError(f"Unknown semantic hierarchy {name!r}.")

    def relationship_tables(self) -> dict[str, set[str]]:
        """Undirected adjacency between tables, used by the join planner."""

        adjacency: dict[str, set[str]] = {table.name: set() for table in self.tables}
        for relationship in self.relationships:
            adjacency[relationship.from_table].add(relationship.to_table)
            adjacency[relationship.to_table].add(relationship.from_table)
        return adjacency

    # -- validation --------------------------------------------------------

    def validate(self, schemas: Mapping[str, TableSchema]) -> list[str]:
        """Return human-readable schema problems; empty means valid."""

        problems: list[str] = []
        for table in self.tables:
            if table.name not in schemas:
                problems.append(f"Table {table.name!r} has no schema.")
        for relationship in self.relationships:
            for table_name, column in (
                (relationship.from_table, relationship.from_column),
                (relationship.to_table, relationship.to_column),
            ):
                schema = schemas.get(table_name)
                if schema is not None and column not in schema:
                    problems.append(
                        f"Relationship column {table_name}.{column} does not exist."
                    )
        for dimension in self.dimensions:
            schema = schemas.get(dimension.table)
            if schema is not None and dimension.column not in schema:
                problems.append(
                    f"Dimension {dimension.name!r} column "
                    f"{dimension.table}.{dimension.column} does not exist."
                )
        resolver = self.column_resolver(schemas)
        for measure in self.measures:
            try:
                compile_measure(measure.expression, resolver)
            except (MeasureError, KeyError, SemanticError) as exc:
                problems.append(f"Measure {measure.name!r}: {exc}")
        return problems

    def validate_or_raise(self, schemas: Mapping[str, TableSchema]) -> None:
        problems = self.validate(schemas)
        if problems:
            raise SemanticError(
                "Semantic model does not match the project schemas: "
                + "; ".join(problems)
            )

    def column_resolver(
        self, schemas: Mapping[str, TableSchema]
    ) -> Any:
        """Build a resolver that compiles column references for this model."""

        def resolve(identifier: str) -> ResolvedColumn:
            if "." in identifier:
                table_name, column_name = identifier.split(".", 1)
                try:
                    self.table(table_name)
                except KeyError as exc:
                    raise SemanticError(
                        f"Unknown table in column reference {identifier!r}."
                    ) from exc
                schema = schemas.get(table_name)
                if schema is None or column_name not in schema:
                    raise SemanticError(
                        f"Unknown column {identifier!r}."
                    )
                column = schema.column(column_name)
                return ResolvedColumn(
                    sql=f'"{_alias(table_name)}"."{column_name}"',
                    table=table_name,
                    name=column_name,
                    numeric=column.dtype in NUMERIC_TYPES,
                )
            matches: list[tuple[str, Any]] = []
            for table in self.tables:
                schema = schemas.get(table.name)
                if schema is None:
                    continue
                for column in schema.columns:
                    if column.name.casefold() == identifier.casefold():
                        matches.append((table.name, column))
            if not matches:
                raise SemanticError(f"Unknown column {identifier!r}.")
            if len(matches) > 1:
                tables = ", ".join(name for name, _ in matches)
                raise SemanticError(
                    f"Ambiguous column {identifier!r}; qualify it with one of: {tables}."
                )
            table_name, column = matches[0]
            return ResolvedColumn(
                sql=f'"{_alias(table_name)}"."{column.name}"',
                table=table_name,
                name=column.name,
                numeric=column.dtype in NUMERIC_TYPES,
            )

        return resolve

    def compiled_measure(
        self, name: str, schemas: Mapping[str, TableSchema]
    ) -> CompiledMeasure:
        measure = self.measure(name)
        return compile_measure(measure.expression, self.column_resolver(schemas))

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "default_table": self.default_table,
            "tables": [table.to_dict() for table in self.tables],
            "relationships": [item.to_dict() for item in self.relationships],
            "dimensions": [item.to_dict() for item in self.dimensions],
            "measures": [item.to_dict() for item in self.measures],
            "hierarchies": [item.to_dict() for item in self.hierarchies],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SemanticModel:
        if not isinstance(payload, Mapping):
            raise SemanticError("A semantic model document must be an object.")
        return cls(
            name=str(payload.get("name", "")),
            tables=tuple(
                SemanticTable.from_dict(item) for item in payload.get("tables", [])
            ),
            relationships=tuple(
                Relationship.from_dict(item)
                for item in payload.get("relationships", [])
            ),
            dimensions=tuple(
                SemanticDimension.from_dict(item)
                for item in payload.get("dimensions", [])
            ),
            measures=tuple(
                SemanticMeasure.from_dict(item) for item in payload.get("measures", [])
            ),
            hierarchies=tuple(
                SemanticHierarchy.from_dict(item)
                for item in payload.get("hierarchies", [])
            ),
            default_table=payload.get("default_table"),
            schema_version=str(
                payload.get("schema_version", MODEL_SCHEMA_VERSION)
            ),
        )

    # -- derivation --------------------------------------------------------

    @classmethod
    def derive(
        cls,
        schemas: Mapping[str, TableSchema],
        *,
        name: str = "Semantic model",
        base_table: str | None = None,
        dataset_ids: Sequence[str] | None = None,
    ) -> SemanticModel:
        """Build a starter model from recorded schemas.

        String, boolean and temporal columns become dimensions; numeric columns become
        ``total_*`` and ``average_*`` measures. No relationships are guessed: a wrong
        join is worse than an explicit one, and the model JSON is editable.
        """

        if not schemas:
            raise SemanticError("Cannot derive a model without any datasets.")
        ordered = list(dataset_ids) if dataset_ids is not None else list(schemas)
        ordered = [dataset for dataset in ordered if dataset in schemas]
        if not ordered:
            raise SemanticError("Cannot derive a model without any datasets.")

        tables: list[SemanticTable] = []
        used_tables: set[str] = set()
        table_by_dataset: dict[str, str] = {}
        for dataset in ordered:
            table_name = _choose_name(dataset, used_tables)
            used_tables.add(table_name)
            table_by_dataset[dataset] = table_name
            tables.append(
                SemanticTable(
                    name=table_name,
                    dataset=dataset,
                    title=_humanize(dataset),
                )
            )

        dimensions: list[SemanticDimension] = []
        measures: list[SemanticMeasure] = []
        used_dimensions: set[str] = set()
        used_measures: set[str] = set()
        for dataset in ordered:
            schema = schemas[dataset]
            table_name = table_by_dataset[dataset]
            for column in schema.columns:
                title = column.description or _humanize(column.name)
                if column.dtype in {
                    DataType.STRING,
                    DataType.CATEGORY,
                    DataType.BOOLEAN,
                    DataType.DATE,
                    DataType.DATETIME,
                    DataType.TIME,
                } or column.role in {Role.DIMENSION, Role.TIME, Role.GEOGRAPHY}:
                    candidate = _choose_name(column.name, used_dimensions)
                    used_dimensions.add(candidate)
                    dimensions.append(
                        SemanticDimension(
                            name=candidate,
                            table=table_name,
                            column=column.name,
                            title=title,
                        )
                    )
                elif column.dtype in NUMERIC_TYPES and column.role != Role.IDENTIFIER:
                    for prefix, format_name in (
                        ("total", _guess_format(column.name)),
                        ("average", "decimal"),
                    ):
                        candidate = _choose_name(
                            f"{prefix}_{column.name}", used_measures
                        )
                        used_measures.add(candidate)
                        function = "sum" if prefix == "total" else "avg"
                        measures.append(
                            SemanticMeasure(
                                name=candidate,
                                expression=f"{function}({column.name})",
                                title=f"{_humanize(prefix)} {_humanize(column.name)}",
                                format=format_name,
                                description=(
                                    "Sum" if prefix == "total" else "Average"
                                )
                                + f" of {column.name}.",
                            )
                        )

        default = None
        if base_table:
            if base_table not in table_by_dataset:
                raise SemanticError(
                    f"Base dataset {base_table!r} is not part of the model."
                )
            default = table_by_dataset[base_table]
        else:
            default = tables[0].name

        return cls(
            name=name,
            tables=tuple(tables),
            dimensions=tuple(dimensions),
            measures=tuple(measures),
            default_table=default,
        )


def _alias(table_name: str) -> str:
    return f"t_{table_name}"


def _guess_format(column_name: str) -> str:
    lowered = column_name.casefold()
    if any(
        token in lowered
        for token in ("amount", "revenue", "sales", "price", "cost", "spend")
    ):
        return "currency"
    if lowered.endswith(("_pct", "_percent", "percent", "rate")):
        return "percent"
    return "number"


__all__ = [
    "MEASURE_FORMATS",
    "MODEL_SCHEMA_VERSION",
    "RELATIONSHIP_KINDS",
    "Relationship",
    "SemanticDimension",
    "SemanticError",
    "SemanticHierarchy",
    "SemanticMeasure",
    "SemanticModel",
    "SemanticTable",
]
