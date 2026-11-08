"""Execution engines.

Phase 1 ships one production engine: DuckDB over lazy SQL relations. The package
boundary exists so a Polars or pandas engine can be added later without touching the
step or pipeline layers.
"""

from __future__ import annotations

from .duckdb_engine import (
    DuckDBEngine,
    DuckDBTable,
    EngineError,
    SourceRef,
    dtype_for_duckdb,
    file_fingerprint,
    quote_identifier,
    role_for_column,
    sql_literal,
    sql_path,
    sql_string,
)

__all__ = [
    "DuckDBEngine",
    "DuckDBTable",
    "EngineError",
    "SourceRef",
    "dtype_for_duckdb",
    "file_fingerprint",
    "quote_identifier",
    "role_for_column",
    "sql_literal",
    "sql_path",
    "sql_string",
]
