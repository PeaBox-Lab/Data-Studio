"""Lossless-enough conversion between pandas objects and kernel contracts.

pandas is kept as a compatibility layer for small data and legacy workflows. It is not
the default engine: DuckDB over Arrow is. This adapter exists so Phase 1 can ingest
CSV/Excel with pandas, and so tests can build fixtures quickly.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
from pandas.api import types as ptypes

from ..kernel.schema import Column, DataType, Role, TableSchema
from ..kernel.table import TableData

_IDENTIFIER_SUFFIXES = ("_id", "_key", "_code", "_sku")


def _dtype_for(series: pd.Series) -> DataType:
    dtype = series.dtype
    if isinstance(dtype, pd.CategoricalDtype):
        return DataType.CATEGORY
    if ptypes.is_bool_dtype(dtype):
        return DataType.BOOLEAN
    if ptypes.is_integer_dtype(dtype):
        return DataType.INTEGER
    if ptypes.is_float_dtype(dtype):
        return DataType.FLOAT
    if ptypes.is_datetime64_any_dtype(dtype):
        return DataType.DATETIME
    if ptypes.is_timedelta64_dtype(dtype):
        return DataType.DURATION
    if ptypes.is_string_dtype(dtype) or ptypes.is_object_dtype(dtype):
        return DataType.STRING
    return DataType.UNKNOWN


def _role_for(name: str, dtype: DataType) -> Role:
    lowered = name.casefold()
    if lowered in {"id", "key"} or lowered.endswith(_IDENTIFIER_SUFFIXES):
        return Role.IDENTIFIER
    if dtype is DataType.DATETIME or dtype is DataType.DATE:
        return Role.TIME
    if dtype in {DataType.INTEGER, DataType.FLOAT, DataType.DECIMAL}:
        return Role.MEASURE
    return Role.DIMENSION


def schema_from_pandas(frame: pd.DataFrame) -> TableSchema:
    """Infer a kernel schema from a pandas DataFrame.

    The inference is deliberately conservative: it sets logical types, roles and
    nullability, but never invents a primary key.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("schema_from_pandas expects a pandas DataFrame.")
    columns: list[Column] = []
    for name in frame.columns:
        series = frame[name]
        dtype = _dtype_for(series)
        columns.append(
            Column(
                name=str(name),
                dtype=dtype,
                nullable=bool(series.isna().any()),
                role=_role_for(str(name), dtype),
            )
        )
    return TableSchema(columns=tuple(columns))


def _to_python(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    if isinstance(value, np.floating) and np.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, pd.Timedelta):
        return value.to_pytimedelta()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (datetime, date, str, int, float, bool)):
        return value
    return str(value)


def table_from_pandas(
    frame: pd.DataFrame,
    *,
    schema: TableSchema | None = None,
    limit: int | None = None,
) -> TableData:
    """Materialise a pandas frame into a validated `TableData` block."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("table_from_pandas expects a pandas DataFrame.")
    if limit is not None:
        if limit < 0:
            raise ValueError("limit must be >= 0.")
        frame = frame.head(limit)
    effective_schema = schema or schema_from_pandas(frame)
    if tuple(str(name) for name in frame.columns) != effective_schema.names:
        raise ValueError("Frame columns do not match the provided schema.")
    rows = tuple(
        tuple(_to_python(value) for value in row)
        for row in frame.itertuples(index=False, name=None)
    )
    return TableData(effective_schema, rows)


def to_pandas(data: TableData) -> pd.DataFrame:
    """Convert a `TableData` block back to a pandas DataFrame."""

    if not isinstance(data, TableData):
        raise TypeError("to_pandas expects TableData.")
    return pd.DataFrame(list(data.rows), columns=list(data.schema.names))
