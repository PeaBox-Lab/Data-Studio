"""Tests for the Phase 2D saved-analysis API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_studio.api.app import create_app
from data_studio.api.state import ProjectManager

CSV_BYTES = b"region,amount\nNorth,10\nSouth,20\nNorth,15\n"
UPDATED_CSV = b"region,amount\nNorth,11\nSouth,21\nNorth,16\n"


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
        "/api/v1/project/create", json={"path": str(root), "name": "Saved"}
    )
    assert response.status_code == 200, response.text
    upload = client.post(
        "/api/v1/datasets",
        params={"filename": "sales.csv", "dataset_id": "sales"},
        content=CSV_BYTES,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert upload.status_code == 201, upload.text


def save_payload(**overrides: object) -> dict:
    payload = {
        "name": "Revenue by region",
        "analysis": "descriptive_stats",
        "params": {"columns": ["amount"]},
        "dataset": "sales",
        "use_pipeline": False,
        "schedule": "daily",
        "report": True,
    }
    payload.update(overrides)
    return payload


def test_save_list_and_get(client: TestClient, tmp_path: Path) -> None:
    prepare(client, tmp_path / "Demo")

    response = client.post("/api/v1/saved-analyses", json=save_payload())
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["id"] == "revenue-by-region"
    assert saved["status"] == "fresh"
    assert saved["report_path"] == "reports/revenue-by-region.html"
    assert saved["result"]["kind"] == "descriptive_stats"
    assert (tmp_path / "Demo" / "reports" / "revenue-by-region.html").is_file()

    listing = client.get("/api/v1/saved-analyses")
    assert listing.status_code == 200
    payload = listing.json()
    assert [item["id"] for item in payload["analyses"]] == ["revenue-by-region"]
    assert payload["schedules"] == ["manual", "on_open", "hourly", "daily"]
    assert "result" not in payload["analyses"][0]

    detail = client.get("/api/v1/saved-analyses/revenue-by-region")
    assert detail.status_code == 200
    assert detail.json()["result"]["metrics"]

    report = client.get("/api/v1/saved-analyses/revenue-by-region/report")
    assert report.status_code == 200
    assert "<html" in report.text.lower()
    assert "Descriptive statistics" in report.text


def test_saved_analysis_becomes_stale_and_refreshes(
    client: TestClient, tmp_path: Path
) -> None:
    prepare(client, tmp_path / "Demo")
    assert client.post("/api/v1/saved-analyses", json=save_payload()).status_code == 201

    update = client.post(
        "/api/v1/datasets",
        params={"filename": "sales.csv", "dataset_id": "sales", "replace": "true"},
        content=UPDATED_CSV,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert update.status_code == 201, update.text

    listing = client.get("/api/v1/saved-analyses").json()
    assert listing["analyses"][0]["status"] == "stale"
    assert listing["analyses"][0]["due"] is True

    refreshed = client.post("/api/v1/saved-analyses/refresh", json={"due": True})
    assert refreshed.status_code == 200, refreshed.text
    body = refreshed.json()
    assert [item["id"] for item in body["analyses"]] == ["revenue-by-region"]
    assert body["analyses"][0]["status"] == "fresh"
    assert body["failed"] == []


def test_refresh_one_and_delete(client: TestClient, tmp_path: Path) -> None:
    prepare(client, tmp_path / "Demo")
    client.post("/api/v1/saved-analyses", json=save_payload(name="First"))
    client.post("/api/v1/saved-analyses", json=save_payload(name="Second"))

    one = client.post("/api/v1/saved-analyses/first/refresh")
    assert one.status_code == 200
    assert one.json()["id"] == "first"

    removed = client.delete("/api/v1/saved-analyses/first")
    assert removed.status_code == 200
    assert removed.json() == {"removed": "first"}
    assert [item["id"] for item in client.get("/api/v1/saved-analyses").json()["analyses"]] == [
        "second"
    ]

    missing = client.get("/api/v1/saved-analyses/first")
    assert missing.status_code == 404


def test_refresh_all_and_schedule_filter(client: TestClient, tmp_path: Path) -> None:
    prepare(client, tmp_path / "Demo")
    client.post("/api/v1/saved-analyses", json=save_payload(name="Manual", schedule="manual"))
    client.post("/api/v1/saved-analyses", json=save_payload(name="Daily", schedule="daily"))

    due = client.post("/api/v1/saved-analyses/refresh", json={"due": True})
    assert due.status_code == 200
    assert due.json()["analyses"] == []

    refresh_all = client.post("/api/v1/saved-analyses/refresh", json={"due": False})
    assert refresh_all.status_code == 200
    assert {item["id"] for item in refresh_all.json()["analyses"]} == {"manual", "daily"}


def test_unsafe_report_path_is_rejected(client: TestClient, tmp_path: Path) -> None:
    prepare(client, tmp_path / "Demo")
    response = client.post(
        "/api/v1/saved-analyses",
        json=save_payload(report_path="../outside.html"),
    )
    assert response.status_code == 400


def test_saved_analysis_validation(client: TestClient, tmp_path: Path) -> None:
    prepare(client, tmp_path / "Demo")

    no_name = client.post("/api/v1/saved-analyses", json=save_payload(name="  "))
    assert no_name.status_code == 400

    bad_schedule = client.post(
        "/api/v1/saved-analyses", json=save_payload(schedule="sometimes")
    )
    assert bad_schedule.status_code == 400

    bad_analysis = client.post(
        "/api/v1/saved-analyses", json=save_payload(analysis="not_real")
    )
    assert bad_analysis.status_code == 400

    bad_params = client.post(
        "/api/v1/saved-analyses", json=save_payload(params=["amount"])
    )
    assert bad_params.status_code == 400


def test_saved_analyses_require_an_open_project(client: TestClient) -> None:
    assert client.get("/api/v1/saved-analyses").status_code == 409
    assert (
        client.post("/api/v1/saved-analyses", json=save_payload()).status_code == 409
    )
