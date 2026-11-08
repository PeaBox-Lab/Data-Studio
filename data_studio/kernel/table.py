"""Table value objects and the engine-neutral table protocol.

`TableData` is a small, immutable, validated value object used for previews, test
fixtures and API payloads. Large tables are represented by implementations of the
`Table` protocol (DuckDB/Arrow in Phase 1), never by materialising everything here.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol, runtime_checkable

from .schema import TableSchema


@dataclass(frozen=True, slots=True)
class TableData:
    """An immutable block of rows that conforms to a schema."""

    schema: TableSchema
    rows: tuple[tuple[Any, ...], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.schema, TableSchema):
            raise TypeError("TableData requires a TableSchema.")
        rows = tuple(tuple(row) for row in self.rows)
        width = len(self.schema)
        non_nullable = {
            position
            for position, column in enumerate(self.schema.columns)
            if not column.nullable
        }
        for row_number, row in enumerate(rows):
            if len(row) != width:
                raise ValueError(
                    f"Row {row_number} has {len(row)} values; schema expects {width}."
                )
            for position in non_nullable:
                if row[position] is None:
                    raise ValueError(
                        f"Row {row_number} has null in non-nullable column "
                        f"{self.schema.columns[position].name!r}."
                    )
        object.__setattr__(self, "rows", rows)

    def __len__(self) -> int:
        return len(self.rows)

    def head(self, limit: int = 20) -> TableData:
        if limit < 0:
            raise ValueError("limit must be >= 0.")
        return TableData(self.schema, self.rows[:limit])

    def column_values(self, name: str) -> tuple[Any, ...]:
        position = self.schema.index(name)
        return tuple(row[position] for row in self.rows)

    def to_records(self) -> list[dict[str, Any]]:
        names = self.schema.names
        return [dict(zip(names, row, strict=True)) for row in self.rows]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema.to_dict(),
            "rows": [list(row) for row in self.rows],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TableData:
        return cls(
            schema=TableSchema.from_dict(payload["schema"]),
            rows=tuple(tuple(row) for row in payload.get("rows", [])),
        )


@runtime_checkable
class Table(Protocol):
    """The contract every execution engine must satisfy."""

    @property
    def schema(self) -> TableSchema: ...

    def count(self) -> int: ...

    def preview(self, limit: int = 100) -> TableData: ...

    def collect(self) -> TableData: ...

    def fingerprint(self) -> str: ...


@dataclass(frozen=True, slots=True)
class InMemoryTable:
    """A table backed by `TableData`, for tests, fixtures and small results."""

    data: TableData

    @property
    def schema(self) -> TableSchema:
        return self.data.schema

    def count(self) -> int:
        return len(self.data)

    def preview(self, limit: int = 100) -> TableData:
        return self.data.head(limit)

    def collect(self) -> TableData:
        return self.data

    def fingerprint(self) -> str:
        payload = {
            "schema": self.schema.to_dict(),
            "rows": [list(row) for row in self.data.rows],
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        return "sha256:" + sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def from_rows(
        cls,
        schema: TableSchema,
        rows: Sequence[Sequence[Any]],
    ) -> InMemoryTable:
        return cls(TableData(schema, tuple(tuple(row) for row in rows)))
