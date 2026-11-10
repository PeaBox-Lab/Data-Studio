"""Plain-language data helpers for the Data Studio desktop app.

Everything in this module is Qt-free so it can be unit tested and reused.
The UI layer in ``app.py`` calls these helpers and never re-implements them.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SAMPLE = pd.DataFrame(
    {
        "time": ["2026-10-01 08:00", "2026-10-01 09:00", "2026-10-01 10:00",
                 "2026-10-01 11:00", "2026-10-01 12:00", "2026-10-01 13:00"],
        "machine": ["M-101", "M-102", "M-103", "M-101", "M-104", "M-102"],
        "temperature": [82.4, 91.8, np.nan, 84.2, 96.5, 88.1],
        "pressure": [4.9, 5.6, 5.1, 4.8, 6.2, 5.3],
        "vibration": [2.1, 4.8, 2.5, 2.3, 6.4, 3.2],
        "output": [1180, 1042, 1158, 1204, 906, 1098],
        "status": ["Normal", "Review", "Missing", "Normal", "Anomaly", "Normal"],
    }
)

SUPPORTED = "CSV, Excel, JSON or Parquet"


def load_table(path: str | Path) -> pd.DataFrame:
    """Read a CSV/Excel/JSON/Parquet file into a DataFrame."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return pd.read_excel(path)
    if suffix == ".json":
        return pd.read_json(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    raise ValueError(f"'{path.name}' is not supported yet. Supported files: {SUPPORTED}.")


def save_table(df: pd.DataFrame, path: str | Path) -> Path:
    """Write a DataFrame to CSV/Excel/Parquet based on the file extension."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df.to_csv(path, index=False)
    elif suffix in {".xlsx", ".xlsm"}:
        df.to_excel(path, index=False)
    elif suffix == ".parquet":
        df.to_parquet(path, index=False)
    else:
        raise ValueError("Choose a .csv, .xlsx or .parquet file name.")
    return path


def quality_summary(df: pd.DataFrame) -> dict[str, int | float]:
    """Friendly headline numbers about a table."""
    rows = len(df)
    missing = int(df.isna().sum().sum())
    duplicates = int(df.duplicated().sum())
    completeness = 100.0
    if df.size:
        completeness = round(100 * (1 - missing / df.size), 1)
    return {
        "rows": rows,
        "columns": len(df.columns),
        "missing": missing,
        "duplicates": duplicates,
        "completeness": completeness,
    }


def fill_missing(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Fill gaps: median for numbers, most common value for text."""
    result = df.copy()
    changed = int(result.isna().sum().sum())
    for column in result.columns:
        series = result[column]
        if not series.isna().any():
            continue
        if pd.api.types.is_numeric_dtype(series):
            result[column] = series.fillna(series.median())
        else:
            mode = series.mode(dropna=True)
            if not mode.empty:
                result[column] = series.fillna(mode.iloc[0])
    return result, changed


def drop_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    removed = int(df.duplicated().sum())
    return df.drop_duplicates().reset_index(drop=True), removed


def trim_text(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    result = df.copy()
    changed = 0
    for column in result.columns:
        if pd.api.types.is_numeric_dtype(result[column]):
            continue
        trimmed = result[column].map(lambda v: v.strip() if isinstance(v, str) else v)
        changed += int((trimmed != result[column]).sum())
        result[column] = trimmed
    return result, changed


def to_numbers(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Convert text columns that are really numbers (e.g. '1,200' or '82.4')."""
    result = df.copy()
    converted = 0
    for column in result.columns:
        if pd.api.types.is_numeric_dtype(result[column]):
            continue
        cleaned = result[column].astype(str).str.replace(",", "", regex=False).str.strip()
        numeric = pd.to_numeric(cleaned, errors="coerce")
        original_missing = int(result[column].isna().sum())
        new_missing = int(numeric.isna().sum())
        # Only convert when the column is mostly numeric to begin with.
        mostly_numeric = (len(result) - new_missing) >= max(1, len(result) // 2)
        not_much_worse = new_missing <= original_missing + len(result) // 10
        if mostly_numeric and not_much_worse:
            result[column] = numeric
            converted += 1
    return result, converted


def clean_all(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """One pass of the gentle fixes, in a safe order."""
    steps: list[str] = []
    df, n = trim_text(df)
    if n:
        steps.append(f"Trimmed extra spaces in {n} cell(s)")
    df, n = to_numbers(df)
    if n:
        steps.append(f"Converted {n} text column(s) to numbers")
    df, n = drop_duplicates(df)
    if n:
        steps.append(f"Removed {n} duplicate row(s)")
    df, n = fill_missing(df)
    if n:
        steps.append(f"Filled {n} missing value(s)")
    if not steps:
        steps.append("Nothing needed fixing - the table looks tidy already")
    return df, steps


def describe(df: pd.DataFrame) -> str:
    """A short, readable summary suitable for pasting into an email."""
    summary = quality_summary(df)
    lines = [
        f"Rows: {summary['rows']:,}",
        f"Columns: {summary['columns']}",
        f"Missing values: {summary['missing']}",
        f"Duplicate rows: {summary['duplicates']}",
        f"Completeness: {summary['completeness']}%",
        "",
        "Numbers at a glance:",
    ]
    numeric = df.select_dtypes(include="number")
    if numeric.empty:
        lines.append("(no numeric columns)")
    else:
        lines.append(numeric.describe().round(2).to_string())
    return "\n".join(lines)


__all__ = [
    "SAMPLE",
    "SUPPORTED",
    "clean_all",
    "describe",
    "drop_duplicates",
    "fill_missing",
    "load_table",
    "quality_summary",
    "save_table",
    "to_numbers",
    "trim_text",
]
