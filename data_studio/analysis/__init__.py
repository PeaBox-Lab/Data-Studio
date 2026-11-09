"""Built-in analyses.

Importing this package builds the default analysis registry from every analysis module.
Analyses are pure functions over an `AnalysisContext` and return typed `AnalysisResult`
objects, so the HTTP API, the CLI and the workspace all render the same numbers.
"""

from __future__ import annotations

from . import descriptive, inference, timeseries
from .registry import (
    AnalysisContext,
    AnalysisDefinition,
    AnalysisError,
    AnalysisRegistry,
)
from .saved import (
    SCHEDULES,
    SavedAnalysis,
    SavedAnalysisError,
    SavedAnalysisStore,
    input_fingerprint,
    slugify,
)


def build_registry() -> AnalysisRegistry:
    """Create a fresh registry containing every built-in analysis."""

    return AnalysisRegistry(
        (*descriptive.ANALYSES, *inference.ANALYSES, *timeseries.ANALYSES)
    )


DEFAULT_ANALYSIS_REGISTRY = build_registry()

__all__ = [
    "DEFAULT_ANALYSIS_REGISTRY",
    "SCHEDULES",
    "AnalysisContext",
    "AnalysisDefinition",
    "AnalysisError",
    "AnalysisRegistry",
    "SavedAnalysis",
    "SavedAnalysisError",
    "SavedAnalysisStore",
    "build_registry",
    "descriptive",
    "inference",
    "input_fingerprint",
    "slugify",
    "timeseries",
]
