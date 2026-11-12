"""Built-in data steps.

Importing this package builds the default registry from every step module. Steps are
pure compilers: they validate parameters and return a new lazy `DuckDBTable`.
"""

from __future__ import annotations

from . import aggregate, clean, code, datetime_steps, join, reshape, sample, text, transform, window
from .registry import (
    StepContext,
    StepDefinition,
    StepError,
    StepParam,
    StepRegistry,
    UnknownStepError,
)


def build_registry() -> StepRegistry:
    """Create a fresh registry containing every built-in step."""

    definitions: list[StepDefinition] = []
    for module in (
        clean,
        transform,
        text,
        datetime_steps,
        aggregate,
        join,
        reshape,
        window,
        sample,
        code,
    ):
        definitions.extend(module.STEPS)
    return StepRegistry(definitions)


DEFAULT_REGISTRY = build_registry()

__all__ = [
    "DEFAULT_REGISTRY",
    "StepContext",
    "StepDefinition",
    "StepError",
    "StepParam",
    "StepRegistry",
    "UnknownStepError",
    "build_registry",
]
