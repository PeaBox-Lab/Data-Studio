"""Tests for chart and dashboard specifications."""

from __future__ import annotations

import pytest

from data_studio.kernel.spec import (
    ChartSpec,
    ChartType,
    DashboardFilter,
    DashboardSpec,
    DashboardTile,
    Encoding,
    FilterKind,
    TileKind,
    validate_chart_fields,
)


def test_encoding_normalises_and_lists_fields() -> None:
    encoding = Encoding(x=" region ", y="amount", tooltip=("region", "amount", "region"))
    assert encoding.x == "region"
    assert encoding.fields() == ("region", "amount")
    assert encoding.to_dict()["tooltip"] == ["region", "amount", "region"]


def test_encoding_rejects_blank_tooltip() -> None:
    with pytest.raises(ValueError):
        Encoding(tooltip=("",))


def test_chart_spec_roundtrip() -> None:
    chart = ChartSpec(
        chart_type=ChartType.LINE,
        title="Revenue over time",
        encoding=Encoding(x="month", y="revenue"),
        data=(
            {"month": "2024-01", "revenue": 12.5},
            {"month": "2024-02", "revenue": 15.0},
        ),
        options={"smooth": True},
        transforms=({"filter": "amount > 0"},),
    )
    restored = ChartSpec.from_dict(chart.to_dict())
    assert restored == chart
    assert restored.chart_type is ChartType.LINE
    assert restored.to_dict()["data"] == chart.to_dict()["data"]


def test_chart_spec_coerces_and_validates_data() -> None:
    chart = ChartSpec(
        chart_type=ChartType.BAR,
        title="Coerced",
        data=({"region": "North", "amount": 10},),
    )
    assert chart.data == ({"region": "North", "amount": 10},)

    with pytest.raises(TypeError):
        ChartSpec(chart_type=ChartType.BAR, title="x", data=(object(),))
    with pytest.raises(TypeError):
        ChartSpec(chart_type=ChartType.BAR, title="x", data=({"a": object()},))
    with pytest.raises(ValueError):
        ChartSpec(chart_type=ChartType.BAR, title="x", data=({"": 1},))


def test_chart_spec_validation() -> None:
    with pytest.raises(ValueError):
        ChartSpec(chart_type=ChartType.BAR, title="  ")
    with pytest.raises(ValueError):
        ChartSpec(chart_type="not-a-chart", title="x")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ChartSpec(chart_type=ChartType.BAR, title="x", encoding="y")  # type: ignore[arg-type]


def test_validate_chart_fields() -> None:
    chart = ChartSpec(
        chart_type=ChartType.BAR,
        title="x",
        encoding=Encoding(x="region", y="amount"),
    )
    assert validate_chart_fields(chart, ["region", "amount"]) == []
    assert validate_chart_fields(chart, ["region"]) == ["amount"]


def test_dashboard_filter() -> None:
    item = DashboardFilter(" region ", FilterKind.CATEGORICAL, default="North")
    assert item.column == "region"
    assert DashboardFilter.from_dict(item.to_dict()) == item
    with pytest.raises(ValueError):
        DashboardFilter("  ")
    with pytest.raises(ValueError):
        DashboardFilter("region", "invalid")  # type: ignore[arg-type]


def test_dashboard_tile_kind_requirements() -> None:
    with pytest.raises(ValueError, match="chart tile"):
        DashboardTile("t1", TileKind.CHART, "Title")
    with pytest.raises(ValueError, match="KPI tile"):
        DashboardTile("t1", TileKind.KPI, "Title")
    with pytest.raises(ValueError, match="table tile"):
        DashboardTile("t1", TileKind.TABLE, "Title")
    with pytest.raises(ValueError, match="text tile"):
        DashboardTile("t1", TileKind.TEXT, "Title")
    with pytest.raises(ValueError, match="Tile id"):
        DashboardTile("bad id!", TileKind.TEXT, "Title", text="hello")
    with pytest.raises(ValueError, match="at least 1"):
        DashboardTile("t1", TileKind.TEXT, "Title", text="hello", w=0)

    chart = ChartSpec(ChartType.BAR, "Chart", Encoding(x="region", y="amount"))
    tile = DashboardTile("t1", TileKind.CHART, "Title", chart=chart, x=1, y=2, w=3, h=2)
    assert DashboardTile.from_dict(tile.to_dict()) == tile


def test_dashboard_spec_constraints_and_roundtrip() -> None:
    chart = ChartSpec(ChartType.BAR, "Chart", Encoding(x="region", y="amount"))
    tile = DashboardTile("t1", TileKind.CHART, "Sales", chart=chart)
    dashboard = DashboardSpec(
        name="Executive",
        dataset="sales",
        tiles=(tile,),
        filters=(DashboardFilter("region"),),
    )
    assert dashboard.tile("t1") == tile
    assert DashboardSpec.from_dict(dashboard.to_dict()) == dashboard
    with pytest.raises(KeyError):
        dashboard.tile("unknown")


def test_dashboard_spec_rejects_duplicates_and_blank_names() -> None:
    tile = DashboardTile("t1", TileKind.TEXT, "Note", text="hello")
    with pytest.raises(ValueError, match="unique"):
        DashboardSpec("D", "sales", tiles=(tile, tile))
    with pytest.raises(ValueError):
        DashboardSpec("  ", "sales")
    with pytest.raises(ValueError, match="distinct"):
        DashboardSpec(
            "D",
            "sales",
            filters=(DashboardFilter("region"), DashboardFilter("region")),
        )
