"""Tests for the Phase 2A CLI analysis commands."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

cli_main = importlib.import_module("data_studio.cli.main")

CSV_TEXT = "id,region,amount\n1,North,10.5\n2,North,7\n3,South,\n4,South,20\n"


@pytest.fixture
def project_root(tmp_path: Path, csv_file: Path) -> Path:
    root = tmp_path / "Demo"
    assert cli_main.main(["init", str(root), "--name", "Demo"]) == 0
    assert cli_main.main(["add-dataset", str(root), str(csv_file)]) == 0
    return root


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "sales.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    return path


def test_analyses_catalog(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main.main(["analyses", "--json"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert {item["name"] for item in catalog} >= {
        "descriptive_stats",
        "value_counts",
        "crosstab",
        "correlation",
        "quality_profile",
    }
    assert {"name", "title", "category", "description", "params"} <= set(catalog[0])

    assert cli_main.main(["analyses"]) == 0
    output = capsys.readouterr().out
    assert "descriptive_stats" in output
    assert "quality_profile" in output


def test_analyze_runs_on_the_pipeline_by_default(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_main.main(
            [
                "add-step",
                str(project_root),
                "filter",
                "--params",
                json.dumps({"expression": "region != 'South'"}),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        cli_main.main(
            [
                "analyze",
                str(project_root),
                "descriptive_stats",
                "--params",
                json.dumps({"columns": ["amount"]}),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "descriptive_stats"
    assert payload["metrics"] == {"rows": 2, "columns": 1, "missing": 0}
    assert payload["provenance"]["step_ids"] == ["filter"]


def test_analyze_raw_dataset_and_human_output(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_main.main(
            [
                "analyze",
                str(project_root),
                "quality_profile",
                "--params",
                json.dumps({"columns": ["region", "amount"]}),
                "--raw",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Data-quality profile" in output
    assert "duplicate_rows" in output
    assert "column" in output
    assert "region" in output


def test_analyze_reports_errors(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_main.main(
            [
                "analyze",
                str(project_root),
                "value_counts",
                "--params",
                json.dumps({"column": "missing"}),
                "--raw",
            ]
        )
        == 1
    )
    assert "Unknown column" in capsys.readouterr().err

    assert cli_main.main(["analyze", str(project_root), "not_real"]) == 1
    assert "Unknown analysis" in capsys.readouterr().err
