"""Tests for format detection, readers and atomic writers."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from data_studio.engine.duckdb_engine import DuckDBEngine
from data_studio.io.readers import describe_source, excel_sheets, read_source
from data_studio.io.sniff import (
    CsvOptions,
    SourceFormat,
    detect_format,
    normalise_format,
    sniff_csv,
)
from data_studio.io.writers import write_records, write_table
from data_studio.kernel.schema import Column, DataType, TableSchema


@pytest.fixture
def engine() -> DuckDBEngine:
    instance = DuckDBEngine()
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def sample(engine: DuckDBEngine):
    return engine.from_records(
        [
            {"id": 1, "name": "Alice", "amount": 10.5},
            {"id": 2, "name": "Bob", "amount": 7.0},
        ],
        name="sample",
    )


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("a.parquet", SourceFormat.PARQUET),
        ("a.pq", SourceFormat.PARQUET),
        ("a.csv", SourceFormat.CSV),
        ("a.tsv", SourceFormat.CSV),
        ("a.txt", SourceFormat.CSV),
        ("a.json", SourceFormat.JSON),
        ("a.jsonl", SourceFormat.JSON),
        ("a.xlsx", SourceFormat.EXCEL),
        ("a.xlsm", SourceFormat.EXCEL),
        ("a.duckdb", SourceFormat.DUCKDB),
        ("a.unknown", SourceFormat.UNKNOWN),
    ],
)
def test_detect_format(filename: str, expected: SourceFormat) -> None:
    assert detect_format(filename) is expected


def test_normalise_format_accepts_values_and_paths(tmp_path: Path) -> None:
    assert normalise_format("CSV") is SourceFormat.CSV
    assert normalise_format(SourceFormat.JSON) is SourceFormat.JSON
    assert normalise_format(None, tmp_path / "a.parquet") is SourceFormat.PARQUET
    assert normalise_format(None) is SourceFormat.UNKNOWN


def test_normalise_format_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        normalise_format("xml")


def test_csv_options_roundtrip_and_validation() -> None:
    options = CsvOptions(delimiter=";", quote="'", header=False, encoding="cp1252")
    assert CsvOptions.from_dict(options.to_dict()) == options
    assert CsvOptions.from_dict(None) == CsvOptions()
    with pytest.raises(ValueError):
        CsvOptions(delimiter=";;")
    with pytest.raises(ValueError):
        CsvOptions(quote="")
    with pytest.raises(TypeError):
        CsvOptions(header="yes")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CsvOptions(sample_size=-1)


def test_sniff_csv_detects_semicolon_and_encoding(tmp_path: Path) -> None:
    path = tmp_path / "euro.csv"
    path.write_bytes("id;amount;name\n1;10,5;André\n2;7,0;Beat\n".encode("cp1252"))
    options = sniff_csv(path)
    assert options.delimiter == ";"
    assert options.header is True
    assert options.delimiter == path.read_text(encoding="cp1252").splitlines()[0][2]


def test_sniff_csv_detects_tab(tmp_path: Path) -> None:
    path = tmp_path / "dump.tsv"
    path.write_text("id\tname\n1\tAlice\n2\tBob\n", encoding="utf-8")
    assert sniff_csv(path).delimiter == "\t"


def test_sniff_csv_strips_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    path.write_bytes(b"\xef\xbb\xbfid,name\n1,Alice\n")
    options = sniff_csv(path)
    assert options.encoding == "utf-8-sig"
    assert options.delimiter == ","


def test_sniff_csv_rejects_binary(tmp_path: Path) -> None:
    path = tmp_path / "binary.csv"
    path.write_bytes(b"PK\x03\x04\x00\x00\x00")
    with pytest.raises(ValueError, match="binary"):
        sniff_csv(path)


def test_sniff_csv_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        sniff_csv(tmp_path / "missing.csv")


def test_read_source_csv_with_explicit_options(
    engine: DuckDBEngine, tmp_path: Path
) -> None:
    path = tmp_path / "data.csv"
    path.write_text("id;name\n1;Alice\n2;Bob\n", encoding="utf-8")
    table = read_source(
        engine,
        path,
        csv_options=CsvOptions(delimiter=";", encoding="utf-8"),
    )
    assert table.count() == 2
    assert table.schema.names == ("id", "name")


def test_read_source_parquet_json_excel(engine: DuckDBEngine, tmp_path: Path) -> None:
    frame = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    parquet = tmp_path / "data.parquet"
    frame.to_parquet(parquet, index=False)
    assert read_source(engine, parquet).count() == 2

    json_path = tmp_path / "data.json"
    frame.to_json(json_path, orient="records")
    assert read_source(engine, json_path).count() == 2

    excel = tmp_path / "data.xlsx"
    frame.to_excel(excel, index=False, engine="openpyxl")
    assert read_source(engine, excel).count() == 2


def test_read_source_requires_duckdb_table(engine: DuckDBEngine, tmp_path: Path) -> None:
    database = tmp_path / "data.duckdb"
    database.write_bytes(b"")
    with pytest.raises(ValueError, match="table name"):
        read_source(engine, database)


def test_read_source_missing_and_unsupported(
    engine: DuckDBEngine, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        read_source(engine, tmp_path / "missing.csv")
    unsupported = tmp_path / "data.xml"
    unsupported.write_text("<x/>", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported format"):
        read_source(engine, unsupported)


def test_excel_sheets_and_describe_source(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="one", index=False)
        pd.DataFrame({"b": [2]}).to_excel(writer, sheet_name="two", index=False)
    assert excel_sheets(path) == ["one", "two"]
    described = describe_source(path)
    assert described["format"] == "xlsx"
    assert described["sheets"] == ["one", "two"]


def test_describe_source_csv(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("a;b\n1;2\n", encoding="utf-8")
    described = describe_source(path)
    assert described["format"] == "csv"
    assert described["csv"]["delimiter"] == ";"


def test_describe_source_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        describe_source(tmp_path / "missing.csv")


@pytest.mark.parametrize("suffix", [".parquet", ".csv", ".json", ".xlsx"])
def test_write_table_roundtrip(
    engine: DuckDBEngine, sample, tmp_path: Path, suffix: str
) -> None:
    destination = tmp_path / f"out{suffix}"
    written = write_table(engine, sample, destination)
    assert written == destination.resolve()
    assert destination.is_file()

    read_back = read_source(engine, destination)
    assert read_back.count() == 2
    assert read_back.schema.names == ("id", "name", "amount")


def test_write_table_tsv_uses_tabs(engine: DuckDBEngine, sample, tmp_path: Path) -> None:
    destination = tmp_path / "out.tsv"
    write_table(engine, sample, destination)
    header = destination.read_text(encoding="utf-8").splitlines()[0]
    assert "\t" in header


def test_write_table_rejects_unknown_format(
    engine: DuckDBEngine, sample, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="Cannot export"):
        write_table(engine, sample, tmp_path / "out.xml")


def test_write_table_honours_overwrite(
    engine: DuckDBEngine, sample, tmp_path: Path
) -> None:
    destination = tmp_path / "out.csv"
    write_table(engine, sample, destination)
    with pytest.raises(FileExistsError):
        write_table(engine, sample, destination, overwrite=False)


def test_write_table_is_atomic_on_failure(
    engine: DuckDBEngine, sample, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import data_studio.io.writers as writers

    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(writers, "_write_with_duckdb", boom)
    destination = tmp_path / "out.parquet"
    with pytest.raises(RuntimeError, match="disk full"):
        write_table(engine, sample, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob("*.tmp*"))


def test_write_records(engine: DuckDBEngine, tmp_path: Path) -> None:
    destination = tmp_path / "records.csv"
    write_records(
        engine,
        ("id", "name"),
        ((1, "Alice"), (2, "Bob")),
        destination,
    )
    assert read_source(engine, destination).count() == 2
    with pytest.raises(ValueError):
        write_records(engine, (), (), tmp_path / "empty.csv")


def test_write_nothing_for_a_directory(
    engine: DuckDBEngine, sample, tmp_path: Path
) -> None:
    folder = tmp_path / "folder.parquet"
    folder.mkdir()
    with pytest.raises(IsADirectoryError):
        write_table(engine, sample, folder)


def test_schema_helper_from_names() -> None:
    schema = TableSchema.from_names(["a", "b"], dtype=DataType.STRING)
    assert isinstance(schema.columns[0], Column)
    assert schema.names == ("a", "b")
