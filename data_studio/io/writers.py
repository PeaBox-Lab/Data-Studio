"""Atomic writers for derived tables.

Every export is written to a temporary file in the destination directory, flushed by
the database or pandas, and then moved into place with ``os.replace``. A failed export
never leaves a half-written file that looks valid.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd

from ..engine.duckdb_engine import DuckDBEngine, DuckDBTable, sql_path, sql_string
from ..kernel.schema import TableSchema
from ..kernel.table import TableData
from .sniff import SourceFormat, normalise_format


def write_table(
    engine: DuckDBEngine,
    table: DuckDBTable,
    path: str | Path,
    *,
    source_format: str | SourceFormat | None = None,
    overwrite: bool = True,
) -> Path:
    """Export a lazy table to CSV, Parquet, JSON or Excel atomically."""

    resolved = Path(path).resolve()
    fmt = normalise_format(source_format, resolved)
    if fmt is SourceFormat.UNKNOWN:
        raise ValueError(
            f"Cannot export to {resolved.name!r}: use .parquet, .csv, .json or .xlsx."
        )
    if resolved.exists() and not overwrite:
        raise FileExistsError(f"Destination already exists: {resolved}")
    if resolved.exists() and resolved.is_dir():
        raise IsADirectoryError(f"Destination is a directory: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{resolved.stem}-",
        suffix=f".tmp{fmt.suffix}",
        dir=resolved.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        if fmt is SourceFormat.EXCEL:
            _write_excel(table, temporary)
        else:
            _write_with_duckdb(
                engine,
                table,
                temporary,
                fmt,
                tab_separated=resolved.suffix.casefold() == ".tsv",
            )
        os.replace(temporary, resolved)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return resolved


def _write_with_duckdb(
    engine: DuckDBEngine,
    table: DuckDBTable,
    path: Path,
    fmt: SourceFormat,
    *,
    tab_separated: bool = False,
) -> None:
    target = sql_path(path)
    if fmt is SourceFormat.PARQUET:
        options = "FORMAT PARQUET, COMPRESSION ZSTD"
    elif fmt is SourceFormat.CSV:
        delimiter = "\t" if tab_separated else ","
        options = f"FORMAT CSV, HEADER true, DELIMITER {sql_string(delimiter)}"
    elif fmt is SourceFormat.JSON:
        options = "FORMAT JSON, ARRAY true"
    else:
        raise ValueError(f"DuckDB cannot write {fmt.value!r} directly.")
    engine.execute(f"COPY ({table.sql}) TO {target} ({options})")


def _write_excel(table: DuckDBTable, path: Path) -> None:
    try:
        frame = table.to_pandas()
        frame.to_excel(path, index=False, engine="openpyxl")
    except ImportError as exc:
        raise RuntimeError(
            "Writing Excel requires the optional 'openpyxl' package: pip install openpyxl"
        ) from exc


def write_records(
    engine: DuckDBEngine,
    columns: tuple[str, ...],
    rows: tuple[tuple[object, ...], ...],
    path: str | Path,
    *,
    source_format: str | SourceFormat | None = None,
    overwrite: bool = True,
) -> Path:
    """Export already-materialised rows by wrapping them in a small DuckDB table."""

    if not columns:
        raise ValueError("At least one column is required.")
    schema = TableSchema.from_names(columns)
    data = TableData(schema, rows)
    frame = pd.DataFrame([list(row) for row in data.rows], columns=list(columns))
    table = engine.from_pandas(frame, name="export")
    return write_table(engine, table, path, source_format=source_format, overwrite=overwrite)


__all__ = ["write_records", "write_table"]
