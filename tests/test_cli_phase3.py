"""Tests for the Phase 3 semantic and dashboard CLI commands."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

cli_main = importlib.import_module("data_studio.cli.main")

CSV_TEXT = "region,amount,units\nNorth,10,1\nSouth,20,2\nNorth,15,3\n"


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "Demo"
    csv = tmp_path / "sales.csv"
    csv.write_text(CSV_TEXT, encoding="utf-8")
    assert cli_main.main(["init", str(root), "--name", "Demo"]) == 0
    assert cli_main.main(["add-dataset", str(root), str(csv)]) == 0
    return root


def test_model_commands(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main.main(["model-derive", str(project_root)]) == 0
    output = capsys.readouterr().out
    assert "Derived" in output
    assert (project_root / "models" / "semantic.json").is_file()

    assert cli_main.main(["model-show", str(project_root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["exists"] is True
    assert payload["problems"] == []
    assert payload["model"]["tables"][0]["name"] == "sales"

    assert cli_main.main(["query", str(project_root), "--dimensions", "region",
                         "--measures", "total_amount", "--order", "total_amount desc",
                         "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["rows"][0] == {"region": "North", "total_amount": 25}

    assert cli_main.main(["query", str(project_root), "--measures", "total_amount"]) == 0
    text = capsys.readouterr().out
    assert "45" in text

    assert cli_main.main(["model-remove", str(project_root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"removed": "semantic"}
    assert not (project_root / "models" / "semantic.json").is_file()


def test_dashboard_commands(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main.main(["model-derive", str(project_root)]) == 0
    capsys.readouterr()

    tiles = json.dumps(
        [
            {"id": "total", "title": "Total", "kind": "kpi",
             "measures": ["total_amount"]},
            {"id": "by-region", "title": "By region", "kind": "chart",
             "dimensions": ["region"], "measures": ["total_amount"],
             "chart": "column"},
        ]
    )
    slicers = json.dumps([{"id": "region", "dimension": "region"}])
    assert (
        cli_main.main(
            [
                "dashboard-save",
                str(project_root),
                "--title",
                "Sales dashboard",
                "--tiles",
                tiles,
                "--slicers",
                slicers,
            ]
        )
        == 0
    )
    assert "Saved dashboard" in capsys.readouterr().out
    assert (project_root / "dashboards" / "sales-dashboard.json").is_file()

    assert cli_main.main(["dashboard-list", str(project_root), "--json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["dashboards"][0]["id"] == "sales-dashboard"

    assert (
        cli_main.main(
            [
                "dashboard-render",
                str(project_root),
                "sales-dashboard",
                "--select",
                "region=North",
                "--json",
            ]
        )
        == 0
    )
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["tiles"][0]["result"]["rows"] == [{"total_amount": 25}]
    assert rendered["tiles"][1]["svg"].startswith("<svg")

    assert (
        cli_main.main(
            ["dashboard-report", str(project_root), "sales-dashboard"]
        )
        == 0
    )
    report = project_root / "reports" / "dashboard-sales-dashboard.html"
    assert report.is_file()
    assert "<svg" in report.read_text(encoding="utf-8")
    capsys.readouterr()

    assert (
        cli_main.main(
            ["dashboard-remove", str(project_root), "sales-dashboard", "--json"]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"removed": "sales-dashboard"}
