"""Readers that turn external files into lazy DuckDB tables.

A reader never changes the file. It validates that the path exists, infers or accepts
explicit dialect options, and returns a `DuckDBTable` whose SQL still points at the
source. Persisting a dataset into a project is a separate, explicit step
(`Project.add_dataset`), which writes Parquet.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..engine.duckdb_engine import DuckDBEngine, DuckDBTable
from .sniff import (
    CsvOptions,
    SourceFormat,
    detect_format,
    normalise_format,
    sniff_csv,
)


def excel_sheets(path: str | Path) -> list[str]:
    """List worksheet names in an Excel workbook."""

    resolved = Path(path)
    if not resolved.is_file():
        raise FileNotFoundError(f"Excel file not found: {resolved}")
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - pandas is a core dependency
        raise RuntimeError("Reading Excel requires pandas.") from exc
    try:
        with pd.ExcelFile(resolved, engine="openpyxl") as workbook:
            return [str(name) for name in workbook.sheet_names]
    except ImportError as exc:
        raise RuntimeError(
            "Reading Excel requires the optional 'openpyxl' package: pip install openpyxl"
        ) from exc


def read_source(
    engine: DuckDBEngine,
    path: str | Path,
    *,
    name: str | None = None,
    source_format: str | SourceFormat | None = None,
    csv_options: CsvOptions | dict[str, object] | None = None,
    sheet_name: str | int = 0,
    table: str | None = None,
) -> DuckDBTable:
    """Read a supported source into a lazy table.

    Parameters
    ----------
    engine:
        Engine that owns the connection.
    path:
        Existing source file.
    name:
        Logical dataset name; defaults to the file stem.
    source_format:
        Override suffix detection.
    csv_options:
        Explicit CSV dialect, or ``None`` to sniff.
    sheet_name:
        Worksheet for Excel sources.
    table:
        Table name for DuckDB database sources.
    """

    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Source file not found: {resolved}")

    fmt = normalise_format(source_format, resolved)
    dataset_name = name or resolved.stem

    if fmt is SourceFormat.CSV:
        options = (
            CsvOptions.from_dict(csv_options)
            if isinstance(csv_options, dict)
            else csv_options or sniff_csv(resolved)
        )
        return engine.from_csv(
            resolved,
            name=dataset_name,
            header=options.header,
            delimiter=options.delimiter,
            quote=options.quote,
            encoding=options.encoding,
            sample_size=options.sample_size,
        )
    if fmt is SourceFormat.PARQUET:
        return engine.from_parquet(resolved, name=dataset_name)
    if fmt is SourceFormat.JSON:
        return engine.from_json(resolved, name=dataset_name)
    if fmt is SourceFormat.EXCEL:
        return engine.from_excel(resolved, name=dataset_name, sheet_name=sheet_name)
    if fmt is SourceFormat.DUCKDB:
        if not table:
            raise ValueError("A DuckDB source requires a table name.")
        alias = _safe_alias(dataset_name)
        return engine.attach_duckdb(resolved, alias=alias, table=table)

    raise ValueError(
        f"Cannot read {resolved.name!r}: unsupported format. "
        "Supported formats are CSV, Parquet, JSON, Excel and DuckDB."
    )


def _safe_alias(value: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not candidate or not (candidate[0].isalpha() or candidate[0] == "_"):
        candidate = "src_" + candidate
    return candidate[:60]


def describe_source(path: str | Path, *, sample_bytes: int = 64 * 1024) -> dict[str, object]:
    """Return a lightweight, JSON-safe description used by the workspace UI."""

    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Source file not found: {resolved}")
    fmt = detect_format(resolved)
    payload: dict[str, object] = {
        "path": str(resolved),
        "name": resolved.name,
        "format": fmt.value,
        "size_bytes": resolved.stat().st_size,
    }
    if fmt is SourceFormat.CSV:
        payload["csv"] = sniff_csv(resolved).to_dict()
    elif fmt is SourceFormat.EXCEL:
        payload["sheets"] = excel_sheets(resolved)
    return payload


__all__ = ["describe_source", "excel_sheets", "read_source"]
