"""Calendar (date) table generation for semantic models.

Time-intelligence measures order rows by a *date* column, and drill hierarchies
walk year -> quarter -> month -> day. A one-row-per-day calendar dataset gives a
project a stable, joinable axis with all of those levels already materialised.

``generate_calendar`` writes a proper project dataset (Parquet + manifest entry),
so the calendar is verified, fingerprinted and exported like any other source.
Re-deriving the semantic model afterwards exposes ``year_start``, ``quarter_start``,
``month_start``, ``week_start`` and ``date`` as dimensions; marking the
``date``-like column on the grain you analyse by (for example ``month_start`` with
grain ``month``) unlocks ``running_total``, ``yoy`` and ``yoy_pct`` measures.
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING

from ..io.writers import write_table  # noqa: F401  (kept for symmetry with io API)

if TYPE_CHECKING:
    from ..project.manifest import DatasetEntry
    from ..project.store import Project

CALENDAR_DATASET = "calendar"
_MAX_YEARS = 100
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_CALENDAR_SQL = """
SELECT
    d                                AS date,
    year(d)                          AS year,
    quarter(d)                       AS quarter,
    month(d)                         AS month,
    monthname(d)                     AS month_name,
    strftime(d, '%Y-%m')             AS year_month,
    week(d)                          AS week,
    isodow(d)                        AS weekday,
    day(d)                           AS day,
    dayname(d)                       AS day_name,
    isodow(d) >= 6                   AS is_weekend,
    CAST(date_trunc('year', d) AS DATE)    AS year_start,
    CAST(date_trunc('quarter', d) AS DATE) AS quarter_start,
    CAST(date_trunc('month', d) AS DATE)   AS month_start,
    CAST(date_trunc('week', d) AS DATE)    AS week_start
FROM generate_series(
    DATE '{start}',
    DATE '{end}',
    INTERVAL 1 DAY
) AS calendar(d)
ORDER BY d
"""


class CalendarError(ValueError):
    """A calendar could not be generated for the requested range."""


def _parse_day(value: date | str, label: str) -> date:
    if isinstance(value, date):
        return value
    text = str(value).strip() if isinstance(value, str) else ""
    if not _DATE.match(text):
        raise CalendarError(f"{label} must be an ISO date (YYYY-MM-DD); got {value!r}.")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise CalendarError(f"{label} is not a valid date: {exc}") from exc


def calendar_sql(start: date | str, end: date | str) -> str:
    """Return the SQL that produces one calendar row per day in the range."""

    first = _parse_day(start, "start")
    last = _parse_day(end, "end")
    if first > last:
        raise CalendarError(f"start {first.isoformat()} is after end {last.isoformat()}.")
    if (last - first).days > _MAX_YEARS * 366:
        raise CalendarError(f"A calendar may span at most {_MAX_YEARS} years.")
    return _CALENDAR_SQL.format(start=first.isoformat(), end=last.isoformat())


def generate_calendar(
    project: Project,
    *,
    start: date | str,
    end: date | str,
    dataset_id: str = CALENDAR_DATASET,
    overwrite: bool = False,
) -> DatasetEntry:
    """Create (or replace) the project's calendar dataset."""

    sql = calendar_sql(start, end)
    dataset_id = str(dataset_id).strip() or CALENDAR_DATASET
    table = project.engine.table(sql, name=dataset_id)
    return project.add_table(
        table,
        dataset_id=dataset_id,
        replace=overwrite,
        source_name=f"generated calendar {_parse_day(start, 'start').isoformat()}.."
        f"{_parse_day(end, 'end').isoformat()}",
    )


__all__ = ["CALENDAR_DATASET", "CalendarError", "calendar_sql", "generate_calendar"]
