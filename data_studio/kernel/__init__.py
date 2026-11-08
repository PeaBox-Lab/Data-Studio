"""The analytics kernel: schemas, tables, typed results and chart specifications.

Nothing in this package imports a UI toolkit. Everything here must be usable from a
test, a CLI, a scheduled job and a web request.
"""

from __future__ import annotations

from .provenance import Provenance
from .results import AnalysisResult, Diagnostic, Estimate, ResultTable
from .schema import Column, DataType, Role, TableSchema
from .spec import (
    ChartSpec,
    ChartType,
    DashboardFilter,
    DashboardSpec,
    DashboardTile,
    Encoding,
    TileKind,
)
from .table import InMemoryTable, Table, TableData

__all__ = [
    "AnalysisResult",
    "ChartSpec",
    "ChartType",
    "Column",
    "DashboardFilter",
    "DashboardSpec",
    "DashboardTile",
    "DataType",
    "Diagnostic",
    "Encoding",
    "Estimate",
    "InMemoryTable",
    "Provenance",
    "ResultTable",
    "Role",
    "Table",
    "TableData",
    "TableSchema",
    "TileKind",
]
