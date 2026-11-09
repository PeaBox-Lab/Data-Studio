"""Tests for the Phase 3 semantic model and dashboard API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_studio.api.app import create_app
from data_studio.api.state import ProjectManager

SALES_CSV = b"region,amount,units\nNorth,10,1\nSouth,20,2\nNorth,15,3\nEast,5,1\n"
REGIONS_CSV = b"region,manager,weight\nNorth,Ana,1\nSouth,Bo,2\nEast,Cyd,3\n"


@pytest.fixture
def manager() -> ProjectManager:
    return ProjectManager()


@pytest.fixture
def client(manager: ProjectManager):
    with TestClient(create_app(manager=manager)) as instance:
        yield instance
    manager.close()


def prepare(client: TestClient, root: Path) -> None:
    response = client.post(
        "/api/v1/project/create", json={"path": str(root), "name": "Semantic"}
    )
    assert response.status_code == 200, response.text
    for filename, dataset_id, content in (
        ("sales.csv", "sales", SALES_CSV),
        ("regions.csv", "regions", REGIONS_CSV),
    ):
        upload = client.post(
            "/api/v1/datasets",
            params={"filename": filename, "dataset_id": dataset_id},
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert upload.status_code == 201, upload.text


def derived_model(client: TestClient) -> dict:
    response = client.post("/api/v1/model/derive", json={"name": "Sales model"})
    assert response.status_code == 201, response.text
    model = response.json()["model"]
    model["relationships"] = [
        {
            "from_table": "sales",
            "from_column": "region",
            "to_table": "regions",
            "to_column": "region",
            "kind": "many_to_one",
        }
    ]
    model["default_table"] = "sales"
    return model


def test_meta_announces_phase_three(client: TestClient) -> None:
    meta = client.get("/api/v1/meta").json()
    assert meta["phase"] == 3
    assert meta["capabilities"]["semantic_model"] is True
    assert meta["capabilities"]["dashboards"] is True


def test_model_endpoint_without_model(client: TestClient, tmp_path: Path) -> None:
    prepare(client, tmp_path / "Demo")
    payload = client.get("/api/v1/model").json()
    assert payload["exists"] is False
    assert payload["model"] is None
    assert payload["datasets"]
    assert client.post("/api/v1/model/query", json={"measures": ["x"]}).status_code == 404


def test_derive_save_and_query_model(client: TestClient, tmp_path: Path) -> None:
    prepare(client, tmp_path / "Demo")
    model = derived_model(client)

    saved = client.put("/api/v1/model", json={"model": model})
    assert saved.status_code == 200, saved.text
    assert saved.json()["problems"] == []

    loaded = client.get("/api/v1/model").json()
    assert loaded["exists"] is True
    assert loaded["model"]["default_table"] == "sales"

    response = client.post(
        "/api/v1/model/query",
        json={
            "dimensions": ["region"],
            "measures": ["total_amount"],
            "order_by": [{"field": "region", "direction": "asc"}],
            "use_pipeline": False,
        },
    )
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert rows == [
        {"region": "East", "total_amount": 5},
        {"region": "North", "total_amount": 25},
        {"region": "South", "total_amount": 20},
    ]

    values = client.get(
        "/api/v1/model/dimensions/region/values", params={"search": "n"}
    )
    assert values.status_code == 200
    assert values.json()["values"] == ["North"]


def test_dashboard_create_render_and_reports(
    client: TestClient, tmp_path: Path
) -> None:
    prepare(client, tmp_path / "Demo")
    assert client.put("/api/v1/model", json={"model": derived_model(client)}).status_code == 200

    payload = {
        "title": "Sales dashboard",
        "description": "Revenue overview",
        "tiles": [
            {
                "id": "total",
                "title": "Total revenue",
                "kind": "kpi",
                "measures": ["total_amount"],
            },
            {
                "id": "by-region",
                "title": "Revenue by region",
                "kind": "chart",
                "dimensions": ["region"],
                "measures": ["total_amount"],
                "chart": "column",
            },
            {
                "id": "detail",
                "title": "Detail",
                "kind": "table",
                "dimensions": ["region"],
                "measures": ["total_amount"],
            },
        ],
        "slicers": [{"id": "region", "dimension": "region"}],
    }
    created = client.post("/api/v1/dashboards", json=payload)
    assert created.status_code == 201, created.text
    dashboard = created.json()
    assert dashboard["id"] == "sales-dashboard"

    listing = client.get("/api/v1/dashboards").json()
    assert [item["id"] for item in listing["dashboards"]] == ["sales-dashboard"]
    assert listing["errors"] == []

    rendered = client.post(
        "/api/v1/dashboards/sales-dashboard/render",
        json={"selections": {"region": ["North"]}, "use_pipeline": False},
    )
    assert rendered.status_code == 200, rendered.text
    tiles = rendered.json()["tiles"]
    assert tiles[0]["result"]["rows"] == [{"total_amount": 25}]
    assert tiles[1]["svg"].startswith("<svg")
    assert tiles[2]["result"]["rows"] == [{"region": "North", "total_amount": 25}]

    report = client.get("/api/v1/dashboards/sales-dashboard/report")
    assert report.status_code == 200
    assert "<svg" in report.text
    assert "Total revenue" in report.text

    written = client.post(
        "/api/v1/dashboards/sales-dashboard/report",
        json={"destination": "reports/sales.html", "theme": "light"},
    )
    assert written.status_code == 200, written.text
    assert (tmp_path / "Demo" / "reports" / "sales.html").is_file()

    removed = client.delete("/api/v1/dashboards/sales-dashboard")
    assert removed.status_code == 200
    assert client.get("/api/v1/dashboards/sales-dashboard").status_code == 404


def test_dashboard_rejects_unknown_measure(
    client: TestClient, tmp_path: Path
) -> None:
    prepare(client, tmp_path / "Demo")
    assert client.put("/api/v1/model", json={"model": derived_model(client)}).status_code == 200
    response = client.post(
        "/api/v1/dashboards",
        json={
            "title": "Bad",
            "tiles": [
                {"id": "k", "title": "K", "kind": "kpi", "measures": ["nope"]}
            ],
        },
    )
    assert response.status_code == 400
    assert "unknown measure" in response.json()["detail"]


def test_dashboard_bookmarks_and_table_styles(
    client: TestClient, tmp_path: Path
) -> None:
    prepare(client, tmp_path / "Demo")
    assert client.put("/api/v1/model", json={"model": derived_model(client)}).status_code == 200
    created = client.post(
        "/api/v1/dashboards",
        json={
            "title": "Sales dashboard",
            "tiles": [
                {
                    "id": "detail",
                    "title": "Detail",
                    "kind": "table",
                    "dimensions": ["region"],
                    "measures": ["total_amount"],
                    "order_by": [{"field": "total_amount", "direction": "desc"}],
                    "limit": 2,
                    "table_style": "data_bars",
                }
            ],
            "slicers": [{"id": "region", "dimension": "region"}],
        },
    )
    assert created.status_code == 201, created.text
    dashboard = created.json()
    assert dashboard["tiles"][0]["table_style"] == "data_bars"
    assert dashboard["tiles"][0]["limit"] == 2
    assert dashboard["bookmarks"] == []

    rendered = client.post(
        "/api/v1/dashboards/sales-dashboard/render",
        json={"use_pipeline": False},
    )
    assert rendered.status_code == 200, rendered.text
    tile = rendered.json()["tiles"][0]
    assert [row["region"] for row in tile["result"]["rows"]] == ["North", "South"]
    assert tile["percentages"][0][1] == pytest.approx(1.0)
    assert tile["percentages"][1][1] == pytest.approx(0.0)

    report = client.get("/api/v1/dashboards/sales-dashboard/report")
    assert report.status_code == 200
    assert "linear-gradient(90deg" in report.text
    assert "@media print" in report.text

    bookmark = client.post(
        "/api/v1/dashboards/sales-dashboard/bookmarks",
        json={"title": "North only", "selections": {"region": ["North"]}},
    )
    assert bookmark.status_code == 201, bookmark.text
    payload = bookmark.json()
    assert payload["bookmark"]["id"] == "north-only"
    assert payload["bookmark"]["selections"] == {"region": ["North"]}
    assert payload["dashboard"]["bookmarks"][0]["id"] == "north-only"

    duplicate = client.post(
        "/api/v1/dashboards/sales-dashboard/bookmarks",
        json={"title": "North only", "selections": {"region": ["South"]}},
    )
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["bookmark"]["id"] == "north-only-2"

    reloaded = client.get("/api/v1/dashboards/sales-dashboard").json()
    assert [item["id"] for item in reloaded["bookmarks"]] == [
        "north-only",
        "north-only-2",
    ]

    unknown = client.post(
        "/api/v1/dashboards/sales-dashboard/bookmarks",
        json={"title": "Bad", "selections": {"nope": ["x"]}},
    )
    assert unknown.status_code == 400

    removed = client.delete(
        "/api/v1/dashboards/sales-dashboard/bookmarks/north-only"
    )
    assert removed.status_code == 200, removed.text
    assert [item["id"] for item in removed.json()["bookmarks"]] == ["north-only-2"]
    assert (
        client.delete(
            "/api/v1/dashboards/sales-dashboard/bookmarks/north-only"
        ).status_code
        == 404
    )
