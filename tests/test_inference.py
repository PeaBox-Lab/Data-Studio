"""Reference tests for the Phase 2B inferential analyses.

Expected values were generated independently with SciPy and statsmodels and are pinned
here so that the kernel cannot silently drift.
"""

from __future__ import annotations

import math

import pytest

from data_studio.analysis import AnalysisContext, AnalysisError
from data_studio.analysis.inference import (
    _adjust,
    anova,
    chi_square,
    linear_regression,
    logistic_regression,
    pairwise_tests,
    proportion_test,
    t_test,
)
from data_studio.engine.duckdb_engine import DuckDBEngine

TOLERANCE = 1e-6


@pytest.fixture
def engine() -> DuckDBEngine:
    instance = DuckDBEngine()
    try:
        yield instance
    finally:
        instance.close()


def context(
    engine: DuckDBEngine, rows: list[dict[str, object]], name: str = "data"
) -> AnalysisContext:
    return AnalysisContext(
        dataset=name, table=engine.from_records(rows, name=name), step_ids=("clean",)
    )


def coefficients(result):
    return {row[0]: row for row in result.primary_table.rows}


# -- t tests ---------------------------------------------------------------


def test_two_sample_t_tests_match_reference(engine: DuckDBEngine) -> None:
    rows = [{"value": value, "group": "a"} for value in (10, 12, 14, 16, 18)]
    rows += [{"value": value, "group": "b"} for value in (8, 9, 11, 13, 15)]
    ctx = context(engine, rows, "ttest")

    welch = t_test(
        ctx,
        {"test": "two_sample", "column": "value", "group": "group"},
    )
    assert welch.kind == "t_test"
    assert welch.metrics["statistic"] == pytest.approx(
        1.467598771410686, abs=TOLERANCE
    )
    assert welch.metrics["p_value"] == pytest.approx(
        0.18075714894503023, abs=TOLERANCE
    )
    assert welch.metrics["ci_low"] == pytest.approx(
        -1.6070774222049726, abs=TOLERANCE
    )
    assert welch.metrics["ci_high"] == pytest.approx(
        7.207077422204974, abs=TOLERANCE
    )
    assert welch.metrics["n"] == 10
    assert welch.metrics["method"].startswith("Welch")

    student = t_test(
        ctx,
        {
            "test": "two_sample",
            "column": "value",
            "group": "group",
            "equal_var": True,
        },
    )
    assert student.metrics["p_value"] == pytest.approx(
        0.18039544877850114, abs=TOLERANCE
    )
    assert student.metrics["ci_low"] == pytest.approx(
        -1.5995754864016716, abs=TOLERANCE
    )
    assert student.metrics["ci_high"] == pytest.approx(
        7.199575486401673, abs=TOLERANCE
    )
    assert student.metrics["method"].startswith("Student")


def test_one_sample_and_paired_t_tests_match_reference(
    engine: DuckDBEngine,
) -> None:
    one = t_test(
        context(engine, [{"value": value} for value in (10, 12, 14, 16, 18)]),
        {"test": "one_sample", "column": "value", "mu": 12.0},
    )
    assert one.metrics["statistic"] == pytest.approx(
        1.414213562373095, abs=TOLERANCE
    )
    assert one.metrics["p_value"] == pytest.approx(
        0.2301996410804989, abs=TOLERANCE
    )
    assert one.metrics["ci_low"] == pytest.approx(
        10.073513677044886, abs=TOLERANCE
    )
    assert one.metrics["ci_high"] == pytest.approx(
        17.926486322955114, abs=TOLERANCE
    )

    paired_rows = [
        {"before": before, "after": after}
        for before, after in zip(
            (120, 130, 125, 140, 115),
            (118, 126, 124, 135, 112),
            strict=True,
        )
    ]
    paired = t_test(
        context(engine, paired_rows),
        {"test": "paired", "column": "before", "other": "after"},
    )
    assert paired.metrics["statistic"] == pytest.approx(
        4.242640687119285, abs=TOLERANCE
    )
    assert paired.metrics["p_value"] == pytest.approx(
        0.01323559956368269, abs=TOLERANCE
    )
    assert paired.metrics["mean_difference"] == pytest.approx(3.0, abs=TOLERANCE)
    assert paired.metrics["ci_low"] == pytest.approx(
        1.0367568385224428, abs=TOLERANCE
    )
    assert paired.metrics["ci_high"] == pytest.approx(
        4.963243161477557, abs=TOLERANCE
    )


def test_t_test_validates_inputs(engine: DuckDBEngine) -> None:
    ctx = context(engine, [{"value": 1.0}, {"value": 2.0}, {"value": 3.0}])
    with pytest.raises(AnalysisError, match="second column or a grouping column"):
        t_test(ctx, {"test": "two_sample", "column": "value"})
    with pytest.raises(AnalysisError, match="numeric"):
        t_test(
            context(engine, [{"value": "x"}]),
            {"test": "one_sample", "column": "value"},
        )
    with pytest.raises(AnalysisError, match="Confidence"):
        t_test(
            ctx,
            {"test": "one_sample", "column": "value", "confidence": 1.2},
        )


# -- ANOVA -----------------------------------------------------------------


def test_anova_matches_reference_and_tukey_orientation(
    engine: DuckDBEngine,
) -> None:
    rows = [
        {"value": value, "group": "g1"} for value in (10, 12, 14, 16, 18)
    ]
    rows += [{"value": value, "group": "g2"} for value in (8, 9, 11, 13, 15)]
    rows += [{"value": value, "group": "g3"} for value in (6, 9, 12, 15, 18)]
    result = anova(context(engine, rows, "anova"), {"value": "value", "group": "group"})

    assert result.metrics["F"] == pytest.approx(0.7665847665847667, abs=TOLERANCE)
    assert result.metrics["p_value"] == pytest.approx(
        0.4860605143508992, abs=TOLERANCE
    )
    assert result.metrics["eta_squared"] == pytest.approx(
        0.1132897603485839, abs=TOLERANCE
    )
    assert result.metrics["omega_squared"] == pytest.approx(
        -0.03212172442941671, abs=TOLERANCE
    )
    assert result.metrics["df_between"] == 2
    assert result.metrics["df_within"] == 12

    tukey = result.tables[2]
    assert tukey.title.startswith("Tukey")
    first = tukey.rows[0]
    assert first[0] == "g1 vs g2"
    # SciPy reports mean(g1) - mean(g2).
    assert first[1] == pytest.approx(2.8, abs=TOLERANCE)
    assert result.metrics["p_value_text"] == "0.4861"


def test_anova_requires_two_groups(engine: DuckDBEngine) -> None:
    rows = [{"value": 1.0, "group": "only"}, {"value": 2.0, "group": "only"}]
    with pytest.raises(AnalysisError, match="at least two"):
        anova(context(engine, rows), {"value": "value", "group": "group"})


# -- chi-square ------------------------------------------------------------


def test_chi_square_matches_reference(engine: DuckDBEngine) -> None:
    observed = ((10, 20, 30), (20, 15, 5))
    rows: list[dict[str, object]] = []
    for row_index, counts in enumerate(observed):
        for column_index, count in enumerate(counts):
            rows.extend(
                {"row": f"r{row_index}", "column": f"c{column_index}"}
                for _ in range(count)
            )
    result = chi_square(
        context(engine, rows, "chi"), {"row": "row", "column": "column"}
    )

    assert result.metrics["chi_square"] == pytest.approx(
        18.650793650793652, abs=TOLERANCE
    )
    assert result.metrics["p_value"] == pytest.approx(
        8.913158246593224e-05, abs=1e-10
    )
    assert result.metrics["df"] == 2
    assert result.metrics["cramers_v"] == pytest.approx(
        0.43186564636231084, abs=TOLERANCE
    )
    assert result.metrics["n"] == 100
    assert result.metrics["min_expected"] == pytest.approx(12.0, abs=TOLERANCE)
    assert result.tables[0].rows[0] == ("r0", 10, 20, 30, 60)
    expected = result.tables[1]
    assert expected.rows[0][1] == pytest.approx(18.0, abs=TOLERANCE)
    assert not any(
        item.code == "small_expected_counts" for item in result.diagnostics
    )


def test_chi_square_flags_small_expected_counts(engine: DuckDBEngine) -> None:
    observed = ((1, 4), (4, 1))
    rows: list[dict[str, object]] = []
    for row_index, counts in enumerate(observed):
        for column_index, count in enumerate(counts):
            rows.extend(
                {"row": f"r{row_index}", "column": f"c{column_index}"}
                for _ in range(count)
            )
    result = chi_square(context(engine, rows), {"row": "row", "column": "column"})
    assert any(item.code == "small_expected_counts" for item in result.diagnostics)


# -- proportions -----------------------------------------------------------


def test_proportion_tests_match_reference(engine: DuckDBEngine) -> None:
    rows = [{"flag": 1, "group": "a"}] * 6 + [{"flag": 0, "group": "a"}] * 14
    rows += [{"flag": 1, "group": "b"}] * 12 + [{"flag": 0, "group": "b"}] * 8
    two = proportion_test(
        context(engine, rows, "prop2"),
        {"test": "two_sample", "column": "flag", "group": "group"},
    )
    assert two.metrics["statistic"] == pytest.approx(
        -1.9069251785, abs=TOLERANCE
    )
    assert two.metrics["p_value"] == pytest.approx(
        0.0565302772, abs=TOLERANCE
    )
    assert two.metrics["difference"] == pytest.approx(-0.3, abs=TOLERANCE)

    one_rows = [{"flag": 1}] * 6 + [{"flag": 0}] * 14
    one = proportion_test(
        context(engine, one_rows, "prop1"),
        {"test": "one_sample", "column": "flag", "p0": 0.5},
    )
    assert one.metrics["statistic"] == pytest.approx(
        -1.9518001459, abs=TOLERANCE
    )
    assert one.metrics["p_value"] == pytest.approx(
        0.0509619370, abs=TOLERANCE
    )


def test_proportion_success_value(engine: DuckDBEngine) -> None:
    rows = [{"result": "yes"}] * 7 + [{"result": "no"}] * 3
    result = proportion_test(
        context(engine, rows),
        {
            "test": "one_sample",
            "column": "result",
            "success_value": "yes",
            "p0": 0.5,
        },
    )
    assert result.metrics["successes"] == 7
    assert result.metrics["n"] == 10


# -- linear regression -----------------------------------------------------


def test_linear_regression_matches_reference(engine: DuckDBEngine) -> None:
    x1 = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    x2 = (2, 1, 4, 3, 6, 5, 8, 7, 10, 9)
    y = (3, 5, 4, 8, 7, 10, 9, 12, 11, 14)
    rows = [
        {"x1": a, "x2": b, "y": c}
        for a, b, c in zip(x1, x2, y, strict=True)
    ]
    result = linear_regression(
        context(engine, rows, "ols"),
        {"response": "y", "predictors": ["x1", "x2"]},
    )
    assert result.kind == "linear_regression"
    assert result.metrics["r_squared"] == pytest.approx(0.9900947459, abs=TOLERANCE)
    assert result.metrics["adj_r_squared"] == pytest.approx(
        0.9872646733, abs=TOLERANCE
    )
    assert result.metrics["f_statistic"] == pytest.approx(
        349.847826087, abs=1e-5
    )
    assert result.metrics["f_p_value"] == pytest.approx(
        9.6722980810e-08, abs=1e-10
    )
    assert result.metrics["residual_se"] == pytest.approx(
        0.4053217417, abs=TOLERANCE
    )
    assert result.metrics["durbin_watson"] == pytest.approx(
        1.8173913043, abs=TOLERANCE
    )
    assert result.metrics["breusch_pagan_p"] == pytest.approx(
        0.04064632, abs=TOLERANCE
    )
    assert result.metrics["max_vif"] == pytest.approx(8.5078125, abs=TOLERANCE)

    coefficients_by_term = coefficients(result)
    assert coefficients_by_term["(Intercept)"][1] == pytest.approx(
        2.3874999999999984, abs=TOLERANCE
    )
    assert coefficients_by_term["x1"][1] == pytest.approx(
        2.037500000000001, abs=TOLERANCE
    )
    assert coefficients_by_term["x1"][2] == pytest.approx(
        0.13016130102958515, abs=TOLERANCE
    )
    assert coefficients_by_term["x2"][1] == pytest.approx(
        -0.9625000000000005, abs=TOLERANCE
    )
    assert coefficients_by_term["x1"][8] == pytest.approx(1.717546, abs=1e-5)
    assert any(
        item.code in {"multicollinearity", "moderate_multicollinearity"}
        for item in result.diagnostics
    )
    assert any(item.code == "heteroscedasticity" for item in result.diagnostics)


def test_linear_regression_validates_predictors(engine: DuckDBEngine) -> None:
    ctx = context(engine, [{"y": 1.0, "x": 2.0, "label": "a"}])
    with pytest.raises(AnalysisError, match="At least one predictor"):
        linear_regression(ctx, {"response": "y", "predictors": []})
    with pytest.raises(AnalysisError, match="Unknown predictor"):
        linear_regression(ctx, {"response": "y", "predictors": ["missing"]})
    with pytest.raises(AnalysisError, match="numeric"):
        linear_regression(ctx, {"response": "y", "predictors": ["label"]})


# -- logistic regression ---------------------------------------------------


def _logistic_rows() -> list[dict[str, object]]:
    x1 = list(range(1, 25))
    x2 = [0, 1] * 12
    y = [0, 0, 1, 0, 1, 0, 1, 1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 1, 1, 0, 1, 1, 1, 1]
    return [
        {"x1": a, "x2": b, "y": c}
        for a, b, c in zip(x1, x2, y, strict=True)
    ]


def test_logistic_regression_matches_reference(engine: DuckDBEngine) -> None:
    result = logistic_regression(
        context(engine, _logistic_rows(), "logit"),
        {"response": "y", "predictors": ["x1", "x2"]},
    )
    assert result.kind == "logistic_regression"
    assert result.metrics["pseudo_r_squared"] == pytest.approx(
        0.1225661852, abs=TOLERANCE
    )
    assert result.metrics["auc"] == pytest.approx(0.734375, abs=TOLERANCE)
    assert result.metrics["accuracy"] == pytest.approx(0.75, abs=TOLERANCE)
    assert result.metrics["aic"] == pytest.approx(32.807954636, abs=TOLERANCE)
    assert result.metrics["bic"] == pytest.approx(36.342116127, abs=TOLERANCE)
    assert result.metrics["events"] == 16
    assert result.metrics["converged"] is True

    coefficients_by_term = coefficients(result)
    assert coefficients_by_term["(Intercept)"][1] == pytest.approx(
        -0.7635091732, abs=TOLERANCE
    )
    assert coefficients_by_term["x1"][1] == pytest.approx(
        0.1325291278, abs=TOLERANCE
    )
    assert coefficients_by_term["x2"][1] == pytest.approx(
        -0.1325291278, abs=TOLERANCE
    )
    assert coefficients_by_term["x1"][8] == pytest.approx(
        math.exp(0.1325291278), abs=TOLERANCE
    )


def test_logistic_regression_rejects_non_binary_response(
    engine: DuckDBEngine,
) -> None:
    rows = [{"y": 0, "x": 1.0}, {"y": 1, "x": 2.0}, {"y": 2, "x": 3.0}]
    with pytest.raises(AnalysisError, match="binary"):
        logistic_regression(
            context(engine, rows), {"response": "y", "predictors": ["x"]}
        )


# -- pairwise tests --------------------------------------------------------


def test_pairwise_tests_with_holm_correction(engine: DuckDBEngine) -> None:
    rows = [{"value": value, "group": "g1"} for value in (10, 12, 14, 16, 18)]
    rows += [{"value": value, "group": "g2"} for value in (8, 9, 11, 13, 15)]
    rows += [{"value": value, "group": "g3"} for value in (6, 9, 12, 15, 18)]
    result = pairwise_tests(
        context(engine, rows),
        {"value": "value", "group": "group", "correction": "holm"},
    )
    assert result.metrics["pairs"] == 3
    assert result.metrics["correction"] == "holm"
    table = result.primary_table
    assert [row[0] for row in table.rows] == [
        "g1 vs g2",
        "g1 vs g3",
        "g2 vs g3",
    ]
    for row in table.rows:
        assert row[4] is not None and row[5] is not None
        assert row[6] is None or row[6] >= row[5] or math.isclose(
            row[6], row[5], abs_tol=TOLERANCE
        )


def test_multiple_comparison_corrections() -> None:
    raw = [0.01, 0.02, 0.03, 0.04]
    assert _adjust(raw, "none") == pytest.approx(raw)
    assert _adjust(raw, "bonferroni") == pytest.approx([0.04, 0.08, 0.12, 0.16])
    assert _adjust(raw, "holm") == pytest.approx([0.04, 0.06, 0.06, 0.06])
    assert _adjust(raw, "fdr_bh") == pytest.approx([0.04, 0.04, 0.04, 0.04])
    with pytest.raises(AnalysisError, match="Unknown multiple-comparison"):
        _adjust(raw, "nope")


# -- typed results ---------------------------------------------------------


def test_inference_result_round_trips(engine: DuckDBEngine) -> None:
    result = t_test(
        context(engine, [{"value": value} for value in (10, 12, 14, 16, 18)]),
        {"test": "one_sample", "column": "value"},
    )
    payload = result.to_dict()
    assert payload["kind"] == "t_test"
    assert payload["provenance"]["step_ids"] == ["clean"]
    assert payload["metrics"]["p_value"] is not None
    assert result.result_id
