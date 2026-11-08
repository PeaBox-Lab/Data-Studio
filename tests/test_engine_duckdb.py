"""Tests for the DuckDB execution engine."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from data_studio.engine.duckdb_engine import (
    DuckDBEngine,
    DuckDBTable,
    EngineError,
    SourceRef,
    dtype_for_duckdb,
    file_fingerprint,
    quote_identifier,
    role_for_column,
    sql_literal,
)
from data_studio.kernel.schema import DataType, Role, TableSchema


@pytest.fixture
def engine() -> DuckDBEngine:
    instance = DuckDBEngine()
    try:
        yield instance
    finally:
        instance.close()


def test_from_records_infers_schema_and_count(engine: DuckDBEngine) -> None:
    table = engine.from_records([{"a": 1, "b": "x"}, {"a": 2, "b": None}], name="demo")
    assert table.name == "demo"
    assert table.schema.names == ("a", "b")
    assert table.schema.column("a").dtype is DataType.INTEGER
    assert table.schema.column("b").dtype is DataType.STRING
    assert table.count() == 2


def test_from_records_rejects_ragged_rows(engine: DuckDBEngine) -> None:
    with pytest.raises(ValueError, match="same columns"):
        engine.from_records([{"a": 1}, {"b": 2}])


@pytest.mark.parametrize(
    ("sql_type", "expected"),
    [
        ("BOOLEAN", DataType.BOOLEAN),
        ("BIGINT", DataType.INTEGER),
        ("DOUBLE", DataType.FLOAT),
        ("DECIMAL(18,4)", DataType.DECIMAL),
        ("VARCHAR", DataType.STRING),
        ("DATE", DataType.DATE),
        ("TIMESTAMP", DataType.DATETIME),
        ("TIME", DataType.TIME),
        ("INTERVAL", DataType.DURATION),
        ("BLOB", DataType.BINARY),
        ("JSON", DataType.JSON),
        ("STRUCT(a INTEGER)", DataType.JSON),
        ("MYSTERY", DataType.UNKNOWN),
    ],
)
def test_dtype_for_duckdb(sql_type: str, expected: DataType) -> None:
    assert dtype_for_duckdb(sql_type) is expected


@pytest.mark.parametrize(
    ("name", "dtype", "expected"),
    [
        ("id", DataType.STRING, Role.IDENTIFIER),
        ("customer_id", DataType.STRING, Role.IDENTIFIER),
        ("ordered_at", DataType.DATETIME, Role.TIME),
        ("amount", DataType.FLOAT, Role.MEASURE),
        ("amount", DataType.DECIMAL, Role.MEASURE),
        ("region", DataType.STRING, Role.DIMENSION),
        ("active", DataType.BOOLEAN, Role.DIMENSION),
        ("payload", DataType.UNKNOWN, Role.DIMENSION),
    ],
)
def test_role_for_column(name: str, dtype: DataType, expected: Role) -> None:
    assert role_for_column(name, dtype) is expected


def test_preview_respects_limit(engine: DuckDBEngine) -> None:
    table = engine.from_records([{"a": 1}, {"a": 2}, {"a": 3}])
    data = table.preview(2)
    assert len(data.rows) == 2
    assert len(table.preview(0).rows) == 0
    with pytest.raises(ValueError):
        table.preview(-1)


def test_collect_and_arrow_and_pandas(engine: DuckDBEngine) -> None:
    table = engine.from_records([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    assert table.collect().to_records()[0] == {"a": 1, "b": "x"}
    assert table.to_pandas().shape == (2, 2)
    arrow = table.to_arrow()
    assert arrow.num_rows == 2
    assert arrow.column_names == ["a", "b"]


def test_fingerprint_is_stable_and_changes_with_sql(engine: DuckDBEngine) -> None:
    table = engine.from_records([{"a": 1}])
    assert table.fingerprint() == table.fingerprint()
    other = engine.table("SELECT a + 1 AS a FROM (VALUES (1)) AS v(a)")
    assert table.fingerprint() != other.fingerprint()


def test_source_refs_change_the_fingerprint(engine: DuckDBEngine) -> None:
    sql = "SELECT * FROM (VALUES (1)) AS v(a)"
    first = engine.table(sql, sources=(SourceRef("s", "csv", "sha256:1"),))
    second = engine.table(sql, sources=(SourceRef("s", "csv", "sha256:2"),))
    assert first.fingerprint() != second.fingerprint()


def test_table_rejects_empty_sql(engine: DuckDBEngine) -> None:
    with pytest.raises(ValueError):
        DuckDBTable(engine, "   ", TableSchema())


def test_table_with_name(engine: DuckDBEngine) -> None:
    table = engine.from_records([{"a": 1}], name="old")
    renamed = table.with_name("new")
    assert renamed.name == "new"
    assert renamed.sql == table.sql


def test_bad_sql_is_wrapped(engine: DuckDBEngine) -> None:
    with pytest.raises(EngineError):
        engine.query("SELECT * FROM does_not_exist")
    with pytest.raises(EngineError):
        engine.describe("SELECT * FROM does_not_exist")


def test_query_one_without_rows_raises(engine: DuckDBEngine) -> None:
    with pytest.raises(EngineError, match="no rows"):
        engine.query_one("SELECT 1 WHERE false")


def test_quote_identifier_and_literals() -> None:
    assert quote_identifier('a"b') == '"a""b"'
    assert sql_literal(None) == "NULL"
    assert sql_literal(True) == "TRUE"
    assert sql_literal(False) == "FALSE"
    assert sql_literal(3) == "3"
    assert sql_literal("o'neil") == "'o''neil'"
    with pytest.raises(ValueError):
        quote_identifier("")


def test_from_csv_reads_and_fingerprints(engine: DuckDBEngine, tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("a,b\n1,x\n2,y\n", encoding="utf-8")
    table = engine.from_csv(path)
    assert table.count() == 2
    assert table.sources[0].kind == "csv"
    assert table.sources[0].fingerprint == file_fingerprint(path)


def test_from_csv_falls_back_to_pandas_for_legacy_encoding(
    engine: DuckDBEngine, tmp_path: Path
) -> None:
    path = tmp_path / "cp1252.csv"
    path.write_bytes("name\nAndré\nMüller\n".encode("cp1252"))
    table = engine.from_csv(path, encoding="cp1252")
    assert table.count() == 2
    assert table.to_pandas()["name"].tolist() == ["André", "Müller"]


def test_from_parquet_roundtrip(engine: DuckDBEngine, tmp_path: Path) -> None:
    path = tmp_path / "data.parquet"
    pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_parquet(path, index=False)
    table = engine.from_parquet(path)
    assert table.count() == 2
    assert table.schema.names == ("a", "b")


def test_from_json_records(engine: DuckDBEngine, tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_json(path, orient="records")
    table = engine.from_json(path)
    assert table.count() == 2
    assert table.schema.names == ("a", "b")


def test_from_excel_sheet(engine: DuckDBEngine, tmp_path: Path) -> None:
    path = tmp_path / "data.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame({"a": [1, 2]}).to_excel(writer, sheet_name="first", index=False)
        pd.DataFrame({"b": ["x"]}).to_excel(writer, sheet_name="second", index=False)
    table = engine.from_excel(path, sheet_name="second")
    assert table.schema.names == ("b",)
    assert table.count() == 1


def test_attach_duckdb(engine: DuckDBEngine, tmp_path: Path) -> None:
    database = tmp_path / "source.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute("CREATE TABLE items (id INTEGER, label VARCHAR)")
    connection.execute("INSERT INTO items VALUES (1, 'one'), (2, 'two')")
    connection.close()

    table = engine.attach_duckdb(database, alias="src", table="items")
    assert table.count() == 2
    assert table.sources[0].kind == "duckdb"


def test_describe_preserves_base_schema_roles(engine: DuckDBEngine) -> None:
    from data_studio.kernel.schema import Column

    base = TableSchema(columns=(Column("id", DataType.INTEGER, role=Role.IDENTIFIER),))
    table = engine.table("SELECT 1 AS id", base_schema=base)
    assert table.schema.column("id").role is Role.IDENTIFIER
