"""Tests for the Python/R code step and the script exporter."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from data_studio.io.sniff import CsvOptions
from data_studio.pipeline import make_step
from data_studio.project.store import Project
from data_studio.script import SCRIPT_FORMATS, ScriptExportError, generate_script
from data_studio.steps import DEFAULT_REGISTRY

CSV_TEXT = "region,amount\nNorth,10\nSouth,20\nNorth,5\n"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    instance = Project.create(tmp_path / "CodeStepDemo", "Code steps")
    source = tmp_path / "sales.csv"
    source.write_text(CSV_TEXT, encoding="utf-8")
    instance.add_dataset(source, dataset_id="sales", csv_options=CsvOptions(header=True))
    try:
        yield instance
    finally:
        instance.close()


def _double_code() -> str:
    return (
        "import duckdb, os\n"
        "con = duckdb.connect()\n"
        "con.execute(f\"CREATE TABLE out AS SELECT region, amount * 2 AS doubled "
        "FROM read_parquet('{os.environ['DATA_STUDIO_INPUT_PARQUET']}')\")\n"
        "con.execute(f\"COPY out TO '{os.environ['DATA_STUDIO_OUTPUT_PARQUET']}' "
        "(FORMAT PARQUET)\")\n"
    )


def test_code_step_registered() -> None:
    assert "code" in DEFAULT_REGISTRY
    definition = DEFAULT_REGISTRY.get("code")
    assert definition.category == "code"


def test_code_step_runs_and_flows(project: Project) -> None:
    pipeline = project.load_pipeline()
    pipeline = project.add_step(
        pipeline, make_step("code", {"language": "python", "code": _double_code()})
    )
    result = project.run_pipeline()
    rows = {(row[0], row[1]) for row in result.preview(10).rows}
    assert rows == {("North", 20), ("South", 40), ("North", 10)}


def test_code_step_timeout(project: Project) -> None:
    pipeline = project.load_pipeline()
    pipeline = project.add_step(
        pipeline,
        make_step(
            "code",
            {
                "language": "python",
                "code": "import time\ntime.sleep(30)\n",
                "timeout": 1,
            },
        ),
    )
    with pytest.raises(Exception, match="timed out"):
        project.run_pipeline()


def test_code_step_reports_failure(project: Project) -> None:
    pipeline = project.load_pipeline()
    pipeline = project.add_step(
        pipeline,
        make_step(
            "code",
            {
                "language": "python",
                "code": "raise SystemExit('boom: something broke')\n",
            },
        ),
    )
    with pytest.raises(Exception, match="boom"):
        project.run_pipeline()


def test_code_step_missing_output(project: Project) -> None:
    pipeline = project.load_pipeline()
    pipeline = project.add_step(
        pipeline,
        make_step("code", {"language": "python", "code": "pass\n"}),
    )
    with pytest.raises(Exception, match="did not write"):
        project.run_pipeline()


def test_code_step_bad_language_input() -> None:
    from data_studio.steps import DEFAULT_REGISTRY, StepError  # noqa: F401

    with pytest.raises(Exception, match="python"):
        DEFAULT_REGISTRY.get("code").validate(
            {"language": "cobol", "code": "x = 1"}
        )


def _pipeline_with_code(project: Project) -> None:
    pipeline = project.load_pipeline()
    pipeline = project.add_step(
        pipeline, make_step("trim_text", {"columns": ["region"]}, step_id="trim")
    )
    pipeline = project.add_step(
        pipeline,
        make_step("code", {"language": "python", "code": _double_code()}, step_id="py"),
    )


def test_script_export_python_roundtrip(
    project: Project, tmp_path: Path
) -> None:
    _pipeline_with_code(project)
    script = generate_script(project, fmt="python")
    assert "Reproducible pipeline" in script
    assert "stage_1.parquet" in script
    assert "stage_2.parquet" not in script
    script_path = tmp_path / "pipeline_export.py"
    script_path.write_text(script, encoding="utf-8")
    output = tmp_path / "out.parquet"
    completed = subprocess.run(
        [sys.executable, str(script_path), str(project.root), str(output)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
    rows = {tuple(row) for row in duckdb.sql(
        f"SELECT * FROM read_parquet('{output.as_posix()}')"
    ).fetchall()}
    assert rows == {("North", 20), ("South", 40), ("North", 10)}


def test_script_export_python_roundtrip_multiple_code_steps(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MultiDemo"
    instance = Project.create(root, "Multi")
    source = tmp_path / "sales.csv"
    source.write_text(CSV_TEXT, encoding="utf-8")
    instance.add_dataset(source, dataset_id="sales", csv_options=CsvOptions(header=True))
    doubled = (
        "import duckdb, os\n"
        "con = duckdb.connect()\n"
        "con.execute(f\"CREATE TABLE out AS SELECT region, amount * 2 AS doubled "
        "FROM read_parquet('{os.environ['DATA_STUDIO_INPUT_PARQUET']}')\")\n"
        "con.execute(f\"COPY out TO '{os.environ['DATA_STUDIO_OUTPUT_PARQUET']}' "
        "(FORMAT PARQUET)\")\n"
    )
    boosted = (
        "import duckdb, os\n"
        "con = duckdb.connect()\n"
        "con.execute(f\"CREATE TABLE out AS SELECT upper(region) AS region, "
        "doubled + 1000 AS boosted FROM read_parquet('"
        "{os.environ['DATA_STUDIO_INPUT_PARQUET']}')\")\n"
        "con.execute(f\"COPY out TO '{os.environ['DATA_STUDIO_OUTPUT_PARQUET']}' "
        "(FORMAT PARQUET)\")\n"
    )
    pipeline = instance.load_pipeline()
    pipeline = instance.add_step(
        pipeline, make_step("trim_text", {"columns": ["region"]})
    )
    pipeline = instance.add_step(
        pipeline, make_step("code", {"language": "python", "code": doubled}, step_id="c1")
    )
    pipeline = instance.add_step(
        pipeline, make_step("code", {"language": "python", "code": boosted}, step_id="c2")
    )
    instance.run_pipeline()
    instance.close()

    instance = Project.open(root)
    script = generate_script(instance, fmt="python")
    assert "stage_1.parquet" in script
    assert "stage_2.parquet" in script
    instance.close()
    script_path = tmp_path / "pipeline_export.py"
    script_path.write_text(script, encoding="utf-8")
    output = tmp_path / "out.parquet"
    completed = subprocess.run(
        [sys.executable, str(script_path), str(root), str(output)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    rows = {
        tuple(row)
        for row in duckdb.sql(
            f"SELECT * FROM read_parquet('{output.as_posix()}')"
        ).fetchall()
    }
    assert rows == {("NORTH", 1020), ("SOUTH", 1040), ("NORTH", 1010)}


def test_script_export_requires_steps(project: Project) -> None:
    with pytest.raises(ScriptExportError, match="no steps"):
        generate_script(project)


def test_script_export_formats(project: Project) -> None:
    # Pure-SQL pipeline: both exports succeed.
    pipeline = project.load_pipeline()
    pipeline = project.add_step(
        pipeline, make_step("trim_text", {"columns": ["region"]})
    )
    python_script = generate_script(project, fmt="python")
    r_script = generate_script(project, fmt="r")
    assert "pipeline" in python_script.lower()
    assert "dbConnect" in r_script or "library" in r_script
    with pytest.raises(ScriptExportError, match="format"):
        generate_script(project, fmt="csharp")
    assert "python" in SCRIPT_FORMATS


def test_python_code_step_blocks_r_export(project: Project) -> None:
    _pipeline_with_code(project)
    with pytest.raises(ScriptExportError, match="R export"):
        generate_script(project, fmt="r")


def test_cli_script_stdout(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    import importlib
    import json

    cli_main = importlib.import_module("data_studio.cli.main")
    _pipeline_with_code(project)
    result = cli_main.main(["script", str(project.root), "--json"])
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "python"
    assert "Reproducible pipeline" in payload["script"]
