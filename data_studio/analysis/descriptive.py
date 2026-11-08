"""Descriptive analyses and data-quality profiling.

Every function here is a pure kernel analysis: it receives an `AnalysisContext`, reads
only through the engine, and returns an `AnalysisResult`. No printing, no HTTP, no widget
toolkit. The web workspace, CLI and tests therefore get identical numbers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from ..engine.duckdb_engine import EngineError
from ..kernel.results import AnalysisResult, Diagnostic, ResultTable
from ..steps.registry import StepParam
from ..steps.sql import literal, q, wrap
from ._common import (
    UNORDERED_TYPES,
    cell,
    num,
    numeric_columns,
    require_columns,
    result,
    sort_key,
    unique_labels,
)
from .registry import AnalysisContext, AnalysisDefinition, AnalysisError

# -- descriptive statistics ------------------------------------------------


def descriptive_stats(
    context: AnalysisContext,
    params: Mapping[str, Any],
) -> AnalysisResult:
    table = context.table
    columns = numeric_columns(table, params.get("columns"))
    engine = table.engine
    rows: list[tuple[Any, ...]] = []
    diagnostics: list[Diagnostic] = []
    total_rows = 0

    for name in columns:
        column = q(name)
        summary = engine.query_one(
            f"""
            SELECT
                COUNT(*),
                COUNT({column}),
                AVG({column}),
                STDDEV_SAMP({column}),
                MIN({column}),
                QUANTILE_CONT({column}, 0.25),
                MEDIAN({column}),
                QUANTILE_CONT({column}, 0.75),
                MAX({column})
            FROM {wrap(table)} AS _summary
            """
        )
        count = int(summary[0])
        total_rows = max(total_rows, count)
        present = int(summary[1])
        missing = count - present
        if present == 0:
            diagnostics.append(
                Diagnostic.warning(
                    "empty_column",
                    f"{name!r} has no non-missing values.",
                    column=name,
                )
            )
        elif summary[3] is not None and num(summary[3]) == 0.0:
            diagnostics.append(
                Diagnostic.info(
                    "constant_column",
                    f"{name!r} has no variation.",
                    column=name,
                )
            )
        rows.append(
            (
                name,
                count,
                missing,
                num(summary[2]),
                num(summary[3]),
                num(summary[4]),
                num(summary[5]),
                num(summary[6]),
                num(summary[7]),
                num(summary[8]),
            )
        )

    total_missing = sum(int(row[2]) for row in rows)
    metrics = {
        "rows": total_rows,
        "columns": len(columns),
        "missing": total_missing,
    }
    table_result = ResultTable(
        title="Descriptive statistics",
        columns=(
            "column",
            "count",
            "missing",
            "mean",
            "std",
            "min",
            "p25",
            "median",
            "p75",
            "max",
        ),
        rows=tuple(rows),
        note=(
            "Standard deviation uses the sample formula (n-1). "
            "Quantiles use linear interpolation."
        ),
    )
    narrative = (
        f"Summarised {len(columns)} numeric column(s) across {total_rows:,} row(s). "
        f"{total_missing:,} value(s) are missing."
    )
    return result(
        context,
        "descriptive_stats",
        "Descriptive statistics",
        params,
        tables=(table_result,),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
    )


# -- value counts ----------------------------------------------------------


def value_counts(
    context: AnalysisContext,
    params: Mapping[str, Any],
) -> AnalysisResult:
    table = context.table
    column_name = params["column"]
    (column_name,) = require_columns(table, (column_name,))
    limit = max(1, min(int(params.get("limit") or 10), 1000))
    drop_missing = bool(params.get("drop_missing", False))
    column = q(column_name)
    engine = table.engine

    total = table.count()
    distinct = int(
        engine.query_one(
            f"SELECT COUNT(DISTINCT {column}) FROM {wrap(table)} AS _counts"
        )[0]
    )
    missing = int(
        engine.query_one(
            f"SELECT COUNT(*) FROM {wrap(table)} AS _counts WHERE {column} IS NULL"
        )[0]
    )
    raw_rows = engine.query(
        f"""
        SELECT {column} AS value, COUNT(*) AS n
        FROM {wrap(table)} AS _counts
        WHERE {column} IS NOT NULL
        GROUP BY {column}
        ORDER BY n DESC, value ASC
        LIMIT {limit}
        """
    )
    rows: list[tuple[Any, ...]] = [
        (
            cell(value),
            int(count),
            round(100.0 * int(count) / total, 2) if total else 0.0,
        )
        for value, count in raw_rows
    ]
    if not drop_missing and missing:
        rows.append(
            (
                None,
                missing,
                round(100.0 * missing / total, 2) if total else 0.0,
            )
        )

    diagnostics: list[Diagnostic] = []
    if distinct > limit:
        diagnostics.append(
            Diagnostic.info(
                "truncated_categories",
                f"Showing the top {limit} of {distinct} distinct values.",
                distinct=distinct,
                shown=limit,
            )
        )
    if missing:
        diagnostics.append(
            Diagnostic.info(
                "missing_values",
                f"{missing:,} value(s) are missing.",
                column=column_name,
                missing=missing,
            )
        )

    result_table = ResultTable(
        title=f"Value counts · {column_name}",
        columns=("value", "count", "percent"),
        rows=tuple(rows),
        note="Percent is share of all rows, including missing values.",
    )
    metrics = {
        "rows": total,
        "missing": missing,
        "distinct": distinct,
        "shown": len(raw_rows),
    }
    narrative = (
        f"{column_name!r} has {distinct:,} distinct non-missing value(s) across "
        f"{total:,} row(s); {missing:,} value(s) are missing."
    )
    return result(
        context,
        "value_counts",
        f"Value counts · {column_name}",
        params,
        tables=(result_table,),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
    )


# -- crosstab --------------------------------------------------------------


def crosstab(
    context: AnalysisContext,
    params: Mapping[str, Any],
) -> AnalysisResult:
    table = context.table
    row_name = params["row"]
    column_name = params["column"]
    if row_name == column_name:
        raise AnalysisError("Crosstab rows and columns must be different columns.")
    require_columns(table, (row_name, column_name))
    normalize = str(params.get("normalize") or "none")
    max_columns = max(1, min(int(params.get("max_columns") or 20), 100))
    max_rows = max(1, min(int(params.get("max_rows") or 100), 500))
    row_col = q(row_name)
    column_col = q(column_name)
    engine = table.engine

    distinct_columns = int(
        engine.query_one(
            f"""
            SELECT COUNT(DISTINCT {column_col})
            FROM {wrap(table)} AS _distinct
            WHERE {column_col} IS NOT NULL
            """
        )[0]
    )
    top = engine.query(
        f"""
        SELECT {column_col} AS value, COUNT(*) AS n
        FROM {wrap(table)} AS _top
        WHERE {column_col} IS NOT NULL
        GROUP BY {column_col}
        ORDER BY n DESC, value ASC
        LIMIT {max_columns}
        """
    )
    raw_values = sorted((value for value, _ in top), key=sort_key)
    labels = unique_labels(raw_values, reserved=(row_name, "Total"))
    case_expressions = [
        f"SUM(CASE WHEN {column_col} = {literal(value)} THEN 1 ELSE 0 END)"
        for value in raw_values
    ]
    select_cells = ", ".join(case_expressions) if case_expressions else "0"
    raw_rows = engine.query(
        f"""
        SELECT {row_col} AS row_value, {select_cells}, COUNT(*) AS total
        FROM {wrap(table)} AS _pivot
        WHERE {row_col} IS NOT NULL AND {column_col} IS NOT NULL
        GROUP BY {row_col}
        ORDER BY total DESC, row_value ASC
        LIMIT {max_rows}
        """
    )

    raw_matrix = [[int(value) for value in data[1:-1]] for data in raw_rows]
    row_totals = [int(data[-1]) for data in raw_rows]
    grand_total = sum(row_totals)
    column_totals = [
        sum(row[index] for row in raw_matrix) for index in range(len(raw_values))
    ]

    result_rows: list[tuple[Any, ...]] = []
    for data, values, row_total in zip(raw_rows, raw_matrix, row_totals, strict=True):
        cells: list[float | int | None] = []
        for index, value in enumerate(values):
            if normalize == "none":
                cells.append(value)
            elif normalize == "row":
                cells.append(round(100.0 * value / row_total, 2) if row_total else None)
            elif normalize == "column":
                denominator = column_totals[index]
                cells.append(
                    round(100.0 * value / denominator, 2) if denominator else None
                )
            else:  # all
                cells.append(
                    round(100.0 * value / grand_total, 2) if grand_total else None
                )
        if normalize == "none":
            total_cell: float | int | None = row_total
        elif normalize == "row":
            total_cell = 100.0 if row_total else None
        else:
            total_cell = (
                round(100.0 * row_total / grand_total, 2) if grand_total else None
            )
        result_rows.append((cell(data[0]), *cells, total_cell))

    diagnostics: list[Diagnostic] = []
    if distinct_columns > max_columns:
        diagnostics.append(
            Diagnostic.info(
                "truncated_categories",
                f"Showing the {max_columns} most frequent of {distinct_columns} "
                f"values in {column_name!r}.",
                distinct=distinct_columns,
                shown=max_columns,
            )
        )
    if len(raw_rows) >= max_rows:
        diagnostics.append(
            Diagnostic.info(
                "truncated_rows",
                f"Showing the first {max_rows} row groups.",
                shown=max_rows,
            )
        )

    notes = {
        "none": "Cell values are row counts.",
        "row": "Cell values are row percentages; each row totals 100%.",
        "column": "Cell values are column percentages; each column totals 100%.",
        "all": "Cell values are percentages of the grand total.",
    }
    result_table = ResultTable(
        title=f"Crosstab · {row_name} × {column_name}",
        columns=(row_name, *labels, "Total"),
        rows=tuple(result_rows),
        note=notes[normalize],
    )
    metrics = {
        "rows": len(raw_rows),
        "columns": len(raw_values),
        "grand_total": grand_total,
        "normalize": normalize,
    }
    narrative = (
        f"Cross-tabulated {row_name!r} by {column_name!r} over {grand_total:,} "
        f"non-missing pair(s), showing {len(raw_rows)} row group(s) and "
        f"{len(raw_values)} column value(s)."
    )
    return result(
        context,
        "crosstab",
        f"Crosstab · {row_name} × {column_name}",
        params,
        tables=(result_table,),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
    )


# -- correlation -----------------------------------------------------------


def correlation(
    context: AnalysisContext,
    params: Mapping[str, Any],
) -> AnalysisResult:
    table = context.table
    columns = numeric_columns(table, params.get("columns"))
    if len(columns) < 2:
        raise AnalysisError("Correlation needs at least two numeric columns.")
    method = str(params.get("method") or "pearson")
    engine = table.engine
    size = len(columns)
    matrix: list[list[float | None]] = [
        [None for _ in range(size)] for _ in range(size)
    ]

    if method == "spearman":
        selected = ", ".join(q(name) for name in columns)
        frame = pd.DataFrame(
            engine.query(f"SELECT {selected} FROM {wrap(table)} AS _corr"),
            columns=list(columns),
        )
        calculated = frame.corr(method="spearman")
        for row_index in range(size):
            for column_index in range(size):
                value = calculated.iat[row_index, column_index]
                matrix[row_index][column_index] = (
                    1.0 if row_index == column_index else num(value, 4)
                )
    else:
        for row_index, first in enumerate(columns):
            matrix[row_index][row_index] = 1.0
            for column_index in range(row_index + 1, size):
                second = columns[column_index]
                value = engine.query_one(
                    f"""
                    SELECT CORR({q(second)}, {q(first)})
                    FROM {wrap(table)} AS _corr
                    """
                )[0]
                coefficient = num(value, 4)
                matrix[row_index][column_index] = coefficient
                matrix[column_index][row_index] = coefficient

    rows = [(name, *matrix[index]) for index, name in enumerate(columns)]
    result_table = ResultTable(
        title=f"{method.title()} correlation",
        columns=("column", *columns),
        rows=tuple(rows),
        note="Pairwise complete observations. Values range from -1 to 1.",
    )

    strongest: tuple[str, str, float] | None = None
    for row_index in range(size):
        for column_index in range(row_index + 1, size):
            value = matrix[row_index][column_index]
            if value is not None and (
                strongest is None or abs(value) > abs(strongest[2])
            ):
                strongest = (
                    columns[row_index],
                    columns[column_index],
                    float(value),
                )

    diagnostics: list[Diagnostic] = []
    if strongest is not None and abs(strongest[2]) >= 0.7:
        diagnostics.append(
            Diagnostic.info(
                "strong_correlation",
                f"{strongest[0]!r} and {strongest[1]!r} are strongly correlated "
                f"({strongest[2]:.2f}).",
                first=strongest[0],
                second=strongest[1],
                correlation=strongest[2],
            )
        )

    metrics = {
        "columns": size,
        "pairs": size * (size - 1) // 2,
        "method": method,
    }
    if strongest is not None:
        metrics["strongest_pair"] = f"{strongest[0]} / {strongest[1]}"
        metrics["strongest_value"] = strongest[2]
    narrative = (
        f"Computed {method} correlations for {size} numeric column(s). "
        + (
            f"The strongest association is {strongest[0]!r} with {strongest[1]!r} "
            f"at {strongest[2]:.2f}."
            if strongest is not None
            else "No pair had enough complete observations."
        )
    )
    return result(
        context,
        "correlation",
        f"{method.title()} correlation",
        params,
        tables=(result_table,),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
    )


# -- data quality ----------------------------------------------------------


def quality_profile(
    context: AnalysisContext,
    params: Mapping[str, Any],
) -> AnalysisResult:
    table = context.table
    columns = require_columns(table, params.get("columns") or table.schema.names)
    threshold = float(params.get("missing_threshold") or 5.0)
    check_duplicates = bool(params.get("check_duplicates", True))
    engine = table.engine
    rows_total = table.count()
    rows: list[tuple[Any, ...]] = []
    diagnostics: list[Diagnostic] = []

    for name in columns:
        column = table.schema.column(name)
        quoted = q(name)
        ordered = column.dtype not in UNORDERED_TYPES
        minimum = f"MIN({quoted})" if ordered else f"MIN(CAST({quoted} AS VARCHAR))"
        maximum = f"MAX({quoted})" if ordered else f"MAX(CAST({quoted} AS VARCHAR))"
        summary = engine.query_one(
            f"""
            SELECT
                COUNT(*),
                COUNT({quoted}),
                COUNT(DISTINCT CAST({quoted} AS VARCHAR)),
                {minimum},
                {maximum}
            FROM {wrap(table)} AS _profile
            """
        )
        count = int(summary[0])
        present = int(summary[1])
        missing = count - present
        missing_pct = round(100.0 * missing / count, 2) if count else 0.0
        distinct = int(summary[2])
        rows.append(
            (
                name,
                column.dtype.value,
                column.role.value if column.role else None,
                count,
                missing,
                missing_pct,
                distinct,
                cell(summary[3]),
                cell(summary[4]),
            )
        )
        if count and missing_pct > threshold:
            diagnostics.append(
                Diagnostic.warning(
                    "high_missing",
                    f"{name!r} is {missing_pct:.1f}% missing.",
                    column=name,
                    missing_pct=missing_pct,
                )
            )
        if count and distinct <= 1:
            diagnostics.append(
                Diagnostic.info(
                    "constant_column",
                    f"{name!r} has a single distinct value.",
                    column=name,
                )
            )

    duplicate_rows: int | None = None
    if check_duplicates and rows_total:
        try:
            duplicate_rows = int(
                engine.query_one(
                    f"""
                    SELECT
                        (SELECT COUNT(*) FROM {wrap(table)} AS _all)
                        - (SELECT COUNT(*) FROM (
                            SELECT DISTINCT * FROM {wrap(table)} AS _distinct
                        ) AS _unique)
                    """
                )[0]
            )
        except EngineError:
            diagnostics.append(
                Diagnostic.info(
                    "duplicates_unavailable",
                    "Duplicate-row detection was not possible for this schema.",
                )
            )
    if duplicate_rows:
        diagnostics.append(
            Diagnostic.warning(
                "duplicate_rows",
                f"{duplicate_rows:,} duplicate row(s) detected.",
                duplicate_rows=duplicate_rows,
            )
        )

    total_missing = sum(int(row[4]) for row in rows)
    cell_count = rows_total * len(columns)
    missing_pct = round(100.0 * total_missing / cell_count, 2) if cell_count else 0.0
    constant_columns = sum(1 for row in rows if row[6] <= 1)
    metrics = {
        "rows": rows_total,
        "columns": len(columns),
        "missing": total_missing,
        "missing_pct": missing_pct,
        "duplicate_rows": duplicate_rows,
        "constant_columns": constant_columns,
    }
    result_table = ResultTable(
        title="Data-quality profile",
        columns=(
            "column",
            "type",
            "role",
            "count",
            "missing",
            "missing_pct",
            "distinct",
            "min",
            "max",
        ),
        rows=tuple(rows),
        note=(
            "Missing percent is calculated per column. "
            "Distinct counts compare text representations."
        ),
    )
    narrative = (
        f"Profiled {len(columns)} column(s) across {rows_total:,} row(s). "
        f"{total_missing:,} value(s) are missing ({missing_pct:.2f}% of cells); "
        f"{constant_columns} column(s) are constant."
    )
    if duplicate_rows is not None:
        narrative += f" {duplicate_rows:,} duplicate row(s) were found."
    return result(
        context,
        "quality_profile",
        "Data-quality profile",
        params,
        tables=(result_table,),
        metrics=metrics,
        diagnostics=diagnostics,
        narrative=narrative,
    )


ANALYSES: tuple[AnalysisDefinition, ...] = (
    AnalysisDefinition(
        name="descriptive_stats",
        title="Descriptive statistics",
        category="summary",
        description=(
            "Count, missing values, mean, standard deviation, quartiles and range for "
            "one or more numeric columns."
        ),
        run=descriptive_stats,
        params=(
            StepParam(
                "columns",
                "columns",
                required=False,
                description="Numeric columns. Leave blank for every numeric column.",
            ),
        ),
    ),
    AnalysisDefinition(
        name="value_counts",
        title="Value counts",
        category="distribution",
        description="Most frequent values in one column with counts and percentages.",
        run=value_counts,
        params=(
            StepParam("column", "column", description="Column to count."),
            StepParam(
                "limit",
                "integer",
                required=False,
                default=10,
                description="Maximum distinct values to show (1-1000).",
            ),
            StepParam(
                "drop_missing",
                "boolean",
                required=False,
                default=False,
                description="Exclude the missing-value bucket.",
            ),
        ),
    ),
    AnalysisDefinition(
        name="crosstab",
        title="Crosstab",
        category="distribution",
        description=(
            "Cross-tabulate two columns with counts or row, column or overall percentages."
        ),
        run=crosstab,
        params=(
            StepParam("row", "column", description="Column for rows."),
            StepParam("column", "column", description="Column for columns."),
            StepParam(
                "normalize",
                "enum",
                required=False,
                default="none",
                choices=("none", "row", "column", "all"),
                description="Cell values to show.",
            ),
            StepParam(
                "max_columns",
                "integer",
                required=False,
                default=20,
                description="Maximum column values to show (1-100).",
            ),
            StepParam(
                "max_rows",
                "integer",
                required=False,
                default=100,
                description="Maximum row groups to show (1-500).",
            ),
        ),
    ),
    AnalysisDefinition(
        name="correlation",
        title="Correlation matrix",
        category="relationship",
        description="Pairwise Pearson or Spearman correlations between numeric columns.",
        run=correlation,
        params=(
            StepParam(
                "columns",
                "columns",
                required=False,
                description="Numeric columns. Leave blank for every numeric column.",
            ),
            StepParam(
                "method",
                "enum",
                required=False,
                default="pearson",
                choices=("pearson", "spearman"),
                description="Correlation method.",
            ),
        ),
    ),
    AnalysisDefinition(
        name="quality_profile",
        title="Data-quality profile",
        category="quality",
        description=(
            "Per-column completeness, distinct values, ranges, constant columns and "
            "duplicate rows."
        ),
        run=quality_profile,
        params=(
            StepParam(
                "columns",
                "columns",
                required=False,
                description="Columns to profile. Leave blank for all columns.",
            ),
            StepParam(
                "missing_threshold",
                "number",
                required=False,
                default=5.0,
                description="Warn when a column is more than this percent missing.",
            ),
            StepParam(
                "check_duplicates",
                "boolean",
                required=False,
                default=True,
                description="Count fully duplicate rows.",
            ),
        ),
    ),
)


__all__ = [
    "ANALYSES",
    "correlation",
    "crosstab",
    "descriptive_stats",
    "quality_profile",
    "value_counts",
]
