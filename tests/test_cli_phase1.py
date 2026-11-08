"""Tests for the Phase 1 CLI commands."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

cli_main = importlib.import_module("data_studio.cli.main")

CSV_TEXT = "id,region,amount\n1, North ,10.5\n2,north,7\n3,South,\n"


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "Demo"
    assert cli_main.main(["init", str(root), "--name", "Demo"]) == 0
    return root


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "sales.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    return path


def add_dataset(root: Path, source: Path) -> None:
    assert cli_main.main(["add-dataset", str(root), str(source)]) == 0


def add_clean_steps(root: Path) -> None:
    commands = [
        ("trim_text", {"columns": ["region"]}),
        ("normalize_text", {"columns": ["region"], "case": "title"}),
        ("cast", {"column": "amount", "dtype": "float"}),
        ("deduplicate", {"columns": ["id"]}),
    ]
    for operation, params in commands:
        assert (
            cli_main.main(
                [
                    "add-step",
                    str(root),
                    operation,
                    "--params",
                    json.dumps(params),
                ]
            )
            == 0
        )


def test_add_dataset_and_list_schema_preview(
    project_root: Path, csv_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main.main(["add-dataset", str(project_root), str(csv_file)]) == 0
    output = capsys.readouterr().out
    assert "Imported 'sales': 3 rows, 3 columns" in output
    assert "datasets/sales.parquet" in output

    assert cli_main.main(["datasets", str(project_root), "--json"]) == 0
    datasets = json.loads(capsys.readouterr().out)
    assert datasets[0]["id"] == "sales"
    assert datasets[0]["rows"] == 3
    assert datasets[0]["columns"] == 3

    assert cli_main.main(["schema", str(project_root), "sales", "--json"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert [column["name"] for column in schema["columns"]] == ["id", "region", "amount"]
    assert schema["columns"][0]["role"] == "identifier"

    assert (
        cli_main.main(
            ["preview", str(project_root), "sales", "--limit", "2", "--json"]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert len(preview["rows"]) == 2
    assert preview["rows"][0] == [1, " North ", 10.5]


def test_datasets_human_output(project_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main.main(["datasets", str(project_root)]) == 0
    assert "No datasets" in capsys.readouterr().out


def test_add_dataset_options(
    project_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    semicolon = tmp_path / "euro.csv"
    semicolon.write_text("id;amount\n1;10,5\n2;7\n", encoding="utf-8")
    assert (
        cli_main.main(
            [
                "add-dataset",
                str(project_root),
                str(semicolon),
                "--id",
                "euro",
                "--delimiter",
                ";",
                "--json",
            ]
        )
        == 0
    )
    entry = json.loads(capsys.readouterr().out)
    assert entry["id"] == "euro"
    assert entry["rows"] == 2

    assert (
        cli_main.main(["add-dataset", str(project_root), str(semicolon), "--id", "euro"])
        == 0
    )
    assert "Imported 'euro_2'" in capsys.readouterr().out

    assert (
        cli_main.main(
            ["add-dataset", str(project_root), str(semicolon), "--id", "euro", "--replace"]
        )
        == 0
    )
    assert "Imported 'euro'" in capsys.readouterr().out


def test_add_dataset_missing_file_fails(
    project_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main.main(["add-dataset", str(project_root), str(tmp_path / "missing.csv")]) == 1
    assert "ERROR:" in capsys.readouterr().err


def test_steps_and_pipeline_editing(
    project_root: Path, csv_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_dataset(project_root, csv_file)
    capsys.readouterr()

    assert (
        cli_main.main(
            [
                "add-step",
                str(project_root),
                "trim_text",
                "--params",
                '{"columns": ["region"]}',
                "--title",
                "Trim region",
            ]
        )
        == 0
    )
    assert "Added step 'trim_text'" in capsys.readouterr().out

    assert (
        cli_main.main(
            [
                "add-step",
                str(project_root),
                "sort",
                "--params",
                '{"columns": ["amount"]}',
                "--position",
                "0",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert cli_main.main(["steps", str(project_root), "--json"]) == 0
    pipeline = json.loads(capsys.readouterr().out)
    assert [step["operation"] for step in pipeline["steps"]] == ["sort", "trim_text"]
    assert pipeline["dataset"] == "sales"

    assert cli_main.main(["steps", str(project_root)]) == 0
    output = capsys.readouterr().out
    assert "sort" in output
    assert "trim_text" in output

    assert cli_main.main(["remove-step", str(project_root), "sort"]) == 0
    assert "Removed step 'sort'" in capsys.readouterr().out

    assert cli_main.main(["undo", str(project_root)]) == 0
    assert "Undid the last step" in capsys.readouterr().out
    assert cli_main.main(["steps", str(project_root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["steps"] == []


def test_add_step_reads_params_from_a_file(
    project_root: Path, csv_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_dataset(project_root, csv_file)
    params = tmp_path / "params.json"
    params.write_text('{"columns": ["region"]}', encoding="utf-8")
    assert (
        cli_main.main(
            ["add-step", str(project_root), "trim_text", "--params", f"@{params}"]
        )
        == 0
    )
    capsys.readouterr()


def test_add_step_rejects_bad_params(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_main.main(
            ["add-step", str(project_root), "trim_text", "--params", "{not json"]
        )
        == 1
    )
    assert "ERROR:" in capsys.readouterr().err

    assert (
        cli_main.main(["add-step", str(project_root), "trim_text", "--params", "[]"])
        == 1
    )
    assert "JSON object" in capsys.readouterr().err


def test_run_pipeline_json_and_human(
    project_root: Path, csv_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_dataset(project_root, csv_file)
    add_clean_steps(project_root)
    capsys.readouterr()

    assert cli_main.main(["run", str(project_root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"] == 3
    assert payload["dataset"] == "sales"
    assert payload["cache_hits"] == 0
    assert len(payload["steps"]) == 4

    assert cli_main.main(["run", str(project_root)]) == 0
    output = capsys.readouterr().out
    assert "Ran 'Pipeline' on 'sales': 3 rows" in output
    assert "Cache hits: 0/4" in output
    assert "ran" in output

    assert cli_main.main(["run", str(project_root), "--no-count", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] is None


def test_run_without_steps_still_counts(
    project_root: Path, csv_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_dataset(project_root, csv_file)
    capsys.readouterr()
    assert cli_main.main(["run", str(project_root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"] == 3
    assert payload["steps"] == []


def test_run_failure_reports_the_step(
    project_root: Path, csv_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_dataset(project_root, csv_file)
    assert (
        cli_main.main(
            [
                "add-step",
                str(project_root),
                "select",
                "--params",
                '{"columns": ["missing"]}',
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert cli_main.main(["run", str(project_root)]) == 1
    assert "select" in capsys.readouterr().err


def test_export(
    project_root: Path, csv_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_dataset(project_root, csv_file)
    add_clean_steps(project_root)
    capsys.readouterr()

    destination = tmp_path / "out.parquet"
    assert (
        cli_main.main(["export", str(project_root), str(destination), "--json"]) == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"] == 3
    assert Path(payload["destination"]) == destination.resolve()
    assert destination.is_file()

    assert cli_main.main(["export", str(project_root), str(destination), "--no-overwrite"]) == 1
    assert "exists" in capsys.readouterr().err.lower()

    csv_destination = tmp_path / "out.csv"
    assert cli_main.main(["export", str(project_root), str(csv_destination)]) == 0
    assert "Exported 3 rows" in capsys.readouterr().out
    assert csv_destination.is_file()


def test_export_bad_extension(
    project_root: Path, csv_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_dataset(project_root, csv_file)
    capsys.readouterr()
    assert cli_main.main(["export", str(project_root), str(tmp_path / "out.xml")]) == 1
    assert "Cannot export" in capsys.readouterr().err


def test_commands_fail_for_a_missing_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = str(tmp_path / "missing")
    assert cli_main.main(["datasets", missing]) == 1
    assert "No project" in capsys.readouterr().err
    assert cli_main.main(["run", missing]) == 1
    capsys.readouterr()


def test_add_dataset_requires_an_open_project(
    csv_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Pointing add-dataset at a directory without a manifest yields a project error.
    assert cli_main.main(["add-dataset", str(tmp_path), str(csv_file)]) == 1
    assert "No project" in capsys.readouterr().err


def test_serve_with_project_sets_the_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    root = tmp_path / "Demo"
    assert cli_main.main(["init", str(root), "--name", "Demo"]) == 0
    capsys.readouterr()

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    monkeypatch.delenv("DATA_STUDIO_PROJECT", raising=False)

    assert cli_main.main(["serve", "--project", str(root), "--port", "8899"]) == 0
    assert os.environ["DATA_STUDIO_PROJECT"] == str(root.resolve())
    assert calls[0]["port"] == 8899
    assert calls[0]["factory"] is True
