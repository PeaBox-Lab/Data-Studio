"""Adapters between external libraries and kernel contracts."""

from __future__ import annotations

from .pandas_adapter import schema_from_pandas, table_from_pandas, to_pandas
from .readers import describe_source, excel_sheets, read_source
from .sniff import CsvOptions, SourceFormat, detect_format, normalise_format, sniff_csv
from .writers import write_records, write_table

__all__ = [
    "CsvOptions",
    "SourceFormat",
    "describe_source",
    "detect_format",
    "excel_sheets",
    "normalise_format",
    "read_source",
    "schema_from_pandas",
    "sniff_csv",
    "table_from_pandas",
    "to_pandas",
    "write_records",
    "write_table",
]
