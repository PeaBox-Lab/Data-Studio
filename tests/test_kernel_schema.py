"""Tests for schema contracts."""

from __future__ import annotations

import pytest

from data_studio.kernel.schema import Column, DataType, Role, TableSchema


def test_column_infers_role_from_type() -> None:
    assert Column("amount", DataType.FLOAT).role is Role.MEASURE
    assert Column("order_date", DataType.DATE).role is Role.TIME
    assert Column("region", DataType.STRING).role is Role.DIMENSION


def test_column_normalises_and_validates_name() -> None:
    column = Column("  amount  ", DataType.FLOAT)
    assert column.name == "amount"
    with pytest.raises(ValueError):
        Column("   ", DataType.STRING)


def test_column_metadata_is_read_only() -> None:
    column = Column("x", DataType.INTEGER, metadata={"a": 1})
    with pytest.raises(TypeError):
        column.metadata["a"] = 2  # type: ignore[index]
    assert column.metadata["a"] == 1


def test_column_accepts_string_enum_values() -> None:
    column = Column("x", "integer", role="identifier")  # type: ignore[arg-type]
    assert column.dtype is DataType.INTEGER
    assert column.role is Role.IDENTIFIER
    with pytest.raises(ValueError):
        Column("x", "not-a-type")  # type: ignore[arg-type]


def test_schema_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        TableSchema(columns=(Column("a", DataType.INTEGER), Column("a", DataType.STRING)))


def test_schema_rejects_case_insensitive_collisions() -> None:
    with pytest.raises(ValueError, match="case-insensitively"):
        TableSchema(
            columns=(
                Column("Amount", DataType.INTEGER),
                Column("amount", DataType.INTEGER),
            )
        )


def test_schema_primary_key_must_exist_and_be_non_nullable() -> None:
    with pytest.raises(ValueError, match="not found"):
        TableSchema(columns=(Column("id", DataType.INTEGER),), primary_key=("missing",))
    with pytest.raises(ValueError, match="non-nullable"):
        TableSchema(
            columns=(Column("id", DataType.INTEGER, nullable=True),),
            primary_key=("id",),
        )
    schema = TableSchema(
        columns=(Column("id", DataType.INTEGER, nullable=False), Column("v", DataType.FLOAT)),
        primary_key=("id",),
    )
    assert schema.primary_key == ("id",)


def test_schema_lookup_select_and_iteration() -> None:
    schema = TableSchema.from_names(["id", "amount", "region"])
    assert schema.names == ("id", "amount", "region")
    assert len(schema) == 3
    assert "amount" in schema
    assert "missing" not in schema
    assert [column.name for column in schema] == ["id", "amount", "region"]
    assert schema.index("region") == 2
    assert schema.select("region", "id").names == ("region", "id")
    with pytest.raises(KeyError):
        schema.column("missing")


def test_schema_select_drops_primary_key_outside_selection() -> None:
    schema = TableSchema(
        columns=(
            Column("id", DataType.INTEGER, nullable=False),
            Column("v", DataType.FLOAT),
        ),
        primary_key=("id",),
    )
    assert schema.select("v").primary_key == ()


def test_schema_fingerprint_is_stable_and_sensitive() -> None:
    first = TableSchema.from_names(["a", "b"])
    second = TableSchema.from_names(["a", "b"])
    different = TableSchema.from_names(["a", "c"])
    assert first.fingerprint() == second.fingerprint()
    assert first.fingerprint() != different.fingerprint()
    assert first.fingerprint().startswith("sha256:")


def test_schema_roundtrip() -> None:
    schema = TableSchema(
        columns=(
            Column("id", DataType.INTEGER, nullable=False, role=Role.IDENTIFIER, unit="key"),
            Column("amount", DataType.DECIMAL, unit="USD", description="Gross amount"),
        ),
        primary_key=("id",),
    )
    restored = TableSchema.from_dict(schema.to_dict())
    assert restored == schema
    assert restored.fingerprint() == schema.fingerprint()


def test_schema_with_columns() -> None:
    schema = TableSchema.from_names(["a"])
    replacement = schema.with_columns([Column("b", DataType.STRING)])
    assert replacement.names == ("b",)
    assert schema.names == ("a",)
