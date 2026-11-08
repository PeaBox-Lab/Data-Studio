"""Tests for table value objects."""

from __future__ import annotations

import pytest

from data_studio.kernel.schema import Column, DataType, TableSchema
from data_studio.kernel.table import InMemoryTable, Table, TableData


@pytest.fixture
def schema() -> TableSchema:
    return TableSchema(
        columns=(
            Column("id", DataType.INTEGER, nullable=False),
            Column("region", DataType.STRING),
            Column("amount", DataType.FLOAT),
        )
    )


def test_table_data_validates_width(schema: TableSchema) -> None:
    with pytest.raises(ValueError, match="expects 3"):
        TableData(schema, ((1, "North"),))


def test_table_data_validates_nullability(schema: TableSchema) -> None:
    with pytest.raises(ValueError, match="non-nullable"):
        TableData(schema, ((None, "North", 1.0),))


def test_table_data_is_immutable(schema: TableSchema) -> None:
    data = TableData(schema, ((1, "North", 10.0),))
    assert isinstance(data.rows, tuple)
    with pytest.raises(TypeError):
        data.rows[0] = (2, "South", 3.0)  # type: ignore[index]


def test_table_data_accessors(schema: TableSchema) -> None:
    data = TableData(
        schema,
        ((1, "North", 10.0), (2, "South", 20.0), (3, "North", 30.0)),
    )
    assert len(data) == 3
    assert data.column_values("region") == ("North", "South", "North")
    assert data.head(2).rows == ((1, "North", 10.0), (2, "South", 20.0))
    assert data.to_records()[1] == {"id": 2, "region": "South", "amount": 20.0}
    with pytest.raises(KeyError):
        data.column_values("missing")
    with pytest.raises(ValueError):
        data.head(-1)


def test_table_data_roundtrip(schema: TableSchema) -> None:
    data = TableData(schema, ((1, "North", 10.0),))
    restored = TableData.from_dict(data.to_dict())
    assert restored == data


def test_in_memory_table_protocol(schema: TableSchema) -> None:
    table = InMemoryTable.from_rows(schema, [(1, "North", 10.0), (2, "South", 20.0)])
    assert isinstance(table, Table)
    assert table.count() == 2
    assert table.schema is schema
    assert table.preview(1).rows == ((1, "North", 10.0),)
    assert table.collect() == table.preview(100)


def test_in_memory_table_fingerprint_changes_with_data(schema: TableSchema) -> None:
    first = InMemoryTable.from_rows(schema, [(1, "North", 10.0)])
    same = InMemoryTable.from_rows(schema, [(1, "North", 10.0)])
    different = InMemoryTable.from_rows(schema, [(1, "North", 11.0)])
    assert first.fingerprint() == same.fingerprint()
    assert first.fingerprint() != different.fingerprint()
    assert first.fingerprint().startswith("sha256:")
