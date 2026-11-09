"""Reference tests for the Phase 2C time-series analyses.

Reference values were generated independently with SciPy, ``statsmodels`` and
``pymannkendall`` and are pinned here so the kernel cannot silently drift. The ARIMA
reference is an ordinary least-squares AR(1) fit, which is the closed-form answer the
conditional-sum-of-squares optimiser must recover.
"""

from __future__ import annotations

import math

import pytest

from data_studio.analysis import AnalysisContext, AnalysisError
from data_studio.analysis import timeseries as ts
from data_studio.engine.duckdb_engine import DuckDBEngine

SERIES = [
    52.5,
    51.5,
    51.0,
    50.5,
    50.0,
    52.0,
    53.0,
    54.5,
    56.0,
    55.5,
    55.5,
    54.5,
    58.0,
    57.5,
    57.0,
    57.0,
    56.0,
    57.5,
    59.0,
    60.5,
    62.5,
    61.5,
    61.0,
    60.5,
    64.0,
    64.0,
    63.0,
    62.5,
    62.0,
    63.5,
    65.5,
    66.5,
    68.0,
    67.5,
    67.0,
    67.0,
    70.0,
    69.5,
    69.0,
    68.5,
    68.5,
    69.5,
    71.0,
    72.5,
    74.0,
    74.0,
    73.0,
    72.5,
]

ACF = [
    0.9401145326,
    0.8677777138,
    0.7888495244,
    0.7145977949,
    0.6489454324,
    0.5907443772,
    0.5362623265,
    0.4833804673,
]
PACF = [
    0.9401145326,
    -0.1380356061,
    -0.0860090109,
    0.0068583075,
    0.0265887075,
    0.0076470350,
    -0.0208836503,
    -0.0277610849,
]
SEASONAL_INDICES = [
    1.7390046296,
    0.9056712963,
    -0.2609953704,
    -1.1012731481,
    -2.1012731481,
    -1.2609953704,
    -0.1012731481,
    0.7390046296,
    1.9056712963,
    0.7390046296,
    -0.1012731481,
    -1.1012731481,
]

TOLERANCE = 1e-6


@pytest.fixture
def engine() -> DuckDBEngine:
    instance = DuckDBEngine()
    try:
        yield instance
    finally:
        instance.close()


def monthly_rows() -> list[dict[str, object]]:
    return [
        {"period": f"{2015 + index // 12}-{(index % 12) + 1:02d}-01", "value": value}
        for index, value in enumerate(SERIES)
    ]


def integer_rows() -> list[dict[str, object]]:
    return [{"period": index, "value": value} for index, value in enumerate(SERIES)]


def context(engine: DuckDBEngine, rows, name: str = "series") -> AnalysisContext:
    return AnalysisContext(
        dataset=name, table=engine.from_records(rows, name=name), step_ids=("clean",)
    )


def execute(name: str, engine: DuckDBEngine, params: dict, rows=None) -> object:
    definition = ts.ANALYSES[[item.name for item in ts.ANALYSES].index(name)]
    return definition.execute(context(engine, rows or monthly_rows()), params)


# -- summary ---------------------------------------------------------------


def test_time_series_summary_profiles_the_series(engine: DuckDBEngine) -> None:
    result = execute(
        "time_series_summary",
        engine,
        {"time": "period", "value": "value"},
    )
    metrics = result.metrics
    assert metrics["n"] == 48
    assert metrics["start"] == "2015-01-01"
    assert metrics["end"] == "2018-12-01"
    assert metrics["frequency"] == "MS"
    assert metrics["seasonal_period"] == 12
    assert metrics["mean"] == pytest.approx(62.020833, abs=TOLERANCE)
    assert metrics["std"] == pytest.approx(7.071036, abs=TOLERANCE)
    assert metrics["trend_per_step"] == pytest.approx(0.497286, abs=TOLERANCE)
    assert metrics["trend_p_value"] < 0.001
    assert len(result.charts) == 2
    assert result.charts[0].chart_type.value == "line"


def test_time_series_summary_rejects_non_numeric_values(engine: DuckDBEngine) -> None:
    rows = [{"period": f"2020-01-{index + 1:02d}", "value": "text"} for index in range(5)]
    with pytest.raises(AnalysisError):
        execute(
            "time_series_summary",
            engine,
            {"time": "period", "value": "value"},
            rows=rows,
        )


def test_time_series_summary_needs_three_observations(engine: DuckDBEngine) -> None:
    rows = [
        {"period": "2020-01-01", "value": 1.0},
        {"period": "2020-02-01", "value": 2.0},
    ]
    with pytest.raises(AnalysisError):
        execute(
            "time_series_summary",
            engine,
            {"time": "period", "value": "value"},
            rows=rows,
        )


# -- autocorrelation -------------------------------------------------------


def test_acf_and_pacf_match_reference(engine: DuckDBEngine) -> None:
    result = execute(
        "acf_pacf", engine, {"time": "period", "value": "value", "max_lag": 8}
    )
    rows = result.tables[0].rows
    assert [row[1] for row in rows] == pytest.approx(ACF, abs=2 * TOLERANCE)
    assert [row[2] for row in rows] == pytest.approx(PACF, abs=2 * TOLERANCE)
    assert result.metrics["max_lag"] == 8
    assert result.metrics["ljung_box_stat"] == pytest.approx(
        219.7618225, abs=1e-4
    )
    assert result.metrics["ljung_box_p"] < 1e-30


def test_acf_rejects_constant_series(engine: DuckDBEngine) -> None:
    rows = [
        {"period": f"2020-01-{index + 1:02d}", "value": 5.0} for index in range(8)
    ]
    with pytest.raises(AnalysisError):
        execute("acf_pacf", engine, {"time": "period", "value": "value"}, rows=rows)


def test_acf_max_lag_is_clamped(engine: DuckDBEngine) -> None:
    result = execute(
        "acf_pacf", engine, {"time": "period", "value": "value", "max_lag": 10_000}
    )
    assert result.metrics["max_lag"] == len(SERIES) - 2


# -- decomposition ---------------------------------------------------------


def test_seasonal_decomposition_matches_reference(engine: DuckDBEngine) -> None:
    result = execute(
        "seasonal_decompose",
        engine,
        {"time": "period", "value": "value", "period": 12},
    )
    indices = [row[1] for row in result.tables[0].rows]
    assert indices == pytest.approx(SEASONAL_INDICES, abs=2e-6)
    assert result.metrics["seasonal_strength"] == pytest.approx(0.980584, abs=1e-5)
    assert result.metrics["trend_strength"] == pytest.approx(0.998963, abs=1e-5)
    assert result.metrics["residual_std"] == pytest.approx(0.167916, abs=1e-5)
    assert len(result.charts) == 3


def test_multiplicative_decomposition_needs_positive_values(
    engine: DuckDBEngine,
) -> None:
    rows = [dict(row) for row in monthly_rows()]
    rows[0]["value"] = 0.0
    with pytest.raises(AnalysisError):
        execute(
            "seasonal_decompose",
            engine,
            {"time": "period", "value": "value", "period": 12, "model": "multiplicative"},
            rows=rows,
        )


def test_decomposition_needs_two_full_seasons(engine: DuckDBEngine) -> None:
    rows = monthly_rows()[:20]
    with pytest.raises(AnalysisError):
        execute(
            "seasonal_decompose",
            engine,
            {"time": "period", "value": "value", "period": 12},
            rows=rows,
        )


def test_decomposition_rejects_unknown_model(engine: DuckDBEngine) -> None:
    with pytest.raises(ValueError):
        execute(
            "seasonal_decompose",
            engine,
            {"time": "period", "value": "value", "period": 12, "model": "additivex"},
        )


# -- trend -----------------------------------------------------------------


def test_mann_kendall_matches_reference(engine: DuckDBEngine) -> None:
    result = execute(
        "trend_test", engine, {"time": "period", "value": "value"}, rows=integer_rows()
    )
    metrics = result.metrics
    assert metrics["s_statistic"] == pytest.approx(987.0, abs=TOLERANCE)
    assert metrics["z"] == pytest.approx(8.768111, abs=1e-5)
    assert metrics["p_value"] == pytest.approx(0.0, abs=1e-12)
    assert metrics["kendall_tau"] == pytest.approx(0.875, abs=TOLERANCE)
    assert metrics["sens_slope"] == pytest.approx(0.5, abs=TOLERANCE)
    assert metrics["direction"] == "increasing"
    assert metrics["significant"] is True


def test_mann_kendall_needs_four_observations(engine: DuckDBEngine) -> None:
    rows = [
        {"period": index, "value": value} for index, value in enumerate(SERIES[:3])
    ]
    with pytest.raises(AnalysisError):
        execute("trend_test", engine, {"time": "period", "value": "value"}, rows=rows)


def test_mann_kendall_rejects_invalid_alpha(engine: DuckDBEngine) -> None:
    with pytest.raises(AnalysisError):
        execute(
            "trend_test",
            engine,
            {"time": "period", "value": "value", "alpha": 1.5},
            rows=integer_rows(),
        )


# -- ARIMA -----------------------------------------------------------------


def test_arima_recovers_ols_ar1_and_forecasts(engine: DuckDBEngine) -> None:
    result = execute(
        "arima_forecast",
        engine,
        {
            "time": "period",
            "value": "value",
            "p": 1,
            "d": 0,
            "q": 0,
            "horizon": 6,
            "backtest": 6,
        },
        rows=integer_rows(),
    )
    coefficients = {row[0]: row for row in result.tables[0].rows}
    # Independent OLS fit of y[t] = a + b * y[t-1].
    assert coefficients["ar1"][1] == pytest.approx(0.98817719, abs=1e-4)
    assert coefficients["constant"][1] == pytest.approx(1.15615671, abs=1e-3)
    forecasts = result.tables[1].rows
    assert len(forecasts) == 6
    assert forecasts[0][2] == pytest.approx(72.798999, abs=1e-4)
    widths = [row[4] - row[3] for row in forecasts]
    assert all(widths[index] < widths[index + 1] for index in range(len(widths) - 1))
    assert result.metrics["converged"] is True
    assert result.metrics["backtest_rmse"] > 0
    assert result.metrics["backtest_mape"] > 0
    assert result.charts[0].chart_type.value == "line"


def test_arima_differencing_forecasts_on_original_scale(
    engine: DuckDBEngine,
) -> None:
    result = execute(
        "arima_forecast",
        engine,
        {
            "time": "period",
            "value": "value",
            "p": 1,
            "d": 1,
            "q": 0,
            "horizon": 4,
            "trend": "none",
        },
        rows=integer_rows(),
    )
    forecasts = [row[2] for row in result.tables[1].rows]
    assert all(math.isfinite(value) for value in forecasts)
    # A differenced series with a positive drift must keep trending upward.
    assert forecasts[0] > SERIES[-1] - 5


def test_arima_rejects_invalid_orders(engine: DuckDBEngine) -> None:
    with pytest.raises(AnalysisError):
        execute(
            "arima_forecast",
            engine,
            {"time": "period", "value": "value", "p": -1},
            rows=integer_rows(),
        )
    with pytest.raises(AnalysisError):
        execute(
            "arima_forecast",
            engine,
            {"time": "period", "value": "value", "horizon": 0},
            rows=integer_rows(),
        )
    with pytest.raises(AnalysisError):
        execute(
            "arima_forecast",
            engine,
            {"time": "period", "value": "value", "confidence": 1.5},
            rows=integer_rows(),
        )


def test_arima_needs_enough_observations(engine: DuckDBEngine) -> None:
    rows = [{"period": index, "value": value} for index, value in enumerate(SERIES[:6])]
    with pytest.raises(AnalysisError):
        execute(
            "arima_forecast",
            engine,
            {"time": "period", "value": "value", "p": 1},
            rows=rows,
        )


# -- shared behaviour ------------------------------------------------------


def test_duplicate_timestamps_are_averaged(engine: DuckDBEngine) -> None:
    rows = [dict(row) for row in monthly_rows()]
    rows.append({"period": "2015-01-01", "value": 99.0})
    result = execute("time_series_summary", engine, {"time": "period", "value": "value"}, rows=rows)
    assert result.metrics["n"] == 48
    assert any(item.code == "duplicate_timestamps" for item in result.diagnostics)


def test_missing_values_are_dropped(engine: DuckDBEngine) -> None:
    rows = [dict(row) for row in monthly_rows()]
    rows[3]["value"] = None
    result = execute("time_series_summary", engine, {"time": "period", "value": "value"}, rows=rows)
    assert result.metrics["n"] == 47
    assert any(item.code == "missing_values_dropped" for item in result.diagnostics)
