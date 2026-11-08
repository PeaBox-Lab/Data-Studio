"""Tests for the Phase 2A analysis API and the redesigned workspace shell."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_studio.api.app import create_app
from data_studio.api.state import ProjectManager

CSV_BYTES = (
    b"id,region,amount,score\n"
    b"1, North ,10.5,1\n"
    b"2,north,7,2\n"
    b"3,South,,2\n"
    b"4,South,20,3\n"
)


@pytest.fixture
def manager() -> ProjectManager:
    return ProjectManager()


@pytest.fixture
def client(manager: ProjectManager):
    with TestClient(create_app(manager=manager)) as instance:
        yield instance
    manager.close()


def create_project(client: TestClient, root: Path) -> None:
    response = client.post(
        "/api/v1/project/create", json={"path": str(root), "name": "Analyses"}
    )
    assert response.status_code == 200, response.text


def upload(client: TestClient, dataset_id: str = "sales") -> dict:
    response = client.post(
        "/api/v1/datasets",
        params={"filename": "sales.csv", "dataset_id": dataset_id},
        content=CSV_BYTES,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_analysis_catalog_is_available_without_a_project(client: TestClient) -> None:
    response = client.get("/api/v1/analyses")
    assert response.status_code == 200
    catalog = response.json()
    by_name = {item["name"]: item for item in catalog}
    assert {
        "descriptive_stats",
        "value_counts",
        "crosstab",
        "correlation",
        "quality_profile",
    } <= set(by_name)
    assert {"name", "title", "category", "description", "params"} <= set(catalog[0])
    assert by_name["value_counts"]["params"][0]["name"] == "column"


def test_run_requires_an_open_project(client: TestClient) -> None:
    response = client.post(
        "/api/v1/analyses/run",
        json={"analysis": "descriptive_stats", "params": {}},
    )
    assert response.status_code == 409
    assert "No project" in response.json()["detail"]


def test_descriptive_stats_on_raw_data(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    upload(client)
    response = client.post(
        "/api/v1/analyses/run",
        json={
            "analysis": "descriptive_stats",
            "params": {"columns": ["amount", "score"]},
            "use_pipeline": False,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["kind"] == "descriptive_stats"
    assert payload["schema_version"] == "1"
    assert payload["result_id"].startswith("res_")
    assert payload["metrics"] == {"rows": 4, "columns": 2, "missing": 1}
    assert payload["provenance"]["dataset"] == "sales"
    assert payload["provenance"]["step_ids"] == []
    assert payload["tables"][0]["columns"][0] == "column"
    assert payload["narrative"]


def test_analysis_uses_the_pipeline_by_default(
    client: TestClient, tmp_path: Path
) -> None:
    create_project(client, tmp_path / "Demo")
    upload(client)
    step = client.post(
        "/api/v1/pipeline/steps",
        json={"operation": "filter", "params": {"expression": "region != 'South'"}},
    )
    assert step.status_code == 201, step.text

    response = client.post(
        "/api/v1/analyses/run",
        json={"analysis": "descriptive_stats", "params": {"columns": ["amount"]}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["metrics"]["rows"] == 2
    assert payload["provenance"]["step_ids"] == ["filter"]


def test_value_counts_and_quality_profile(
    client: TestClient, tmp_path: Path
) -> None:
    create_project(client, tmp_path / "Demo")
    upload(client)

    counts = client.post(
        "/api/v1/analyses/run",
        json={
            "analysis": "value_counts",
            "params": {"column": "region", "limit": 10},
            "dataset": "sales",
            "use_pipeline": False,
        },
    )
    assert counts.status_code == 200, counts.text
    assert counts.json()["metrics"]["missing"] == 0
    assert counts.json()["tables"][0]["rows"][0][1] == 2

    quality = client.post(
        "/api/v1/analyses/run",
        json={
            "analysis": "quality_profile",
            "params": {"columns": ["region", "amount"], "check_duplicates": False},
            "use_pipeline": False,
        },
    )
    assert quality.status_code == 200, quality.text
    payload = quality.json()
    assert payload["metrics"]["columns"] == 2
    assert payload["metrics"]["missing"] == 1
    assert payload["metrics"]["duplicate_rows"] is None


def test_analysis_validation_errors(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    upload(client)

    missing_name = client.post("/api/v1/analyses/run", json={"params": {}})
    assert missing_name.status_code == 400
    assert "analysis" in missing_name.json()["detail"]

    bad_params = client.post(
        "/api/v1/analyses/run",
        json={"analysis": "descriptive_stats", "params": []},
    )
    assert bad_params.status_code == 400
    assert "params" in bad_params.json()["detail"]

    unknown = client.post(
        "/api/v1/analyses/run",
        json={"analysis": "not_real", "params": {}},
    )
    assert unknown.status_code == 400
    assert "Unknown analysis" in unknown.json()["detail"]

    bad_column = client.post(
        "/api/v1/analyses/run",
        json={
            "analysis": "value_counts",
            "params": {"column": "missing"},
            "use_pipeline": False,
        },
    )
    assert bad_column.status_code == 400
    assert "Unknown column" in bad_column.json()["detail"]


def test_analysis_can_target_another_dataset(
    client: TestClient, tmp_path: Path
) -> None:
    create_project(client, tmp_path / "Demo")
    upload(client, dataset_id="sales")
    upload(client, dataset_id="other")

    response = client.post(
        "/api/v1/analyses/run",
        json={
            "analysis": "descriptive_stats",
            "params": {"columns": ["amount"]},
            "dataset": "other",
            "use_pipeline": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["provenance"]["dataset"] == "other"


def test_workspace_has_the_new_navigation(client: TestClient) -> None:
    body = client.get("/").text
    assert "<title>Data Studio</title>" in body
    assert "Data Studio" in body
    assert "Pipeline" in body
    assert "/api/v1/project" in body
    assert "/api/v1/analyses" in body
    for label in ("Overview", "Data", "Prepare", "Analyze", "Export"):
        assert label in body
