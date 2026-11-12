"""Shared schedule vocabulary for saved analyses and dashboards.

Schedules are evaluated on demand (project open, a workspace timer or
``data-studio refresh --due``); there is no background daemon to leak between
processes. Dashboards re-query live data, so for them "refresh" means: confirm the
inputs still match the recorded fingerprint, re-render the tiles and stamp the
document — and optionally rewrite the HTML report artifact when one is configured.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

SCHEDULES = ("manual", "on_open", "hourly", "daily")
SCHEDULE_INTERVAL = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
}


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"Invalid timestamp {value!r}.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def is_due(
    schedule: str,
    status: str,
    refreshed_at: str,
    now: datetime,
) -> bool:
    if schedule == "manual":
        return False
    if status != "fresh":
        return True
    if schedule == "on_open":
        return False
    interval = SCHEDULE_INTERVAL.get(schedule)
    if interval is None:
        return False
    try:
        last = parse_time(refreshed_at)
    except ValueError:
        return True
    return now - last >= interval


def next_refresh(schedule: str, refreshed_at: str) -> str | None:
    if schedule in ("manual", "on_open"):
        return None
    interval = SCHEDULE_INTERVAL.get(schedule)
    if interval is None:
        return None
    try:
        last = parse_time(refreshed_at)
    except ValueError:
        return None
    return (last + interval).astimezone(UTC).isoformat(timespec="seconds")


__all__ = [
    "SCHEDULES",
    "SCHEDULE_INTERVAL",
    "is_due",
    "next_refresh",
    "parse_time",
]
