"""Deterministic, dependency-free SVG renderer for chart specifications.

The kernel describes a visual as data (`ChartSpec` + inline records); this module turns
that description into a standalone SVG string. The same renderer serves the workspace API
and the HTML report exporter, so a chart looks identical everywhere and never depends on a
JavaScript charting library.

The renderer is deliberately small and predictable: it supports the eleven marks in
`ChartType`, uses a fixed colour palette and produces no external references, scripts or
fonts. Text is always escaped, so untrusted cell values cannot inject markup.
"""

from __future__ import annotations

import html
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .spec import ChartSpec, ChartType

PALETTE: tuple[str, ...] = (
    "#2563eb",
    "#0f766e",
    "#c2410c",
    "#7c3aed",
    "#be123c",
    "#0369a1",
    "#4d7c0f",
    "#a16207",
    "#475569",
    "#db2777",
)
POSITIVE_COLOR = "#0f766e"
NEGATIVE_COLOR = "#be123c"
THEMES: dict[str, dict[str, str]] = {
    "light": {
        "background": "#ffffff",
        "title": "#0f172a",
        "text": "#334155",
        "muted": "#64748b",
        "grid": "#e2e8f0",
        "axis": "#94a3b8",
        "surface": "#f8fafc",
    },
    "dark": {
        "background": "#0f172a",
        "title": "#f8fafc",
        "text": "#cbd5e1",
        "muted": "#94a3b8",
        "grid": "#1e293b",
        "axis": "#475569",
        "surface": "#111c31",
    },
}
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 360


class ChartRenderError(ValueError):
    """A chart could not be rendered from its specification."""


# -- generic helpers -------------------------------------------------------


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return None
    if not math.isfinite(number):
        return None
    return number


def _fmt(value: float | None, *, digits: int = 4) -> str:
    if value is None:
        return ""
    if value == 0:
        return "0"
    magnitude = abs(value)
    if magnitude >= 1_000_000 or magnitude < 0.001:
        return f"{value:.3g}"
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _label(value: Any) -> str:
    if value is None:
        return "(missing)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _text(
    x: float,
    y: float,
    content: Any,
    *,
    fill: str,
    size: int = 12,
    anchor: str = "middle",
    weight: int | None = None,
    rotate: float | None = None,
) -> str:
    transform = f' transform="rotate({rotate:g} {x:g} {y:g})"' if rotate else ""
    weight_attr = f' font-weight="{weight}"' if weight else ""
    return (
        f'<text x="{x:g}" y="{y:g}" fill="{fill}" font-size="{size}" '
        f'font-family="{FONT}" text-anchor="{anchor}"{weight_attr}{transform}>'
        f"{_escape(content)}</text>"
    )


def _nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    if not math.isfinite(lo) or not math.isfinite(hi):
        return [0.0, 1.0]
    if lo == hi:
        delta = 1.0 if lo == 0 else abs(lo) * 0.1
        lo -= delta
        hi += delta
    span = hi - lo
    raw_step = span / max(1, target)
    power = 10.0 ** math.floor(math.log10(raw_step))
    for multiplier in (1.0, 2.0, 2.5, 5.0, 10.0):
        step = multiplier * power
        if span / step <= target:
            break
    else:  # pragma: no cover - defensive
        step = raw_step
    start = math.floor(lo / step) * step
    end = math.ceil(hi / step) * step
    count = round((end - start) / step)
    ticks = [start + index * step for index in range(count + 1)]
    if len(ticks) > 2 * target + 2:  # pragma: no cover - defensive
        ticks = ticks[:: max(1, len(ticks) // target)]
    return ticks


def _domain(values: Sequence[float], *, zero: bool = False) -> tuple[float, float]:
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        return (0.0, 1.0)
    lo, hi = min(finite), max(finite)
    if zero:
        lo, hi = min(0.0, lo), max(0.0, hi)
    if lo == hi:
        delta = 1.0 if lo == 0 else abs(lo) * 0.1
        return (lo - delta, hi + delta)
    if not zero:
        padding = (hi - lo) * 0.06
        lo -= padding
        hi += padding
    return (lo, hi)


def _map(value: float, lo: float, hi: float, start: float, end: float) -> float:
    if hi == lo:
        return (start + end) / 2.0
    ratio = (value - lo) / (hi - lo)
    return start + ratio * (end - start)


def _records(chart: ChartSpec) -> list[dict[str, Any]]:
    return [dict(row) for row in chart.data]


def _column(records: Sequence[Mapping[str, Any]], name: str | None) -> list[Any]:
    if not name:
        return []
    return [row.get(name) for row in records]


def _unique(values: Sequence[Any]) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _series(
    chart: ChartSpec, records: Sequence[Mapping[str, Any]]
) -> list[tuple[str, list[dict[str, Any]]]]:
    color = chart.encoding.color
    if not color:
        return [("", list(records))]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        grouped.setdefault(_label(row.get(color)), []).append(row)
    return list(grouped.items())


def _palette(index: int) -> str:
    return PALETTE[index % len(PALETTE)]


def _layout(
    width: int, height: int, *, title: bool = True
) -> tuple[float, float, float, float]:
    top = 46.0 if title else 16.0
    left = 62.0
    right = float(width - 20)
    bottom = float(height - 48)
    if right <= left or bottom <= top:
        raise ChartRenderError("Chart canvas is too small for its axes.")
    return left, top, right, bottom


def _frame(
    parts: list[str],
    chart: ChartSpec,
    theme: Mapping[str, str],
    width: int,
    height: int,
) -> None:
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" height="auto" role="img" aria-label="{_escape(chart.title)}">'
    )
    parts.append(f"<title>{_escape(chart.title)}</title>")
    parts.append(
        f'<rect x="0" y="0" width="{width}" height="{height}" '
        f'fill="{theme["background"]}"/>'
    )
    parts.append(
        _text(
            20,
            28,
            chart.title,
            fill=theme["title"],
            size=15,
            anchor="start",
            weight=600,
        )
    )


def _value_axis(
    parts: list[str],
    *,
    left: float,
    right: float,
    top: float,
    bottom: float,
    lo: float,
    hi: float,
    theme: Mapping[str, str],
    ticks: Sequence[float] | None = None,
) -> list[float]:
    values = list(ticks) if ticks is not None else _nice_ticks(lo, hi)
    for tick in values:
        y = _map(tick, lo, hi, bottom, top)
        parts.append(
            f'<line x1="{left:g}" y1="{y:g}" x2="{right:g}" y2="{y:g}" '
            f'stroke="{theme["grid"]}" stroke-width="1"/>'
        )
        parts.append(
            _text(
                left - 8,
                y + 4,
                _fmt(tick),
                fill=theme["muted"],
                size=11,
                anchor="end",
            )
        )
    parts.append(
        f'<line x1="{left:g}" y1="{top:g}" x2="{left:g}" y2="{bottom:g}" '
        f'stroke="{theme["axis"]}" stroke-width="1"/>'
    )
    parts.append(
        f'<line x1="{left:g}" y1="{bottom:g}" x2="{right:g}" y2="{bottom:g}" '
        f'stroke="{theme["axis"]}" stroke-width="1"/>'
    )
    return values


def _category_axis(
    parts: list[str],
    *,
    labels: Sequence[str],
    positions: Sequence[float],
    y: float,
    theme: Mapping[str, str],
    rotate: bool = False,
) -> None:
    if not labels:
        return
    stride = max(1, math.ceil(len(labels) / 14))
    for index, (label, x) in enumerate(zip(labels, positions, strict=True)):
        if index % stride:
            continue
        parts.append(
            _text(
                x,
                y,
                label,
                fill=theme["muted"],
                size=11,
                anchor="end" if rotate else "middle",
                rotate=-32 if rotate else None,
            )
        )


def _legend(
    parts: list[str],
    entries: Sequence[tuple[str, str]],
    *,
    width: int,
    theme: Mapping[str, str],
) -> None:
    if len(entries) < 2:
        return
    x = 20.0
    y = 40.0
    for index, (name, color) in enumerate(entries):
        if index and index % 4 == 0:
            x = 20.0
            y += 16
        parts.append(
            f'<rect x="{x:g}" y="{y - 8:g}" width="10" height="10" rx="2" '
            f'fill="{color}"/>'
        )
        parts.append(_text(x + 15, y + 1, name, fill=theme["muted"], size=11, anchor="start"))
        x += 18 + max(40, len(name) * 7)


def _empty(parts: list[str], width: int, height: int, theme: Mapping[str, str]) -> None:
    parts.append(
        _text(
            width / 2,
            height / 2,
            "No data to chart",
            fill=theme["muted"],
            size=13,
        )
    )


# -- marks -----------------------------------------------------------------


def _line_or_area(
    chart: ChartSpec,
    records: Sequence[Mapping[str, Any]],
    parts: list[str],
    theme: Mapping[str, str],
    width: int,
    height: int,
) -> None:
    left, top, right, bottom = _layout(width, height)
    x_field = chart.encoding.x
    y_field = chart.encoding.y
    if not y_field:
        raise ChartRenderError("A line or area chart needs a y field.")
    series = _series(chart, records)
    all_y = [
        value
        for _, rows in series
        for value in (_number(row.get(y_field)) for row in rows)
        if value is not None
    ]
    if not all_y:
        _empty(parts, width, height, theme)
        return

    raw_x = [row.get(x_field) for row in series[0][1]] if x_field else []
    numeric_x = bool(raw_x) and all(_number(value) is not None for value in raw_x)
    x_values = [
        _number(row.get(x_field)) if numeric_x else _label(row.get(x_field))
        for _, rows in series
        for row in rows
    ]
    if numeric_x:
        x_lo, x_hi = _domain([value for value in x_values if value is not None])
        x_positions = [
            _map(float(value), x_lo, x_hi, left, right)  # type: ignore[arg-type]
            for value in x_values
        ]
        x_ticks = _nice_ticks(x_lo, x_hi)
        for tick in x_ticks:
            x = _map(tick, x_lo, x_hi, left, right)
            parts.append(
                f'<line x1="{x:g}" y1="{top:g}" x2="{x:g}" y2="{bottom:g}" '
                f'stroke="{theme["grid"]}" stroke-width="1"/>'
            )
            parts.append(_text(x, bottom + 18, _fmt(tick), fill=theme["muted"], size=11))
    else:
        categories = _unique([_label(value) for value in x_values])
        count = max(1, len(categories))
        step = (right - left) / max(1, count - 1) if count > 1 else 0.0
        positions = {
            category: left + index * step for index, category in enumerate(categories)
        }
        x_positions = [positions[_label(value)] for value in x_values]
        _category_axis(
            parts,
            labels=categories,
            positions=[positions[category] for category in categories],
            y=bottom + 18,
            theme=theme,
            rotate=len(categories) > 8,
        )

    y_lo, y_hi = _domain(all_y, zero=chart.chart_type is ChartType.AREA)
    _value_axis(
        parts, left=left, right=right, top=top, bottom=bottom, lo=y_lo, hi=y_hi, theme=theme
    )

    offset = 0
    entries: list[tuple[str, str]] = []
    for index, (name, rows) in enumerate(series):
        color = _palette(index)
        entries.append((name or chart.title, color))
        points: list[tuple[float, float]] = []
        for row in rows:
            y_value = _number(row.get(y_field))
            if y_value is None or offset >= len(x_positions):
                offset += 1
                continue
            points.append((x_positions[offset], _map(y_value, y_lo, y_hi, bottom, top)))
            offset += 1
        if not points:
            continue
        if chart.chart_type is ChartType.AREA:
            baseline = _map(max(0.0, y_lo), y_lo, y_hi, bottom, top)
            path = (
                f"M {points[0][0]:g} {baseline:g} "
                + " ".join(f"L {x:g} {y:g}" for x, y in points)
                + f" L {points[-1][0]:g} {baseline:g} Z"
            )
            parts.append(
                f'<path d="{path}" fill="{color}" fill-opacity="0.18" stroke="none"/>'
            )
        path = "M " + " L ".join(f"{x:g} {y:g}" for x, y in points)
        parts.append(
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
        )
    _legend(parts, entries, width=width, theme=theme)


def _categorical_bars(
    chart: ChartSpec,
    records: Sequence[Mapping[str, Any]],
    parts: list[str],
    theme: Mapping[str, str],
    width: int,
    height: int,
    *,
    horizontal: bool,
) -> None:
    left, top, right, bottom = _layout(width, height)
    category_field = chart.encoding.y if horizontal else chart.encoding.x
    value_field = chart.encoding.x if horizontal else chart.encoding.y
    if not category_field or not value_field:
        raise ChartRenderError("A bar chart needs a category and a value field.")
    series = _series(chart, records)
    values = [
        value
        for _, rows in series
        for value in (_number(row.get(value_field)) for row in rows)
        if value is not None
    ]
    if not values:
        _empty(parts, width, height, theme)
        return
    categories = _unique(
        [_label(row.get(category_field)) for _, rows in series for row in rows]
    )
    value_lo, value_hi = _domain(values, zero=True)
    band = (bottom - top) / max(1, len(categories)) if horizontal else (
        (right - left) / max(1, len(categories))
    )
    group_width = band * 0.72
    bar_width = group_width / max(1, len(series))
    entries: list[tuple[str, str]] = []

    for index, (name, rows) in enumerate(series):
        color = _palette(index)
        entries.append((name or chart.title, color))
        for row in rows:
            value = _number(row.get(value_field))
            if value is None:
                continue
            category = _label(row.get(category_field))
            if category not in categories:
                continue
            position = categories.index(category)
            if horizontal:
                y = top + position * band + (band - group_width) / 2 + index * bar_width
                x = _map(min(0.0, value), value_lo, value_hi, left, right)
                width_value = abs(
                    _map(value, value_lo, value_hi, left, right)
                    - _map(0.0, value_lo, value_hi, left, right)
                )
                parts.append(
                    f'<rect x="{min(x, _map(0.0, value_lo, value_hi, left, right)):g}" '
                    f'y="{y:g}" width="{width_value:g}" height="{max(1.0, bar_width - 2):g}" '
                    f'rx="2" fill="{color}"><title>{_escape(category)}: '
                    f"{_fmt(value)}</title></rect>"
                )
            else:
                x = left + position * band + (band - group_width) / 2 + index * bar_width
                y = _map(max(0.0, value), value_lo, value_hi, bottom, top)
                height_value = abs(
                    _map(value, value_lo, value_hi, bottom, top)
                    - _map(0.0, value_lo, value_hi, bottom, top)
                )
                parts.append(
                    f'<rect x="{x:g}" y="{min(y, _map(0.0, value_lo, value_hi, bottom, top)):g}" '
                    f'width="{max(1.0, bar_width - 2):g}" height="{height_value:g}" '
                    f'rx="2" fill="{color}"><title>{_escape(category)}: '
                    f"{_fmt(value)}</title></rect>"
                )

    if horizontal:
        _value_axis(
            parts,
            left=left,
            right=right,
            top=top,
            bottom=bottom,
            lo=value_lo,
            hi=value_hi,
            theme=theme,
        )
        for index, category in enumerate(categories):
            y = top + index * band + band / 2
            parts.append(
                _text(
                    left - 8,
                    y + 4,
                    category,
                    fill=theme["muted"],
                    size=11,
                    anchor="end",
                )
            )
    else:
        ticks = _nice_ticks(value_lo, value_hi)
        for tick in ticks:
            y = _map(tick, value_lo, value_hi, bottom, top)
            parts.append(
                f'<line x1="{left:g}" y1="{y:g}" x2="{right:g}" y2="{y:g}" '
                f'stroke="{theme["grid"]}" stroke-width="1"/>'
            )
            parts.append(
                _text(
                    left - 8,
                    y + 4,
                    _fmt(tick),
                    fill=theme["muted"],
                    size=11,
                    anchor="end",
                )
            )
        parts.append(
            f'<line x1="{left:g}" y1="{top:g}" x2="{left:g}" y2="{bottom:g}" '
            f'stroke="{theme["axis"]}" stroke-width="1"/>'
        )
        parts.append(
            f'<line x1="{left:g}" y1="{bottom:g}" x2="{right:g}" y2="{bottom:g}" '
            f'stroke="{theme["axis"]}" stroke-width="1"/>'
        )
        positions = [left + index * band + band / 2 for index in range(len(categories))]
        _category_axis(
            parts,
            labels=categories,
            positions=positions,
            y=bottom + 18,
            theme=theme,
            rotate=len(categories) > 8,
        )
    _legend(parts, entries, width=width, theme=theme)


def _scatter(
    chart: ChartSpec,
    records: Sequence[Mapping[str, Any]],
    parts: list[str],
    theme: Mapping[str, str],
    width: int,
    height: int,
) -> None:
    left, top, right, bottom = _layout(width, height)
    x_field, y_field = chart.encoding.x, chart.encoding.y
    if not x_field or not y_field:
        raise ChartRenderError("A scatter chart needs x and y fields.")
    points = [
        (_number(row.get(x_field)), _number(row.get(y_field)), row)
        for row in records
    ]
    points = [point for point in points if point[0] is not None and point[1] is not None]
    if not points:
        _empty(parts, width, height, theme)
        return
    x_lo, x_hi = _domain([point[0] for point in points])  # type: ignore[misc]
    y_lo, y_hi = _domain([point[1] for point in points])  # type: ignore[misc]
    _value_axis(
        parts, left=left, right=right, top=top, bottom=bottom, lo=y_lo, hi=y_hi, theme=theme
    )
    for tick in _nice_ticks(x_lo, x_hi):
        x = _map(tick, x_lo, x_hi, left, right)
        parts.append(
            f'<line x1="{x:g}" y1="{top:g}" x2="{x:g}" y2="{bottom:g}" '
            f'stroke="{theme["grid"]}" stroke-width="1"/>'
        )
        parts.append(_text(x, bottom + 18, _fmt(tick), fill=theme["muted"], size=11))
    series = _series(chart, records)
    entries: list[tuple[str, str]] = []
    for index, (name, rows) in enumerate(series):
        color = _palette(index)
        entries.append((name or chart.title, color))
        for row in rows:
            x_value = _number(row.get(x_field))
            y_value = _number(row.get(y_field))
            if x_value is None or y_value is None:
                continue
            x = _map(x_value, x_lo, x_hi, left, right)
            y = _map(y_value, y_lo, y_hi, bottom, top)
            parts.append(
                f'<circle cx="{x:g}" cy="{y:g}" r="3.4" fill="{color}" '
                f'fill-opacity="0.78"><title>{_escape(_fmt(x_value))}, '
                f"{_fmt(y_value)}</title></circle>"
            )
    _legend(parts, entries, width=width, theme=theme)


def _histogram(
    chart: ChartSpec,
    records: Sequence[Mapping[str, Any]],
    parts: list[str],
    theme: Mapping[str, str],
    width: int,
    height: int,
) -> None:
    left, top, right, bottom = _layout(width, height)
    field = chart.encoding.x or chart.encoding.y
    if not field:
        raise ChartRenderError("A histogram needs a numeric field.")
    values = [
        value
        for value in (_number(row.get(field)) for row in records)
        if value is not None
    ]
    if not values:
        _empty(parts, width, height, theme)
        return
    bins = max(1, min(int(chart.options.get("bins", 10)), 100))
    lo, hi = min(values), max(values)
    if lo == hi:
        lo -= 0.5
        hi += 0.5
    step = (hi - lo) / bins
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, max(0, int((value - lo) / step)))
        counts[index] += 1
    y_lo, y_hi = 0.0, float(max(counts) or 1)
    _value_axis(
        parts, left=left, right=right, top=top, bottom=bottom, lo=y_lo, hi=y_hi, theme=theme
    )
    band = (right - left) / bins
    for index, count in enumerate(counts):
        if not count:
            continue
        x = left + index * band
        y = _map(float(count), y_lo, y_hi, bottom, top)
        parts.append(
            f'<rect x="{x + 1:g}" y="{y:g}" width="{max(1.0, band - 2):g}" '
            f'height="{bottom - y:g}" rx="2" fill="{PALETTE[0]}">'
            f"<title>{_fmt(lo + index * step)} – "
            f"{_fmt(lo + (index + 1) * step)}: {count}</title></rect>"
        )
        if index % max(1, bins // 8) == 0:
            parts.append(
                _text(
                    x + band / 2,
                    bottom + 18,
                    _fmt(lo + index * step, digits=2),
                    fill=theme["muted"],
                    size=11,
                )
            )
    parts.append(
        f'<line x1="{left:g}" y1="{top:g}" x2="{left:g}" y2="{bottom:g}" '
        f'stroke="{theme["axis"]}" stroke-width="1"/>'
    )
    parts.append(
        f'<line x1="{left:g}" y1="{bottom:g}" x2="{right:g}" y2="{bottom:g}" '
        f'stroke="{theme["axis"]}" stroke-width="1"/>'
    )


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _box(
    chart: ChartSpec,
    records: Sequence[Mapping[str, Any]],
    parts: list[str],
    theme: Mapping[str, str],
    width: int,
    height: int,
) -> None:
    left, top, right, bottom = _layout(width, height)
    category_field = chart.encoding.x
    value_field = chart.encoding.y
    if not value_field:
        raise ChartRenderError("A box plot needs a numeric y field.")
    grouped: dict[str, list[float]] = {}
    for row in records:
        value = _number(row.get(value_field))
        if value is None:
            continue
        grouped.setdefault(_label(row.get(category_field)), []).append(value)
    if not grouped:
        _empty(parts, width, height, theme)
        return
    categories = list(grouped)
    all_values = [value for values in grouped.values() for value in values]
    y_lo, y_hi = _domain(all_values, zero=min(all_values) >= 0)
    _value_axis(
        parts, left=left, right=right, top=top, bottom=bottom, lo=y_lo, hi=y_hi, theme=theme
    )
    band = (right - left) / max(1, len(categories))
    box_width = min(48.0, band * 0.55)
    for index, category in enumerate(categories):
        values = sorted(grouped[category])
        q1 = _quantile(values, 0.25)
        median = _quantile(values, 0.5)
        q3 = _quantile(values, 0.75)
        iqr = q3 - q1
        low_fence = q1 - 1.5 * iqr
        high_fence = q3 + 1.5 * iqr
        inside = [value for value in values if low_fence <= value <= high_fence]
        whisker_low = min(inside) if inside else q1
        whisker_high = max(inside) if inside else q3
        center = left + index * band + band / 2
        color = _palette(0)
        y_q1 = _map(q1, y_lo, y_hi, bottom, top)
        y_q3 = _map(q3, y_lo, y_hi, bottom, top)
        y_median = _map(median, y_lo, y_hi, bottom, top)
        y_low = _map(whisker_low, y_lo, y_hi, bottom, top)
        y_high = _map(whisker_high, y_lo, y_hi, bottom, top)
        parts.append(
            f'<line x1="{center:g}" y1="{y_high:g}" x2="{center:g}" y2="{y_low:g}" '
            f'stroke="{color}" stroke-width="1.4"/>'
        )
        for y in (y_high, y_low):
            parts.append(
                f'<line x1="{center - box_width / 4:g}" y1="{y:g}" '
                f'x2="{center + box_width / 4:g}" y2="{y:g}" stroke="{color}" stroke-width="1.4"/>'
            )
        parts.append(
            f'<rect x="{center - box_width / 2:g}" y="{y_q3:g}" width="{box_width:g}" '
            f'height="{max(1.0, y_q1 - y_q3):g}" rx="2" fill="{color}" fill-opacity="0.16" '
            f'stroke="{color}" stroke-width="1.4"/>'
        )
        parts.append(
            f'<line x1="{center - box_width / 2:g}" y1="{y_median:g}" '
            f'x2="{center + box_width / 2:g}" y2="{y_median:g}" stroke="{color}" stroke-width="2"/>'
        )
        for value in values:
            if low_fence <= value <= high_fence:
                continue
            y = _map(value, y_lo, y_hi, bottom, top)
            parts.append(
                f'<circle cx="{center:g}" cy="{y:g}" r="2.4" fill="none" '
                f'stroke="{color}" stroke-width="1.2"/>'
            )
        rotate = -32 if len(categories) > 7 else None
        parts.append(
            _text(
                center,
                bottom + 18,
                category,
                fill=theme["muted"],
                size=11,
                rotate=rotate,
                anchor="end" if rotate else "middle",
            )
        )


def _heatmap(
    chart: ChartSpec,
    records: Sequence[Mapping[str, Any]],
    parts: list[str],
    theme: Mapping[str, str],
    width: int,
    height: int,
) -> None:
    left, top, right, bottom = _layout(width, height, title=False)
    x_field = chart.encoding.x
    y_field = chart.encoding.y
    value_field = chart.encoding.color or chart.options.get("value")
    if not x_field or not y_field or not value_field:
        raise ChartRenderError("A heatmap needs x, y and colour (value) fields.")
    cells: dict[tuple[str, str], float] = {}
    for row in records:
        value = _number(row.get(value_field))
        if value is None:
            continue
        cells[(_label(row.get(x_field)), _label(row.get(y_field)))] = value
    if not cells:
        _empty(parts, width, height, theme)
        return
    x_categories = _unique([key[0] for key in cells])
    y_categories = _unique([key[1] for key in cells])
    values = list(cells.values())
    lo, hi = min(values), max(values)
    cell_width = (right - left) / max(1, len(x_categories))
    cell_height = (bottom - top) / max(1, len(y_categories))
    for (x_name, y_name), value in cells.items():
        column = x_categories.index(x_name)
        row = y_categories.index(y_name)
        ratio = 0.0 if hi == lo else (value - lo) / (hi - lo)
        color = _blend("#eff6ff", "#1d4ed8", ratio)
        x = left + column * cell_width
        y = top + row * cell_height
        parts.append(
            f'<rect x="{x:g}" y="{y:g}" width="{cell_width - 2:g}" '
            f'height="{cell_height - 2:g}" rx="2" fill="{color}">'
            f"<title>{_escape(x_name)} · {_escape(y_name)}: "
            f"{_fmt(value)}</title></rect>"
        )
    for index, category in enumerate(x_categories):
        x = left + index * cell_width + cell_width / 2
        parts.append(_text(x, bottom + 16, category, fill=theme["muted"], size=11))
    for index, category in enumerate(y_categories):
        y = top + index * cell_height + cell_height / 2 + 4
        parts.append(
            _text(left - 8, y, category, fill=theme["muted"], size=11, anchor="end")
        )
    parts.append(
        _text(
            right,
            top - 10,
            f"{_fmt(lo)} – {_fmt(hi)}",
            fill=theme["muted"],
            size=11,
            anchor="end",
        )
    )


def _blend(start: str, end: str, ratio: float) -> str:
    ratio = min(1.0, max(0.0, ratio))
    first = tuple(int(start[index : index + 2], 16) for index in (1, 3, 5))
    second = tuple(int(end[index : index + 2], 16) for index in (1, 3, 5))
    mixed = tuple(round(a + (b - a) * ratio) for a, b in zip(first, second, strict=True))
    return "#" + "".join(f"{value:02x}" for value in mixed)


def _pie(
    chart: ChartSpec,
    records: Sequence[Mapping[str, Any]],
    parts: list[str],
    theme: Mapping[str, str],
    width: int,
    height: int,
    *,
    donut: bool,
) -> None:
    label_field = chart.encoding.x or chart.encoding.color
    value_field = chart.encoding.y
    if not label_field or not value_field:
        raise ChartRenderError("A pie chart needs a category and a value field.")
    slices = [
        (_label(row.get(label_field)), _number(row.get(value_field)))
        for row in records
    ]
    slices = [(name, value) for name, value in slices if value is not None and value > 0]
    if not slices:
        _empty(parts, width, height, theme)
        return
    total = sum(value for _, value in slices)  # type: ignore[misc]
    cx = (width - 150) / 2 + 10
    cy = height / 2 + 8
    radius = min(cy - 30, cx - 30, 120)
    inner = radius * 0.56 if donut else 0.0
    angle = -math.pi / 2
    entries: list[tuple[str, str]] = []
    for index, (name, value) in enumerate(slices):
        color = _palette(index)
        entries.append((name, color))
        sweep = 2 * math.pi * float(value) / total
        end = angle + sweep
        large = 1 if sweep > math.pi else 0
        x1 = cx + radius * math.cos(angle)
        y1 = cy + radius * math.sin(angle)
        x2 = cx + radius * math.cos(end)
        y2 = cy + radius * math.sin(end)
        if inner:
            x3 = cx + inner * math.cos(end)
            y3 = cy + inner * math.sin(end)
            x4 = cx + inner * math.cos(angle)
            y4 = cy + inner * math.sin(angle)
            path = (
                f"M {x1:g} {y1:g} A {radius:g} {radius:g} 0 {large} 1 {x2:g} {y2:g} "
                f"L {x3:g} {y3:g} A {inner:g} {inner:g} 0 {large} 0 {x4:g} {y4:g} Z"
            )
        else:
            path = (
                f"M {cx:g} {cy:g} L {x1:g} {y1:g} "
                f"A {radius:g} {radius:g} 0 {large} 1 {x2:g} {y2:g} Z"
            )
        percentage = 100.0 * float(value) / total
        parts.append(
            f'<path d="{path}" fill="{color}" stroke="{theme["background"]}" '
            f'stroke-width="2"><title>{_escape(name)}: {_fmt(value)} '
            f"({percentage:.1f}%)</title></path>"
        )
        angle = end
    legend_x = width - 138
    legend_y = cy - (len(entries) * 20) / 2 + 6
    for index, (name, color) in enumerate(entries):
        y = legend_y + index * 20
        parts.append(
            f'<rect x="{legend_x:g}" y="{y - 9:g}" width="10" height="10" rx="2" fill="{color}"/>'
        )
        parts.append(_text(legend_x + 16, y, name, fill=theme["text"], size=11, anchor="start"))


def _waterfall(
    chart: ChartSpec,
    records: Sequence[Mapping[str, Any]],
    parts: list[str],
    theme: Mapping[str, str],
    width: int,
    height: int,
) -> None:
    left, top, right, bottom = _layout(width, height)
    category_field = chart.encoding.x
    value_field = chart.encoding.y
    if not value_field:
        raise ChartRenderError("A waterfall chart needs a value field.")
    steps = [
        (_label(row.get(category_field)), _number(row.get(value_field)))
        for row in records
    ]
    steps = [(name, value) for name, value in steps if value is not None]
    if not steps:
        _empty(parts, width, height, theme)
        return
    cumulative = 0.0
    levels: list[tuple[str, float, float, float | None]] = []
    for name, value in steps:  # type: ignore[misc]
        start = cumulative
        cumulative += float(value)
        levels.append((name, start, cumulative, float(value)))
    values = [0.0, cumulative] + [value for _, value in steps]  # type: ignore[misc]
    y_lo, y_hi = _domain(values, zero=True)
    _value_axis(
        parts, left=left, right=right, top=top, bottom=bottom, lo=y_lo, hi=y_hi, theme=theme
    )
    band = (right - left) / max(1, len(levels))
    bar_width = min(56.0, band * 0.62)
    for index, (name, start, end, delta) in enumerate(levels):
        center = left + index * band + band / 2
        y_start = _map(start, y_lo, y_hi, bottom, top)
        y_end = _map(end, y_lo, y_hi, bottom, top)
        y = min(y_start, y_end)
        height_value = max(1.0, abs(y_end - y_start))
        color = POSITIVE_COLOR if (delta is None or delta >= 0) else NEGATIVE_COLOR
        parts.append(
            f'<rect x="{center - bar_width / 2:g}" y="{y:g}" width="{bar_width:g}" '
            f'height="{height_value:g}" rx="2" fill="{color}" fill-opacity="0.85">'
            f"<title>{_escape(name)}: {_fmt(delta)}</title></rect>"
        )
        parts.append(
            _text(
                center,
                bottom + 18,
                name,
                fill=theme["muted"],
                size=11,
                rotate=-32 if len(levels) > 7 else None,
                anchor="end" if len(levels) > 7 else "middle",
            )
        )


# -- public entry points ---------------------------------------------------


def render_chart(
    chart: ChartSpec,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    theme: str = "light",
) -> str:
    """Render one chart specification to a standalone SVG document string."""

    if not isinstance(chart, ChartSpec):
        raise TypeError("render_chart expects a ChartSpec instance.")
    if width < 200 or height < 160:
        raise ChartRenderError("Chart width must be >= 200 and height >= 160.")
    palette = THEMES.get(theme)
    if palette is None:
        raise ChartRenderError(f"Unknown chart theme {theme!r}.")
    records = _records(chart)
    parts: list[str] = []
    _frame(parts, chart, palette, width, height)
    chart_type = chart.chart_type
    if chart_type in (ChartType.LINE, ChartType.AREA):
        _line_or_area(chart, records, parts, palette, width, height)
    elif chart_type is ChartType.COLUMN:
        _categorical_bars(chart, records, parts, palette, width, height, horizontal=False)
    elif chart_type is ChartType.BAR:
        _categorical_bars(chart, records, parts, palette, width, height, horizontal=True)
    elif chart_type is ChartType.SCATTER:
        _scatter(chart, records, parts, palette, width, height)
    elif chart_type is ChartType.HISTOGRAM:
        _histogram(chart, records, parts, palette, width, height)
    elif chart_type is ChartType.BOX:
        _box(chart, records, parts, palette, width, height)
    elif chart_type is ChartType.HEATMAP:
        _heatmap(chart, records, parts, palette, width, height)
    elif chart_type is ChartType.PIE:
        _pie(chart, records, parts, palette, width, height, donut=False)
    elif chart_type is ChartType.DONUT:
        _pie(chart, records, parts, palette, width, height, donut=True)
    elif chart_type is ChartType.WATERFALL:
        _waterfall(chart, records, parts, palette, width, height)
    else:  # pragma: no cover - ChartType is exhaustive
        raise ChartRenderError(f"Unsupported chart type {chart_type.value!r}.")
    parts.append("</svg>")
    return "".join(parts)


def render_charts(
    charts: Sequence[ChartSpec],
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    theme: str = "light",
) -> list[str]:
    """Render several chart specifications, preserving order."""

    return [
        render_chart(chart, width=width, height=height, theme=theme) for chart in charts
    ]


__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "PALETTE",
    "ChartRenderError",
    "render_chart",
    "render_charts",
]
