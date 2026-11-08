"""Column and table schema contracts.

A schema is the single source of truth for names, types, nullability and analytical
roles. Engines validate against it, the UI renders from it, and results carry it so a
table can be interpreted without the original data.

Design rules
------------
* Names are unique case-insensitively, because SQL identifiers are.
* Types are logical types, not storage types. Engines map them to Arrow/DuckDB types.
* Roles describe intent (dimension, measure, identifier, time) so charts and analyses
  can pick sensible defaults.
* A schema is immutable and hashable so it can be cached and fingerprinted.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Any


class DataType(StrEnum):
    """Logical column type, independent of the storage engine."""

    BOOLEAN = "boolean"
    INTEGER = "integer"
    FLOAT = "float"
    DECIMAL = "decimal"
    STRING = "string"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    DURATION = "duration"
    CATEGORY = "category"
    BINARY = "binary"
    JSON = "json"
    UNKNOWN = "unknown"


class Role(StrEnum):
    """How a column is intended to be used."""

    DIMENSION = "dimension"
    MEASURE = "measure"
    IDENTIFIER = "identifier"
    TIME = "time"
    GEOGRAPHY = "geography"
    TEXT = "text"
    IGNORE = "ignore"


NUMERIC_TYPES = frozenset(
    {DataType.INTEGER, DataType.FLOAT, DataType.DECIMAL}
)
TEMPORAL_TYPES = frozenset(
    {DataType.DATE, DataType.DATETIME, DataType.TIME, DataType.DURATION}
)
DEFAULT_ROLE_BY_TYPE: Mapping[DataType, Role] = MappingProxyType(
    {
        DataType.INTEGER: Role.MEASURE,
        DataType.FLOAT: Role.MEASURE,
        DataType.DECIMAL: Role.MEASURE,
        DataType.BOOLEAN: Role.DIMENSION,
        DataType.STRING: Role.DIMENSION,
        DataType.CATEGORY: Role.DIMENSION,
        DataType.DATE: Role.TIME,
        DataType.DATETIME: Role.TIME,
        DataType.TIME: Role.TIME,
        DataType.DURATION: Role.MEASURE,
    }
)


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _sha256_text(text: str) -> str:
    return "sha256:" + sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Column:
    """One column in a table schema."""

    name: str
    dtype: DataType
    nullable: bool = True
    role: Role | None = None
    unit: str | None = None
    description: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("Column name must be a string.")
        name = self.name.strip()
        if not name:
            raise ValueError("Column name must not be empty.")
        if name != self.name:
            object.__setattr__(self, "name", name)

        dtype = self.dtype if isinstance(self.dtype, DataType) else DataType(str(self.dtype))
        object.__setattr__(self, "dtype", dtype)

        role = self.role
        if role is not None and not isinstance(role, Role):
            role = Role(str(role))
        if role is None:
            role = DEFAULT_ROLE_BY_TYPE.get(dtype, Role.DIMENSION)
        object.__setattr__(self, "role", role)

        unit = self.unit.strip() if isinstance(self.unit, str) else self.unit
        object.__setattr__(self, "unit", unit or None)

        if not isinstance(self.metadata, Mapping):
            raise TypeError("Column metadata must be a mapping.")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dtype": self.dtype.value,
            "nullable": self.nullable,
            "role": self.role.value if self.role else None,
            "unit": self.unit,
            "description": self.description,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Column:
        if not isinstance(payload, Mapping):
            raise TypeError("Column payload must be a mapping.")
        return cls(
            name=str(payload["name"]),
            dtype=DataType(str(payload["dtype"])),
            nullable=bool(payload.get("nullable", True)),
            role=Role(str(payload["role"])) if payload.get("role") else None,
            unit=payload.get("unit"),
            description=payload.get("description"),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class TableSchema:
    """An ordered, immutable set of columns plus an optional primary key."""

    columns: tuple[Column, ...] = ()
    primary_key: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        columns = tuple(self.columns)
        if any(not isinstance(column, Column) for column in columns):
            raise TypeError("TableSchema columns must be Column instances.")
        names: set[str] = set()
        lowered: set[str] = set()
        for column in columns:
            if column.name in names:
                raise ValueError(f"Duplicate column name: {column.name!r}.")
            key = column.name.casefold()
            if key in lowered:
                raise ValueError(
                    f"Column names must be unique case-insensitively; {column.name!r} collides."
                )
            names.add(column.name)
            lowered.add(key)

        primary_key = tuple(self.primary_key)
        for name in primary_key:
            match = next((c for c in columns if c.name == name), None)
            if match is None:
                raise ValueError(f"Primary key column not found: {name!r}.")
            if match.nullable:
                raise ValueError(f"Primary key column must be non-nullable: {name!r}.")
        if len(set(primary_key)) != len(primary_key):
            raise ValueError("Primary key columns must be unique.")

        object.__setattr__(self, "columns", columns)
        object.__setattr__(self, "primary_key", primary_key)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    def __len__(self) -> int:
        return len(self.columns)

    def __iter__(self) -> Iterator[Column]:
        return iter(self.columns)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and any(c.name == name for c in self.columns)

    def column(self, name: str) -> Column:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(f"Unknown column {name!r}. Available: {', '.join(self.names) or '(none)'}.")

    def index(self, name: str) -> int:
        for position, column in enumerate(self.columns):
            if column.name == name:
                return position
        raise KeyError(f"Unknown column {name!r}.")

    def select(self, *names: str) -> TableSchema:
        selected = tuple(self.column(name) for name in names)
        primary_key = tuple(name for name in self.primary_key if name in names)
        return replace(self, columns=selected, primary_key=primary_key)

    def with_columns(self, columns: Sequence[Column]) -> TableSchema:
        return replace(self, columns=tuple(columns))

    def fingerprint(self) -> str:
        """Stable hash of the schema, safe to use for caches and provenance."""
        return _sha256_text(_canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": [column.to_dict() for column in self.columns],
            "primary_key": list(self.primary_key),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TableSchema:
        if not isinstance(payload, Mapping):
            raise TypeError("TableSchema payload must be a mapping.")
        columns = payload.get("columns", [])
        if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
            raise TypeError("TableSchema columns must be a sequence.")
        return cls(
            columns=tuple(Column.from_dict(column) for column in columns),
            primary_key=tuple(str(name) for name in payload.get("primary_key", [])),
        )

    @classmethod
    def from_names(
        cls,
        names: Sequence[str],
        dtype: DataType = DataType.UNKNOWN,
        nullable: bool = True,
    ) -> TableSchema:
        """Convenience constructor used by tests and early adapters."""
        return cls(columns=tuple(Column(name, dtype, nullable) for name in names))
