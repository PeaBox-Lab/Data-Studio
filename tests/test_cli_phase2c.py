"""Tests for the Phase 2C CLI report command."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

cli_main = importlib.import_module("data_studio.cli.main")

SERIES_VALUES = [
    52.5,
    51.5,
    51.0,
    50.5,
    50.0,
    52.0,
    53.0,
    54.5,
    56.0,
    55.5,
    55.5,
    54.5,
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
CSV_TEXT = "period,value\n" + "".join(
    f"{2015 + index // 12}-{(index % 12) + 1:02d}-01,{value}\n"
    for index, value in enumerate(SERIES_VALUES)
)


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "series.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    return path


@pytest.fixture
def project_root(tmp_path: Path, csv_file: Path) -> Path:
    root = tmp_path / "Demo"
    assert cli_main.main(["init", str(root), "--name", "Demo"]) == 0
    assert cli_main.main(["add-dataset", str(root), str(csv_file)]) == 0
    return root


def report_args(project_root: Path, **overrides: object) -> list[str]:
    args = [
        "report",
        str(project_root),
        "time_series_summary",
        "--params",
        json.dumps({"time": "period", "value": "value"}),
        "--raw",
    ]
    for key, value in overrides.items():
        if value is True:
            args.append(f"--{key}")
        else:
            args.extend([f"--{key}", str(value)])
    return args


def test_report_writes_a_default_timestamped_file(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main.main(report_args(project_root)) == 0
    output = capsys.readouterr().out
    assert "report" in output.lower()

    reports = list((project_root / "reports").glob("report-*.html"))
    assert len(reports) == 1
    text = reports[0].read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in text
    assert "Time-series summary" in text
    assert "<svg" in text


def test_report_honours_destination_title_and_theme(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_main.main(
            report_args(
                project_root,
                destination="reports/custom.html",
                title="Quarterly review",
                theme="dark",
            )
        )
        == 0
    )
    capsys.readouterr()
    written = project_root / "reports" / "custom.html"
    assert written.is_file()
    text = written.read_text(encoding="utf-8")
    assert "Quarterly review" in text
    assert "#0f172a" in text


def test_report_json_output(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_main.main(report_args(project_root, destination="reports/json.html", json=True))
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["destination"].endswith("reports\\json.html") or payload[
        "destination"
    ].endswith("reports/json.html")
    assert payload["bytes"] > 0
    assert payload["result_id"].startswith("res_")
    assert (project_root / "reports" / "json.html").is_file()


def test_report_rejects_invalid_analysis_and_path(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main.main(["report", str(project_root), "not_real"]) == 1
    assert "Unknown analysis" in capsys.readouterr().err

    assert (
        cli_main.main(report_args(project_root, destination="../escape.html")) == 1
    )
    assert ".." in capsys.readouterr().err
