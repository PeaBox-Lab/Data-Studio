"""Tests for dashboard what-if parameters and parameter substitution."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_studio.dashboard import (
    Dashboard,
    DashboardError,
    DashboardParameter,
    DashboardRenderer,
    DashboardStore,
    DashboardTile,
)
from data_studio.io.sniff import CsvOptions
from data_studio.project.store import Project
from data_studio.semantic import (
    QueryFilter,
    SemanticDimension,
    SemanticEngine,
    SemanticMeasure,
    SemanticModel,
    SemanticTable,
)

CSV_TEXT = "region,amount\nNorth,10\nSouth,20\nNorth,30\nEast,5\nSouth,40\n"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    instance = Project.create(tmp_path / "ParamDemo", "Parameters")
    source = tmp_path / "sales.csv"
    source.write_text(CSV_TEXT, encoding="utf-8")
    instance.add_dataset(source, dataset_id="sales", csv_options=CsvOptions(header=True))
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def model(project: Project) -> SemanticModel:
    return SemanticModel(
        name="Sales",
        tables=(SemanticTable("sales", "sales"),),
        dimensions=(SemanticDimension("region", "sales", "region"),),
        measures=(
            SemanticMeasure("total_amount", "sum(amount)", "Total amount", "currency"),
            SemanticMeasure("rows", "count()", "Rows", "integer"),
        ),
        default_table="sales",
    )


def _dashboard() -> Dashboard:
    return Dashboard(
        id="param-demo",
        title="Parameter demo",
        parameters=(
            DashboardParameter(
                "top_n", "number", title="Row cap", default=5, minimum=1
            ),
            DashboardParameter(
                "min_total", "number", title="Minimum total", default=0, minimum=0
            ),
            DashboardParameter(
                "top_region",
                "choice",
                title="Highlight region",
                options=("North", "South", "East"),
                default="North",
            ),
            DashboardParameter("show_east", "boolean", title="Show East", default=True),
        ),
        tiles=(
            DashboardTile(
                "by-region",
                "Sales by region",
                kind="chart",
                dimensions=("region",),
                measures=("total_amount",),
                chart="column",
                filters=(QueryFilter("total_amount", ">=", "@min_total"),),
                limit="@top_n",
            ),
        ),
    )


def test_parameter_validation_errors() -> None:
    with pytest.raises(DashboardError):
        DashboardParameter("bad name", "number")
    with pytest.raises(DashboardError):
        DashboardParameter("options_missing", "choice")
    with pytest.raises(DashboardError):
        DashboardParameter("bad_default", "boolean", default="yes")
    with pytest.raises(DashboardError):
        DashboardParameter("inverted", "number", minimum=10, maximum=1)
    with pytest.raises(DashboardError):
        DashboardParameter("bad_option", "choice", options=("a",), default="b")


def test_parameter_roundtrip_and_defaults() -> None:
    parameter = DashboardParameter(
        "rate", "number", title="Rate", default=0.15, minimum=0, maximum=1, step=0.05
    )
    clone = DashboardParameter.from_dict(parameter.to_dict())
    assert clone == parameter
    assert parameter.coerce(None) == pytest.approx(0.15)
    assert parameter.coerce("0.5") == pytest.approx(0.5)
    assert parameter.coerce(5) == pytest.approx(1.0)  # clamped to maximum
    bool_parameter = DashboardParameter("flag", "boolean", default=False)
    assert bool_parameter.coerce("yes") is True
    with pytest.raises(DashboardError):
        bool_parameter.coerce("maybe")


def test_unknown_parameter_reference_rejected() -> None:
    with pytest.raises(DashboardError, match="unknown parameter"):
        Dashboard(
            id="bad-ref",
            title="Bad reference",
            tiles=(
                DashboardTile(
                    "tile",
                    "Tile",
                    kind="chart",
                    dimensions=("region",),
                    measures=("total_amount",),
                    limit="@missing",
                ),
            ),
        )


def test_dashboard_roundtrip_preserves_parameters() -> None:
    dashboard = _dashboard()
    clone = Dashboard.from_dict(dashboard.to_dict())
    assert [item.name for item in clone.parameters] == [
        item.name for item in dashboard.parameters
    ]
    assert clone.schedule == "manual"
    assert clone.tiles[0].limit == "@top_n"


def test_render_substitutes_parameters(
    project: Project, model: SemanticModel
) -> None:
    dashboard = Dashboard(
        id="param-render",
        title="Parameter render",
        parameters=(
            DashboardParameter("min_total", "number", default=0),
            DashboardParameter("cap", "number", default=10, minimum=1),
        ),
        tiles=(
            DashboardTile(
                "by-region",
                "Sales by region",
                kind="chart",
                dimensions=("region",),
                measures=("total_amount",),
                chart="bar",
                filters=(QueryFilter("total_amount", ">=", "@min_total"),),
                limit="@cap",
                order_by=({"field": "total_amount", "direction": "desc"},),
            ),
        ),
    )
    engine = SemanticEngine(project, model)
    view = DashboardRenderer(engine).render(dashboard, parameters={"min_total": 15})
    tile = view.tiles[0]
    assert tile.error is None, tile.error
    assert tile.result is not None
    assert [row["region"] for row in tile.result.rows] == ["South", "North"]
    assert view.parameters == {"min_total": 15, "cap": 10}


def test_render_rejects_unknown_and_applies_defaults(
    project: Project, model: SemanticModel
) -> None:
    dashboard = _dashboard()
    engine = SemanticEngine(project, model)
    with pytest.raises(DashboardError, match="Unknown parameter"):
        DashboardRenderer(engine).render(dashboard, parameters={"nope": 1})
    view = DashboardRenderer(engine).render(dashboard)
    # Missing parameters fall back to their declared defaults.
    assert view.parameters["min_total"] == 0
    assert view.parameters["top_region"] == "North"
    assert view.parameters["show_east"] is True


def test_parameter_reference_to_choice_value(
    project: Project, model: SemanticModel
) -> None:
    dashboard = Dashboard(
        id="choice-filter",
        title="Choice filter",
        parameters=(
            DashboardParameter(
                "only_region",
                "choice",
                options=("North", "South", "East"),
                default="North",
            ),
        ),
        tiles=(
            DashboardTile(
                "by-region",
                "Sales by region",
                kind="table",
                dimensions=("region",),
                measures=("total_amount",),
                filters=(QueryFilter("region", "=", "@only_region"),),
            ),
        ),
    )
    engine = SemanticEngine(project, model)
    default_view = DashboardRenderer(engine).render(dashboard)
    assert [row["region"] for row in default_view.tiles[0].result.rows] == ["North"]
    south_view = DashboardRenderer(engine).render(
        dashboard, parameters={"only_region": "South"}
    )
    assert [row["region"] for row in south_view.tiles[0].result.rows] == ["South"]
    with pytest.raises(DashboardError):
        DashboardRenderer(engine).render(dashboard, parameters={"only_region": "Mars"})


def test_dashboard_store_roundtrip_with_parameters(
    project: Project, model: SemanticModel, tmp_path: Path
) -> None:
    from data_studio.semantic import SemanticModelStore

    store = SemanticModelStore(project)
    store.save(model)
    dashboards = DashboardStore(project)
    saved = dashboards.save(_dashboard(), model=model, overwrite=True)
    reloaded = dashboards.get(saved.id)
    assert [item.name for item in reloaded.parameters] == [
        "top_n",
        "min_total",
        "top_region",
        "show_east",
    ]


def test_api_render_with_parameters(
    tmp_path: Path, model: SemanticModel
) -> None:
    from fastapi.testclient import TestClient

    from data_studio.api.app import create_app
    from data_studio.api.state import ProjectManager

    manager = ProjectManager()
    with TestClient(create_app(manager=manager)) as client:
        root = tmp_path / "ApiDemo"
        created = client.post(
            "/api/v1/project/create", json={"path": str(root), "name": "ParamDemo"}
        )
        assert created.status_code == 200, created.text
        upload = client.post(
            "/api/v1/datasets",
            params={"filename": "sales.csv", "dataset_id": "sales"},
            content=CSV_TEXT.encode("utf-8"),
            headers={"Content-Type": "application/octet-stream"},
        )
        assert upload.status_code == 201, upload.text
        put = client.put("/api/v1/model", json={"model": model.to_dict()})
        assert put.status_code == 200, put.text
        saved = client.post(
            "/api/v1/dashboards",
            json={**_dashboard().to_dict(), "overwrite": True},
        )
        assert saved.status_code == 201, saved.text
        response = client.post(
            "/api/v1/dashboards/param-demo/render",
            json={"parameters": {"min_total": 25}},
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["parameters"]["min_total"] == 25
        rows = view["tiles"][0]["result"]["rows"]
        assert {row["region"] for row in rows} == {"North", "South"}
        bad = client.post(
            "/api/v1/dashboards/param-demo/render",
            json={"parameters": {"unknown": 1}},
        )
        assert bad.status_code == 400
    manager.close()
