"""Tests for dashboard definitions, storage and rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_studio.dashboard import (
    Dashboard,
    DashboardError,
    DashboardRenderer,
    DashboardSlicer,
    DashboardStore,
    DashboardTile,
    render_dashboard_html,
    slugify,
    validate_dashboard,
    write_dashboard_report,
)
from data_studio.io.sniff import CsvOptions
from data_studio.project.store import Project
from data_studio.semantic import (
    QueryFilter,
    Relationship,
    SemanticDimension,
    SemanticEngine,
    SemanticMeasure,
    SemanticModel,
    SemanticTable,
)

CSV_TEXT = "region,amount,units\nNorth,10,1\nSouth,20,2\nNorth,15,3\nEast,5,1\n"
REGIONS_TEXT = "region,manager,weight\nNorth,Ana,1\nSouth,Bo,2\nEast,Cyd,3\n"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    instance = Project.create(tmp_path / "Demo", "Dashboards")
    sales = tmp_path / "sales.csv"
    sales.write_text(CSV_TEXT, encoding="utf-8")
    instance.add_dataset(
        sales, dataset_id="sales", csv_options=CsvOptions(header=True)
    )
    regions = tmp_path / "regions.csv"
    regions.write_text(REGIONS_TEXT, encoding="utf-8")
    instance.add_dataset(
        regions, dataset_id="regions", csv_options=CsvOptions(header=True)
    )
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def model() -> SemanticModel:
    return SemanticModel(
        name="Sales",
        tables=(
            SemanticTable("sales", "sales"),
            SemanticTable("regions", "regions"),
        ),
        relationships=(Relationship("sales", "region", "regions", "region"),),
        dimensions=(
            SemanticDimension("region", "sales", "region"),
            SemanticDimension("manager", "regions", "manager"),
        ),
        measures=(
            SemanticMeasure("total_amount", "sum(amount)", "Total amount", "currency"),
            SemanticMeasure("units", "sum(units)", "Units", "integer"),
        ),
        default_table="sales",
    )


def sample_dashboard(dashboard_id: str = "sales-dashboard") -> Dashboard:
    return Dashboard(
        id=dashboard_id,
        title="Sales dashboard",
        description="Revenue overview",
        tiles=(
            DashboardTile(
                "total", "Total revenue", kind="kpi", measures=("total_amount",)
            ),
            DashboardTile(
                "by-manager",
                "Revenue by manager",
                kind="chart",
                dimensions=("manager",),
                measures=("total_amount",),
                chart="bar",
            ),
            DashboardTile(
                "detail",
                "Region detail",
                kind="table",
                dimensions=("region",),
                measures=("total_amount", "units"),
                order_by=("total_amount desc",),
            ),
        ),
        slicers=(DashboardSlicer("region", "region"),),
    )


def test_slugify() -> None:
    assert slugify("Sales Dashboard") == "sales-dashboard"
    assert slugify("  ") == "dashboard"


def test_tile_validation() -> None:
    with pytest.raises(DashboardError):
        DashboardTile("x", "KPI", kind="kpi", measures=("a", "b"))
    with pytest.raises(DashboardError):
        DashboardTile("x", "KPI", kind="kpi", dimensions=("d",), measures=("a",))
    with pytest.raises(DashboardError):
        DashboardTile("x", "Chart", kind="chart", measures=("a",))
    with pytest.raises(DashboardError):
        DashboardTile("x", "Chart", kind="chart", chart="nope", dimensions=("d",), measures=("a",))
    with pytest.raises(DashboardError):
        DashboardTile("Bad ID", "Chart", dimensions=("d",), measures=("a",))


def test_dashboard_validation_against_model(model: SemanticModel) -> None:
    dashboard = sample_dashboard()
    assert validate_dashboard(dashboard, model) == []
    broken = Dashboard(
        id="broken",
        title="Broken",
        tiles=(DashboardTile("k", "K", kind="kpi", measures=("nope",)),),
        slicers=(DashboardSlicer("s", "nope"),),
    )
    problems = validate_dashboard(broken, model)
    assert any("unknown dimension" in item for item in problems)
    assert any("unknown measure" in item for item in problems)


def test_store_round_trip_and_unique_ids(project: Project, model: SemanticModel) -> None:
    store = DashboardStore(project)
    first = store.create(title="Sales Dashboard", tiles=sample_dashboard().tiles, model=model)
    assert first.id == "sales-dashboard"
    assert first.created_at
    second = store.create(title="Sales Dashboard", tiles=(), model=model)
    assert second.id == "sales-dashboard-2"
    assert {item.id for item in store.list()} == {
        "sales-dashboard",
        "sales-dashboard-2",
    }
    assert store.get(first.id).title == "Sales Dashboard"
    store.remove(first.id)
    assert [item.id for item in store.list()] == ["sales-dashboard-2"]


def test_store_rejects_invalid_references(project: Project, model: SemanticModel) -> None:
    store = DashboardStore(project)
    broken = Dashboard(
        id="broken",
        title="Broken",
        tiles=(DashboardTile("k", "K", kind="kpi", measures=("nope",)),),
    )
    with pytest.raises(DashboardError):
        store.save(broken, model=model)


def test_render_dashboard_with_slicer(project: Project, model: SemanticModel) -> None:
    dashboard = sample_dashboard()
    engine = SemanticEngine(project, model, use_pipeline=False)
    view = DashboardRenderer(engine).render(
        dashboard, selections={"region": ["North"]}
    )
    assert view.filters
    kpi = view.tiles[0]
    assert kpi.result is not None
    assert kpi.result.rows == ({"total_amount": 25},)
    chart = view.tiles[1]
    assert chart.svg is not None and chart.svg.startswith("<svg")
    table = view.tiles[2]
    assert [row["region"] for row in table.result.rows] == ["North"]
    assert view.model == "Sales"


def test_render_reports_tile_errors(project: Project, model: SemanticModel) -> None:
    dashboard = Dashboard(
        id="broken",
        title="Broken",
        tiles=(
            DashboardTile(
                "k", "K", kind="kpi", measures=("total_amount",),
                filters=(QueryFilter("bad_field", "=", 1),),
            ),
        ),
    )
    view = DashboardRenderer(
        SemanticEngine(project, model, use_pipeline=False)
    ).render(dashboard)
    assert view.tiles[0].error


def test_report_is_self_contained(project: Project, model: SemanticModel) -> None:
    engine = SemanticEngine(project, model, use_pipeline=False)
    view = DashboardRenderer(engine).render(sample_dashboard())
    html = render_dashboard_html(view)
    assert "<!doctype html>" in html
    assert "<svg" in html
    assert "Sales dashboard" in html
    assert "<script" not in html
    assert "src=" not in html
    path = write_dashboard_report(view, project.root / "reports" / "dash.html")
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == html
