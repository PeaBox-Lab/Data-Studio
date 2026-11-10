"""Qt-free step bridge for the Data Studio desktop app.

Takes a pandas DataFrame, registers it with a DuckDBEngine, creates a
StepContext, applies a step from the built-in DEFAULT_REGISTRY, and returns
the transformed DataFrame plus basic diagnostics.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from ..engine.duckdb_engine import DuckDBEngine
from ..steps import DEFAULT_REGISTRY
from ..steps.registry import StepContext

# Steps that require a second dataset are not supported by the scratch app yet.
_UNSUPPORTED_STEPS = {"join", "union"}
FRIENDLY_NAMES = {
    "drop_missing": "Drop rows with gaps",
    "fill_missing": "Fill missing values",
    "deduplicate": "Remove duplicate rows",
    "flag_outliers": "Flag outliers",
    "select": "Keep only these columns",
    "drop": "Delete these columns",
    "rename": "Rename columns",
    "cast": "Change column type",
    "derive": "Add calculated column",
    "filter": "Keep rows where...",
    "sort": "Sort rows",
    "distinct": "Distinct rows",
    "trim_text": "Trim spaces",
    "replace_text": "Find and replace text",
    "normalize_text": "Normalize case",
    "extract_text": "Extract text pattern",
    "split_text": "Split text on delimiter",
    "concat_columns": "Join columns into one text column",
    "date_parse": "Parse text into dates",
    "date_extract": "Extract date part (year, month, weekday...)",
    "date_trunc": "Round dates to unit",
    "date_shift": "Shift dates by interval",
    "group_aggregate": "Group and aggregate",
    "value_counts": "Count and percentage",
    "pivot": "Pivot wider (values -> columns)",
    "unpivot": "Unpivot (columns -> rows)",
    "window": "Window function (lag, rank, cumulative...)",
    "sample": "Sample rows",
    "limit": "Keep first N rows",
}


def list_steps() -> list[dict[str, Any]]:
    """Return every supported built-in step as a catalog entry for parameter forms.

    Each entry carries name, title, category, description and the declared
    parameter specs (name/kind/required/default/choices/description).
    """
    entries: list[dict[str, Any]] = []
    for definition in DEFAULT_REGISTRY.catalog():
        if definition["name"] in _UNSUPPORTED_STEPS:
            continue
        entries.append(
            {
                "name": definition["name"],
                "title": definition["title"],
                "category": definition["category"],
                "description": definition["description"],
                "friendly_name": FRIENDLY_NAMES.get(
                    definition["name"], definition["title"]
                ),
                "params": [dict(param) for param in definition["params"]],
            }
        )
    entries.sort(key=lambda e: (e["category"], FRIENDLY_NAMES.get(e["name"], e["title"])))
    return entries


def apply_step(
    df: pd.DataFrame,
    step_name: str,
    params: Mapping[str, Any],
) -> pd.DataFrame:
    """Apply one step to a DataFrame and return the transformed DataFrame.

    Args:
        df: The DataFrame to transform.
        step_name: Name of the step (e.g. 'fill_missing').
        params: Resolved StepParam values.

    Returns:
        The transformed DataFrame.
    """
    engine = DuckDBEngine(database=":memory:")
    try:
        table = engine.from_pandas(df)
        context = StepContext(engine, {})
        result_table = DEFAULT_REGISTRY.apply(step_name, context, table, params)
        return result_table.to_pandas()
    finally:
        engine.close()


def parse_aggregations(text: str) -> list[dict[str, str]]:
    """Parse shorthand aggregations like 'column:sum,temperature:mean' into objects.

    Each item becomes {'column': col, 'function': fn}.
    An item of the form '*:count' produces {'column': '*', 'function': 'count'}.
    Whitespace is ignored. Raises ValueError on malformed input.
    """
    if not text or not text.strip():
        return []
    parsed: list[dict[str, str]] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(
                f"Aggregation {part!r} must use the form column:function, e.g. 'output:sum'."
            )
        column, function = [item.strip() for item in part.split(":", 1)]
        if not column or not function:
            raise ValueError(f"Aggregation {part!r} needs both column and function.")
        parsed.append({"column": column, "function": function})
    return parsed


def parse_mapping(text: str) -> dict[str, str]:
    """Parse shorthand rename mappings like 'from=to,old=new' into a dict.

    Whitespace is ignored. Raises ValueError on malformed input.
    """
    if not text or not text.strip():
        return {}
    mapping: dict[str, str] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(
                f"Mapping {part!r} must use the form old=new, e.g. 'machine=id'."
            )
        old, new = [item.strip() for item in part.split("=", 1)]
        if not old or not new:
            raise ValueError(f"Mapping {part!r} needs both old and new names.")
        mapping[old] = new
    return mapping


__all__ = ["apply_step", "list_steps", "parse_aggregations", "parse_mapping"]
