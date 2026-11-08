"""Tests for the Phase 1 FastAPI surface (Phase 0 system endpoints included)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from data_studio.api.app import create_app
from data_studio.version import API_VERSION, PROJECT_FORMAT_VERSION, __version__


def test_health_reports_version_and_status() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "api": API_VERSION,
    }


def test_meta_describes_phase_one_capabilities() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/meta")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Data Studio"
    assert payload["version"] == __version__
    assert payload["api_version"] == API_VERSION
    assert payload["project_format_version"] == PROJECT_FORMAT_VERSION
    assert payload["phase"] == 1
    assert payload["capabilities"]["projects"] is True
    assert payload["capabilities"]["datasets"] is True
    assert payload["capabilities"]["pipelines"] is True
    assert payload["capabilities"]["exports"] is True
    assert payload["capabilities"]["analyses"] is False
    assert payload["capabilities"]["dashboards"] is False
    assert payload["operations"] >= 25


def test_index_renders_workspace() -> None:
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "<title>Data Studio</title>" in body
    assert "Data Studio" in body
    assert "Pipeline" in body
    assert "/api/v1/project" in body


def test_openapi_schema_includes_system_and_phase_one_endpoints() -> None:
    client = TestClient(create_app())
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Data Studio API"
    assert schema["info"]["version"] == __version__
    assert "/health" in schema["paths"]
    assert "/api/v1/meta" in schema["paths"]
    assert "/api/v1/datasets" in schema["paths"]
    assert "/api/v1/pipeline" in schema["paths"]
    assert "/api/v1/run" in schema["paths"]
    assert "/" not in schema["paths"]


def test_cors_allows_the_local_vite_origin() -> None:
    client = TestClient(create_app())
    response = client.get(
        "/health",
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
