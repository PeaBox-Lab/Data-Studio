"""Inferential analyses: hypothesis tests, regression and pairwise comparisons.

Every function is a pure kernel analysis over an `AnalysisContext`. Results are typed,
reference-tested and render identically in the API, the CLI and the workspace. Numerical
work uses NumPy and SciPy only; the statistics are computed here so that coefficients,
standard errors, confidence intervals, diagnostics and effect sizes are all explicit.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from scipy.special import expit

from ..engine.duckdb_engine import DuckDBTable
from ..kernel.results import AnalysisResult, Diagnostic, ResultTable
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
    sort_key,
    unique_labels,
)
from .registry import AnalysisContext, AnalysisDefinition, AnalysisError

ALTERNATIVES = ("two-sided", "less", "greater")
CORRECTIONS = ("none", "bonferroni", "holm", "fdr_bh")
MAX_NORMALITY_SAMPLE = 5000


# -- data access -----------------------------------------------------------


def _require_numeric(table: DuckDBTable, name: str, label_: str = "column") -> str:
    require_columns(table, (name,), label=label_)
    if table.schema.column(name).dtype not in NUMERIC_TYPES:
        raise AnalysisError(f"{name!r} must be a numeric {label_}.")
    return name


def _load(
    table: DuckDBTable,
    columns: Sequence[str],
    *,
    numeric: Sequence[str] = (),
) -> pd.DataFrame:
    """Load selected columns, casting the numeric ones to DOUBLE."""

    numeric_set = set(numeric)
    selected = ", ".join(
        (
            f"CAST({q(name)} AS DOUBLE) AS {q(name)}"
            if name in numeric_set
            else f"{q(name)} AS {q(name)}"
        )
        for name in columns
    )
    rows = table.engine.query(f"SELECT {selected} FROM {wrap(table)} AS _inference")
    return pd.DataFrame(rows, columns=list(columns))


def _numeric_series(table: DuckDBTable, name: str) -> np.ndarray:
    frame = _load(table, (name,), numeric=(name,))
    values = pd.to_numeric(frame[name], errors="coerce").dropna()
    return values.to_numpy(dtype=float)


def _complete_numeric(
    table: DuckDBTable, columns: Sequence[str]
) -> pd.DataFrame:
    frame = _load(table, columns, numeric=columns)
    for name in columns:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    return frame.dropna(subset=list(columns))


def _grouped_samples(
    table: DuckDBTable,
    value: str,
    group: str,
) -> tuple[list[str], list[np.ndarray]]:
    frame = _load(table, (value, group), numeric=(value,))
    frame[value] = pd.to_numeric(frame[value], errors="coerce")
    frame = frame.dropna(subset=[value, group])
    if frame.empty:
        raise AnalysisError("No complete rows remain after removing missing values.")
    raw_groups = sorted(frame[group].unique(), key=sort_key)
    labels = unique_labels(raw_groups)
    samples = [
        frame.loc[frame[group] == raw, value].to_numpy(dtype=float)
        for raw in raw_groups
    ]
    return labels, samples


# -- statistical helpers ---------------------------------------------------


def _confidence(params: Mapping[str, Any]) -> float:
    value = params.get("confidence")
    confidence = 0.95 if value is None else float(value)
    if not 0.0 < confidence < 1.0:
        raise AnalysisError("Confidence must be strictly between 0 and 1.")
    return confidence


def _alternative(params: Mapping[str, Any]) -> str:
    alternative = str(params.get("alternative") or "two-sided")
    if alternative not in ALTERNATIVES:
        raise AnalysisError(
            "Alternative must be one of: " + ", ".join(ALTERNATIVES) + "."
        )
    return alternative


def _t_critical(confidence: float, df: float) -> float:
    return float(stats.t.ppf(0.5 + confidence / 2.0, df))


def _normal_critical(confidence: float) -> float:
    return float(stats.norm.ppf(0.5 + confidence / 2.0))


def _t_pvalue(statistic: float, df: float, alternative: str) -> float:
    if alternative == "less":
        return float(stats.t.cdf(statistic, df))
    if alternative == "greater":
        return float(stats.t.sf(statistic, df))
    return float(2.0 * stats.t.sf(abs(statistic), df))


def _z_pvalue(statistic: float, alternative: str) -> float:
    if alternative == "less":
        return float(stats.norm.cdf(statistic))
    if alternative == "greater":
        return float(stats.norm.sf(statistic))
    return float(2.0 * stats.norm.sf(abs(statistic)))


def _hedges(coefficient: float | None, n1: int, n2: int | None = None) -> float | None:
    if coefficient is None or not math.isfinite(coefficient):
        return None
    total = n1 + (n2 or 0)
    denominator = 4 * total - 9
    if denominator <= 0:
        return None
    return coefficient * (1.0 - 3.0 / denominator)


def _sample_summary(values: np.ndarray) -> tuple[int, float, float, float]:
    n = int(values.size)
    mean = float(values.mean())
    std = float(values.std(ddof=1)) if n > 1 else 0.0
    se = std / math.sqrt(n) if n > 0 else 0.0
    return n, mean, std, se


def _two_sample(
    first: np.ndarray,
    second: np.ndarray,
    *,
    equal_var: bool,
    confidence: float,
    alternative: str,
) -> dict[str, Any]:
    n1, m1, s1, _ = _sample_summary(first)
    n2, m2, s2, _ = _sample_summary(second)
    if n1 < 2 or n2 < 2:
        raise AnalysisError("Each group needs at least two observations.")
    v1 = s1 * s1
    v2 = s2 * s2
    difference = m1 - m2
    if equal_var:
        df = float(n1 + n2 - 2)
        pooled = ((n1 - 1) * v1 + (n2 - 1) * v2) / df
        se = math.sqrt(pooled * (1.0 / n1 + 1.0 / n2))
        pooled_sd = math.sqrt(pooled)
        method = "Student t-test (pooled variance)"
    else:
        df = (v1 / n1 + v2 / n2) ** 2 / (
            (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
        )
        se = math.sqrt(v1 / n1 + v2 / n2)
        pooled_sd = math.sqrt(
            ((n1 - 1) * v1 + (n2 - 1) * v2) / max(1, n1 + n2 - 2)
        )
        method = "Welch t-test (unequal variance)"
    statistic = difference / se if se > 0 else 0.0
    pvalue = _t_pvalue(statistic, df, alternative)
    critical = _t_critical(confidence, df)
    d = difference / pooled_sd if pooled_sd > 0 else None
    return {
        "n1": n1,
        "n2": n2,
        "mean1": m1,
        "mean2": m2,
        "std1": s1,
        "std2": s2,
        "difference": difference,
        "se": se,
        "df": df,
        "statistic": statistic,
        "pvalue": pvalue,
        "ci_low": difference - critical * se,
        "ci_high": difference + critical * se,
        "cohens_d": d,
        "hedges_g": _hedges(d, n1, n2),
        "method": method,
    }


def _adjust(pvalues: Sequence[float], method: str) -> list[float]:
    count = len(pvalues)
    if count == 0:
        return []
    if method == "none":
        return [min(1.0, max(0.0, float(value))) for value in pvalues]
    if method == "bonferroni":
        return [min(1.0, max(0.0, float(value) * count)) for value in pvalues]
    if method == "holm":
        order = sorted(range(count), key=lambda index: pvalues[index])
        adjusted = [0.0] * count
        running = 0.0
        for rank, index in enumerate(order, start=1):
            candidate = min(1.0, pvalues[index] * (count - rank + 1))
            running = max(running, candidate)
            adjusted[index] = running
        return adjusted
    if method == "fdr_bh":
        order = sorted(range(count), key=lambda index: pvalues[index], reverse=True)
        adjusted = [0.0] * count
        running = 1.0
        for rank in range(count, 0, -1):
            index = order[count - rank]
            candidate = min(1.0, pvalues[index] * count / rank)
            running = min(running, candidate)
            adjusted[index] = running
        return adjusted
    raise AnalysisError("Unknown multiple-comparison correction: " + method + ".")


def _diagnose_normality(
    values: np.ndarray, name: str
) -> Diagnostic | None:
    n = int(values.size)
    if n < 3:
        return Diagnostic.info(
            "normality_not_tested",
            f"{name} has fewer than three observations; normality was not tested.",
        )
    if n > MAX_NORMALITY_SAMPLE:
        return Diagnostic.info(
            "normality_skipped_large_sample",
            f"{name} has {n:,} observations; the Shapiro-Wilk test was skipped.",
        )
    statistic, pvalue = stats.shapiro(values)
    if pvalue < 0.05:
        return Diagnostic.warning(
            "non_normal",
            f"{name} departs from normality (Shapiro-Wilk p={p_text(pvalue)}).",
            statistic=num(float(statistic), 6),
            p_value=p_value(float(pvalue)),
        )
    return None


def _diagnose_equal_variance(samples: Sequence[np.ndarray]) -> Diagnostic | None:
    if len(samples) < 2 or any(sample.size < 2 for sample in samples):
        return None
    statistic, pvalue = stats.levene(*samples)
    if pvalue < 0.05:
        return Diagnostic.warning(
            "unequal_variance",
            "Group variances differ significantly "
            f"(Levene p={p_text(pvalue)}); Welch's correction is advisable.",
            statistic=num(float(statistic), 6),
            p_value=p_value(float(pvalue)),
        )
    return None


def _p_row(pvalue: float | None) -> tuple[float | None, str]:
    return p_value(pvalue), p_text(pvalue)


# -- t tests ---------------------------------------------------------------


def t_test(context: AnalysisContext, params: Mapping[str, Any]) -> AnalysisResult:
    table = context.table
    kind = str(params.get("test") or "one_sample")
    if kind not in {"one_sample", "two_sample", "paired"}:
        raise AnalysisError("Test must be one_sample, two_sample or paired.")
    column = _require_numeric(table, params["column"])
    confidence = _confidence(params)
    alternative = _alternative(params)
    diagnostics: list[Diagnostic] = []

    summary_rows: list[tuple[Any, ...]] = []
    if kind == "one_sample":
        values = _numeric_series(table, column)
        n, mean, std, se = _sample_summary(values)
        if n < 2:
            raise AnalysisError("A one-sample t-test needs at least two observations.")
        sample_size = n
        mu = float(params.get("mu") or 0.0)
        difference = mean - mu
        df = float(n - 1)
        statistic = difference / se if se > 0 else 0.0
        pvalue = _t_pvalue(statistic, df, alternative)
        critical = _t_critical(confidence, df)
        d = difference / std if std > 0 else None
        hedges = _hedges(d, n)
        method = "One-sample t-test"
        summary_rows.append(("sample", n, num(mean), num(std), num(se)))
        cohens_d, hedges_g = d, hedges
        ci_low, ci_high = mean - critical * se, mean + critical * se
        extra = {"null_value": mu}
        normality = _diagnose_normality(values, repr(column))
        if normality is not None:
            diagnostics.append(normality)
        if n < 30:
            diagnostics.append(
                Diagnostic.info(
                    "small_sample",
                    f"Only {n} observation(s); interpret the test cautiously.",
                    n=n,
                )
            )
    elif kind == "paired":
        other = params.get("other")
        if not other:
            raise AnalysisError("A paired t-test needs a second column.")
        other = _require_numeric(table, other)
        if other == column:
            raise AnalysisError("Paired columns must be different.")
        frame = _complete_numeric(table, (column, other))
        if len(frame) < 2:
            raise AnalysisError("A paired t-test needs at least two complete pairs.")
        first = frame[column].to_numpy(dtype=float)
        second = frame[other].to_numpy(dtype=float)
        differences = first - second
        n, mean, std, se = _sample_summary(differences)
        sample_size = n
        difference = mean
        df = float(n - 1)
        statistic = mean / se if se > 0 else 0.0
        pvalue = _t_pvalue(statistic, df, alternative)
        critical = _t_critical(confidence, df)
        d = mean / std if std > 0 else None
        hedges_g = _hedges(d, n)
        cohens_d = d
        ci_low, ci_high = mean - critical * se, mean + critical * se
        method = "Paired t-test"
        first_se = float(first.std(ddof=1)) / math.sqrt(n)
        second_se = float(second.std(ddof=1)) / math.sqrt(n)
        summary_rows.append(
            (
                column,
                n,
                num(float(first.mean())),
                num(float(first.std(ddof=1))),
                num(first_se),
            )
        )
        summary_rows.append(
            (
                other,
                n,
                num(float(second.mean())),
                num(float(second.std(ddof=1))),
                num(second_se),
            )
        )
        summary_rows.append(
            ("difference", n, num(mean), num(std), num(se))
        )
        extra = {"other": other, "mean_difference": mean, "sd_difference": std}
        normality = _diagnose_normality(differences, "the paired differences")
        if normality is not None:
            diagnostics.append(normality)
        if n < 30:
            diagnostics.append(
                Diagnostic.info(
                    "small_sample",
                    f"Only {n} pair(s); interpret the test cautiously.",
                    n=n,
                )
            )
    else:
        other = params.get("other")
        group = params.get("group")
        if other:
            other = _require_numeric(table, other)
            if other == column:
                raise AnalysisError("Two-sample columns must be different.")
            frame = _complete_numeric(table, (column, other))
            first = frame[column].to_numpy(dtype=float)
            second = frame[other].to_numpy(dtype=float)
            first_label, second_label = column, other
        elif group:
            require_columns(table, (group,))
            labels, samples = _grouped_samples(table, column, group)
            if len(samples) != 2:
                raise AnalysisError(
                    "The grouping column must contain exactly two non-missing groups."
                )
            first, second = samples
            first_label, second_label = labels
        else:
            raise AnalysisError(
                "A two-sample t-test needs either a second column or a grouping column."
            )
        equal_var = bool(params.get("equal_var", False))
        calculated = _two_sample(
            first,
            second,
            equal_var=equal_var,
            confidence=confidence,
            alternative=alternative,
        )
        n1, n2 = calculated["n1"], calculated["n2"]
        sample_size = n1 + n2
        summary_rows.append(
            (
                first_label,
                n1,
                num(calculated["mean1"]),
                num(calculated["std1"]),
                num(calculated["std1"] / math.sqrt(n1)),
            )
        )
        summary_rows.append(
            (
                second_label,
                n2,
                num(calculated["mean2"]),
                num(calculated["std2"]),
                num(calculated["std2"] / math.sqrt(n2)),
            )
        )
        statistic = calculated["statistic"]
        df = calculated["df"]
        pvalue = calculated["pvalue"]
        difference = calculated["difference"]
        ci_low, ci_high = calculated["ci_low"], calculated["ci_high"]
        method = calculated["method"]
        cohens_d = calculated["cohens_d"]
        hedges_g = calculated["hedges_g"]
        extra = {
            "second": other or group,
            "mean_difference": difference,
            "standard_error": calculated["se"],
            "equal_var": equal_var,
        }
        variance = _diagnose_equal_variance((first, second))
        if variance is not None:
            diagnostics.append(variance)
        if not equal_var and calculated["se"] <= 0:
            diagnostics.append(
                Diagnostic.warning(
                    "zero_variance",
                    "Both groups have zero variance; the test is undefined.",
                )
            )
        if min(n1, n2) < 30:
            diagnostics.append(
                Diagnostic.info(
                    "small_sample",
                    f"Smallest group has {min(n1, n2)} observation(s); interpret cautiously.",
                    n=min(n1, n2),
                )
            )

    summary = ResultTable(
        title="Group summary",
        columns=("group", "n", "mean", "std", "se"),
        rows=tuple(summary_rows),
        note="Standard deviation uses the sample (n-1) denominator.",
    )
    raw_p, readable_p = _p_row(pvalue)
    test_table = ResultTable(
        title="Test result",
        columns=(
            "method",
            "statistic",
            "df",
            "p_value",
            "p_value_text",
            "mean_difference",
            "ci_low",
            "ci_high",
            "cohens_d",
            "hedges_g",
        ),
        rows=(
            (
                method,
                num(statistic),
                num(df),
                raw_p,
                readable_p,
                num(difference),
                num(ci_low),
                num(ci_high),
                num(cohens_d),
                num(hedges_g),
            ),
        ),
        note=f"{round(confidence * 100)}% confidence intervals are two-sided.",
    )
    metrics: dict[str, Any] = {
        "test": kind,
        "method": method,
        "n": sample_size,
        "statistic": num(statistic),
        "df": num(df),
        "p_value": raw_p,
        "p_value_text": readable_p,
        "mean_difference": num(difference),
        "ci_low": num(ci_low),
        "ci_high": num(ci_high),
        "cohens_d": num(cohens_d),
        "hedges_g": num(hedges_g),
        "confidence": confidence,
    }
    metrics.update(extra)
    significant = raw_p is not None and raw_p < 0.05
    narrative = (
        f"{method} compared the mean response; t={statistic:.3f}, df={df:.2f}, "
        f"p={readable_p}. The difference is "
        + ("statistically significant" if significant else "not statistically significant")
        + f" at the 5% level, with a {round(confidence * 100)}% interval of "
        f"[{ci_low:.3f}, {ci_high:.3f}]."
    )
    return result(
        context,
        "t_test",
        f"t-test · {column}",
        params,
        tables=(summary, test_table),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
    )


# -- ANOVA -----------------------------------------------------------------


def anova(context: AnalysisContext, params: Mapping[str, Any]) -> AnalysisResult:
    table = context.table
    value = _require_numeric(table, params["value"])
    group = params["group"]
    require_columns(table, (group,))
    if group == value:
        raise AnalysisError("The value and grouping columns must be different.")
    confidence = _confidence(params)
    posthoc = bool(params.get("posthoc", True))
    labels, samples = _grouped_samples(table, value, group)
    if len(samples) < 2:
        raise AnalysisError("ANOVA needs at least two non-missing groups.")
    if any(sample.size < 2 for sample in samples):
        raise AnalysisError("Every group needs at least two observations.")

    result_test = stats.f_oneway(*samples)
    statistic = float(result_test.statistic)
    pvalue = float(result_test.pvalue)

    all_values = np.concatenate(samples)
    grand_mean = float(all_values.mean())
    ss_between = sum(
        sample.size * (float(sample.mean()) - grand_mean) ** 2 for sample in samples
    )
    ss_within = sum(
        float(((sample - sample.mean()) ** 2).sum()) for sample in samples
    )
    ss_total = ss_between + ss_within
    df_between = len(samples) - 1
    df_within = int(all_values.size - len(samples))
    ms_between = ss_between / df_between if df_between else None
    ms_within = ss_within / df_within if df_within else None
    eta_squared = ss_between / ss_total if ss_total > 0 else None
    omega_squared = (
        (ss_between - df_between * ms_within) / (ss_total + ms_within)
        if ms_within is not None and ss_total + ms_within > 0
        else None
    )

    summary_rows = [
        (
            name,
            int(sample.size),
            num(float(sample.mean())),
            num(float(sample.std(ddof=1))),
        )
        for name, sample in zip(labels, samples, strict=True)
    ]
    anova_rows: list[tuple[Any, ...]] = [
        (
            "Between groups",
            num(ss_between),
            df_between,
            num(ms_between),
            num(statistic),
            p_value(pvalue),
        ),
        (
            "Within groups",
            num(ss_within),
            df_within,
            num(ms_within),
            None,
            None,
        ),
        ("Total", num(ss_total), df_between + df_within, None, None, None),
    ]

    tables: list[ResultTable] = [
        ResultTable(
            title="Group summary",
            columns=("group", "n", "mean", "std"),
            rows=tuple(summary_rows),
        ),
        ResultTable(
            title="ANOVA",
            columns=("source", "sum_squares", "df", "mean_square", "F", "p_value"),
            rows=tuple(anova_rows),
            note="One-way analysis of variance. p-value uses the F distribution.",
        ),
    ]

    diagnostics: list[Diagnostic] = []
    variance = _diagnose_equal_variance(samples)
    if variance is not None:
        diagnostics.append(variance)
    for name, sample in zip(labels, samples, strict=True):
        normality = _diagnose_normality(sample, repr(name))
        if normality is not None:
            diagnostics.append(normality)

    if posthoc and len(samples) >= 2:
        try:
            tukey = stats.tukey_hsd(*samples)
            interval = tukey.confidence_interval(confidence_level=confidence)
            rows: list[tuple[Any, ...]] = []
            for first in range(len(samples)):
                for second in range(first + 1, len(samples)):
                    rows.append(
                        (
                            f"{labels[first]} vs {labels[second]}",
                            num(float(samples[first].mean() - samples[second].mean())),
                            num(float(tukey.statistic[first, second])),
                            p_value(float(tukey.pvalue[first, second])),
                            num(float(interval.low[first, second])),
                            num(float(interval.high[first, second])),
                        )
                    )
            tables.append(
                ResultTable(
                    title="Tukey HSD pairwise comparisons",
                    columns=(
                        "comparison",
                        "mean_difference",
                        "statistic",
                        "p_value",
                        "ci_low",
                        "ci_high",
                    ),
                    rows=tuple(rows),
                    note=(
                        "Tukey p-values already control the family-wise error rate. "
                        "Differences are first group minus second group."
                    ),
                )
            )
        except Exception as exc:  # pragma: no cover - defensive against SciPy limits
            diagnostics.append(
                Diagnostic.warning(
                    "posthoc_unavailable",
                    f"Tukey HSD could not be computed: {exc}",
                )
            )

    raw_p, readable_p = _p_row(pvalue)
    metrics = {
        "F": num(statistic),
        "p_value": raw_p,
        "p_value_text": readable_p,
        "df_between": df_between,
        "df_within": df_within,
        "eta_squared": num(eta_squared),
        "omega_squared": num(omega_squared),
        "groups": len(samples),
        "n": int(all_values.size),
        "confidence": confidence,
    }
    significant = raw_p is not None and raw_p < 0.05
    narrative = (
        f"One-way ANOVA compared {len(samples)} groups over {all_values.size:,} "
        f"observation(s): F({df_between}, {df_within})={statistic:.3f}, "
        f"p={readable_p}. The group means are "
        + ("significantly different" if significant else "not significantly different")
        + f"; eta-squared is {eta_squared:.3f}."
        if eta_squared is not None
        else f"One-way ANOVA: F={statistic:.3f}, p={readable_p}."
    )
    return result(
        context,
        "anova",
        f"One-way ANOVA · {value} by {group}",
        params,
        tables=tuple(tables),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
    )


# -- chi-square ------------------------------------------------------------


def chi_square(context: AnalysisContext, params: Mapping[str, Any]) -> AnalysisResult:
    table = context.table
    row_name = params["row"]
    column_name = params["column"]
    if row_name == column_name:
        raise AnalysisError("Chi-square rows and columns must be different.")
    require_columns(table, (row_name, column_name))
    correction = bool(params.get("correction", False))

    frame = _load(table, (row_name, column_name)).dropna()
    if frame.empty:
        raise AnalysisError("No complete row/column pairs remain after removing missing values.")
    row_values = sorted(frame[row_name].unique(), key=sort_key)
    column_values = sorted(frame[column_name].unique(), key=sort_key)
    if len(row_values) < 2 or len(column_values) < 2:
        raise AnalysisError("Chi-square needs at least two rows and two columns.")
    observed = (
        pd.crosstab(frame[row_name], frame[column_name])
        .reindex(index=row_values, columns=column_values, fill_value=0)
        .to_numpy(dtype=float)
    )
    statistic, pvalue, dof, expected = stats.chi2_contingency(
        observed, correction=correction
    )
    total = float(observed.sum())
    minimum_dimension = min(observed.shape) - 1
    cramers_v = (
        math.sqrt(statistic / (total * minimum_dimension))
        if total > 0 and minimum_dimension > 0
        else None
    )

    row_labels = unique_labels(row_values, reserved=(row_name, "Total"))
    column_labels = unique_labels(column_values, reserved=(row_name, "Total", *row_labels))
    observed_rows = [
        (
            row_labels[index],
            *[int(count) for count in observed[index]],
            int(observed[index].sum()),
        )
        for index in range(len(row_values))
    ]
    expected_rows = [
        (
            row_labels[index],
            *[num(value, 4) for value in expected[index]],
            num(float(expected[index].sum()), 4),
        )
        for index in range(len(row_values))
    ]

    diagnostics: list[Diagnostic] = []
    if float(expected.min()) < 5:
        diagnostics.append(
            Diagnostic.warning(
                "small_expected_counts",
                "At least one expected cell count is below 5; the chi-square "
                "approximation may be unreliable.",
                minimum_expected=num(float(expected.min()), 4),
            )
        )
    if correction and observed.shape == (2, 2):
        diagnostics.append(
            Diagnostic.info(
                "yates_correction",
                "Yates' continuity correction was applied to the 2x2 table.",
            )
        )
    elif not correction and observed.shape == (2, 2):
        diagnostics.append(
            Diagnostic.info(
                "no_yates_correction",
                "No continuity correction was applied to the 2x2 table.",
            )
        )

    raw_p, readable_p = _p_row(pvalue)
    metrics = {
        "chi_square": num(float(statistic)),
        "p_value": raw_p,
        "p_value_text": readable_p,
        "df": int(dof),
        "cramers_v": num(cramers_v),
        "n": int(total),
        "min_expected": num(float(expected.min()), 4),
        "rows": len(row_values),
        "columns": len(column_values),
        "correction": correction,
    }
    significant = raw_p is not None and raw_p < 0.05
    narrative = (
        f"Chi-square test of independence over {int(total):,} observations: "
        f"chi2({int(dof)})={statistic:.3f}, p={readable_p}, "
        f"Cramer's V={cramers_v:.3f}."
        if cramers_v is not None
        else f"Chi-square test: chi2={statistic:.3f}, p={readable_p}."
    )
    narrative += (
        " The row and column variables are significantly associated."
        if significant
        else " There is no significant association at the 5% level."
    )
    return result(
        context,
        "chi_square",
        f"Chi-square test · {row_name} × {column_name}",
        params,
        tables=(
            ResultTable(
                title="Observed counts",
                columns=(row_name, *column_labels, "Total"),
                rows=tuple(observed_rows),
            ),
            ResultTable(
                title="Expected counts",
                columns=(row_name, *column_labels, "Total"),
                rows=tuple(expected_rows),
                note="Expected counts under independence.",
            ),
        ),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
    )


# -- proportions -----------------------------------------------------------


def _is_success(value: Any, success_value: str | None) -> bool | None:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (float, np.floating)) and math.isnan(float(value)):
        return None
    if success_value not in (None, ""):
        return str(value) == success_value or label(value) == success_value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, np.integer)):
        return int(value) != 0
    if isinstance(value, (float, np.floating)):
        return bool(value) and not math.isnan(float(value))
    return str(value).strip().lower() not in {"", "0", "false", "no", "n"}


def proportion_test(
    context: AnalysisContext, params: Mapping[str, Any]
) -> AnalysisResult:
    table = context.table
    kind = str(params.get("test") or "one_sample")
    if kind not in {"one_sample", "two_sample"}:
        raise AnalysisError("Test must be one_sample or two_sample.")
    column = params["column"]
    require_columns(table, (column,))
    confidence = _confidence(params)
    alternative = _alternative(params)
    success_value = params.get("success_value")
    if success_value is not None:
        success_value = str(success_value)

    if kind == "one_sample":
        p0 = float(params.get("p0") if params.get("p0") is not None else 0.5)
        if not 0.0 < p0 < 1.0:
            raise AnalysisError("p0 must be strictly between 0 and 1.")
        frame = _load(table, (column,)).dropna(subset=[column])
        flags = [_is_success(value, success_value) for value in frame[column]]
        flags = [flag for flag in flags if flag is not None]
        n = len(flags)
        if n == 0:
            raise AnalysisError("No non-missing observations are available.")
        successes = int(sum(flags))
        proportion = successes / n
        se_estimate = math.sqrt(proportion * (1.0 - proportion) / n)
        statistic = (proportion - p0) / se_estimate if se_estimate > 0 else 0.0
        pvalue = _z_pvalue(statistic, alternative)
        critical = _normal_critical(confidence)
        ci_low = proportion - critical * se_estimate
        ci_high = proportion + critical * se_estimate
        effect = 2.0 * math.asin(math.sqrt(proportion)) - 2.0 * math.asin(
            math.sqrt(p0)
        )
        sample_rows = (
            (column, successes, n, num(proportion), num(ci_low), num(ci_high)),
        )
        test_row = (
            "One-sample proportion z-test",
            num(statistic),
            p_value(pvalue),
            p_text(pvalue),
            num(proportion - p0),
            num(ci_low),
            num(ci_high),
            num(effect),
        )
        extra = {"null_value": p0, "successes": successes, "n": n}
        diagnostics: list[Diagnostic] = []
        if n < 30:
            diagnostics.append(
                Diagnostic.info(
                    "small_sample",
                    f"Only {n} observation(s); the normal approximation may be weak.",
                    n=n,
                )
            )
        if successes < 5 or (n - successes) < 5:
            diagnostics.append(
                Diagnostic.warning(
                    "few_successes",
                    "Fewer than five successes or failures; consider an exact test.",
                    successes=successes,
                    failures=n - successes,
                )
            )
    else:
        group = params.get("group")
        if not group:
            raise AnalysisError("A two-sample proportion test needs a grouping column.")
        require_columns(table, (group,))
        if group == column:
            raise AnalysisError("The success and grouping columns must be different.")
        frame = _load(table, (column, group)).dropna(subset=[group, column])
        labels = unique_labels(sorted(frame[group].unique(), key=sort_key))
        raw_groups = sorted(frame[group].unique(), key=sort_key)
        if len(raw_groups) != 2:
            raise AnalysisError(
                "The grouping column must contain exactly two non-missing groups."
            )
        counts: list[int] = []
        totals: list[int] = []
        proportions: list[float] = []
        for raw in raw_groups:
            flags = [
                _is_success(value, success_value)
                for value in frame.loc[frame[group] == raw, column]
            ]
            flags = [flag for flag in flags if flag is not None]
            successes = int(sum(flags))
            total = len(flags)
            if total == 0:
                raise AnalysisError("A group has no non-missing observations.")
            counts.append(successes)
            totals.append(total)
            proportions.append(successes / total)
        difference = proportions[0] - proportions[1]
        pooled = (counts[0] + counts[1]) / (totals[0] + totals[1])
        se_null = math.sqrt(
            pooled * (1.0 - pooled) * (1.0 / totals[0] + 1.0 / totals[1])
        )
        statistic = difference / se_null if se_null > 0 else 0.0
        pvalue = _z_pvalue(statistic, alternative)
        critical = _normal_critical(confidence)
        se_estimate = math.sqrt(
            proportions[0] * (1.0 - proportions[0]) / totals[0]
            + proportions[1] * (1.0 - proportions[1]) / totals[1]
        )
        ci_low = difference - critical * se_estimate
        ci_high = difference + critical * se_estimate
        effect = 2.0 * math.asin(math.sqrt(proportions[0])) - 2.0 * math.asin(
            math.sqrt(proportions[1])
        )
        sample_rows = tuple(
            (
                name,
                counts[index],
                totals[index],
                num(proportions[index]),
                num(
                    proportions[index]
                    - critical
                    * math.sqrt(
                        proportions[index]
                        * (1.0 - proportions[index])
                        / totals[index]
                    )
                ),
                num(
                    proportions[index]
                    + critical
                    * math.sqrt(
                        proportions[index]
                        * (1.0 - proportions[index])
                        / totals[index]
                    )
                ),
            )
            for index, name in enumerate(labels)
        )
        test_row = (
            "Two-sample proportion z-test",
            num(statistic),
            p_value(pvalue),
            p_text(pvalue),
            num(difference),
            num(ci_low),
            num(ci_high),
            num(effect),
        )
        extra = {
            "n1": totals[0],
            "n2": totals[1],
            "successes1": counts[0],
            "successes2": counts[1],
        }
        diagnostics = []
        for index, total in enumerate(totals):
            if counts[index] < 5 or (total - counts[index]) < 5:
                diagnostics.append(
                    Diagnostic.warning(
                        "few_successes",
                        f"{labels[index]} has fewer than five successes or failures; "
                        "the normal approximation may be weak.",
                        group=labels[index],
                        successes=counts[index],
                        failures=total - counts[index],
                    )
                )

    raw_p, readable_p = _p_row(pvalue)
    samples = ResultTable(
        title="Sample proportions",
        columns=("sample", "successes", "n", "proportion", "ci_low", "ci_high"),
        rows=sample_rows,
        note="Intervals use the normal approximation to the binomial.",
    )
    test_table = ResultTable(
        title="Test result",
        columns=(
            "method",
            "statistic",
            "p_value",
            "p_value_text",
            "difference",
            "ci_low",
            "ci_high",
            "effect_size_h",
        ),
        rows=(test_row,),
    )
    metrics = {
        "test": kind,
        "statistic": num(statistic),
        "p_value": raw_p,
        "p_value_text": readable_p,
        "difference": num(difference) if kind == "two_sample" else num(proportion - p0),
        "ci_low": num(ci_low),
        "ci_high": num(ci_high),
        "effect_size_h": num(effect),
        "confidence": confidence,
    }
    metrics.update(extra)
    significant = raw_p is not None and raw_p < 0.05
    narrative = (
        f"{test_row[0]} gave z={statistic:.3f}, p={readable_p}. "
        + (
            "The proportion difference is significant"
            if significant
            else "No significant difference was detected"
        )
        + f" at the 5% level ({round(confidence * 100)}% interval "
        f"[{ci_low:.4f}, {ci_high:.4f}])."
    )
    return result(
        context,
        "proportion_test",
        f"Proportion test · {column}",
        params,
        tables=(samples, test_table),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
    )


# -- linear regression -----------------------------------------------------


def _vif(predictors: np.ndarray) -> list[float | None]:
    columns = predictors.shape[1]
    if columns == 1:
        return [1.0]
    values: list[float | None] = []
    for index in range(columns):
        target = predictors[:, index]
        others = np.delete(predictors, index, axis=1)
        design = np.column_stack([np.ones(len(target)), others])
        coefficient, _, _, _ = np.linalg.lstsq(design, target, rcond=None)
        residual = target - design @ coefficient
        total = float(((target - target.mean()) ** 2).sum())
        if total <= 0:
            values.append(None)
            continue
        r_squared = 1.0 - float(residual @ residual) / total
        if r_squared >= 1.0:
            values.append(float("inf"))
        else:
            values.append(1.0 / (1.0 - r_squared))
    return values


def linear_regression(
    context: AnalysisContext, params: Mapping[str, Any]
) -> AnalysisResult:
    table = context.table
    response = _require_numeric(table, params["response"], "response column")
    predictors = tuple(params.get("predictors") or ())
    if not predictors:
        raise AnalysisError("At least one predictor column is required.")
    require_columns(table, predictors, label="predictor")
    for name in predictors:
        if table.schema.column(name).dtype not in NUMERIC_TYPES:
            raise AnalysisError(f"Predictor {name!r} must be numeric.")
    if response in predictors:
        raise AnalysisError("The response cannot also be a predictor.")
    if len(set(predictors)) != len(predictors):
        raise AnalysisError("Predictor columns must be unique.")
    confidence = _confidence(params)
    intercept = bool(params.get("intercept", True))

    frame = _complete_numeric(table, (response, *predictors))
    n = len(frame)
    if n < 3:
        raise AnalysisError("Linear regression needs at least three complete rows.")
    y = frame[response].to_numpy(dtype=float)
    raw_predictors = frame[list(predictors)].to_numpy(dtype=float)
    design = (
        np.column_stack([np.ones(n), raw_predictors])
        if intercept
        else raw_predictors
    )
    terms = (["(Intercept)"] if intercept else []) + list(predictors)
    if n <= design.shape[1]:
        raise AnalysisError(
            "Not enough complete rows for the number of predictors. "
            f"Have {n} row(s) and {design.shape[1]} term(s)."
        )

    coefficients, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    rank = int(rank)
    fitted = design @ coefficients
    residuals = y - fitted
    total = float(((y - y.mean()) ** 2).sum())
    residual_sum = float(residuals @ residuals)
    ss_model = total - residual_sum
    df_resid = n - rank
    if df_resid <= 0:
        raise AnalysisError("Residual degrees of freedom are not positive.")
    df_model = rank - 1 if intercept else rank
    r_squared = 1.0 - residual_sum / total if total > 0 else None
    adjusted = (
        1.0 - (1.0 - r_squared) * (n - 1) / df_resid
        if r_squared is not None
        else None
    )
    sigma_squared = residual_sum / df_resid
    covariance = sigma_squared * np.linalg.pinv(design.T @ design)
    standard_errors = np.sqrt(np.clip(np.diag(covariance), 0.0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        t_values = np.divide(
            coefficients,
            standard_errors,
            out=np.full_like(coefficients, np.nan),
            where=standard_errors > 0,
        )
    p_values = 2.0 * stats.t.sf(np.abs(t_values), df_resid)
    critical = _t_critical(confidence, df_resid)
    ci_low = coefficients - critical * standard_errors
    ci_high = coefficients + critical * standard_errors
    statistic_f = (
        (ss_model / df_model) / sigma_squared
        if df_model > 0 and sigma_squared > 0
        else None
    )
    p_f = float(stats.f.sf(statistic_f, df_model, df_resid)) if statistic_f else None

    response_std = float(y.std(ddof=1))
    standardized: list[float | None] = []
    offset = 1 if intercept else 0
    for index in range(len(predictors)):
        coefficient = float(coefficients[offset + index])
        predictor_std = float(raw_predictors[:, index].std(ddof=1))
        if response_std > 0:
            standardized.append(coefficient * predictor_std / response_std)
        else:
            standardized.append(None)

    diagnostic_vif = _vif(raw_predictors)
    bp_lm = None
    bp_p = None
    if total > 0:
        squared = residuals**2
        bp_coefficients, _, _, _ = np.linalg.lstsq(design, squared, rcond=None)
        bp_fitted = design @ bp_coefficients
        bp_residual = squared - bp_fitted
        bp_total = float(((squared - squared.mean()) ** 2).sum())
        if bp_total > 0:
            bp_r2 = 1.0 - float(bp_residual @ bp_residual) / bp_total
            bp_lm = n * max(0.0, bp_r2)
            bp_p = float(stats.chi2.sf(bp_lm, max(1, design.shape[1] - 1)))
    denominator = float(residuals @ residuals)
    durbin_watson = (
        float(((np.diff(residuals) ** 2).sum()) / denominator)
        if denominator > 0
        else None
    )
    shapiro_statistic = None
    shapiro_p = None
    if 3 <= n <= MAX_NORMALITY_SAMPLE and denominator > 0:
        shapiro_statistic, shapiro_p = stats.shapiro(residuals)

    coefficient_rows = []
    for index, term in enumerate(terms):
        coefficient_rows.append(
            (
                term,
                num(float(coefficients[index])),
                num(float(standard_errors[index])),
                num(float(t_values[index])),
                p_value(float(p_values[index])),
                p_text(float(p_values[index])),
                num(float(ci_low[index])),
                num(float(ci_high[index])),
                num(standardized[index - offset]) if index >= offset else None,
            )
        )

    residual_se = math.sqrt(sigma_squared)
    rmse = math.sqrt(residual_sum / n)
    aic = (
        n * math.log(residual_sum / n) + 2 * rank if residual_sum > 0 else None
    )
    bic = (
        n * math.log(residual_sum / n) + rank * math.log(n)
        if residual_sum > 0
        else None
    )
    fit_rows = (
        ("Observations", n),
        ("Terms", len(terms)),
        ("R-squared", num(r_squared)),
        ("Adjusted R-squared", num(adjusted)),
        ("F statistic", num(statistic_f)),
        ("F p-value", p_value(p_f)),
        ("Residual standard error", num(residual_se)),
        ("RMSE", num(rmse)),
        ("Durbin-Watson", num(durbin_watson)),
        ("AIC", num(aic)),
        ("BIC", num(bic)),
    )

    diagnostics: list[Diagnostic] = []
    if rank < design.shape[1]:
        diagnostics.append(
            Diagnostic.warning(
                "rank_deficient",
                "The design matrix is rank deficient; some coefficients are not "
                "uniquely identified.",
                rank=rank,
                terms=design.shape[1],
            )
        )
    for name, value in zip(predictors, diagnostic_vif, strict=True):
        if value is not None and math.isfinite(value) and value > 10:
            diagnostics.append(
                Diagnostic.warning(
                    "multicollinearity",
                    f"{name!r} has a variance inflation factor of {value:.2f}.",
                    column=name,
                    vif=num(value),
                )
            )
        elif value is not None and math.isfinite(value) and value > 5:
            diagnostics.append(
                Diagnostic.info(
                    "moderate_multicollinearity",
                    f"{name!r} has a variance inflation factor of {value:.2f}; "
                    "coefficient estimates may be unstable.",
                    column=name,
                    vif=num(value),
                )
            )
    if bp_p is not None and bp_p < 0.05:
        diagnostics.append(
            Diagnostic.warning(
                "heteroscedasticity",
                "Residual variance may not be constant "
                f"(Breusch-Pagan p={p_text(bp_p)}).",
                statistic=num(bp_lm),
                p_value=p_value(bp_p),
            )
        )
    if shapiro_p is not None and shapiro_p < 0.05:
        diagnostics.append(
            Diagnostic.warning(
                "non_normal_residuals",
                "Residuals depart from normality "
                f"(Shapiro-Wilk p={p_text(shapiro_p)}).",
                statistic=num(float(shapiro_statistic)),
                p_value=p_value(shapiro_p),
            )
        )
    if durbin_watson is not None and not 1.5 <= durbin_watson <= 2.5:
        diagnostics.append(
            Diagnostic.info(
                "autocorrelation",
                f"Durbin-Watson is {durbin_watson:.2f}; residuals may be autocorrelated.",
                durbin_watson=num(durbin_watson),
            )
        )
    if n < 30:
        diagnostics.append(
            Diagnostic.info(
                "small_sample",
                f"Only {n} complete observation(s); estimates may be unstable.",
                n=n,
            )
        )

    vif_metric = None
    finite_vif = [
        value
        for value in diagnostic_vif
        if value is not None and math.isfinite(value)
    ]
    if finite_vif:
        vif_metric = max(finite_vif)
    metrics = {
        "response": response,
        "predictors": ", ".join(predictors),
        "n": n,
        "r_squared": num(r_squared),
        "adj_r_squared": num(adjusted),
        "f_statistic": num(statistic_f),
        "f_p_value": p_value(p_f),
        "f_p_value_text": p_text(p_f),
        "residual_se": num(residual_se),
        "rmse": num(rmse),
        "durbin_watson": num(durbin_watson),
        "breusch_pagan_p": p_value(bp_p),
        "max_vif": num(vif_metric),
        "aic": num(aic),
        "bic": num(bic),
        "confidence": confidence,
        "intercept": intercept,
    }
    narrative = (
        f"OLS regression of {response!r} on {len(predictors)} predictor(s) using "
        f"{n:,} complete observation(s): R-squared={r_squared:.3f}"
        if r_squared is not None
        else f"OLS regression of {response!r} on {len(predictors)} predictor(s)."
    )
    if adjusted is not None:
        narrative += f", adjusted R-squared={adjusted:.3f}"
    if statistic_f is not None:
        narrative += f", F={statistic_f:.3f} (p={p_text(p_f)})"
    narrative += "."
    return result(
        context,
        "linear_regression",
        f"Linear regression · {response}",
        params,
        tables=(
            ResultTable(
                title="Coefficients",
                columns=(
                    "term",
                    "estimate",
                    "std_error",
                    "t",
                    "p_value",
                    "p_value_text",
                    "ci_low",
                    "ci_high",
                    "standardized",
                ),
                rows=tuple(coefficient_rows),
                note=(
                    "Standard errors use the OLS covariance matrix. "
                    "Standardized coefficients use sample standard deviations."
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
    )


# -- logistic regression ---------------------------------------------------


def _auc(actual: np.ndarray, score: np.ndarray) -> float | None:
    positives = int(actual.sum())
    negatives = int(actual.size - positives)
    if positives == 0 or negatives == 0:
        return None
    ranks = stats.rankdata(np.round(score, 10))
    return float(
        (ranks[actual == 1].sum() - positives * (positives + 1) / 2.0)
        / (positives * negatives)
    )


def logistic_regression(
    context: AnalysisContext, params: Mapping[str, Any]
) -> AnalysisResult:
    table = context.table
    response = params["response"]
    require_columns(table, (response,), label="response column")
    predictors = tuple(params.get("predictors") or ())
    if not predictors:
        raise AnalysisError("At least one predictor column is required.")
    require_columns(table, predictors, label="predictor")
    for name in predictors:
        if table.schema.column(name).dtype not in NUMERIC_TYPES:
            raise AnalysisError(f"Predictor {name!r} must be numeric.")
    if response in predictors:
        raise AnalysisError("The response cannot also be a predictor.")
    if len(set(predictors)) != len(predictors):
        raise AnalysisError("Predictor columns must be unique.")
    confidence = _confidence(params)
    intercept = bool(params.get("intercept", True))
    max_iterations = max(10, min(int(params.get("max_iterations") or 100), 1000))

    frame = _load(table, (response, *predictors), numeric=predictors)
    for name in predictors:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.dropna()
    n = len(frame)
    if n < 3:
        raise AnalysisError("Logistic regression needs at least three complete rows.")

    raw_response = frame[response]
    encoded: list[float] = []
    for value in raw_response:
        if isinstance(value, (bool, np.bool_)):
            encoded.append(1.0 if bool(value) else 0.0)
        else:
            number = float(value)
            if number not in (0.0, 1.0):
                raise AnalysisError(
                    "The response must be binary: 0/1 or true/false. "
                    f"Found {value!r}."
                )
            encoded.append(number)
    y = np.asarray(encoded, dtype=float)
    if y.min() == y.max():
        raise AnalysisError("The response has only one class.")

    raw_predictors = frame[list(predictors)].to_numpy(dtype=float)
    design = (
        np.column_stack([np.ones(n), raw_predictors])
        if intercept
        else raw_predictors
    )
    terms = (["(Intercept)"] if intercept else []) + list(predictors)
    if n <= design.shape[1]:
        raise AnalysisError(
            "Not enough complete rows for the number of predictors. "
            f"Have {n} row(s) and {design.shape[1]} term(s)."
        )

    coefficients = np.zeros(design.shape[1], dtype=float)
    converged = False
    iterations = 0
    rank = design.shape[1]
    while iterations < max_iterations and not converged:
        iterations += 1
        eta = design @ coefficients
        probabilities = expit(eta)
        weights = np.clip(probabilities * (1.0 - probabilities), 1e-10, None)
        information = design.T @ (design * weights[:, None])
        gradient = design.T @ (y - probabilities)
        try:
            step = np.linalg.solve(information, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(information) @ gradient
        coefficients = coefficients + step
        if float(np.max(np.abs(step))) < 1e-8:
            converged = True

    eta = design @ coefficients
    probabilities = np.clip(expit(eta), 1e-12, 1.0 - 1e-12)
    weights = np.clip(probabilities * (1.0 - probabilities), 1e-12, None)
    information = design.T @ (design * weights[:, None])
    covariance = np.linalg.pinv(information)
    standard_errors = np.sqrt(np.clip(np.diag(covariance), 0.0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        z_values = np.divide(
            coefficients,
            standard_errors,
            out=np.full_like(coefficients, np.nan),
            where=standard_errors > 0,
        )
    p_values = 2.0 * stats.norm.sf(np.abs(z_values))
    critical = _normal_critical(confidence)
    ci_low = coefficients - critical * standard_errors
    ci_high = coefficients + critical * standard_errors
    odds_ratios = np.exp(np.clip(coefficients, -700, 700))

    log_likelihood = float(
        np.sum(y * np.log(probabilities) + (1.0 - y) * np.log(1.0 - probabilities))
    )
    base_rate = float(y.mean())
    null_log_likelihood = (
        float(
            n
            * (
                base_rate * math.log(base_rate)
                + (1.0 - base_rate) * math.log(1.0 - base_rate)
            )
        )
        if 0.0 < base_rate < 1.0
        else None
    )
    pseudo_r_squared = (
        1.0 - log_likelihood / null_log_likelihood
        if null_log_likelihood not in (None, 0.0)
        else None
    )
    aic = -2.0 * log_likelihood + 2.0 * rank
    bic = -2.0 * log_likelihood + rank * math.log(n)
    predictions = (probabilities >= 0.5).astype(float)
    accuracy = float((predictions == y).mean())
    auc = _auc(y, probabilities)

    coefficient_rows = []
    for index, term in enumerate(terms):
        coefficient_rows.append(
            (
                term,
                num(float(coefficients[index])),
                num(float(standard_errors[index])),
                num(float(z_values[index])),
                p_value(float(p_values[index])),
                p_text(float(p_values[index])),
                num(float(ci_low[index])),
                num(float(ci_high[index])),
                num(float(odds_ratios[index])),
            )
        )
    fit_rows = (
        ("Observations", n),
        ("Events", int(y.sum())),
        ("Non-events", int(n - y.sum())),
        ("Log likelihood", num(log_likelihood)),
        ("Null log likelihood", num(null_log_likelihood)),
        ("McFadden pseudo R-squared", num(pseudo_r_squared)),
        ("AIC", num(aic)),
        ("BIC", num(bic)),
        ("Accuracy (threshold 0.5)", num(accuracy)),
        ("ROC AUC", num(auc)),
        ("Iterations", iterations),
        ("Converged", converged),
    )

    diagnostics: list[Diagnostic] = []
    if not converged:
        diagnostics.append(
            Diagnostic.warning(
                "not_converged",
                f"The IRLS solver did not converge within {max_iterations} iterations.",
                iterations=iterations,
            )
        )
    if (
        np.any(np.abs(coefficients) > 20)
        or float(probabilities.min()) < 1e-6
        or float(probabilities.max()) > 1 - 1e-6
    ):
        diagnostics.append(
            Diagnostic.warning(
                "possible_separation",
                "Fitted probabilities approach 0 or 1; some coefficients may be "
                "poorly identified (possible separation).",
            )
        )
    if base_rate < 0.1 or base_rate > 0.9:
        diagnostics.append(
            Diagnostic.info(
                "class_imbalance",
                f"The positive class is {base_rate:.1%} of the sample.",
                base_rate=num(base_rate),
            )
        )
    if n < 30:
        diagnostics.append(
            Diagnostic.info(
                "small_sample",
                f"Only {n} complete observation(s); estimates may be unstable.",
                n=n,
            )
        )

    metrics = {
        "response": response,
        "predictors": ", ".join(predictors),
        "n": n,
        "events": int(y.sum()),
        "pseudo_r_squared": num(pseudo_r_squared),
        "aic": num(aic),
        "bic": num(bic),
        "accuracy": num(accuracy),
        "auc": num(auc),
        "log_likelihood": num(log_likelihood),
        "null_log_likelihood": num(null_log_likelihood),
        "iterations": iterations,
        "converged": converged,
        "confidence": confidence,
        "intercept": intercept,
    }
    narrative = (
        f"Logistic regression of {response!r} on {len(predictors)} predictor(s) "
        f"using {n:,} complete observation(s). McFadden pseudo R-squared is "
        + (f"{pseudo_r_squared:.3f}" if pseudo_r_squared is not None else "n/a")
        + ", ROC AUC is " + (f"{auc:.3f}" if auc is not None else "n/a")
        + f", and accuracy at a 0.5 threshold is {accuracy:.3f}. "
        + ("The solver converged." if converged else "The solver did not converge.")
    )
    return result(
        context,
        "logistic_regression",
        f"Logistic regression · {response}",
        params,
        tables=(
            ResultTable(
                title="Coefficients",
                columns=(
                    "term",
                    "estimate",
                    "std_error",
                    "z",
                    "p_value",
                    "p_value_text",
                    "ci_low",
                    "ci_high",
                    "odds_ratio",
                ),
                rows=tuple(coefficient_rows),
                note="Standard errors use the inverse observed information matrix.",
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
    )


# -- pairwise comparisons --------------------------------------------------


def pairwise_tests(
    context: AnalysisContext, params: Mapping[str, Any]
) -> AnalysisResult:
    table = context.table
    value = _require_numeric(table, params["value"])
    group = params["group"]
    require_columns(table, (group,))
    if group == value:
        raise AnalysisError("The value and grouping columns must be different.")
    confidence = _confidence(params)
    alternative = _alternative(params)
    equal_var = bool(params.get("equal_var", False))
    correction = str(params.get("correction") or "holm")
    if correction not in CORRECTIONS:
        raise AnalysisError(
            "Correction must be one of: " + ", ".join(CORRECTIONS) + "."
        )
    alpha = float(params.get("alpha") if params.get("alpha") is not None else 0.05)
    if not 0.0 < alpha < 1.0:
        raise AnalysisError("Alpha must be strictly between 0 and 1.")

    labels, samples = _grouped_samples(table, value, group)
    if len(samples) < 2:
        raise AnalysisError("Pairwise tests need at least two non-missing groups.")

    pairs: list[tuple[int, int, dict[str, Any]]] = []
    for first in range(len(samples)):
        for second in range(first + 1, len(samples)):
            calculated = _two_sample(
                samples[first],
                samples[second],
                equal_var=equal_var,
                confidence=confidence,
                alternative=alternative,
            )
            pairs.append((first, second, calculated))

    raw = [calculated["pvalue"] for _, _, calculated in pairs]
    adjusted = _adjust(raw, correction)
    rows: list[tuple[Any, ...]] = []
    significant_pairs = 0
    for (first, second, calculated), adjusted_p in zip(pairs, adjusted, strict=True):
        significant = adjusted_p < alpha
        significant_pairs += int(significant)
        rows.append(
            (
                f"{labels[first]} vs {labels[second]}",
                num(calculated["mean1"]),
                num(calculated["mean2"]),
                num(calculated["difference"]),
                num(calculated["statistic"]),
                p_value(calculated["pvalue"]),
                p_value(adjusted_p),
                num(calculated["ci_low"]),
                num(calculated["ci_high"]),
                significant,
            )
        )

    method = "Student t-test (pooled variance)" if equal_var else "Welch t-test"
    diagnostics: list[Diagnostic] = [_diagnose_equal_variance(samples)]
    diagnostics = [item for item in diagnostics if item is not None]
    if len(samples) > 10:
        diagnostics.append(
            Diagnostic.info(
                "many_pairs",
                f"{len(pairs)} pairwise comparisons increase the chance of false positives "
                "even after correction.",
                pairs=len(pairs),
            )
        )

    metrics = {
        "groups": len(samples),
        "pairs": len(pairs),
        "correction": correction,
        "method": method,
        "alpha": alpha,
        "significant_pairs": significant_pairs,
        "n": int(sum(sample.size for sample in samples)),
        "confidence": confidence,
    }
    narrative = (
        f"Compared {len(samples)} groups across {len(pairs)} pair(s) using {method} "
        f"with the {correction} multiple-comparison correction. "
        f"{significant_pairs} pair(s) remain significant at alpha={alpha:.2f}."
    )
    return result(
        context,
        "pairwise_tests",
        f"Pairwise comparisons · {value} by {group}",
        params,
        tables=(
            ResultTable(
                title="Pairwise comparisons",
                columns=(
                    "comparison",
                    "mean_1",
                    "mean_2",
                    "mean_difference",
                    "statistic",
                    "p_value",
                    "p_adjusted",
                    "ci_low",
                    "ci_high",
                    "significant",
                ),
                rows=tuple(rows),
                note=(
                    "p_adjusted applies the selected multiple-comparison correction. "
                    "Differences are first group minus second group."
                ),
            ),
        ),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
    )


ANALYSES: tuple[AnalysisDefinition, ...] = (
    AnalysisDefinition(
        name="t_test",
        title="t-test",
        category="inference",
        description=(
            "One-sample, two-sample (Welch or Student) or paired t-test with "
            "confidence intervals, effect sizes and assumption checks."
        ),
        run=t_test,
        params=(
            StepParam(
                "test",
                "enum",
                required=False,
                default="one_sample",
                choices=("one_sample", "two_sample", "paired"),
                description="Which t-test to run.",
            ),
            StepParam("column", "column", description="Numeric response column."),
            StepParam(
                "other",
                "column",
                required=False,
                description="Second numeric column for a two-sample or paired test.",
            ),
            StepParam(
                "group",
                "column",
                required=False,
                description="Alternative to 'other' for a two-sample test: a two-group column.",
            ),
            StepParam(
                "mu",
                "number",
                required=False,
                default=0.0,
                description="Null-hypothesis mean for a one-sample test.",
            ),
            StepParam(
                "equal_var",
                "boolean",
                required=False,
                default=False,
                description="Assume equal variances (Student) instead of Welch.",
            ),
            StepParam(
                "alternative",
                "enum",
                required=False,
                default="two-sided",
                choices=ALTERNATIVES,
                description="Alternative hypothesis.",
            ),
            StepParam(
                "confidence",
                "number",
                required=False,
                default=0.95,
                description="Confidence level for intervals (0-1).",
            ),
        ),
    ),
    AnalysisDefinition(
        name="anova",
        title="One-way ANOVA",
        category="inference",
        description=(
            "Compare means across two or more groups with eta-squared, omega-squared, "
            "normality and variance checks, and optional Tukey HSD comparisons."
        ),
        run=anova,
        params=(
            StepParam("value", "column", description="Numeric response column."),
            StepParam("group", "column", description="Grouping column."),
            StepParam(
                "posthoc",
                "boolean",
                required=False,
                default=True,
                description="Include Tukey HSD pairwise comparisons.",
            ),
            StepParam(
                "confidence",
                "number",
                required=False,
                default=0.95,
                description="Confidence level for intervals (0-1).",
            ),
        ),
    ),
    AnalysisDefinition(
        name="chi_square",
        title="Chi-square test",
        category="inference",
        description=(
            "Test independence between two categorical columns with expected counts, "
            "Cramer's V and small-cell diagnostics."
        ),
        run=chi_square,
        params=(
            StepParam("row", "column", description="Row categorical column."),
            StepParam("column", "column", description="Column categorical column."),
            StepParam(
                "correction",
                "boolean",
                required=False,
                default=False,
                description="Apply Yates' continuity correction to a 2x2 table.",
            ),
        ),
    ),
    AnalysisDefinition(
        name="proportion_test",
        title="Proportion test",
        category="inference",
        description=(
            "One- or two-sample z-test for proportions with confidence intervals and "
            "effect size h."
        ),
        run=proportion_test,
        params=(
            StepParam(
                "test",
                "enum",
                required=False,
                default="one_sample",
                choices=("one_sample", "two_sample"),
                description="Which proportion test to run.",
            ),
            StepParam(
                "column",
                "column",
                description="Success indicator column (1/true or a category value).",
            ),
            StepParam(
                "group",
                "column",
                required=False,
                description="Two-group column for a two-sample test.",
            ),
            StepParam(
                "success_value",
                "string",
                required=False,
                description="Value treated as success; omit to treat 1/true as success.",
            ),
            StepParam(
                "p0",
                "number",
                required=False,
                default=0.5,
                description="Null-hypothesis proportion for a one-sample test.",
            ),
            StepParam(
                "alternative",
                "enum",
                required=False,
                default="two-sided",
                choices=ALTERNATIVES,
                description="Alternative hypothesis.",
            ),
            StepParam(
                "confidence",
                "number",
                required=False,
                default=0.95,
                description="Confidence level for intervals (0-1).",
            ),
        ),
    ),
    AnalysisDefinition(
        name="linear_regression",
        title="Linear regression",
        category="regression",
        description=(
            "Ordinary least squares with coefficients, standard errors, confidence "
            "intervals, standardized betas, VIF and residual diagnostics."
        ),
        run=linear_regression,
        params=(
            StepParam("response", "column", description="Numeric response column."),
            StepParam(
                "predictors",
                "columns",
                description="Numeric predictor columns.",
            ),
            StepParam(
                "intercept",
                "boolean",
                required=False,
                default=True,
                description="Include an intercept term.",
            ),
            StepParam(
                "confidence",
                "number",
                required=False,
                default=0.95,
                description="Confidence level for intervals (0-1).",
            ),
        ),
    ),
    AnalysisDefinition(
        name="logistic_regression",
        title="Logistic regression",
        category="regression",
        description=(
            "Binary logistic regression with odds ratios, likelihood-ratio fit, "
            "ROC AUC, accuracy and separation diagnostics."
        ),
        run=logistic_regression,
        params=(
            StepParam(
                "response",
                "column",
                description="Binary response column (0/1 or true/false).",
            ),
            StepParam(
                "predictors",
                "columns",
                description="Numeric predictor columns.",
            ),
            StepParam(
                "intercept",
                "boolean",
                required=False,
                default=True,
                description="Include an intercept term.",
            ),
            StepParam(
                "max_iterations",
                "integer",
                required=False,
                default=100,
                description="Maximum IRLS iterations.",
            ),
            StepParam(
                "confidence",
                "number",
                required=False,
                default=0.95,
                description="Confidence level for intervals (0-1).",
            ),
        ),
    ),
    AnalysisDefinition(
        name="pairwise_tests",
        title="Pairwise comparisons",
        category="inference",
        description=(
            "All pairwise group comparisons with Bonferroni, Holm or Benjamini-Hochberg "
            "multiple-comparison corrections."
        ),
        run=pairwise_tests,
        params=(
            StepParam("value", "column", description="Numeric response column."),
            StepParam("group", "column", description="Grouping column."),
            StepParam(
                "equal_var",
                "boolean",
                required=False,
                default=False,
                description="Assume equal variances (Student) instead of Welch.",
            ),
            StepParam(
                "correction",
                "enum",
                required=False,
                default="holm",
                choices=CORRECTIONS,
                description="Multiple-comparison correction.",
            ),
            StepParam(
                "alpha",
                "number",
                required=False,
                default=0.05,
                description="Significance threshold.",
            ),
            StepParam(
                "alternative",
                "enum",
                required=False,
                default="two-sided",
                choices=ALTERNATIVES,
                description="Alternative hypothesis.",
            ),
            StepParam(
                "confidence",
                "number",
                required=False,
                default=0.95,
                description="Confidence level for intervals (0-1).",
            ),
        ),
    ),
)


__all__ = [
    "ANALYSES",
    "anova",
    "chi_square",
    "linear_regression",
    "logistic_regression",
    "pairwise_tests",
    "proportion_test",
    "t_test",
]
