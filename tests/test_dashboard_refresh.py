"""Tests for scheduled dashboard refresh, freshness and report artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_studio.dashboard import (
    Dashboard,
    DashboardStore,
    DashboardTile,
)
from data_studio.io.sniff import CsvOptions
from data_studio.project.store import Project
from data_studio.semantic import (
    SemanticDimension,
    SemanticMeasure,
    SemanticModel,
    SemanticModelStore,
    SemanticTable,
)

CSV_TEXT = "region,amount\nNorth,10\nSouth,20\nNorth,15\n"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    instance = Project.create(tmp_path / "DashRefresh", "Refresh")
    source = tmp_path / "sales.csv"
    source.write_text(CSV_TEXT, encoding="utf-8")
    instance.add_dataset(source, dataset_id="sales", csv_options=CsvOptions(header=True))
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def model(project: Project) -> SemanticModel:
    model = SemanticModel.derive(
        {"sales": project.dataset_schema("sales")}, name="Sales model"
    )
    SemanticModelStore(project).save(model)
    return model


def _dashboard(schedule: str = "daily", report: bool = False) -> Dashboard:
    return Dashboard(
        id="refresh-demo",
        title="Refresh demo",
        tiles=(
            DashboardTile(
                "total",
                "Total revenue",
                kind="kpi",
                measures=("total_amount",),
            ),
        ),
        schedule=schedule,
        report_path=f"reports/dashboard-refresh-demo.html" if report else None,
    )


def test_saved_dashboard_records_fingerprint_and_refreshed_at(
    project: Project, model: SemanticModel
) -> None:
    store = DashboardStore(project)
    saved = store.save(_dashboard(), model=model, overwrite=True)
    assert saved.input_fingerprint.startswith("sha256:")
    assert saved.refreshed_at
    summary = store.describe(saved, model=model)
    assert summary["status"] == "fresh"
    assert summary["due"] is False
    assert summary["next_refresh"]


def test_fingerprint_moves_when_inputs_change(
    project: Project, model: SemanticModel
) -> None:
    store = DashboardStore(project)
    saved = store.save(_dashboard(), model=model, overwrite=True)
    first = saved.input_fingerprint
    # Replacing the dataset changes the recorded checksum.
    replacement = project.root / "sales.csv"
    replacement.write_text(
        "region,amount\nNorth,99\nSouth,20\nNorth,15\n", encoding="utf-8"
    )
    project.add_dataset(
        replacement, dataset_id="sales", replace=True,
        csv_options=CsvOptions(header=True),
    )
    summary = store.describe(saved, model=model)
    assert summary["status"] == "stale"
    assert summary["due"] is True
    refreshed = store.refresh(saved.id)
    assert refreshed.input_fingerprint != first
    assert store.describe(refreshed, model=model)["status"] == "fresh"


def _make_stale(project: Project) -> None:
    """Change the backing dataset so saved fingerprints go stale."""

    replacement = project.root / "sales.csv"
    replacement.write_text(
        "region,amount\nNorth,99\nSouth,20\nNorth,15\n", encoding="utf-8"
    )
    project.add_dataset(
        replacement,
        dataset_id="sales",
        replace=True,
        csv_options=CsvOptions(header=True),
    )


def test_refresh_due_only_touches_scheduled(
    project: Project, model: SemanticModel
) -> None:
    store = DashboardStore(project)
    store.save(_dashboard(schedule="daily"), model=model, overwrite=True)
    store.save(
        Dashboard(
            id="manual-demo",
            title="Manual demo",
            tiles=(
                DashboardTile(
                    "total", "Total revenue", kind="kpi", measures=("total_amount",)
                ),
            ),
            schedule="manual",
        ),
        model=model,
        overwrite=True,
    )
    _make_stale(project)
    refreshed, failures = store.refresh_due()
    assert failures == []
    assert [item.id for item in refreshed] == ["refresh-demo"]
    all_refreshed, failures = store.refresh_all()
    assert failures == []
    assert {item.id for item in all_refreshed} == {"refresh-demo", "manual-demo"}


def test_on_open_scope(project: Project, model: SemanticModel) -> None:
    store = DashboardStore(project)
    store.save(_dashboard(schedule="on_open"), model=model, overwrite=True)
    store.save(
        Dashboard(
            id="hourly-demo",
            title="Hourly demo",
            tiles=(
                DashboardTile(
                    "total", "Total revenue", kind="kpi", measures=("total_amount",)
                ),
            ),
            schedule="hourly",
        ),
        model=model,
        overwrite=True,
    )
    _make_stale(project)
    refreshed, failures = store.refresh_due(only_on_open=True)
    assert failures == []
    assert [item.id for item in refreshed] == ["refresh-demo"]


def test_refresh_writes_report_artifact(
    project: Project, model: SemanticModel
) -> None:
    store = DashboardStore(project)
    saved = store.save(_dashboard(report=True), model=model, overwrite=True)
    assert saved.report_path == "reports/dashboard-refresh-demo.html"
    # Force the artifact path by refreshing.
    refreshed = store.refresh(saved.id)
    artifact = project.root / refreshed.report_path
    assert artifact.is_file()
    assert "Total revenue" in artifact.read_text(encoding="utf-8")


def test_refresh_reports_broken_dashboard(
    project: Project, model: SemanticModel
) -> None:
    store = DashboardStore(project)
    saved = store.save(_dashboard(), model=model, overwrite=True)
    # Replace the model with one that no longer defines the KPI's measure.
    slim = SemanticModel(
        name="Slim",
        tables=(SemanticTable("sales", "sales"),),
        dimensions=(SemanticDimension("region", "sales", "region"),),
        measures=(),
        default_table="sales",
    )
    SemanticModelStore(project).save(slim)
    refreshed, failures = store.refresh_due()
    assert refreshed == []
    assert [item["id"] for item in failures] == ["refresh-demo"]
    assert "total_amount" in failures[0]["error"]


def test_cli_refresh_refreshes_dashboards(
    project: Project, model: SemanticModel, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib

    cli_main = importlib.import_module("data_studio.cli.main")
    store = DashboardStore(project)
    store.save(_dashboard(), model=model, overwrite=True)
    result = cli_main.main(["refresh", str(project.root), "--all", "--json"])
    assert result == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["dashboards"][0]["id"] == "refresh-demo"
    assert payload["dashboard_failures"] == []


def test_cli_dashboard_save_with_schedule_and_parameters(
    project: Project, model: SemanticModel, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib
    import json

    cli_main = importlib.import_module("data_studio.cli.main")
    tiles = json.dumps(
        [
            {
                "id": "total",
                "title": "Total revenue",
                "kind": "kpi",
                "measures": ["total_amount"],
            }
        ]
    )
    parameters = json.dumps(
        [{"name": "cap", "type": "number", "default": 5, "minimum": 1}]
    )
    result = cli_main.main(
        [
            "dashboard-save",
            str(project.root),
            "--title",
            "CLI dashboard",
            "--tiles",
            tiles,
            "--parameters",
            parameters,
            "--schedule",
            "daily",
            "--report",
            "--json",
        ]
    )
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schedule"] == "daily"
    assert payload["report_path"] == "reports/dashboard-cli-dashboard.html"
    assert [item["name"] for item in payload["parameters"]] == ["cap"]


def test_cli_date_table(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib
    import json

    cli_main = importlib.import_module("data_studio.cli.main")
    result = cli_main.main(
        [
            "date-table",
            str(project.root),
            "--start",
            "2023-01-01",
            "--end",
            "2023-12-31",
            "--json",
        ]
    )
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset"] == "calendar"
    assert payload["rows"] == 365


def test_dashboard_describe_reports_missing_model(
    project: Project,
) -> None:
    store = DashboardStore(project)
    saved = store.save(_dashboard())
    summary = store.describe(saved)
    assert summary["status"] == "missing"
    assert summary["due"] is True
