"""Self-contained HTML export for dashboards.

The report embeds the same inline SVG charts and result tables that the workspace shows.
It has no scripts, fonts or external assets, so one file can be archived or shared.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from ..version import __version__
from .render import DashboardTileView, DashboardView

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #f6f7f9; color: #18202f;
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
header { padding: 26px 34px 14px; }
h1 { margin: 0 0 4px; font-size: 24px; letter-spacing: -.02em; }
.sub { color: #5b6474; font-size: 13px; }
.meta { color: #77808f; font-size: 11.5px; margin-top: 6px; }
main { padding: 10px 34px 40px; }
.slicers { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0 18px; }
.slicer { background: #eef2f8; border: 1px solid #dbe2ec; border-radius: 999px;
  padding: 3px 10px; font-size: 11.5px; color: #334155; }
.grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 14px; }
.tile { background: #fff; border: 1px solid #e2e6ed; border-radius: 12px;
  padding: 14px 16px; min-width: 0; box-shadow: 0 1px 2px rgba(15,23,42,.04); }
.tile h2 { margin: 0; font-size: 14px; }
.tile .tile-sub { margin: 2px 0 8px; color: #6b7280; font-size: 11.5px; }
.kpis { display: flex; flex-wrap: wrap; gap: 18px; margin-top: 10px; }
.kpi { min-width: 110px; }
.kpi .label { color: #6b7280; font-size: 11px; text-transform: uppercase;
  letter-spacing: .05em; }
.kpi .value { font-size: 24px; font-weight: 700; letter-spacing: -.03em;
  font-variant-numeric: tabular-nums; }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #eef1f5; }
th { color: #4b5563; font-weight: 600; background: #fafbfc; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.table-wrap { overflow: auto; max-height: 340px; margin-top: 8px; }
.error { color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca;
  border-radius: 8px; padding: 10px 12px; font-size: 12px; }
.empty { color: #6b7280; font-size: 12px; }
svg { max-width: 100%; height: auto; }
footer { color: #8a93a2; font-size: 11px; padding: 0 34px 28px; }
@media (max-width: 760px) { .grid { grid-template-columns: 1fr; } }
"""


def _text(value: Any) -> str:
    return escape("" if value is None else str(value))


def _format(value: Any, fmt: str = "number") -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        try:
            if fmt == "integer":
                return f"{int(value):,}"
            if fmt == "currency":
                return f"${value:,.2f}"
            if fmt == "percent":
                return f"{value:,.2f}%"
            if fmt == "decimal":
                return f"{value:,.2f}"
            if isinstance(value, float):
                return f"{value:,.4f}".rstrip("0").rstrip(".")
            return f"{value:,}"
        except (TypeError, ValueError):
            return _text(value)
    return _text(value)


def _tile_html(tile_view: DashboardTileView) -> str:
    tile = tile_view.tile
    span = max(1, min(4, int(tile.width))) * 3
    head = (
        f'<h2>{_text(tile.title)}</h2>'
        + (f'<p class="tile-sub">{_text(tile.subtitle)}</p>' if tile.subtitle else "")
    )
    if tile_view.error:
        body = f'<div class="error">{_text(tile_view.error)}</div>'
        return f'<section class="tile" style="grid-column: span {span}">{head}{body}</section>'
    result = tile_view.result
    if result is None:
        return f'<section class="tile" style="grid-column: span {span}">{head}' \
               '<p class="empty">No result.</p></section>'

    if tile.kind == "kpi":
        cards = []
        row = result.rows[0] if result.rows else {}
        for column in result.columns:
            cards.append(
                '<div class="kpi">'
                f'<div class="label">{_text(column.title)}</div>'
                f'<div class="value">{_format(row.get(column.name), column.format)}</div>'
                "</div>"
            )
        body = '<div class="kpis">' + "".join(cards) + "</div>"
    elif tile.kind == "chart" and tile_view.svg:
        body = tile_view.svg
    else:
        header = "".join(f"<th>{_text(column.title)}</th>" for column in result.columns)
        rows = []
        for row in result.rows:
            cells = []
            for column in result.columns:
                value = row.get(column.name)
                numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
                cells.append(
                    f'<td class="{"num" if numeric else ""}">'
                    f"{_format(value, column.format)}</td>"
                )
            rows.append("<tr>" + "".join(cells) + "</tr>")
        if not rows:
            rows.append(
                f'<tr><td colspan="{max(1, len(result.columns))}" class="empty">'
                "No rows.</td></tr>"
            )
        body = (
            '<div class="table-wrap"><table><thead><tr>'
            f"{header}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )
    return (
        f'<section class="tile" style="grid-column: span {span}">{head}{body}</section>'
    )


def render_dashboard_html(
    view: DashboardView,
    *,
    title: str | None = None,
    theme: str = "light",
) -> str:
    if not isinstance(view, DashboardView):
        raise TypeError("render_dashboard_html expects a DashboardView.")
    dashboard = view.dashboard
    heading = title or dashboard.title
    slicers = "".join(
        f'<span class="slicer">{_text(item.field)}: '
        f'{_text(", ".join(str(value) for value in item.value))}</span>'
        for item in view.filters
    )
    slicer_html = f'<div class="slicers">{slicers}</div>' if slicers else ""
    description = (
        f'<p class="sub">{_text(dashboard.description)}</p>'
        if dashboard.description
        else ""
    )
    tiles = "".join(_tile_html(tile) for tile in view.tiles)
    generated = view.generated_at or datetime.now(UTC).isoformat(timespec="seconds")
    css = _CSS
    if theme == "dark":
        css += """
body { background:#0f172a; color:#e2e8f0; }
header, main, footer { color: inherit; }
.sub, .meta, .tile .tile-sub, .kpi .label, .empty { color:#94a3b8; }
.tile { background:#111c31; border-color:#243149; }
th { background:#0f1a2e; color:#cbd5e1; }
th, td { border-color:#243149; }
.slicer { background:#17233b; border-color:#243149; color:#cbd5e1; }
"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_text(heading)}</title><style>{css}</style></head>
<body>
<header>
  <h1>{_text(heading)}</h1>
  {description}
  <div class="meta">Model {_text(view.model)} · {len(view.tiles)} tile(s) ·
    generated {_text(generated)}</div>
  {slicer_html}
</header>
<main><div class="grid">{tiles}</div></main>
<footer>Data Studio {_text(__version__)} · self-contained report</footer>
</body></html>
"""


def write_dashboard_report(
    view: DashboardView,
    destination: str | Path,
    *,
    title: str | None = None,
    theme: str = "light",
) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_dashboard_html(view, title=title, theme=theme), encoding="utf-8"
    )
    return path


__all__ = ["render_dashboard_html", "write_dashboard_report"]
