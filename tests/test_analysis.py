"""Tests for the Phase 2A descriptive and data-quality analyses."""

from __future__ import annotations

import pytest

from data_studio.analysis import (
    DEFAULT_ANALYSIS_REGISTRY,
    AnalysisContext,
    AnalysisError,
    AnalysisRegistry,
)
from data_studio.analysis.descriptive import (
    correlation,
    crosstab,
    descriptive_stats,
    quality_profile,
    value_counts,
)
from data_studio.engine.duckdb_engine import DuckDBEngine, DuckDBTable


@pytest.fixture
def engine() -> DuckDBEngine:
    instance = DuckDBEngine()
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def table(engine: DuckDBEngine) -> DuckDBTable:
    return engine.from_records(
        [
            {"id": 1, "region": "North", "amount": 10.0, "score": 1},
            {"id": 2, "region": "North", "amount": 20.0, "score": 2},
            {"id": 3, "region": "South", "amount": None, "score": 2},
            {"id": 4, "region": "South", "amount": 40.0, "score": 3},
            {"id": 5, "region": None, "amount": 50.0, "score": 3},
        ],
        name="sales",
    )


@pytest.fixture
def context(table: DuckDBTable) -> AnalysisContext:
    return AnalysisContext(dataset="sales", table=table, step_ids=("trim",), engine="duckdb")


def test_descriptive_stats(context: AnalysisContext) -> None:
    result = descriptive_stats(context, {"columns": ("amount", "score")})
    assert result.kind == "descriptive_stats"
    assert result.provenance.dataset == "sales"
    assert result.provenance.step_ids == ("trim",)
    assert result.provenance.parameters_hash is not None
    assert result.metrics["rows"] == 5
    assert result.metrics["columns"] == 2
    assert result.metrics["missing"] == 1

    summary = result.primary_table
    assert summary is not None
    assert summary.columns[0] == "column"
    rows = {row[0]: row for row in summary.rows}
    amount = rows["amount"]
    assert amount[1] == 5
    assert amount[2] == 1
    assert amount[3] == 30.0
    assert amount[4] is not None
    assert amount[5] == 10.0
    assert amount[9] == 50.0
    assert "numeric column" in result.narrative


def test_descriptive_stats_defaults_to_numeric_columns(
    context: AnalysisContext,
) -> None:
    result = descriptive_stats(context, {})
    columns = [row[0] for row in result.primary_table.rows]
    assert columns == ["id", "amount", "score"]


def test_descriptive_stats_rejects_non_numeric_and_unknown(
    context: AnalysisContext,
) -> None:
    with pytest.raises(AnalysisError, match="not numeric"):
        descriptive_stats(context, {"columns": ("region",)})
    with pytest.raises(AnalysisError, match="Unknown column"):
        descriptive_stats(context, {"columns": ("missing",)})


def test_descriptive_stats_warns_for_constant_column(
    engine: DuckDBEngine,
) -> None:
    constant = engine.from_records([{"value": 1}, {"value": 1}], name="constant")
    result = descriptive_stats(
        AnalysisContext(dataset="constant", table=constant), {}
    )
    assert any(item.code == "constant_column" for item in result.diagnostics)
    assert result.metrics["missing"] == 0


def test_value_counts_includes_missing(context: AnalysisContext) -> None:
    result = value_counts(context, {"column": "region", "limit": 10})
    assert result.kind == "value_counts"
    rows = result.primary_table.rows
    assert rows[0][:2] == ("North", 2)
    assert rows[0][2] == 40.0
    assert rows[-1] == (None, 1, 20.0)
    assert result.metrics == {
        "rows": 5,
        "missing": 1,
        "distinct": 2,
        "shown": 2,
    }
    assert any(item.code == "missing_values" for item in result.diagnostics)


def test_value_counts_can_drop_missing_and_truncate(
    context: AnalysisContext,
) -> None:
    result = value_counts(
        context, {"column": "region", "limit": 1, "drop_missing": True}
    )
    assert len(result.primary_table.rows) == 1
    assert result.primary_table.rows[0][0] in {"North", "South"}
    assert any(item.code == "truncated_categories" for item in result.diagnostics)


def test_value_counts_unknown_column(context: AnalysisContext) -> None:
    with pytest.raises(AnalysisError, match="Unknown column"):
        value_counts(context, {"column": "missing"})


def test_crosstab_counts_and_row_percentages(context: AnalysisContext) -> None:
    counts = crosstab(context, {"row": "region", "column": "score"})
    assert counts.primary_table.columns == ("region", "1", "2", "3", "Total")
    rows = {row[0]: row for row in counts.primary_table.rows}
    assert rows["North"] == ("North", 1, 1, 0, 2)
    assert rows["South"] == ("South", 0, 1, 1, 2)
    assert counts.metrics["grand_total"] == 4

    percentages = crosstab(
        context,
        {"row": "region", "column": "score", "normalize": "row"},
    )
    north = {row[0]: row for row in percentages.primary_table.rows}["North"]
    assert north[1:4] == (50.0, 50.0, 0.0)
    assert north[4] == 100.0


def test_crosstab_rejects_same_column_and_unknown(context: AnalysisContext) -> None:
    with pytest.raises(AnalysisError, match="different"):
        crosstab(context, {"row": "region", "column": "region"})
    with pytest.raises(AnalysisError, match="Unknown column"):
        crosstab(context, {"row": "region", "column": "missing"})


def test_correlation_pearson(context: AnalysisContext) -> None:
    result = correlation(context, {"columns": ("amount", "score")})
    assert result.kind == "correlation"
    matrix = {row[0]: row for row in result.primary_table.rows}
    assert matrix["amount"][1] == 1.0
    assert matrix["amount"][2] is not None
    assert matrix["amount"][2] == matrix["score"][1]
    assert result.metrics["method"] == "pearson"
    assert result.metrics["pairs"] == 1


def test_correlation_spearman(context: AnalysisContext) -> None:
    result = correlation(
        context, {"columns": ("amount", "score"), "method": "spearman"}
    )
    assert result.metrics["method"] == "spearman"
    assert result.primary_table.rows[0][1] == 1.0


def test_correlation_needs_two_columns(context: AnalysisContext) -> None:
    with pytest.raises(AnalysisError, match="at least two"):
        correlation(context, {"columns": ("amount",)})


def test_quality_profile(context: AnalysisContext) -> None:
    result = quality_profile(context, {})
    assert result.kind == "quality_profile"
    assert result.metrics["rows"] == 5
    assert result.metrics["columns"] == 4
    assert result.metrics["missing"] == 2
    assert result.metrics["missing_pct"] == 10.0
    assert result.metrics["duplicate_rows"] == 0
    assert result.metrics["constant_columns"] == 0
    rows = {row[0]: row for row in result.primary_table.rows}
    assert rows["amount"][4] == 1
    assert rows["amount"][5] == 20.0
    assert rows["region"][6] == 2
    codes = {item.code for item in result.diagnostics}
    assert "high_missing" in codes


def test_quality_profile_detects_duplicates() -> None:
    engine = DuckDBEngine()
    try:
        duplicated = engine.from_records(
            [{"a": 1, "b": "x"}, {"a": 1, "b": "x"}, {"a": 2, "b": "y"}],
            name="dupes",
        )
        result = quality_profile(
            AnalysisContext(dataset="dupes", table=duplicated),
            {"check_duplicates": True},
        )
        assert result.metrics["duplicate_rows"] == 1
        assert any(item.code == "duplicate_rows" for item in result.diagnostics)
    finally:
        engine.close()


def test_registry_catalog_and_validation() -> None:
    catalog = DEFAULT_ANALYSIS_REGISTRY.catalog()
    by_name = {item["name"]: item for item in catalog}
    assert {
        "descriptive_stats",
        "value_counts",
        "crosstab",
        "correlation",
        "quality_profile",
        "t_test",
        "anova",
        "chi_square",
        "proportion_test",
        "linear_regression",
        "logistic_regression",
        "pairwise_tests",
    } <= set(by_name)
    assert by_name["descriptive_stats"]["category"] == "summary"
    assert by_name["quality_profile"]["category"] == "quality"
    assert by_name["t_test"]["category"] == "inference"
    assert by_name["linear_regression"]["category"] == "regression"
    assert {"name", "title", "category", "description", "params"} <= set(catalog[0])

    assert len(DEFAULT_ANALYSIS_REGISTRY) == 12
    assert "value_counts" in DEFAULT_ANALYSIS_REGISTRY
    with pytest.raises(AnalysisError, match="Unknown analysis"):
        DEFAULT_ANALYSIS_REGISTRY.get("missing")

    with pytest.raises(AnalysisError, match="Unknown parameter"):
        DEFAULT_ANALYSIS_REGISTRY.get("descriptive_stats").validate({"nope": 1})
    with pytest.raises(AnalysisError, match="Missing required parameter"):
        DEFAULT_ANALYSIS_REGISTRY.get("value_counts").validate({})


def test_registry_rejects_duplicates_and_bad_definitions() -> None:
    registry = AnalysisRegistry()
    definition = DEFAULT_ANALYSIS_REGISTRY.get("value_counts")
    registry.register(definition)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition)
    with pytest.raises(TypeError):
        registry.register("nope")  # type: ignore[arg-type]


def test_analysis_registry_runs(context: AnalysisContext) -> None:
    result = DEFAULT_ANALYSIS_REGISTRY.run(
        "descriptive_stats", context, {"columns": ["amount"]}
    )
    assert result.kind == "descriptive_stats"
    assert result.provenance.dataset_fingerprint.startswith("sha256:")


def test_analysis_context_validation(table: DuckDBTable) -> None:
    with pytest.raises(ValueError):
        AnalysisContext(dataset="  ", table=table)
    with pytest.raises(TypeError):
        AnalysisContext(dataset="x", table="nope")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AnalysisContext(dataset="x", table=table, step_ids=("",))
