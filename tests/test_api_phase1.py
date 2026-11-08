"""Tests for the Phase 1 HTTP API: projects, datasets, pipelines, run, export."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_studio.api.app import create_app
from data_studio.api.state import ProjectManager
from data_studio.project import Project

CSV_BYTES = b"id,region,amount\n1, North ,10.5\n2,north,7\n3,South,\n"


@pytest.fixture
def manager() -> ProjectManager:
    return ProjectManager()


@pytest.fixture
def client(manager: ProjectManager):
    with TestClient(create_app(manager=manager)) as instance:
        yield instance
    manager.close()


def create_project(client: TestClient, root: Path, name: str = "Api Demo") -> dict:
    response = client.post(
        "/api/v1/project/create", json={"path": str(root), "name": name}
    )
    assert response.status_code == 200, response.text
    return response.json()


def upload_sales(client: TestClient, dataset_id: str = "sales") -> dict:
    response = client.post(
        "/api/v1/datasets",
        params={"filename": "sales.csv", "dataset_id": dataset_id},
        content=CSV_BYTES,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def add_steps(client: TestClient) -> list[dict]:
    definitions = [
        ("trim_text", {"columns": ["region"]}),
        ("normalize_text", {"columns": ["region"], "case": "title"}),
        ("cast", {"column": "amount", "dtype": "float"}),
        ("deduplicate", {"columns": ["id"]}),
    ]
    created = []
    for operation, params in definitions:
        response = client.post(
            "/api/v1/pipeline/steps",
            json={"operation": operation, "params": params},
        )
        assert response.status_code == 201, response.text
        created.append(response.json()["step"])
    return created


# -- no project ------------------------------------------------------------


def test_project_endpoint_reports_no_project(client: TestClient) -> None:
    response = client.get("/api/v1/project")
    assert response.status_code == 200
    assert response.json() == {"open": False, "root": None, "project": None}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/datasets"),
        ("get", "/api/v1/pipeline"),
        ("post", "/api/v1/run"),
        ("post", "/api/v1/run/preview"),
    ],
)
def test_project_endpoints_require_an_open_project(
    client: TestClient, method: str, path: str
) -> None:
    response = getattr(client, method)(path)
    assert response.status_code == 409
    assert "No project" in response.json()["detail"]


def test_operations_catalog_is_available_without_a_project(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/operations")
    assert response.status_code == 200
    catalog = response.json()
    assert len(catalog) >= 25
    assert {"name", "title", "category", "description", "params"} <= set(catalog[0])
    by_name = {item["name"]: item for item in catalog}
    assert by_name["trim_text"]["category"] == "text"
    assert by_name["join"]["category"] == "join"


# -- project lifecycle -----------------------------------------------------


def test_create_and_close_project(client: TestClient, tmp_path: Path) -> None:
    payload = create_project(client, tmp_path / "Demo")
    assert payload["open"] is True
    assert payload["project"]["name"] == "Api Demo"
    assert payload["project"]["datasets"] == []
    assert (tmp_path / "Demo" / "project.json").is_file()

    current = client.get("/api/v1/project").json()
    assert current["open"] is True
    assert current["root"] == str((tmp_path / "Demo").resolve())

    assert client.post("/api/v1/project/close").json() == {"open": False}
    assert client.get("/api/v1/project").json()["open"] is False


def test_create_requires_a_path(client: TestClient) -> None:
    response = client.post("/api/v1/project/create", json={})
    assert response.status_code == 400
    assert "path" in response.json()["detail"]
    response = client.post("/api/v1/project/create", json={"path": "   "})
    assert response.status_code == 400


def test_open_missing_project_returns_400(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/v1/project/open", json={"path": str(tmp_path / "missing")}
    )
    assert response.status_code == 400
    assert "No project" in response.json()["detail"]


def test_open_existing_project(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "Existing"
    Project.create(root, "Existing").close()
    response = client.post("/api/v1/project/open", json={"path": str(root), "verify": True})
    assert response.status_code == 200
    assert response.json()["project"]["name"] == "Existing"


def test_initial_project_root_is_opened_lazily(tmp_path: Path) -> None:
    root = tmp_path / "Auto"
    Project.create(root, "Auto").close()
    with TestClient(create_app(project_root=root)) as client:
        response = client.get("/api/v1/project")
        assert response.status_code == 200
        assert response.json()["project"]["name"] == "Auto"
        assert client.get("/api/v1/datasets").json() == []


def test_switching_projects_closes_the_previous_one(
    client: TestClient, tmp_path: Path
) -> None:
    create_project(client, tmp_path / "One", "One")
    create_project(client, tmp_path / "Two", "Two")
    current = client.get("/api/v1/project").json()
    assert current["project"]["name"] == "Two"


# -- datasets --------------------------------------------------------------


def test_upload_dataset_and_inspect_it(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    entry = upload_sales(client)
    assert entry["id"] == "sales"
    assert entry["rows"] == 3
    assert entry["path"] == "datasets/sales.parquet"
    assert entry["format"] == "parquet"
    assert entry["checksum_sha256"]

    datasets = client.get("/api/v1/datasets").json()
    assert [item["id"] for item in datasets] == ["sales"]
    assert datasets[0]["columns"] == 3

    schema = client.get("/api/v1/datasets/sales/schema").json()
    assert [column["name"] for column in schema["columns"]] == ["id", "region", "amount"]
    assert schema["columns"][0]["role"] == "identifier"

    preview = client.get("/api/v1/datasets/sales/preview", params={"limit": 2}).json()
    assert len(preview["rows"]) == 2
    assert preview["rows"][0] == [1, " North ", 10.5]


def test_upload_empty_body_returns_400(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    response = client.post(
        "/api/v1/datasets",
        params={"filename": "empty.csv"},
        content=b"",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400


def test_upload_duplicate_dataset_ids_get_a_suffix(
    client: TestClient, tmp_path: Path
) -> None:
    create_project(client, tmp_path / "Demo")
    first = upload_sales(client)
    second = upload_sales(client)
    assert first["id"] == "sales"
    assert second["id"] == "sales_2"


def test_preview_limit_is_validated(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    upload_sales(client)
    assert (
        client.get("/api/v1/datasets/sales/preview", params={"limit": -1}).status_code
        == 422
    )
    assert (
        client.get("/api/v1/datasets/sales/preview", params={"limit": 6000}).status_code
        == 422
    )


def test_unknown_dataset_returns_404(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    response = client.get("/api/v1/datasets/missing/schema")
    assert response.status_code == 404
    assert "Unknown dataset" in response.json()["detail"]


def test_delete_dataset(client: TestClient, tmp_path: Path) -> None:
    created = create_project(client, tmp_path / "Demo")
    upload_sales(client)
    response = client.delete("/api/v1/datasets/sales")
    assert response.status_code == 200
    assert response.json() == {"removed": "sales"}
    assert client.get("/api/v1/datasets").json() == []
    stored = Path(created["root"]) / "datasets" / "sales.parquet"
    assert not stored.exists()


# -- pipeline --------------------------------------------------------------


def test_pipeline_step_crud(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    steps = add_steps(client)
    assert [step["operation"] for step in steps] == [
        "trim_text",
        "normalize_text",
        "cast",
        "deduplicate",
    ]

    pipeline = client.get("/api/v1/pipeline").json()
    assert pipeline["schema_version"] == "1.0"
    assert [step["id"] for step in pipeline["steps"]] == [
        "trim_text",
        "normalize_text",
        "cast",
        "deduplicate",
    ]

    response = client.delete("/api/v1/pipeline/steps/cast")
    assert response.status_code == 200
    assert [step["operation"] for step in response.json()["steps"]] == [
        "trim_text",
        "normalize_text",
        "deduplicate",
    ]

    response = client.post("/api/v1/pipeline/undo")
    assert response.status_code == 200
    assert [step["operation"] for step in response.json()["steps"]] == [
        "trim_text",
        "normalize_text",
    ]


def test_add_step_with_position_and_title(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    add_steps(client)
    response = client.post(
        "/api/v1/pipeline/steps",
        json={
            "operation": "sort",
            "params": {"columns": ["amount"]},
            "position": 0,
            "title": "Sort by amount",
        },
    )
    assert response.status_code == 201
    pipeline = client.get("/api/v1/pipeline").json()
    assert pipeline["steps"][0]["operation"] == "sort"
    assert pipeline["steps"][0]["title"] == "Sort by amount"


def test_add_step_validation(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    assert client.post("/api/v1/pipeline/steps", json={}).status_code == 400
    assert (
        client.post(
            "/api/v1/pipeline/steps",
            json={"operation": "trim_text", "params": []},
        ).status_code
        == 400
    )


def test_put_pipeline_roundtrip(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    payload = {
        "schema_version": "1.0",
        "name": "Replacement",
        "dataset": None,
        "steps": [
            {"id": "filter_rows", "operation": "filter", "params": {"expression": "id > 1"}}
        ],
    }
    response = client.put("/api/v1/pipeline", json=payload)
    assert response.status_code == 200
    assert response.json()["name"] == "Replacement"
    assert client.get("/api/v1/pipeline").json()["steps"][0]["id"] == "filter_rows"


def test_put_pipeline_rejects_bad_documents(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    response = client.put(
        "/api/v1/pipeline",
        json={"schema_version": "9.9", "steps": []},
    )
    assert response.status_code == 400
    assert "schema version" in response.json()["detail"]


def test_remove_unknown_step_returns_404(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    response = client.delete("/api/v1/pipeline/steps/missing")
    assert response.status_code == 404


# -- run and export --------------------------------------------------------


def test_run_preview_returns_rows_and_metadata(
    client: TestClient, tmp_path: Path
) -> None:
    create_project(client, tmp_path / "Demo")
    upload_sales(client)
    add_steps(client)

    response = client.post("/api/v1/run/preview", params={"limit": 10})
    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["rows"] == 3
    assert payload["run"]["dataset"] == "sales"
    assert payload["run"]["cache_hits"] == 0
    assert len(payload["run"]["steps"]) == 4
    assert payload["schema"]["columns"][0]["name"] == "id"
    assert len(payload["rows"]) == 3

    cached = client.post("/api/v1/run", params={"count_rows": False}).json()
    assert cached["cache_hits"] == 4
    assert cached["rows"] is None


def test_run_failure_reports_the_step(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    upload_sales(client)
    response = client.post(
        "/api/v1/pipeline/steps",
        json={"operation": "select", "params": {"columns": ["missing"]}},
    )
    assert response.status_code == 201

    run = client.post("/api/v1/run")
    assert run.status_code == 400
    payload = run.json()
    assert payload["step_id"] == "select"
    assert payload["operation"] == "select"
    assert "select" in payload["detail"]


def test_export_to_a_relative_path(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    upload_sales(client)
    add_steps(client)
    response = client.post(
        "/api/v1/export", json={"destination": "exports/result.parquet"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["rows"] == 3
    destination = Path(payload["destination"])
    assert destination.is_file()
    assert destination.parent == (tmp_path / "Demo" / "exports").resolve()

    again = client.post(
        "/api/v1/export",
        json={"destination": "exports/result.csv", "format": "csv"},
    )
    assert again.status_code == 200
    assert Path(again.json()["destination"]).is_file()


def test_export_requires_a_destination(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    response = client.post("/api/v1/export", json={})
    assert response.status_code == 400
    assert "destination" in response.json()["detail"]


def test_export_failure_is_reported(client: TestClient, tmp_path: Path) -> None:
    create_project(client, tmp_path / "Demo")
    upload_sales(client)
    add_steps(client)
    response = client.post(
        "/api/v1/export", json={"destination": "exports/result.xml"}
    )
    assert response.status_code == 400
    assert "Cannot export" in response.json()["detail"]


def test_reopening_through_the_api_reproduces_the_result(
    client: TestClient, tmp_path: Path
) -> None:
    create_project(client, tmp_path / "Demo")
    upload_sales(client)
    add_steps(client)
    before = client.post("/api/v1/run/preview", params={"limit": 10}).json()["rows"]
    client.post("/api/v1/project/close")

    opened = client.post(
        "/api/v1/project/open", json={"path": str(tmp_path / "Demo"), "verify": True}
    )
    assert opened.status_code == 200
    assert len(opened.json()["project"]["datasets"]) == 1
    assert [step["operation"] for step in client.get("/api/v1/pipeline").json()["steps"]] == [
        "trim_text",
        "normalize_text",
        "cast",
        "deduplicate",
    ]
    after = client.post("/api/v1/run/preview", params={"limit": 10}).json()["rows"]
    assert sorted(map(tuple, after)) == sorted(map(tuple, before))
