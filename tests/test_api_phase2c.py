"""Tests for the Phase 2C chart and HTML report API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_studio.api.app import create_app
from data_studio.api.state import ProjectManager

SERIES = [52.5, 51.5, 51.0, 50.5, 50.0, 52.0, 53.0, 54.5, 56.0, 55.5, 55.5, 54.5]
SERIES += [
    58.0,
    57.5,
    57.0,
    57.0,
    56.0,
    57.5,
    59.0,
    60.5,
    62.5,
    61.5,
    61.0,
    60.5,
]
SERIES += [
    64.0,
    64.0,
    63.0,
    62.5,
    62.0,
    63.5,
    65.5,
    66.5,
    68.0,
    67.5,
    67.0,
    67.0,
]
SERIES += [
    70.0,
    69.5,
    69.0,
    68.5,
    68.5,
    69.5,
    71.0,
    72.5,
    74.0,
    74.0,
    73.0,
    72.5,
]
CSV_BYTES = (
    "period,value\n"
    + "".join(
        f"{2015 + index // 12}-{(index % 12) + 1:02d}-01,{value}\n"
        for index, value in enumerate(SERIES)
    )
).encode()


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
        "/api/v1/project/create", json={"path": str(root), "name": "Reports"}
    )
    assert response.status_code == 200, response.text
    upload = client.post(
        "/api/v1/datasets",
        params={"filename": "series.csv", "dataset_id": "series"},
        content=CSV_BYTES,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert upload.status_code == 201, upload.text


def sample_chart() -> dict:
    return {
        "chart_type": "column",
        "title": "Value by period",
        "encoding": {"x": "period", "y": "value"},
        "data": [
            {"period": "2015-01-01", "value": 52.5},
            {"period": "2015-02-01", "value": 51.5},
        ],
    }


def test_render_one_chart(client: TestClient) -> None:
    response = client.post("/api/v1/charts/render", json={"chart": sample_chart()})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["title"] == "Value by period"
    assert payload["chart_type"] == "column"
    assert payload["svg"].startswith("<svg")
    assert payload["svg"].endswith("</svg>")


def test_render_many_charts(client: TestClient) -> None:
    response = client.post(
        "/api/v1/charts/render",
        json={"charts": [sample_chart(), sample_chart()], "theme": "dark"},
    )
    assert response.status_code == 200, response.text
    svgs = response.json()["svgs"]
    assert len(svgs) == 2
    assert "#0f172a" in svgs[0]


def test_render_rejects_invalid_spec(client: TestClient) -> None:
    response = client.post(
        "/api/v1/charts/render", json={"chart": {"chart_type": "nope", "title": "x"}}
    )
    assert response.status_code == 400
    response = client.post("/api/v1/charts/render", json={"charts": []})
    assert response.status_code == 400


def test_time_series_analysis_returns_charts(
    client: TestClient, tmp_path: Path
) -> None:
    prepare(client, tmp_path / "Demo")
    response = client.post(
        "/api/v1/analyses/run",
        json={
            "analysis": "time_series_summary",
            "params": {"time": "period", "value": "value"},
            "dataset": "series",
            "use_pipeline": False,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["kind"] == "time_series_summary"
    assert payload["metrics"]["n"] == 48
    assert len(payload["charts"]) == 2
    assert payload["charts"][0]["data"]


def test_report_writes_into_the_project(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "Demo"
    prepare(client, root)
    response = client.post(
        "/api/v1/analyses/report",
        json={
            "analysis": "time_series_summary",
            "params": {"time": "period", "value": "value"},
            "dataset": "series",
            "use_pipeline": False,
            "title": "Monthly series",
            "destination": "reports/monthly.html",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    written = root / "reports" / "monthly.html"
    assert written.is_file()
    assert payload["relative_path"] == "reports/monthly.html"
    assert payload["bytes"] == written.stat().st_size
    assert payload["results"][0].startswith("res_")
    assert "<!DOCTYPE html>" in payload["html"]
    assert "Monthly series" in payload["html"]
    assert "<svg" in payload["html"]
    assert "62.0208" in payload["html"]


def test_report_can_combine_analyses(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    prepare(client, root)
    response = client.post(
        "/api/v1/analyses/report",
        json={
            "analyses": [
                {
                    "analysis": "time_series_summary",
                    "params": {"time": "period", "value": "value"},
                    "dataset": "series",
                    "use_pipeline": False,
                },
                {
                    "analysis": "trend_test",
                    "params": {"time": "period", "value": "value"},
                    "dataset": "series",
                    "use_pipeline": False,
                },
            ],
            "title": "Trend pack",
            "destination": "reports/trend-pack.html",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["results"]) == 2
    assert "Mann-Kendall trend" in payload["html"]


def test_report_defaults_to_a_timestamped_path(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "Demo"
    prepare(client, root)
    response = client.post(
        "/api/v1/analyses/report",
        json={
            "analysis": "trend_test",
            "params": {"time": "period", "value": "value"},
            "dataset": "series",
            "use_pipeline": False,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["relative_path"].startswith("reports/report-")
    assert (root / payload["relative_path"]).is_file()


def test_report_rejects_unsafe_destinations(
    client: TestClient, tmp_path: Path
) -> None:
    prepare(client, tmp_path / "Demo")
    response = client.post(
        "/api/v1/analyses/report",
        json={
            "analysis": "trend_test",
            "params": {"time": "period", "value": "value"},
            "dataset": "series",
            "use_pipeline": False,
            "destination": "../outside.html",
        },
    )
    assert response.status_code == 400


def test_report_validation(client: TestClient, tmp_path: Path) -> None:
    prepare(client, tmp_path / "Demo")
    missing = client.post("/api/v1/analyses/report", json={})
    assert missing.status_code == 400
    assert "analysis" in missing.json()["detail"].lower()

    bad_theme = client.post(
        "/api/v1/analyses/report",
        json={
            "analysis": "trend_test",
            "params": {"time": "period", "value": "value"},
            "dataset": "series",
            "use_pipeline": False,
            "theme": "neon",
        },
    )
    assert bad_theme.status_code == 400


def test_reports_directory_is_created_with_new_projects(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "Demo"
    prepare(client, root)
    assert (root / "reports").is_dir()
