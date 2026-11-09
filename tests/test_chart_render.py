"""Tests for the dependency-free SVG chart renderer."""

from __future__ import annotations

import pytest

from data_studio.kernel.chart_render import (
    ChartRenderError,
    render_chart,
    render_charts,
)
from data_studio.kernel.spec import ChartSpec, ChartType, Encoding

NUMERIC_ROWS = tuple(
    {"x": index, "y": index * 1.5 + 2.0} for index in range(1, 9)
)


def chart(
    chart_type: ChartType,
    rows=NUMERIC_ROWS,
    *,
    x: str = "x",
    y: str = "y",
    color: str | None = None,
    options: dict | None = None,
    title: str = "Example chart",
) -> ChartSpec:
    return ChartSpec(
        chart_type=chart_type,
        title=title,
        encoding=Encoding(x=x, y=y, color=color),
        data=list(rows),
        options=options or {},
    )


@pytest.mark.parametrize("chart_type", list(ChartType))
def test_every_chart_type_renders_standalone_svg(chart_type: ChartType) -> None:
    if chart_type is ChartType.HEATMAP:
        rows = tuple(
            {"x": f"c{index % 3}", "y": f"r{index // 3}", "z": index + 1.0}
            for index in range(9)
        )
        spec = chart(chart_type, rows, x="x", y="y", color="z")
    elif chart_type in (ChartType.PIE, ChartType.DONUT):
        rows = tuple({"x": f"part{index}", "y": index + 1.0} for index in range(4))
        spec = chart(chart_type, rows, x="x", y="y")
    elif chart_type is ChartType.BOX:
        rows = tuple(
            {"x": f"group{index % 2}", "y": float(index)} for index in range(12)
        )
        spec = chart(chart_type, rows, x="x", y="y")
    else:
        spec = chart(chart_type)

    svg = render_chart(spec)
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "<script" not in svg
    assert "http://" not in svg or "http://www.w3.org/2000/svg" in svg
    assert 'role="img"' in svg
    assert 'height="auto"' not in svg
    assert 'style="height:auto;display:block"' in svg


def test_render_is_deterministic() -> None:
    spec = chart(ChartType.LINE)
    assert render_chart(spec) == render_chart(spec)


def test_dark_theme_changes_background() -> None:
    spec = chart(ChartType.COLUMN)
    light = render_chart(spec, theme="light")
    dark = render_chart(spec, theme="dark")
    assert "#ffffff" in light
    assert "#0f172a" in dark
    assert light != dark


def test_titles_and_labels_are_escaped() -> None:
    spec = chart(
        ChartType.COLUMN,
        rows=(
            {"x": "<script>alert(1)</script>", "y": 5.0},
            {"x": "safe", "y": 7.0},
        ),
        x="x",
        y="y",
        title='Revenue & <margin> "quote"',
    )
    svg = render_chart(spec)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert "&amp;" in svg
    assert "&quot;" in svg


def test_legend_is_rendered_for_grouped_series() -> None:
    rows = tuple(
        {"x": f"period{index}", "series": name, "y": float(index + offset)}
        for name, offset in (("alpha", 1), ("beta", 5))
        for index in range(4)
    )
    spec = chart(ChartType.LINE, rows, x="x", y="y", color="series")
    svg = render_chart(spec)
    assert "alpha" in svg
    assert "beta" in svg


def test_empty_data_draws_placeholder() -> None:
    spec = chart(ChartType.LINE, rows=())
    svg = render_chart(spec)
    assert "No data to chart" in svg


def test_non_numeric_values_are_skipped() -> None:
    rows = (
        {"x": "a", "y": 3.0},
        {"x": "b", "y": "not-a-number"},
        {"x": "c", "y": None},
    )
    svg = render_chart(chart(ChartType.COLUMN, rows))
    assert "No data to chart" not in svg
    assert "a" in svg


def test_render_charts_preserves_order() -> None:
    specs = [
        chart(ChartType.LINE, title="First"),
        chart(ChartType.COLUMN, title="Second"),
    ]
    rendered = render_charts(specs)
    assert len(rendered) == 2
    assert "First" in rendered[0]
    assert "Second" in rendered[1]


def test_render_rejects_invalid_arguments() -> None:
    spec = chart(ChartType.LINE)
    with pytest.raises(TypeError):
        render_chart("not-a-chart")  # type: ignore[arg-type]
    with pytest.raises(ChartRenderError):
        render_chart(spec, width=100)
    with pytest.raises(ChartRenderError):
        render_chart(spec, height=100)
    with pytest.raises(ChartRenderError):
        render_chart(spec, theme="neon")


def test_missing_required_encoding_raises() -> None:
    with pytest.raises(ChartRenderError):
        render_chart(
            ChartSpec(ChartType.LINE, "No y", Encoding(x="x"), data=NUMERIC_ROWS)
        )
    with pytest.raises(ChartRenderError):
        render_chart(
            ChartSpec(ChartType.SCATTER, "No fields", Encoding(), data=NUMERIC_ROWS)
        )
