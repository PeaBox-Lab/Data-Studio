"""Self-contained HTML report export.

A report is another renderer for the same typed `AnalysisResult` objects used by the
workspace, the API and the CLI. It embeds inline SVG charts, deterministic tables, the
metrics grid, diagnostics, narrative and provenance, so a single ``.html`` file can be
shared or archived without network access, scripts or a charting library.

The exporter is intentionally conservative: everything from the data is HTML-escaped, the
only trusted markup is the SVG emitted by `data_studio.kernel.chart_render`, and no asset
is ever loaded from an external URL.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from ..kernel.chart_render import DEFAULT_HEIGHT, DEFAULT_WIDTH, render_chart
from ..kernel.results import AnalysisResult, ResultTable, Severity
from ..kernel.spec import ChartSpec
from ..version import __version__

_REPORT_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f4f6fb;
  --surface: #ffffff;
  --surface-alt: #f8fafc;
  --border: #e2e8f0;
  --text: #0f172a;
  --muted: #64748b;
  --accent: #2563eb;
  --info: #0369a1;
  --warning: #b45309;
  --error: #be123c;
  --radius: 14px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0b1220;
    --surface: #111c31;
    --surface-alt: #16233c;
    --border: #1e293b;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #60a5fa;
  }
}
body.theme-dark {
  --bg: #0b1220;
  --surface: #111c31;
  --surface-alt: #16233c;
  --border: #1e293b;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #60a5fa;
  --info: #38bdf8;
  --warning: #fbbf24;
  --error: #fb7185;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 40px 20px 64px;
  background: var(--bg);
  color: var(--text);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  line-height: 1.55;
}
.report { max-width: 1080px; margin: 0 auto; }
.report-header {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 32px 36px;
  margin-bottom: 24px;
}
.eyebrow {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--accent);
}
.report-header h1 { margin: 0 0 8px; font-size: 30px; line-height: 1.2; }
.subtitle { margin: 0 0 16px; color: var(--muted); font-size: 16px; }
.meta { margin: 0; color: var(--muted); font-size: 13px; }
.analysis {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 28px 32px;
  margin-bottom: 24px;
}
.analysis-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 4px;
}
.analysis-head h2 { margin: 0; font-size: 22px; }
.badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--surface-alt);
  border: 1px solid var(--border);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}
.narrative { margin: 12px 0 20px; font-size: 15px; }
.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
  margin: 0 0 24px;
}
.metric {
  background: var(--surface-alt);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
}
.metric .name {
  display: block;
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .04em;
  text-transform: uppercase;
}
.metric .value { display: block; margin-top: 3px; font-size: 18px; font-weight: 650; }
h3.section {
  margin: 26px 0 12px;
  font-size: 13px;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--muted);
}
.charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 18px;
}
.chart {
  margin: 0;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface-alt);
}
.chart svg { display: block; width: 100%; height: auto; }
.chart figcaption { margin-top: 8px; color: var(--muted); font-size: 12px; }
.diagnostics { margin: 0; padding: 0; list-style: none; display: grid; gap: 8px; }
.diagnostic {
  border-left: 4px solid var(--info);
  background: var(--surface-alt);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 14px;
}
.diagnostic.warning { border-left-color: var(--warning); }
.diagnostic.error { border-left-color: var(--error); }
.diagnostic .code {
  display: block;
  color: var(--muted);
  font-size: 11px;
  letter-spacing: .04em;
  text-transform: uppercase;
}
.table-wrap { overflow-x: auto; margin-bottom: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td {
  text-align: left;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
th {
  background: var(--surface-alt);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: .04em;
  color: var(--muted);
}
tbody tr:last-child td { border-bottom: none; }
.note { margin: 0 0 18px; color: var(--muted); font-size: 12px; }
details { margin-top: 18px; }
summary { cursor: pointer; color: var(--muted); font-size: 13px; font-weight: 600; }
pre {
  overflow-x: auto;
  background: var(--surface-alt);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px;
  font-size: 12px;
  line-height: 1.45;
}
.provenance { margin: 12px 0 0; padding: 0; list-style: none; font-size: 13px; }
.provenance li { display: flex; gap: 8px; padding: 3px 0; }
.provenance .key { color: var(--muted); min-width: 160px; }
.provenance .val { word-break: break-all; }
.report-footer { color: var(--muted); font-size: 12px; text-align: center; margin-top: 32px; }
@media print {
  body { background: #ffffff; padding: 0; }
  .analysis, .report-header { border: none; box-shadow: none; break-inside: avoid; }
}
"""


def _text(value: Any) -> str:
    """Escape any value for HTML text content."""

    return escape(str(value), quote=True)


def _pretty_name(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def _format_scalar(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _as_results(results: AnalysisResult | Sequence[AnalysisResult]) -> list[AnalysisResult]:
    if isinstance(results, AnalysisResult):
        items = [results]
    elif isinstance(results, Sequence) and not isinstance(results, (str, bytes)):
        items = list(results)
    else:
        raise TypeError("results must be an AnalysisResult or a sequence of them.")
    if not items:
        raise ValueError("A report needs at least one analysis result.")
    for item in items:
        if not isinstance(item, AnalysisResult):
            raise TypeError("Every report result must be an AnalysisResult.")
    return items


def _render_metrics(result: AnalysisResult) -> str:
    if not result.metrics:
        return ""
    cards = "".join(
        f'<div class="metric"><span class="name">{_text(_pretty_name(name))}</span>'
        f'<span class="value">{_text(_format_scalar(value))}</span></div>'
        for name, value in result.metrics.items()
    )
    return f'<div class="metrics">{cards}</div>'


def _render_charts(
    charts: Sequence[ChartSpec], *, width: int, height: int, theme: str
) -> str:
    if not charts:
        return ""
    figures = []
    for chart in charts:
        svg = render_chart(chart, width=width, height=height, theme=theme)
        figures.append(
            f'<figure class="chart">{svg}'
            f"<figcaption>{_text(chart.title)}</figcaption></figure>"
        )
    return (
        '<h3 class="section">Charts</h3>'
        f'<div class="charts">{"".join(figures)}</div>'
    )


def _render_diagnostics(result: AnalysisResult) -> str:
    if not result.diagnostics:
        return ""
    items = []
    for diagnostic in result.diagnostics:
        severity = (
            diagnostic.severity.value
            if isinstance(diagnostic.severity, Severity)
            else str(diagnostic.severity)
        )
        items.append(
            f'<li class="diagnostic {_text(severity)}">'
            f'<span class="code">{_text(diagnostic.code)}</span>'
            f"{_text(diagnostic.message)}</li>"
        )
    return (
        '<h3 class="section">Diagnostics</h3>'
        f'<ul class="diagnostics">{"".join(items)}</ul>'
    )


def _render_table(table: ResultTable) -> str:
    header = "".join(f"<th>{_text(column)}</th>" for column in table.columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_text(_format_scalar(cell))}</td>" for cell in row) + "</tr>"
        for row in table.rows
    )
    note = f'<p class="note">{_text(table.note)}</p>' if table.note else ""
    return (
        f'<h3 class="section">{_text(table.title)}</h3>'
        f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>{note}"
    )


def _render_provenance(result: AnalysisResult) -> str:
    provenance = result.provenance
    entries = (
        ("Result ID", result.result_id),
        ("Analysis", result.kind),
        ("Dataset", provenance.dataset),
        ("Fingerprint", provenance.dataset_fingerprint),
        ("Steps", ", ".join(provenance.step_ids) or "(none)"),
        ("Engine", provenance.engine),
        ("Parameters hash", provenance.parameters_hash),
        ("Created", provenance.created_at),
        ("Data Studio", provenance.app_version),
        ("Schema version", result.schema_version),
    )
    rows = "".join(
        f'<li><span class="key">{_text(key)}</span>'
        f'<span class="val">{_text(value)}</span></li>'
        for key, value in entries
        if value not in (None, "")
    )
    params = result.params or {}
    params_rows = "".join(
        f"<tr><td>{_text(key)}</td><td>{_text(_format_scalar(value))}</td></tr>"
        for key, value in params.items()
    )
    params_html = (
        f'<div class="table-wrap"><table><thead><tr><th>Parameter</th><th>Value</th>'
        f"</tr></thead><tbody>{params_rows}</tbody></table></div>"
        if params_rows
        else ""
    )
    return (
        "<details><summary>Provenance and parameters</summary>"
        f'<ul class="provenance">{rows}</ul>{params_html}</details>'
    )


def _render_analysis(
    result: AnalysisResult, *, width: int, height: int, theme: str
) -> str:
    narrative = f'<p class="narrative">{_text(result.narrative)}</p>' if result.narrative else ""
    tables = "".join(_render_table(table) for table in result.tables)
    return (
        '<section class="analysis">'
        f'<div class="analysis-head"><h2>{_text(result.title)}</h2>'
        f'<span class="badge">{_text(result.kind)}</span></div>'
        f"{narrative}"
        f"{_render_metrics(result)}"
        f"{_render_charts(result.charts, width=width, height=height, theme=theme)}"
        f"{_render_diagnostics(result)}"
        f"{tables}"
        f"{_render_provenance(result)}"
        "</section>"
    )


def render_html_report(
    results: AnalysisResult | Sequence[AnalysisResult],
    *,
    title: str = "Data Studio report",
    subtitle: str | None = None,
    generated_at: str | None = None,
    theme: str = "light",
    chart_width: int = DEFAULT_WIDTH,
    chart_height: int = DEFAULT_HEIGHT,
) -> str:
    """Render one or more analysis results as a standalone HTML document."""

    items = _as_results(results)
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Report title must not be empty.")
    if theme not in ("light", "dark"):
        raise ValueError("Report theme must be 'light' or 'dark'.")
    timestamp = generated_at or datetime.now(UTC).isoformat(timespec="seconds")
    subtitle_text = subtitle or " · ".join(item.title for item in items)
    meta = f"Generated {timestamp} · Data Studio {__version__}"
    content = "".join(
        _render_analysis(
            item, width=chart_width, height=chart_height, theme=theme
        )
        for item in items
    )
    body_class = "theme-dark" if theme == "dark" else "theme-light"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_text(title)}</title><style>{_REPORT_CSS}</style></head>"
        f'<body class="{body_class}"><div class="report">'
        '<header class="report-header"><p class="eyebrow">Data Studio report</p>'
        f"<h1>{_text(title)}</h1>"
        f'<p class="subtitle">{_text(subtitle_text)}</p>'
        f'<p class="meta">{_text(meta)}</p></header>'
        f"<main>{content}</main>"
        f'<p class="report-footer">Generated by Data Studio {_text(__version__)}. '
        "All charts and tables are embedded in this file.</p>"
        "</div></body></html>"
    )


def write_html_report(
    results: AnalysisResult | Sequence[AnalysisResult],
    destination: str | Path,
    *,
    title: str = "Data Studio report",
    subtitle: str | None = None,
    generated_at: str | None = None,
    theme: str = "light",
    chart_width: int = DEFAULT_WIDTH,
    chart_height: int = DEFAULT_HEIGHT,
) -> Path:
    """Write a standalone HTML report and return the path that was written."""

    path = Path(destination)
    if path.exists() and path.is_dir():
        raise IsADirectoryError(f"{path} is a directory; provide a file path.")
    path.parent.mkdir(parents=True, exist_ok=True)
    html = render_html_report(
        results,
        title=title,
        subtitle=subtitle,
        generated_at=generated_at,
        theme=theme,
        chart_width=chart_width,
        chart_height=chart_height,
    )
    path.write_text(html, encoding="utf-8")
    return path


__all__ = ["render_html_report", "write_html_report"]
