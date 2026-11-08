"""Tests for the `data-studio` command line interface."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from data_studio.kernel.schema import Column, DataType, TableSchema
from data_studio.project.manifest import (
    MANIFEST_FILENAME,
    DatasetEntry,
    DatasetFormat,
    load_manifest,
    save_manifest,
    sha256_file,
)
from data_studio.version import __version__

cli_main = importlib.import_module("data_studio.cli.main")


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_main.main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_parser_requires_a_command() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_main.main([])
    assert excinfo.value.code == 2


def test_init_and_verify_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "demo"

    assert cli_main.main(["init", str(root), "--name", "Demo"]) == 0
    init_output = capsys.readouterr().out
    assert "Created project 'Demo'" in init_output
    assert (root / MANIFEST_FILENAME).is_file()

    assert cli_main.main(["verify-project", str(root)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project"] == "Demo"
    assert payload["status"] == "ok"
    assert payload["datasets"] == []
    assert payload["problems"] == []


def test_init_defaults_name_to_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "sales project"
    assert cli_main.main(["init", str(root)]) == 0
    assert load_manifest(root).name == "sales project"
    capsys.readouterr()


def test_init_refuses_to_overwrite_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "demo"
    assert cli_main.main(["init", str(root), "--name", "Demo"]) == 0
    capsys.readouterr()

    assert cli_main.main(["init", str(root), "--name", "Again"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_verify_missing_project_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main.main(["verify-project", str(tmp_path / "missing")]) == 1
    assert "No project.json" in capsys.readouterr().err


def test_verify_reports_checksum_problems(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "demo"
    assert cli_main.main(["init", str(root), "--name", "Demo"]) == 0
    capsys.readouterr()

    data_path = root / "datasets" / "sales.parquet"
    data_path.write_bytes(b"original payload")
    entry = DatasetEntry(
        id="sales",
        path="datasets/sales.parquet",
        format=DatasetFormat.PARQUET,
        schema=TableSchema(columns=(Column("id", DataType.INTEGER, nullable=False),)),
        checksum_sha256=sha256_file(data_path),
        rows=1,
    )
    manifest = load_manifest(root).add_dataset(entry)
    save_manifest(root, manifest, touch=False)
    data_path.write_bytes(b"tampered payload")

    assert cli_main.main(["verify-project", str(root)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "problems"
    assert payload["datasets"] == ["sales"]
    assert any("checksum mismatch" in problem for problem in payload["problems"])


def test_doctor_passes_in_the_project_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli_main.main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert f"data-studio {__version__}" in output
    assert "core packages" in output
    assert "project     none in the current directory" in output
    assert "All checks passed." in output


def test_doctor_reports_an_existing_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "demo"
    assert cli_main.main(["init", str(root), "--name", "Demo"]) == 0
    capsys.readouterr()

    monkeypatch.chdir(root)
    assert cli_main.main(["doctor"]) == 0
    assert "project     Demo (0 datasets)" in capsys.readouterr().out


def test_doctor_fails_when_core_packages_are_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "_package_version", lambda _: "MISSING")

    assert cli_main.main(["doctor"]) == 1
    output = capsys.readouterr().out
    assert "7 core package(s) missing" in output


def test_serve_delegates_to_uvicorn(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    calls: list[dict[str, object]] = []

    def fake_run(app: str, **kwargs: object) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(uvicorn, "run", fake_run)

    assert cli_main.main(["serve", "--host", "0.0.0.0", "--port", "9999"]) == 0
    assert "http://0.0.0.0:9999" in capsys.readouterr().out
    assert calls == [
        {
            "app": "data_studio.api.app:create_app",
            "factory": True,
            "host": "0.0.0.0",
            "port": 9999,
            "reload": False,
            "log_level": "info",
        }
    ]
