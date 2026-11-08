"""Sampling and limiting steps."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..engine.duckdb_engine import DuckDBTable
from .registry import StepContext, StepDefinition, StepError, StepParam
from .sql import as_table, wrap


def _sample(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    rows = params["rows"]
    fraction = params["fraction"]
    if (rows is None) == (fraction is None):
        raise StepError("Sample needs exactly one of 'rows' or 'fraction'.")
    seed = int(params["seed"])
    if seed < 0:
        raise StepError("Sample seed must be >= 0.")
    if rows is not None:
        if rows < 0:
            raise StepError("Sample rows must be >= 0.")
        sample = f"{rows} ROWS (reservoir, {seed})"
    else:
        assert fraction is not None
        if not 0.0 < fraction <= 1.0:
            raise StepError("Sample fraction must be greater than 0 and at most 1.")
        percentage = format(fraction * 100.0, ".12g")
        sample = f"{percentage}% (bernoulli, {seed})"
    sql = f"SELECT * FROM {wrap(table)} AS _src USING SAMPLE {sample}"
    return as_table(context, sql, table)


def _limit(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    limit = int(params["n"])
    offset = int(params["offset"])
    if limit < 0:
        raise StepError("Limit must be >= 0.")
    if offset < 0:
        raise StepError("Offset must be >= 0.")
    sql = f"SELECT * FROM {wrap(table)} AS _src LIMIT {limit} OFFSET {offset}"
    return as_table(context, sql, table)


STEPS = (
    StepDefinition(
        name="sample",
        title="Sample rows",
        category="sample",
        description="Take a reproducible random sample by row count or fraction.",
        params=(
            StepParam(
                "rows", "integer", required=False, default=None, description="Number of rows."
            ),
            StepParam(
                "fraction",
                "number",
                required=False,
                default=None,
                description="Fraction between 0 and 1.",
            ),
            StepParam("seed", "integer", required=False, default=42, description="Random seed."),
        ),
        compile=_sample,
    ),
    StepDefinition(
        name="limit",
        title="Limit rows",
        category="sample",
        description="Keep the first N rows, optionally after an offset.",
        params=(
            StepParam("n", "integer", description="Row count."),
            StepParam("offset", "integer", required=False, default=0, description="Rows to skip."),
        ),
        compile=_limit,
    ),
)

__all__ = ["STEPS"]
