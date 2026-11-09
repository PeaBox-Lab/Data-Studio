"""Time-series analyses: profiles, autocorrelation, decomposition, trends and ARIMA.

The analyses follow the same contract as every other kernel analysis: they read through
the engine, validate once, compute with NumPy/SciPy and return a typed `AnalysisResult`
with tables, metrics, diagnostics, a narrative and chart specifications that carry their
own data.

ARIMA is fitted by conditional sum of squares. That keeps the runtime dependency-free
(no `statsmodels`) while still producing coefficients, approximate standard errors,
forecasts with widening intervals, and a hold-out backtest.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy import optimize, stats
from scipy.special import ndtri

from ..engine.duckdb_engine import DuckDBTable
from ..kernel.results import AnalysisResult, Diagnostic, ResultTable
from ..kernel.schema import DataType
from ..kernel.spec import ChartSpec, ChartType, Encoding
from ..steps.registry import StepParam
from ..steps.sql import q, wrap
from ._common import (
    NUMERIC_TYPES,
    label,
    num,
    p_text,
    p_value,
    require_columns,
    result,
)
from .registry import AnalysisContext, AnalysisDefinition, AnalysisError

DECOMPOSITION_MODELS = ("additive", "multiplicative")
ARIMA_TRENDS = ("constant", "none")
MAX_FORECAST_HORIZON = 1000
MAX_LAG_LIMIT = 200


# -- input preparation -----------------------------------------------------


class SeriesData:
    """A cleaned, sorted time series plus the facts analyses need."""

    __slots__ = (
        "duplicates",
        "frame",
        "frequency",
        "gaps",
        "is_datetime",
        "rows_dropped",
        "seasonal_period",
        "step_seconds",
        "time_labels",
        "values",
    )

    def __init__(
        self,
        *,
        frame: pd.DataFrame,
        time_column: str,
        value_column: str,
        is_datetime: bool,
        step_seconds: float | None,
        frequency: str,
        seasonal_period: int,
        duplicates: int,
        gaps: int,
        rows_dropped: int,
    ) -> None:
        self.frame = frame
        self.is_datetime = is_datetime
        self.values = frame[value_column].to_numpy(dtype=float)
        self.time_labels = [
            _time_label(value, is_datetime) for value in frame[time_column]
        ]
        self.step_seconds = step_seconds
        self.frequency = frequency
        self.seasonal_period = seasonal_period
        self.duplicates = duplicates
        self.gaps = gaps
        self.rows_dropped = rows_dropped


def _time_label(value: Any, is_datetime: bool) -> str:
    if is_datetime:
        timestamp = pd.Timestamp(value)
        if timestamp == timestamp.normalize():
            return timestamp.strftime("%Y-%m-%d")
        return timestamp.strftime("%Y-%m-%d %H:%M")
    return label(value)


def _human_seconds(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds:.3g} seconds"
    units = (
        (86400 * 365.25, "year"),
        (86400 * 30.44, "month"),
        (86400 * 7, "week"),
        (86400, "day"),
        (3600, "hour"),
        (60, "minute"),
        (1, "second"),
    )
    for size, name in units:
        if seconds >= size:
            count = seconds / size
            rounded = round(count, 2)
            if abs(rounded - round(rounded)) < 1e-9:
                rounded = round(rounded)
            return f"{rounded:g} {name}{'' if rounded == 1 else 's'}"
    return f"{seconds:g} seconds"  # pragma: no cover - defensive


def _period_for_frequency(frequency: str) -> int:
    upper = frequency.upper()
    if "H" in upper or ("T" in upper and "M" not in upper.replace("T", "")):
        return 24 if "H" in upper else 60
    if upper.startswith("S"):
        return 60
    if upper.startswith("D"):
        return 7
    if upper.startswith("W"):
        return 52
    if upper.startswith("M"):
        return 12
    if upper.startswith("Q"):
        return 4
    if upper.startswith("Y") or upper.startswith("A"):
        return 1
    return 0


def _period_for_seconds(seconds: float | None) -> int:
    if seconds is None:
        return 0
    if seconds <= 1.5:
        return 60
    if seconds <= 90:
        return 60
    if seconds <= 3600:
        return 24
    if seconds <= 86400:
        return 7
    if seconds <= 86400 * 7:
        return 52
    if seconds <= 86400 * 31:
        return 12
    if seconds <= 86400 * 92:
        return 4
    return 0


def _prepare(table: DuckDBTable, time_column: str, value_column: str) -> SeriesData:
    require_columns(table, (time_column, value_column))
    if time_column == value_column:
        raise AnalysisError("The time and value columns must be different.")
    value_type = table.schema.column(value_column).dtype
    if value_type not in NUMERIC_TYPES:
        raise AnalysisError(f"The value column {value_column!r} must be numeric.")

    rows = table.engine.query(
        f"SELECT {q(time_column)} AS {q(time_column)}, "
        f"CAST({q(value_column)} AS DOUBLE) AS {q(value_column)} "
        f"FROM {wrap(table)} AS _series ORDER BY 1"
    )
    data = pd.DataFrame(rows, columns=[time_column, value_column])
    data[value_column] = pd.to_numeric(data[value_column], errors="coerce")
    original_rows = len(data)

    time_type = table.schema.column(time_column).dtype
    is_datetime = time_type in {DataType.DATE, DataType.DATETIME}
    if is_datetime:
        data[time_column] = pd.to_datetime(data[time_column], errors="coerce")
    elif time_type not in NUMERIC_TYPES:
        parsed = pd.to_datetime(data[time_column], errors="coerce")
        if parsed.notna().any():
            if int(parsed.isna().sum()) > int(data[time_column].isna().sum()):
                raise AnalysisError(
                    f"The time column {time_column!r} contains values that are not dates."
                )
            data[time_column] = parsed
            is_datetime = True
        else:
            numeric = pd.to_numeric(data[time_column], errors="coerce")
            if numeric.notna().any():
                data[time_column] = numeric
            else:
                raise AnalysisError(
                    f"The time column {time_column!r} must contain dates or numbers."
                )

    data = data.dropna(subset=[time_column, value_column])
    if len(data) < 3:
        raise AnalysisError(
            "A time series needs at least three complete time/value observations."
        )
    duplicates = int(data[time_column].duplicated().sum())
    if duplicates:
        data = (
            data.groupby(time_column, as_index=False)[value_column]
            .mean()
            .sort_values(time_column)
            .reset_index(drop=True)
        )
    else:
        data = data.sort_values(time_column).reset_index(drop=True)

    if is_datetime:
        deltas = (
            data[time_column]
            .diff()
            .dt.total_seconds()
            .to_numpy(dtype=float)[1:]
        )
    else:
        deltas = np.diff(data[time_column].to_numpy(dtype=float))

    finite_positive = deltas[np.isfinite(deltas) & (deltas > 0)]
    step_seconds = (
        float(np.median(finite_positive)) if finite_positive.size else None
    )
    gaps = 0
    if step_seconds:
        gaps = int(np.sum(finite_positive > step_seconds * 1.5))

    frequency = "irregular"
    seasonal_period = _period_for_seconds(step_seconds)
    if is_datetime and len(data) >= 3:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                inferred = pd.infer_freq(pd.DatetimeIndex(data[time_column]))
        except ValueError:
            inferred = None
        if inferred:
            frequency = getattr(inferred, "freqstr", None) or str(inferred)
            seasonal_period = _period_for_frequency(frequency)
    if frequency == "irregular" and step_seconds:
        frequency = _human_seconds(step_seconds)

    return SeriesData(
        frame=data,
        time_column=time_column,
        value_column=value_column,
        is_datetime=is_datetime,
        step_seconds=step_seconds,
        frequency=frequency,
        seasonal_period=seasonal_period,
        duplicates=duplicates,
        gaps=gaps,
        rows_dropped=original_rows - len(data),
    )


def _line_chart(
    title: str,
    records: Sequence[Mapping[str, Any]],
    *,
    x: str = "period",
    y: str = "value",
    color: str | None = None,
    chart_type: ChartType = ChartType.LINE,
) -> ChartSpec:
    return ChartSpec(
        chart_type=chart_type,
        title=title,
        encoding=Encoding(x=x, y=y, color=color),
        data=[dict(row) for row in records],
    )


def _series_chart(
    title: str,
    labels: Sequence[str],
    values: Sequence[float],
    *,
    series_name: str | None = None,
) -> ChartSpec:
    if series_name is None:
        records = [
            {"period": label_, "value": value}
            for label_, value in zip(labels, values, strict=True)
        ]
    else:
        records = [
            {"period": label_, "series": series_name, "value": value}
            for label_, value in zip(labels, values, strict=True)
        ]
    return _line_chart(title, records, color="series" if series_name else None)


def _standard_diagnostics(series: SeriesData) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if series.duplicates:
        diagnostics.append(
            Diagnostic.info(
                "duplicate_timestamps",
                f"{series.duplicates} duplicate timestamp(s) were averaged.",
                duplicate_timestamps=series.duplicates,
            )
        )
    if series.rows_dropped:
        diagnostics.append(
            Diagnostic.info(
                "missing_values_dropped",
                f"{series.rows_dropped} row(s) with a missing time or value were dropped.",
                dropped=series.rows_dropped,
            )
        )
    if series.gaps:
        diagnostics.append(
            Diagnostic.warning(
                "irregular_spacing",
                f"{series.gaps} gap(s) larger than the usual interval were found; "
                "some time-series methods assume regular spacing.",
                gaps=series.gaps,
            )
        )
    if len(series.values) < 10:
        diagnostics.append(
            Diagnostic.info(
                "short_series",
                f"Only {len(series.values)} observation(s); estimates may be unstable.",
                n=len(series.values),
            )
        )
    return diagnostics


# -- summary ---------------------------------------------------------------


def _linear_trend(values: np.ndarray) -> tuple[float, float, float, float]:
    """Return slope, intercept, standard error and p-value of a linear trend."""

    n = len(values)
    if n < 3:
        return 0.0, float(np.mean(values)) if n else 0.0, 0.0, 1.0
    x = np.arange(n, dtype=float)
    mean_x = float(x.mean())
    mean_y = float(values.mean())
    sxx = float(((x - mean_x) ** 2).sum())
    slope = float(((x - mean_x) * (values - mean_y)).sum() / sxx) if sxx else 0.0
    intercept = mean_y - slope * mean_x
    residuals = values - (intercept + slope * x)
    dof = n - 2
    sigma2 = float((residuals @ residuals) / dof) if dof else 0.0
    se = math.sqrt(sigma2 / sxx) if sxx and sigma2 > 0 else 0.0
    if se > 0:
        statistic = slope / se
        pvalue = float(2.0 * stats.t.sf(abs(statistic), dof))
    else:
        pvalue = 1.0
    return slope, intercept, se, pvalue


def time_series_summary(
    context: AnalysisContext, params: Mapping[str, Any]
) -> AnalysisResult:
    time_column = params["time"]
    value_column = params["value"]
    series = _prepare(context.table, time_column, value_column)
    values = series.values
    labels = series.time_labels
    n = len(values)
    slope, _intercept, _slope_se, slope_p = _linear_trend(values)
    diagnostics = _standard_diagnostics(series)
    if series.seasonal_period and n < 2 * series.seasonal_period:
        diagnostics.append(
            Diagnostic.info(
                "short_for_seasonality",
                f"At least two full seasons ({2 * series.seasonal_period} observations) "
                "are recommended for seasonal analysis.",
                period=series.seasonal_period,
            )
        )

    summary_rows = (
        ("Observations", n),
        ("Start", labels[0]),
        ("End", labels[-1]),
        ("Frequency", series.frequency),
        ("Seasonal period", series.seasonal_period or None),
        ("Gaps", series.gaps),
        ("Mean", num(float(values.mean()))),
        ("Standard deviation", num(float(values.std(ddof=1)))),
        ("Minimum", num(float(values.min()))),
        ("Maximum", num(float(values.max()))),
        ("Trend per step", num(slope)),
        ("Trend p-value", p_value(slope_p)),
    )
    metrics = {
        "n": n,
        "start": labels[0],
        "end": labels[-1],
        "frequency": series.frequency,
        "seasonal_period": series.seasonal_period,
        "gaps": series.gaps,
        "mean": num(float(values.mean())),
        "std": num(float(values.std(ddof=1))),
        "min": num(float(values.min())),
        "max": num(float(values.max())),
        "trend_per_step": num(slope),
        "trend_p_value": p_value(slope_p),
        "total_change": num(float(values[-1] - values[0])),
    }
    direction = "upward" if slope > 0 else "downward" if slope < 0 else "flat"
    narrative = (
        f"The series runs from {labels[0]} to {labels[-1]} with {n:,} observation(s) "
        f"at an inferred frequency of {series.frequency}. It has a {direction} linear "
        f"trend of {slope:.4g} per step (p={p_text(slope_p)})."
    )
    return result(
        context,
        "time_series_summary",
        f"Time-series summary · {value_column}",
        params,
        tables=(
            ResultTable(
                title="Series summary",
                columns=("metric", "value"),
                rows=summary_rows,
                note="The trend is an ordinary least-squares line over observation order.",
            ),
        ),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
        charts=(
            _series_chart(f"{value_column} over time", labels, values),
            _line_chart(
                f"{value_column}: observed and fitted trend",
                [
                    {"period": label_, "series": series_name, "value": value}
                    for series_name, component in (
                        ("observed", values),
                        ("trend", _intercept + slope * np.arange(n)),
                    )
                    for label_, value in zip(labels, component, strict=True)
                ],
                color="series",
            ),
        ),
    )


# -- autocorrelation -------------------------------------------------------


def _acf(values: np.ndarray, max_lag: int) -> np.ndarray:
    centered = values - values.mean()
    denominator = float(centered @ centered)
    if denominator <= 0:
        raise AnalysisError("Autocorrelation is undefined for a constant series.")
    result_values = np.empty(max_lag + 1, dtype=float)
    result_values[0] = 1.0
    for lag in range(1, max_lag + 1):
        result_values[lag] = float(
            (centered[lag:] @ centered[:-lag]) / denominator
        )
    return result_values


def _pacf(acf_values: np.ndarray, max_lag: int) -> np.ndarray:
    """Levinson-Durbin recursion on the biased autocorrelations."""

    pacf_values = np.zeros(max_lag + 1, dtype=float)
    pacf_values[0] = 1.0
    if max_lag == 0:
        return pacf_values
    coefficients = np.zeros((max_lag + 1, max_lag + 1), dtype=float)
    for lag in range(1, max_lag + 1):
        if lag == 1:
            reflection = acf_values[1]
        else:
            numerator = acf_values[lag] - sum(
                coefficients[lag - 1, index] * acf_values[lag - index]
                for index in range(1, lag)
            )
            denominator = 1.0 - sum(
                coefficients[lag - 1, index] * acf_values[index]
                for index in range(1, lag)
            )
            reflection = numerator / denominator if denominator != 0 else 0.0
        coefficients[lag, lag] = reflection
        for index in range(1, lag):
            coefficients[lag, index] = (
                coefficients[lag - 1, index]
                - reflection * coefficients[lag - 1, lag - index]
            )
        pacf_values[lag] = reflection
    return pacf_values


def _ljung_box(values: np.ndarray, acf_values: np.ndarray, lag: int) -> tuple[float, float]:
    n = len(values)
    denominator = n * (n + 2)
    total = 0.0
    for index in range(1, lag + 1):
        total += acf_values[index] ** 2 / (n - index)
    statistic = denominator * total
    return float(statistic), float(stats.chi2.sf(statistic, lag))


def acf_pacf(context: AnalysisContext, params: Mapping[str, Any]) -> AnalysisResult:
    time_column = params["time"]
    value_column = params["value"]
    series = _prepare(context.table, time_column, value_column)
    n = len(series.values)
    requested = int(params.get("max_lag") or min(20, max(1, n // 4)))
    max_lag = max(1, min(requested, n - 2, MAX_LAG_LIMIT))
    acf_values = _acf(series.values, max_lag)
    pacf_values = _pacf(acf_values, max_lag)
    bound = 1.96 / math.sqrt(n)

    rows: list[tuple[Any, ...]] = []
    significant = 0
    for lag in range(1, max_lag + 1):
        statistic, pvalue = _ljung_box(series.values, acf_values, lag)
        is_significant = bool(abs(acf_values[lag]) > bound)
        significant += int(is_significant)
        rows.append(
            (
                lag,
                num(float(acf_values[lag])),
                num(float(pacf_values[lag])),
                num(statistic),
                p_value(pvalue),
                is_significant,
            )
        )
    ljung_stat, ljung_p = _ljung_box(series.values, acf_values, max_lag)

    diagnostics = _standard_diagnostics(series)
    if ljung_p < 0.05:
        diagnostics.append(
            Diagnostic.warning(
                "autocorrelation",
                "The Ljung-Box test rejects independence "
                f"(Q={ljung_stat:.3f}, p={p_text(ljung_p)}).",
                statistic=num(ljung_stat),
                p_value=p_value(ljung_p),
                max_lag=max_lag,
            )
        )
    else:
        diagnostics.append(
            Diagnostic.info(
                "no_autocorrelation",
                f"No significant autocorrelation up to lag {max_lag} "
                f"(p={p_text(ljung_p)}).",
                statistic=num(ljung_stat),
                p_value=p_value(ljung_p),
            )
        )

    metrics = {
        "n": n,
        "max_lag": max_lag,
        "significant_lags": significant,
        "ljung_box_stat": num(ljung_stat),
        "ljung_box_p": p_value(ljung_p),
        "confidence_bound": num(float(bound)),
    }
    narrative = (
        f"The autocorrelation function was estimated to lag {max_lag}. "
        f"{significant} lag(s) fall outside the ±{bound:.3f} approximate 95% band. "
        f"The Ljung-Box statistic is {ljung_stat:.3f} (p={p_text(ljung_p)})."
    )
    acf_records = [
        {"lag": lag, "acf": num(float(acf_values[lag]))}
        for lag in range(1, max_lag + 1)
    ]
    pacf_records = [
        {"lag": lag, "pacf": num(float(pacf_values[lag]))}
        for lag in range(1, max_lag + 1)
    ]
    return result(
        context,
        "acf_pacf",
        f"Autocorrelation · {value_column}",
        params,
        tables=(
            ResultTable(
                title="ACF, PACF and Ljung-Box",
                columns=("lag", "acf", "pacf", "ljung_box", "p_value", "significant"),
                rows=tuple(rows),
                note=(
                    "Autocorrelations use the biased (divide by n) estimator. PACF uses "
                    "Levinson-Durbin. Ljung-Box p-values are tested at each lag."
                ),
            ),
        ),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
        charts=(
            _line_chart(
                "Autocorrelation function",
                acf_records,
                x="lag",
                y="acf",
                chart_type=ChartType.COLUMN,
            ),
            _line_chart(
                "Partial autocorrelation function",
                pacf_records,
                x="lag",
                y="pacf",
                chart_type=ChartType.COLUMN,
            ),
        ),
    )


# -- decomposition ---------------------------------------------------------


def _moving_average_trend(values: np.ndarray, period: int) -> np.ndarray:
    n = len(values)
    trend = np.full(n, np.nan, dtype=float)
    if period < 2 or n < period:
        return trend
    if period % 2 == 0:
        weights = np.array([0.5] + [1.0] * (period - 1) + [0.5]) / period
        length = len(weights)
        filtered = np.convolve(values, weights, mode="valid")
        start = (length - 1) // 2
        trend[start : start + len(filtered)] = filtered
    else:
        weights = np.ones(period) / period
        filtered = np.convolve(values, weights, mode="valid")
        start = (period - 1) // 2
        trend[start : start + len(filtered)] = filtered
    return trend


def seasonal_decompose(
    context: AnalysisContext, params: Mapping[str, Any]
) -> AnalysisResult:
    time_column = params["time"]
    value_column = params["value"]
    model = str(params.get("model") or "additive")
    if model not in DECOMPOSITION_MODELS:
        raise AnalysisError(
            "Model must be one of: " + ", ".join(DECOMPOSITION_MODELS) + "."
        )
    series = _prepare(context.table, time_column, value_column)
    values = series.values
    n = len(values)
    period = int(params.get("period") or series.seasonal_period or 0)
    if period < 2:
        raise AnalysisError(
            "A seasonal period of at least 2 is required. Provide 'period' or use a "
            "time column with a recognisable frequency."
        )
    if period > MAX_LAG_LIMIT:
        raise AnalysisError(f"The seasonal period must not exceed {MAX_LAG_LIMIT}.")
    if n < 2 * period:
        raise AnalysisError(
            f"Decomposition needs at least two full seasons ({2 * period} observations); "
            f"the series has {n}."
        )
    if model == "multiplicative" and np.any(values <= 0):
        raise AnalysisError(
            "A multiplicative decomposition requires strictly positive values."
        )

    trend = _moving_average_trend(values, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        detrended = values - trend if model == "additive" else values / trend
    period_averages = np.array(
        [
            np.nanmean(detrended[phase::period])
            if np.any(np.isfinite(detrended[phase::period]))
            else 0.0
            for phase in range(period)
        ],
        dtype=float,
    )
    if model == "additive":
        period_averages -= period_averages.mean()
    else:
        mean = period_averages.mean()
        if mean == 0 or not math.isfinite(mean):
            raise AnalysisError("The multiplicative seasonal indices are undefined.")
        period_averages /= mean
    seasonal = np.resize(period_averages, n)
    residual = (
        values - trend - seasonal
        if model == "additive"
        else values - trend * seasonal
    )
    valid = np.isfinite(residual)
    residual_variance = float(np.nanvar(residual)) if valid.any() else float("nan")
    detrended_component = residual + seasonal if model == "additive" else residual * seasonal
    trend_plus_residual = residual + trend if model == "additive" else residual * trend
    seasonal_strength = (
        max(0.0, 1.0 - residual_variance / float(np.nanvar(detrended_component)))
        if np.isfinite(residual_variance) and np.nanvar(detrended_component) > 0
        else 0.0
    )
    trend_strength = (
        max(0.0, 1.0 - residual_variance / float(np.nanvar(trend_plus_residual)))
        if np.isfinite(residual_variance) and np.nanvar(trend_plus_residual) > 0
        else 0.0
    )

    phase_rows = tuple(
        (phase + 1, num(float(period_averages[phase])))
        for phase in range(period)
    )
    component_rows = tuple(
        (
            name,
            num(float(np.nanmean(component))) if np.isfinite(component).any() else None,
            num(float(np.nanstd(component))) if np.isfinite(component).any() else None,
            num(float(np.nanmin(component))) if np.isfinite(component).any() else None,
            num(float(np.nanmax(component))) if np.isfinite(component).any() else None,
        )
        for name, component in (
            ("observed", values),
            ("trend", trend),
            ("seasonal", seasonal),
            ("residual", residual),
        )
    )

    diagnostics = _standard_diagnostics(series)
    if series.gaps:
        diagnostics.append(
            Diagnostic.warning(
                "gaps_weighted",
                "The moving-average trend does not interpolate gaps; consider imputing "
                "or resampling the series first.",
            )
        )
    if model == "additive" and np.nanmean(values) < np.nanstd(values):
        diagnostics.append(
            Diagnostic.info(
                "additive_with_large_variation",
                "Values are highly variable relative to their mean; a multiplicative "
                "model may fit better if the seasonal amplitude grows with the level.",
            )
        )

    metrics = {
        "n": n,
        "period": period,
        "model": model,
        "seasonal_strength": num(seasonal_strength),
        "trend_strength": num(trend_strength),
        "residual_std": num(math.sqrt(residual_variance))
        if np.isfinite(residual_variance)
        else None,
        "trend_points": int(np.isfinite(trend).sum()),
    }
    residual_std_text = (
        "n/a"
        if not np.isfinite(residual_variance)
        else f"{math.sqrt(residual_variance):.4g}"
    )
    narrative = (
        f"Classic {model} decomposition with period {period} over {n:,} observation(s). "
        f"Seasonal strength is {seasonal_strength:.3f} and trend strength is "
        f"{trend_strength:.3f}; residual standard deviation is {residual_std_text}."
    )
    observed_trend_records = [
        {"period": period_label, "series": name, "value": value}
        for name, component in (("observed", values), ("trend", trend))
        for period_label, value in zip(series.time_labels, component, strict=True)
        if value is None or math.isfinite(float(value))
    ]
    residual_records = [
        {"period": period_label, "value": value}
        for period_label, value in zip(series.time_labels, residual, strict=True)
        if math.isfinite(float(value))
    ]
    return result(
        context,
        "seasonal_decompose",
        f"Seasonal decomposition · {value_column}",
        params,
        tables=(
            ResultTable(
                title="Seasonal indices",
                columns=("phase", "index"),
                rows=phase_rows,
                note=(
                    "Additive indices sum to zero; multiplicative indices average to one."
                ),
            ),
            ResultTable(
                title="Components",
                columns=("component", "mean", "std", "min", "max"),
                rows=component_rows,
            ),
        ),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
        charts=(
            _line_chart(
                f"{value_column}: observed and trend",
                observed_trend_records,
                x="period",
                y="value",
                color="series",
            ),
            _line_chart(
                "Seasonal component",
                [
                    {"period": period_label, "value": num(float(value))}
                    for period_label, value in zip(series.time_labels, seasonal, strict=True)
                ],
            ),
            _line_chart(
                f"{value_column}: residual",
                residual_records,
            ),
        ),
    )


# -- Mann-Kendall trend ----------------------------------------------------


def _sen_slope(times: np.ndarray, values: np.ndarray) -> float | None:
    slopes: list[float] = []
    n = len(values)
    for first in range(n - 1):
        for second in range(first + 1, n):
            delta = times[second] - times[first]
            if delta != 0:
                slopes.append((values[second] - values[first]) / delta)
    if not slopes:
        return None
    return float(np.median(slopes))


def trend_test(context: AnalysisContext, params: Mapping[str, Any]) -> AnalysisResult:
    time_column = params["time"]
    value_column = params["value"]
    alpha = float(params.get("alpha") if params.get("alpha") is not None else 0.05)
    if not 0.0 < alpha < 1.0:
        raise AnalysisError("Alpha must be strictly between 0 and 1.")
    series = _prepare(context.table, time_column, value_column)
    values = series.values
    n = len(values)
    if n < 4:
        raise AnalysisError("The Mann-Kendall test needs at least four observations.")

    statistic = 0.0
    for first in range(n - 1):
        statistic += float(
            np.sign(values[first + 1 :] - values[first]).sum()
        )
    _, tie_counts = np.unique(values, return_counts=True)
    tie_correction = float(
        sum(count * (count - 1) * (2 * count + 5) for count in tie_counts)
    )
    variance = (n * (n - 1) * (2 * n + 5) - tie_correction) / 18.0
    if variance > 0:
        if statistic > 0:
            z_value = (statistic - 1.0) / math.sqrt(variance)
        elif statistic < 0:
            z_value = (statistic + 1.0) / math.sqrt(variance)
        else:
            z_value = 0.0
        pvalue = float(2.0 * stats.norm.sf(abs(z_value)))
    else:
        z_value = 0.0
        pvalue = 1.0
    tau = statistic / (0.5 * n * (n - 1))

    if series.is_datetime:
        numeric_times = (
            pd.to_datetime(series.frame[time_column]).astype("int64").to_numpy()
            / 1e9
        )
    else:
        numeric_times = series.frame[time_column].to_numpy(dtype=float)
    slope = _sen_slope(numeric_times, values)
    intercept = float(np.median(values - (slope or 0.0) * numeric_times))
    fitted = intercept + (slope or 0.0) * numeric_times

    diagnostics = _standard_diagnostics(series)
    significant = pvalue < alpha
    diagnostics.append(
        Diagnostic.info(
            "trend_detected" if significant else "no_trend",
            (
                "A monotonic trend is significant at the chosen alpha."
                if significant
                else "No monotonic trend was detected at the chosen alpha."
            ),
            alpha=alpha,
            p_value=p_value(pvalue),
        )
    )
    if len(tie_counts) < n:
        diagnostics.append(
            Diagnostic.info(
                "ties_present",
                f"{n - len(tie_counts)} tied observation(s) were corrected for.",
            )
        )

    trend_text = "increasing" if statistic > 0 else "decreasing" if statistic < 0 else "flat"
    metrics = {
        "n": n,
        "s_statistic": num(statistic),
        "z": num(z_value),
        "p_value": p_value(pvalue),
        "p_value_text": p_text(pvalue),
        "kendall_tau": num(tau),
        "sens_slope": num(slope),
        "alpha": alpha,
        "significant": significant,
        "direction": trend_text,
    }
    slope_text = "n/a" if slope is None else f"{slope:.4g}"
    article = "an" if trend_text == "increasing" else "a"
    narrative = (
        f"The Mann-Kendall test over {n:,} observation(s) found {article} {trend_text} "
        f"monotonic trend (S={statistic:.1f}, z={z_value:.3f}, p={p_text(pvalue)}, "
        f"Kendall tau={tau:.3f}). Sen's slope is {slope_text} per time unit."
    )
    return result(
        context,
        "trend_test",
        f"Mann-Kendall trend · {value_column}",
        params,
        tables=(
            ResultTable(
                title="Trend test",
                columns=("metric", "value"),
                rows=(
                    ("S statistic", num(statistic)),
                    ("Variance of S", num(variance)),
                    ("z", num(z_value)),
                    ("p-value", p_value(pvalue)),
                    ("Kendall tau", num(tau)),
                    ("Sen's slope", num(slope)),
                    ("Alpha", alpha),
                    ("Significant", significant),
                ),
                note=(
                    "The normal approximation includes a tie correction. Sen's slope is "
                    "the median of all pairwise slopes."
                ),
            ),
        ),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
        charts=(
            _line_chart(
                f"{value_column}: observed and Sen trend",
                [
                    {"period": period_label, "series": name, "value": num(float(value))}
                    for name, component in (("observed", values), ("trend", fitted))
                    for period_label, value in zip(series.time_labels, component, strict=True)
                ],
                x="period",
                y="value",
                color="series",
            ),
        ),
    )


# -- ARIMA -----------------------------------------------------------------


def _difference(values: np.ndarray, order: int) -> np.ndarray:
    result_values = values
    for _ in range(order):
        result_values = np.diff(result_values)
    return result_values


def _arma_residuals(
    parameters: np.ndarray,
    values: np.ndarray,
    p: int,
    q: int,
    constant: bool,
) -> np.ndarray:
    offset = 1 if constant else 0
    level = float(parameters[0]) if constant else 0.0
    phi = parameters[offset : offset + p]
    theta = parameters[offset + p : offset + p + q]
    n = len(values)
    start = max(p, q)
    errors = np.zeros(n, dtype=float)
    for index in range(start, n):
        predicted = level
        for lag in range(p):
            predicted += phi[lag] * values[index - lag - 1]
        for lag in range(q):
            predicted += theta[lag] * errors[index - lag - 1]
        errors[index] = values[index] - predicted
    return errors[start:]


def _fit_arma(
    values: np.ndarray,
    p: int,
    q: int,
    constant: bool,
) -> dict[str, Any]:
    count = p + q + (1 if constant else 0)
    n = len(values)
    if n <= count + 2:
        raise AnalysisError(
            f"ARIMA({p},{q}) needs more than {count + 2} differenced observations; "
            f"the series provides {n}."
        )

    def objective(parameters: np.ndarray) -> float:
        errors = _arma_residuals(parameters, values, p, q, constant)
        if errors.size == 0:
            return 1e30
        return float(errors @ errors)

    start = np.zeros(count, dtype=float)
    options = {"maxiter": 2000, "ftol": 1e-12}
    fitted = optimize.minimize(
        objective, start, method="L-BFGS-B", options=options
    )
    if not fitted.success or not np.all(np.isfinite(fitted.x)):
        fallback = optimize.minimize(
            objective,
            start,
            method="Nelder-Mead",
            options={"maxiter": 8000, "xatol": 1e-10, "fatol": 1e-10},
        )
        if fallback.fun <= fitted.fun:
            fitted = fallback

    parameters = np.asarray(fitted.x, dtype=float)
    errors = _arma_residuals(parameters, values, p, q, constant)
    effective = errors.size
    if effective <= 0:
        raise AnalysisError("ARIMA residuals could not be computed.")
    residual_sum = float(errors @ errors)
    sigma2 = residual_sum / effective

    # Gauss-Newton covariance from a finite-difference Jacobian of the residuals.
    step = 1e-6
    jacobian = np.empty((effective, count), dtype=float)
    base = errors.copy()
    for index in range(count):
        shifted = parameters.copy()
        magnitude = max(1.0, abs(parameters[index]))
        shifted[index] += step * magnitude
        jacobian[:, index] = (
            _arma_residuals(shifted, values, p, q, constant) - base
        ) / (step * magnitude)
    covariance = None
    try:
        information = jacobian.T @ jacobian
        if np.linalg.matrix_rank(information) == count:
            covariance = sigma2 * np.linalg.inv(information)
    except np.linalg.LinAlgError:  # pragma: no cover - defensive
        covariance = None
    standard_errors = (
        np.sqrt(np.clip(np.diag(covariance), 0.0, None))
        if covariance is not None
        else np.full(count, np.nan)
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        z_values = np.divide(
            parameters,
            standard_errors,
            out=np.full(count, np.nan),
            where=standard_errors > 0,
        )
    p_values = 2.0 * stats.norm.sf(np.abs(z_values))

    log_likelihood = float(
        -0.5 * effective * (math.log(2 * math.pi) + math.log(sigma2) + 1.0)
    ) if sigma2 > 0 else None
    aic = -2.0 * log_likelihood + 2.0 * count if log_likelihood is not None else None
    bic = (
        -2.0 * log_likelihood + count * math.log(effective)
        if log_likelihood is not None
        else None
    )
    return {
        "parameters": parameters,
        "errors": errors,
        "sigma2": sigma2,
        "standard_errors": standard_errors,
        "z_values": z_values,
        "p_values": p_values,
        "covariance": covariance,
        "log_likelihood": log_likelihood,
        "aic": aic,
        "bic": bic,
        "converged": bool(fitted.success) or bool(
            np.all(np.isfinite(parameters))
        ),
        "iterations": int(getattr(fitted, "nit", 0) or 0),
        "objective": residual_sum,
        "effective": effective,
    }


def _forecast_arma(
    parameters: np.ndarray,
    values: np.ndarray,
    errors: np.ndarray,
    *,
    p: int,
    q: int,
    constant: bool,
    horizon: int,
) -> np.ndarray:
    offset = 1 if constant else 0
    level = float(parameters[0]) if constant else 0.0
    phi = parameters[offset : offset + p]
    theta = parameters[offset + p : offset + p + q]
    history = list(values.astype(float))
    known_errors = list(errors.astype(float))
    # Align errors with the observed series: the first max(p, q) errors are unknown.
    padding = len(values) - len(known_errors)
    known_errors = [0.0] * padding + known_errors
    forecasts: list[float] = []
    for _ in range(horizon):
        predicted = level
        for lag in range(p):
            predicted += phi[lag] * history[-lag - 1]
        for lag in range(q):
            predicted += theta[lag] * known_errors[-lag - 1]
        forecasts.append(float(predicted))
        history.append(float(predicted))
        known_errors.append(0.0)
    return np.array(forecasts, dtype=float)


def _integrate_forecasts(
    original: np.ndarray, differences: np.ndarray, order: int
) -> np.ndarray:
    stages: list[np.ndarray] = [_difference(original, index) for index in range(order + 1)]
    forecast = differences
    for level in range(order - 1, -1, -1):
        forecast = float(stages[level][-1]) + np.cumsum(forecast)
    return forecast


def _future_labels(series: SeriesData, horizon: int) -> list[str]:
    labels = series.time_labels
    if series.is_datetime:
        timestamps = pd.to_datetime(series.frame[series.frame.columns[0]])
        if series.step_seconds:
            step = pd.Timedelta(seconds=series.step_seconds)
        else:  # pragma: no cover - guarded by _prepare
            step = pd.Timedelta(days=1)
        return [
            _time_label(timestamps.iloc[-1] + step * (index + 1), True)
            for index in range(horizon)
        ]
    if series.step_seconds:
        last = float(series.frame[series.frame.columns[0]].iloc[-1])
        return [
            _time_label(last + series.step_seconds * (index + 1), False)
            for index in range(horizon)
        ]
    return [f"step {len(labels) + index + 1}" for index in range(horizon)]


def arima_forecast(
    context: AnalysisContext, params: Mapping[str, Any]
) -> AnalysisResult:
    time_column = params["time"]
    value_column = params["value"]
    try:
        p = int(params.get("p") if params.get("p") is not None else 1)
        d = int(params.get("d") if params.get("d") is not None else 0)
        q = int(params.get("q") if params.get("q") is not None else 0)
    except (TypeError, ValueError) as exc:
        raise AnalysisError("ARIMA orders p, d and q must be integers.") from exc
    if any(order < 0 for order in (p, d, q)):
        raise AnalysisError("ARIMA orders must be zero or positive.")
    if p > 10 or q > 10 or d > 2:
        raise AnalysisError("ARIMA supports p <= 10, q <= 10 and d <= 2.")
    horizon = int(params.get("horizon") if params.get("horizon") is not None else 12)
    if horizon < 1 or horizon > MAX_FORECAST_HORIZON:
        raise AnalysisError(f"The forecast horizon must be between 1 and {MAX_FORECAST_HORIZON}.")
    backtest = int(params.get("backtest") if params.get("backtest") is not None else 0)
    if backtest < 0 or backtest > MAX_FORECAST_HORIZON:
        raise AnalysisError(f"Backtest size must be between 0 and {MAX_FORECAST_HORIZON}.")
    confidence = float(
        params.get("confidence") if params.get("confidence") is not None else 0.95
    )
    if not 0.0 < confidence < 1.0:
        raise AnalysisError("Confidence must be strictly between 0 and 1.")
    trend = str(params.get("trend") or "constant")
    if trend not in ARIMA_TRENDS:
        raise AnalysisError("Trend must be one of: " + ", ".join(ARIMA_TRENDS) + ".")
    constant = trend == "constant" and d == 0

    series = _prepare(context.table, time_column, value_column)
    values = series.values
    n = len(values)
    if n < max(8, p + d + q + 4):
        raise AnalysisError(
            f"ARIMA({p},{d},{q}) needs at least {max(8, p + d + q + 4)} observations; "
            f"the series has {n}."
        )
    differenced = _difference(values, d)
    if differenced.size <= p + q + 3:
        raise AnalysisError("Too few observations remain after differencing.")

    fitted = _fit_arma(differenced, p, q, constant)
    parameters = fitted["parameters"]
    errors = fitted["errors"]
    forecast_differences = _forecast_arma(
        parameters,
        differenced,
        errors,
        p=p,
        q=q,
        constant=constant,
        horizon=horizon,
    )
    forecast = _integrate_forecasts(values, forecast_differences, d)

    critical = float(ndtri(0.5 + confidence / 2.0))
    sigma = math.sqrt(max(fitted["sigma2"], 0.0))
    steps = np.arange(1, horizon + 1, dtype=float)
    widening = sigma * np.sqrt(steps)
    lower_differences = forecast_differences - critical * widening
    upper_differences = forecast_differences + critical * widening
    lower = _integrate_forecasts(values, lower_differences, d)
    upper = _integrate_forecasts(values, upper_differences, d)

    # Fitted values on the original scale: invert differencing for the training part.
    errors_padded = np.concatenate([np.zeros(len(differenced) - len(errors)), errors])
    fitted_differenced = differenced - errors_padded
    fitted_levels = _integrate_forecasts(values, fitted_differenced, d)
    fitted_original = np.concatenate(
        [np.full(n - len(fitted_levels), np.nan), fitted_levels]
    ) if len(fitted_levels) < n else fitted_levels[-n:]

    holdout_metrics: dict[str, Any] = {}
    diagnostics = _standard_diagnostics(series)
    if backtest and backtest < n - max(8, p + d + q + 4):
        train = values[:-backtest]
        test = values[-backtest:]
        try:
            train_fit = _fit_arma(_difference(train, d), p, q, constant)
            train_forecast = _integrate_forecasts(
                train,
                _forecast_arma(
                    train_fit["parameters"],
                    _difference(train, d),
                    train_fit["errors"],
                    p=p,
                    q=q,
                    constant=constant,
                    horizon=backtest,
                ),
                d,
            )
            absolute = np.abs(test - train_forecast)
            holdout_metrics = {
                "backtest_size": backtest,
                "backtest_mae": num(float(absolute.mean())),
                "backtest_rmse": num(
                    float(np.sqrt(np.mean((test - train_forecast) ** 2)))
                ),
                "backtest_mape": num(
                    float(np.mean(absolute / np.maximum(np.abs(test), 1e-12)) * 100.0)
                ),
            }
        except AnalysisError as exc:
            diagnostics.append(
                Diagnostic.info(
                    "backtest_skipped",
                    f"The hold-out backtest could not be computed: {exc}",
                )
            )
    elif backtest:
        diagnostics.append(
            Diagnostic.info(
                "backtest_skipped",
                "The requested hold-out is too large for this series.",
                requested=backtest,
            )
        )

    if not fitted["converged"]:
        diagnostics.append(
            Diagnostic.warning(
                "not_converged",
                "The conditional-sum-of-squares optimiser did not converge; "
                "coefficients and forecasts may be unreliable.",
            )
        )
    residual_noise = errors - errors.mean()
    denominator = float(residual_noise @ residual_noise)
    if denominator > 0 and len(errors) > 12:
        acf_values = _acf(errors, min(10, len(errors) // 4))
        lag = len(acf_values) - 1
        statistic, pvalue = _ljung_box(errors, acf_values, lag)
        if pvalue < 0.05:
            diagnostics.append(
                Diagnostic.warning(
                    "residual_autocorrelation",
                    "Residuals still show autocorrelation "
                    f"(Ljung-Box p={p_text(pvalue)}); consider a different order.",
                    statistic=num(statistic),
                    p_value=p_value(pvalue),
                    max_lag=lag,
                )
            )

    term_names = (["constant"] if constant else []) + [
        f"ar{index + 1}" for index in range(p)
    ] + [f"ma{index + 1}" for index in range(q)]
    coefficient_rows = tuple(
        (
            term_names[index],
            num(float(parameters[index])),
            num(float(fitted["standard_errors"][index])),
            num(float(fitted["z_values"][index])),
            p_value(float(fitted["p_values"][index])),
            p_text(float(fitted["p_values"][index])),
        )
        for index in range(len(term_names))
    )
    future = _future_labels(series, horizon)
    forecast_rows = tuple(
        (
            index + 1,
            future[index],
            num(float(forecast[index])),
            num(float(lower[index])),
            num(float(upper[index])),
        )
        for index in range(horizon)
    )
    fit_rows = (
        ("Order", f"ARIMA({p},{d},{q})"),
        ("Trend", trend),
        ("Observations", n),
        ("Differenced observations", int(differenced.size)),
        ("Effective observations", int(fitted["effective"])),
        ("Residual variance", num(fitted["sigma2"])),
        ("Log likelihood", num(fitted["log_likelihood"])),
        ("AIC", num(fitted["aic"])),
        ("BIC", num(fitted["bic"])),
        ("Iterations", int(fitted["iterations"])),
        ("Converged", bool(fitted["converged"])),
    )
    if holdout_metrics:
        fit_rows += (
            ("Backtest MAE", holdout_metrics["backtest_mae"]),
            ("Backtest RMSE", holdout_metrics["backtest_rmse"]),
            ("Backtest MAPE (%)", holdout_metrics["backtest_mape"]),
        )

    metrics = {
        "order": f"{p},{d},{q}",
        "p": p,
        "d": d,
        "q": q,
        "trend": trend,
        "n": n,
        "horizon": horizon,
        "aic": num(fitted["aic"]),
        "bic": num(fitted["bic"]),
        "sigma2": num(fitted["sigma2"]),
        "log_likelihood": num(fitted["log_likelihood"]),
        "converged": bool(fitted["converged"]),
        "iterations": int(fitted["iterations"]),
        "confidence": confidence,
        **holdout_metrics,
    }
    aic_text = "n/a" if fitted["aic"] is None else f"{fitted['aic']:.3f}"
    bic_text = "n/a" if fitted["bic"] is None else f"{fitted['bic']:.3f}"
    narrative = (
        f"ARIMA({p},{d},{q}) was fitted to {n:,} observation(s) by conditional sum of "
        f"squares and produced a {horizon}-step forecast. AIC is "
        f"{aic_text} and BIC is {bic_text}."
    )
    if holdout_metrics:
        narrative += (
            f" The {backtest}-step hold-out backtest has RMSE "
            f"{holdout_metrics['backtest_rmse']:.4g} and MAPE "
            f"{holdout_metrics['backtest_mape']:.2f}%."
        )

    chart_records: list[dict[str, Any]] = []
    for name, component in (("actual", values), ("fitted", fitted_original)):
        for period_label, value in zip(series.time_labels, component, strict=True):
            if math.isfinite(float(value)):
                chart_records.append(
                    {"period": period_label, "series": name, "value": num(float(value))}
                )
    for index in range(horizon):
        chart_records.append(
            {"period": future[index], "series": "forecast", "value": num(float(forecast[index]))}
        )
    return result(
        context,
        "arima_forecast",
        f"ARIMA forecast · {value_column}",
        params,
        tables=(
            ResultTable(
                title="Coefficients",
                columns=("term", "estimate", "std_error", "z", "p_value", "p_value_text"),
                rows=coefficient_rows,
                note=(
                    "Standard errors are Gauss-Newton approximations from the "
                    "conditional-sum-of-squares residuals."
                ),
            ),
            ResultTable(
                title="Forecast",
                columns=("step", "period", "forecast", "ci_low", "ci_high"),
                rows=forecast_rows,
                note=(
                    "Intervals are approximate and widen with the forecast horizon."
                ),
            ),
            ResultTable(
                title="Model fit",
                columns=("metric", "value"),
                rows=fit_rows,
            ),
        ),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
        charts=(
            _line_chart(
                f"{value_column}: actual, fitted and forecast",
                chart_records,
                x="period",
                y="value",
                color="series",
            ),
        ),
    )


# -- registry --------------------------------------------------------------

ANALYSES: tuple[AnalysisDefinition, ...] = (
    AnalysisDefinition(
        name="time_series_summary",
        title="Time-series summary",
        category="time_series",
        description=(
            "Profile a time series: inferred frequency, coverage, gaps, summary "
            "statistics, linear trend and a chart."
        ),
        run=time_series_summary,
        params=(
            StepParam("time", "column", description="Time or date column."),
            StepParam("value", "column", description="Numeric value column."),
        ),
    ),
    AnalysisDefinition(
        name="acf_pacf",
        title="Autocorrelation (ACF/PACF)",
        category="time_series",
        description=(
            "Autocorrelation and partial autocorrelation functions with a Ljung-Box "
            "test for each lag."
        ),
        run=acf_pacf,
        params=(
            StepParam("time", "column", description="Time or date column."),
            StepParam("value", "column", description="Numeric value column."),
            StepParam(
                "max_lag",
                "integer",
                required=False,
                description="Maximum lag (defaults to 20 or a quarter of the series).",
            ),
        ),
    ),
    AnalysisDefinition(
        name="seasonal_decompose",
        title="Seasonal decomposition",
        category="time_series",
        description=(
            "Classic additive or multiplicative decomposition into trend, seasonal and "
            "residual components with strength metrics."
        ),
        run=seasonal_decompose,
        params=(
            StepParam("time", "column", description="Time or date column."),
            StepParam("value", "column", description="Numeric value column."),
            StepParam(
                "period",
                "integer",
                required=False,
                description="Season length (auto-detected from the frequency when omitted).",
            ),
            StepParam(
                "model",
                "enum",
                required=False,
                default="additive",
                choices=DECOMPOSITION_MODELS,
                description="Additive or multiplicative decomposition.",
            ),
        ),
    ),
    AnalysisDefinition(
        name="trend_test",
        title="Mann-Kendall trend",
        category="time_series",
        description=(
            "Non-parametric monotonic trend test with Sen's slope, tie correction and "
            "an observed-versus-trend chart."
        ),
        run=trend_test,
        params=(
            StepParam("time", "column", description="Time or date column."),
            StepParam("value", "column", description="Numeric value column."),
            StepParam(
                "alpha",
                "number",
                required=False,
                default=0.05,
                description="Significance threshold (0-1).",
            ),
        ),
    ),
    AnalysisDefinition(
        name="arima_forecast",
        title="ARIMA forecast",
        category="forecasting",
        description=(
            "Fit ARIMA(p,d,q) by conditional sum of squares, forecast with widening "
            "intervals and optionally score a hold-out backtest."
        ),
        run=arima_forecast,
        params=(
            StepParam("time", "column", description="Time or date column."),
            StepParam("value", "column", description="Numeric value column."),
            StepParam(
                "p",
                "integer",
                required=False,
                default=1,
                description="Autoregressive order.",
            ),
            StepParam(
                "d",
                "integer",
                required=False,
                default=0,
                description="Differencing order.",
            ),
            StepParam(
                "q",
                "integer",
                required=False,
                default=0,
                description="Moving-average order.",
            ),
            StepParam(
                "horizon",
                "integer",
                required=False,
                default=12,
                description="Number of future steps to forecast.",
            ),
            StepParam(
                "backtest",
                "integer",
                required=False,
                default=0,
                description="Hold-out observations for a backtest (0 disables it).",
            ),
            StepParam(
                "trend",
                "enum",
                required=False,
                default="constant",
                choices=ARIMA_TRENDS,
                description="Include a constant when the series is not differenced.",
            ),
            StepParam(
                "confidence",
                "number",
                required=False,
                default=0.95,
                description="Confidence level for forecast intervals (0-1).",
            ),
        ),
    ),
)


__all__ = [
    "ANALYSES",
    "acf_pacf",
    "arima_forecast",
    "seasonal_decompose",
    "time_series_summary",
    "trend_test",
]
