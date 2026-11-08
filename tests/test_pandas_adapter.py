"""Tests for the pandas adapter."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from data_studio.io.pandas_adapter import schema_from_pandas, table_from_pandas, to_pandas
from data_studio.kernel.schema import DataType, Role, TableSchema


@pytest.fixture
def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": [1, 2, 3],
            "region": ["North", "South", None],
            "amount": [10.5, np.nan, 30.0],
            "active": [True, False, True],
            "order_date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
        }
    )


def test_schema_inference(frame: pd.DataFrame) -> None:
    schema = schema_from_pandas(frame)
    assert schema.names == (
        "customer_id",
        "region",
        "amount",
        "active",
        "order_date",
    )
    assert schema.column("customer_id").dtype is DataType.INTEGER
    assert schema.column("customer_id").role is Role.IDENTIFIER
    assert schema.column("region").dtype is DataType.STRING
    assert schema.column("region").nullable is True
    assert schema.column("amount").dtype is DataType.FLOAT
    assert schema.column("amount").role is Role.MEASURE
    assert schema.column("active").dtype is DataType.BOOLEAN
    assert schema.column("order_date").dtype is DataType.DATETIME
    assert schema.column("order_date").role is Role.TIME
    assert schema.primary_key == ()


def test_schema_inference_handles_categorical() -> None:
    frame = pd.DataFrame({"segment": pd.Categorical(["A", "B", "A"])})
    schema = schema_from_pandas(frame)
    assert schema.column("segment").dtype is DataType.CATEGORY


def test_schema_inference_rejects_non_frames() -> None:
    with pytest.raises(TypeError):
        schema_from_pandas([1, 2, 3])  # type: ignore[arg-type]


def test_table_conversion_null_handling(frame: pd.DataFrame) -> None:
    table = table_from_pandas(frame)
    assert len(table) == 3
    assert table.rows[0][0] == 1
    assert table.rows[1][2] is None
    assert table.rows[2][1] is None
    assert isinstance(table.rows[0][4], datetime)


def test_table_conversion_limit_and_schema(frame: pd.DataFrame) -> None:
    schema = TableSchema.from_names(["customer_id", "region", "amount", "active", "order_date"])
    table = table_from_pandas(frame, schema=schema, limit=2)
    assert len(table) == 2
    assert table.schema is schema
    with pytest.raises(ValueError):
        table_from_pandas(frame, schema=TableSchema.from_names(["a", "b"]), limit=2)
    with pytest.raises(ValueError):
        table_from_pandas(frame, limit=-1)


def test_pandas_roundtrip(frame: pd.DataFrame) -> None:
    table = table_from_pandas(frame)
    restored = to_pandas(table)
    assert list(restored.columns) == list(frame.columns)
    assert restored["amount"].isna().sum() == 1
    assert restored["region"].iloc[0] == "North"


def test_empty_frame() -> None:
    frame = pd.DataFrame({"a": pd.Series([], dtype="int64"), "b": pd.Series([], dtype="string")})
    table = table_from_pandas(frame)
    assert len(table) == 0
    assert table.schema.names == ("a", "b")
