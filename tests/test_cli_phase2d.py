"""Tests for the Phase 2D saved-analysis CLI commands."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

cli_main = importlib.import_module("data_studio.cli.main")

CSV_TEXT = "region,amount\nNorth,10\nSouth,20\nNorth,15\n"


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "sales.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    return path


@pytest.fixture
def project_root(tmp_path: Path, csv_file: Path) -> Path:
    root = tmp_path / "Demo"
    assert cli_main.main(["init", str(root), "--name", "Demo"]) == 0
    assert cli_main.main(["add-dataset", str(root), str(csv_file)]) == 0
    return root


def params() -> str:
    return json.dumps({"columns": ["amount"]})


def test_save_list_refresh_and_remove(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_main.main(
            [
                "save-analysis",
                str(project_root),
                "Revenue",
                "descriptive_stats",
                "--params",
                params(),
                "--dataset",
                "sales",
                "--raw",
                "--schedule",
                "daily",
                "--report",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Saved" in output
    assert (project_root / "analyses" / "revenue.json").is_file()
    assert (project_root / "reports" / "revenue.html").is_file()

    assert cli_main.main(["saved", str(project_root), "--json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["analyses"][0]["id"] == "revenue"
    assert listing["analyses"][0]["status"] == "fresh"

    assert cli_main.main(["saved", str(project_root), "revenue"]) == 0
    detail = capsys.readouterr().out
    assert "Descriptive statistics" in detail
    assert "descriptive_stats" in detail

    assert cli_main.main(["refresh", str(project_root), "--all", "--json"]) == 0
    refreshed = json.loads(capsys.readouterr().out)
    assert refreshed["refreshed"][0]["id"] == "revenue"
    assert refreshed["failed"] == []

    assert cli_main.main(["remove-saved", str(project_root), "revenue"]) == 0
    assert not (project_root / "analyses" / "revenue.json").exists()


def test_refresh_due_skips_manual(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_main.main(
            [
                "save-analysis",
                str(project_root),
                "Manual",
                "descriptive_stats",
                "--params",
                params(),
                "--dataset",
                "sales",
                "--raw",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert cli_main.main(["refresh", str(project_root), "--due", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["refreshed"] == []


def test_saved_command_reports_errors(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main.main(["saved", str(project_root), "missing"]) == 1
    assert "does not exist" in capsys.readouterr().err

    assert cli_main.main(["remove-saved", str(project_root), "missing"]) == 1
    assert "does not exist" in capsys.readouterr().err

    with pytest.raises(SystemExit) as error:
        cli_main.main(
            [
                "save-analysis",
                str(project_root),
                "Bad",
                "descriptive_stats",
                "--schedule",
                "sometimes",
            ]
        )
    assert error.value.code == 2
