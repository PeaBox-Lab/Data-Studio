"""Tests for typed analysis results and provenance."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from data_studio.kernel.provenance import Provenance, hash_parameters, utc_now_iso
from data_studio.kernel.results import (
    AnalysisResult,
    Diagnostic,
    Estimate,
    ResultTable,
    Severity,
)
from data_studio.kernel.spec import ChartSpec, ChartType, Encoding


def make_provenance() -> Provenance:
    return Provenance(
        dataset="sales",
        dataset_fingerprint="sha256:abc",
        step_ids=("step_0001",),
        engine="duckdb",
    )


def test_provenance_validates_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        Provenance(dataset="x", created_at="2026-01-01T00:00:00")
    with pytest.raises(ValueError, match="Invalid provenance timestamp"):
        Provenance(dataset="x", created_at="not-a-date")
    value = Provenance(dataset="x")
    assert value.created_at.endswith("+00:00")


def test_provenance_rejects_empty_dataset_and_steps() -> None:
    with pytest.raises(ValueError):
        Provenance(dataset="  ")
    with pytest.raises(ValueError):
        Provenance(dataset="x", step_ids=("",))


def test_provenance_roundtrip() -> None:
    provenance = make_provenance()
    restored = Provenance.from_dict(provenance.to_dict())
    assert restored == provenance


def test_hash_parameters_is_stable_and_order_independent() -> None:
    first = hash_parameters({"a": 1, "b": [1, 2]})
    second = hash_parameters({"b": [1, 2], "a": 1})
    third = hash_parameters({"a": 1, "b": [1, 3]})
    assert first == second
    assert first != third
    assert first.startswith("sha256:")


def test_utc_now_is_timezone_aware() -> None:
    parsed = datetime.fromisoformat(utc_now_iso())
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == UTC.utcoffset(None)


def test_diagnostic_helpers() -> None:
    warning = Diagnostic.warning("assumption", "Variance is unequal", ratio=4.2)
    assert warning.severity is Severity.WARNING
    assert warning.details["ratio"] == 4.2
    with pytest.raises(ValueError):
        Diagnostic("", "message")
    with pytest.raises(ValueError):
        Diagnostic("code", "")


def test_estimate_validation() -> None:
    estimate = Estimate("mean", 10.0, se=1.2, ci_low=7.6, ci_high=12.4, p_value=0.03)
    assert estimate.value == 10.0
    with pytest.raises(ValueError):
        Estimate("p", 1.0, p_value=1.5)
    with pytest.raises(ValueError):
        Estimate("ci", 1.0, ci_low=2.0, ci_high=1.0)
    with pytest.raises(ValueError):
        Estimate("ci", 1.0, ci_low=2.0)
    with pytest.raises(ValueError):
        Estimate("se", 1.0, se=-1.0)


def test_result_table_validates_shape_and_cells() -> None:
    table = ResultTable("Group means", ("group", "n", "mean"), (("A", 10, 4.2),))
    assert table.rows == (("A", 10, 4.2),)
    with pytest.raises(ValueError):
        ResultTable("x", ("a", "b"), ((1,),))
    with pytest.raises(ValueError, match="unique"):
        ResultTable("x", ("a", "A"), ())
    with pytest.raises(TypeError, match="non-scalar"):
        ResultTable("x", ("a",), (({"nested": 1},),))
    with pytest.raises(ValueError):
        ResultTable("", ("a",), ())


def test_result_table_roundtrip() -> None:
    table = ResultTable("Coefficients", ("term", "estimate", "p"), (("x", 1.2, 0.04),))
    assert ResultTable.from_dict(table.to_dict()) == table


def test_analysis_result_minimal() -> None:
    result = AnalysisResult(
        kind="descriptive",
        title="Summary",
        provenance=make_provenance(),
        metrics={"n": 10, "mean": 4.2},
        narrative="The mean is 4.2.",
    )
    assert result.result_id.startswith("res_")
    assert result.metric("n") == 10
    assert result.metric("missing", 0) == 0
    assert result.primary_table is None
    assert not result.has_warnings


def test_analysis_result_validation() -> None:
    with pytest.raises(ValueError):
        AnalysisResult(kind="", title="x", provenance=make_provenance())
    with pytest.raises(ValueError):
        AnalysisResult(kind="not valid!", title="x", provenance=make_provenance())
    with pytest.raises(TypeError):
        AnalysisResult(kind="x", title="x", provenance=make_provenance(), metrics={"a": [1]})  # type: ignore[dict-item]
    with pytest.raises(TypeError):
        AnalysisResult(kind="x", title="x", provenance=make_provenance(), narrative=None)  # type: ignore[arg-type]


def test_analysis_result_id_is_stable_and_sensitive() -> None:
    base = dict(kind="t_test", title="Comparison", provenance=make_provenance())
    first = AnalysisResult(**base, params={"column": "amount"})
    second = AnalysisResult(**base, params={"column": "amount"})
    other = AnalysisResult(**base, params={"column": "score"})
    assert first.result_id == second.result_id
    assert first.result_id != other.result_id


def test_analysis_result_has_warnings() -> None:
    result = AnalysisResult(
        kind="t_test",
        title="Comparison",
        provenance=make_provenance(),
        diagnostics=(Diagnostic.warning("normality", "Non-normal residuals"),),
    )
    assert result.has_warnings


def test_analysis_result_roundtrip_with_everything() -> None:
    result = AnalysisResult(
        kind="t_test",
        title="Amount by region",
        provenance=make_provenance(),
        params={"column": "amount", "group": "region"},
        tables=(ResultTable("Groups", ("group", "n", "mean"), (("North", 5, 3.1),)),),
        metrics={"p_value": 0.02, "cohens_d": 0.8},
        diagnostics=(Diagnostic.info("assumption", "Equal variance assumed"),),
        narrative="North differs from South.",
        charts=(
            ChartSpec(
                chart_type=ChartType.BOX,
                title="Amount by region",
                encoding=Encoding(x="region", y="amount"),
            ),
        ),
    )
    payload = result.to_dict()
    restored = AnalysisResult.from_dict(payload)
    assert restored.to_dict() == payload
    assert restored.result_id == result.result_id
    assert json.loads(result.to_json())["result_id"] == result.result_id
