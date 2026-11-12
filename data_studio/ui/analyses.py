"""Qt-free analysis bridge for the Data Studio desktop app.

Takes a pandas DataFrame, registers it with a DuckDBEngine, creates an
AnalysisContext, runs a DEFAULT_ANALYSIS_REGISTRY analysis, and returns
an AnalysisResult with plain-language narrative, metrics, and diagnostics.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from ..analysis import DEFAULT_ANALYSIS_REGISTRY
from ..analysis.registry import AnalysisContext, AnalysisResult
from ..engine.duckdb_engine import DuckDBEngine


def run_analysis(
    df: pd.DataFrame,
    analysis_name: str | None = None,
    params: Mapping[str, Any] | None = None,
) -> AnalysisResult:
    """Run an analysis on the given DataFrame and return an AnalysisResult.

    The AnalysisResult contains a plain-language narrative, headline metrics,
    diagnostic warnings, and ResultTable(s) that the UI can render.

    Args:
        df: The pandas DataFrame to analyze.
        analysis_name: Name of the analysis to run (defaults to 'descriptive_stats').
        params: Optional mapping of StepParam name -> value (e.g. column, limit, etc.).

    Returns:
        An AnalysisResult containing metrics, narrative, diagnostics, and result tables.
    """
    # Create a fresh DuckDB engine in memory and register the DataFrame as a virtual table
    engine = DuckDBEngine(database=":memory:")
    try:
        table = engine.from_pandas(df)
        # Use the DataFrame's name if available, otherwise a default
        dataset_name = getattr(df, "name", None) or "analysis"
        context = AnalysisContext(
            dataset=dataset_name,
            table=table,
            step_ids=(),
            engine="duckdb",
        )
        result = DEFAULT_ANALYSIS_REGISTRY.run(
            analysis_name or "descriptive_stats", context, params
        )
    finally:
        engine.close()

    return result


def list_analyses() -> list[dict[str, Any]]:
    """Return every registered analysis as a catalog entry for parameter forms.

    Each entry carries name, title, category, description and the declared
    parameter specs (name/kind/required/default/choices/description).
    """
    entries: list[dict[str, Any]] = []
    for definition in DEFAULT_ANALYSIS_REGISTRY.catalog():
        entries.append(
            {
                "name": definition["name"],
                "title": definition["title"],
                "category": definition["category"],
                "description": definition["description"],
                "params": [dict(param) for param in definition["params"]],
            }
        )
    # Keep summary-style analyses first, then groupings/tests, then time-series.
    order = {
        "descriptive_stats": 0,
        "value_counts": 1,
        "crosstab": 2,
        "correlation": 3,
        "quality_profile": 4,
    }
    entries.sort(key=lambda e: order.get(e["name"], 99))
    return entries


__all__ = [
    "list_analyses",
    "run_analysis",
]
