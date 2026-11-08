"""Tests for the folder project store (datasets, pipeline, run, export, integrity)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from data_studio.kernel.schema import Column, DataType, Role, TableSchema
from data_studio.pipeline import make_step
from data_studio.project import (
    PIPELINE_FILENAME,
    Project,
    ProjectError,
    open_project,
)
from data_studio.project.manifest import MANIFEST_FILENAME

CSV_TEXT = (
    "id,region,amount\n"
    "1, North ,10.5\n"
    "2,north,7\n"
    "3,South,\n"
)


@pytest.fixture
def project(tmp_path: Path):
    instance = Project.create(tmp_path / "Demo", "Demo")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "sales.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    return path


def import_sales(project: Project, csv_file: Path) -> str:
    entry = project.add_dataset(csv_file, dataset_id="sales")
    return entry.id


def add_clean_steps(project: Project) -> None:
    pipeline = project.load_pipeline()
    pipeline = pipeline.add_step(make_step("trim_text", {"columns": ["region"]}))
    pipeline = pipeline.add_step(
        make_step("normalize_text", {"columns": ["region"], "case": "title"})
    )
    pipeline = pipeline.add_step(make_step("cast", {"column": "amount", "dtype": "float"}))
    pipeline = pipeline.add_step(make_step("deduplicate", {"columns": ["id"]}))
    project.save_pipeline(pipeline)


# -- lifecycle -------------------------------------------------------------


def test_create_writes_a_manifest(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "Demo", "Demo")
    try:
        assert project.name == "Demo"
        assert (project.root / MANIFEST_FILENAME).is_file()
        assert project.datasets() == []
        assert project.dataset_ids() == ()
        assert project.manifest.active_dataset is None
        assert project.pipeline_path == project.root / "steps" / PIPELINE_FILENAME
        payload = project.to_dict()
        assert payload["name"] == "Demo"
        assert payload["datasets"] == []
        assert payload["root"] == str(project.root)
        assert len(payload["project_id"]) == 36
    finally:
        project.close()


def test_create_uses_directory_name_and_refuses_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "Sales Project"
    project = Project.create(root)
    assert project.name == "Sales Project"
    project.close()
    with pytest.raises(FileExistsError):
        Project.create(root)


def test_open_missing_project_raises(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="No project"):
        Project.open(tmp_path / "missing")


def test_open_project_helper(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    Project.create(root, "Demo").close()
    project = open_project(root)
    try:
        assert project.name == "Demo"
    finally:
        project.close()


def test_context_manager_closes_the_engine(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    Project.create(root).close()
    with Project.open(root) as project:
        assert project.engine.query_one("SELECT 1") == (1,)


# -- datasets --------------------------------------------------------------


def test_import_csv_creates_parquet_and_manifest_entry(
    project: Project, csv_file: Path
) -> None:
    entry = project.add_dataset(csv_file, dataset_id="sales")
    assert entry.id == "sales"
    assert entry.path == "datasets/sales.parquet"
    assert entry.format.value == "parquet"
    assert entry.rows == 3
    assert entry.source == str(csv_file.resolve())
    assert entry.schema.names == ("id", "region", "amount")
    assert entry.schema.column("id").role is Role.IDENTIFIER
    assert (project.root / entry.path).is_file()
    assert project.manifest.active_dataset == "sales"

    summaries = project.datasets()
    assert len(summaries) == 1
    assert summaries[0].columns == 3
    assert summaries[0].to_dict()["rows"] == 3
    assert project.dataset_schema("sales") == entry.schema
    assert project.count("sales") == 3
    assert [row for row in project.preview("sales", 2).to_records()] == [
        {"id": 1, "region": " North ", "amount": 10.5},
        {"id": 2, "region": "north", "amount": 7.0},
    ]


def test_import_defaults_the_dataset_id_to_the_file_stem(
    project: Project, csv_file: Path
) -> None:
    entry = project.add_dataset(csv_file)
    assert entry.id == "sales"


def test_import_duplicate_ids_get_a_suffix(project: Project, csv_file: Path) -> None:
    first = project.add_dataset(csv_file, dataset_id="sales")
    second = project.add_dataset(csv_file, dataset_id="sales")
    assert first.id == "sales"
    assert second.id == "sales_2"
    assert second.path == "datasets/sales_2.parquet"
    assert project.dataset_ids() == ("sales", "sales_2")


def test_replace_dataset_keeps_the_id(project: Project, csv_file: Path) -> None:
    project.add_dataset(csv_file, dataset_id="sales")
    changed = csv_file.with_name("changed.csv")
    changed.write_text("id,region,amount\n9,West,1\n", encoding="utf-8")
    entry = project.add_dataset(changed, dataset_id="sales", replace=True)
    assert entry.id == "sales"
    assert entry.rows == 1
    assert project.count("sales") == 1
    with pytest.raises(ProjectError, match="Cannot replace"):
        project.add_dataset(changed, dataset_id="missing", replace=True)


def test_unsafe_dataset_ids_are_sanitised(project: Project, csv_file: Path) -> None:
    entry = project.add_dataset(csv_file, dataset_id="bad id!")
    assert entry.id == "bad_id_"
    assert entry.path == "datasets/bad_id_.parquet"


def test_import_missing_file(project: Project, tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="not found"):
        project.add_dataset(tmp_path / "missing.csv")


def test_import_bytes(project: Project) -> None:
    entry = project.add_dataset_bytes(CSV_TEXT.encode("utf-8"), "uploaded.csv")
    assert entry.id == "uploaded"
    assert entry.source == "uploaded.csv"
    assert entry.rows == 3
    with pytest.raises(TypeError):
        project.add_dataset_bytes("text", "bad.csv")  # type: ignore[arg-type]


def test_import_excel_json_and_parquet(project: Project, tmp_path: Path) -> None:
    frame = pd.DataFrame({"id": [1, 2], "label": ["a", "b"]})
    excel = tmp_path / "book.xlsx"
    with pd.ExcelWriter(excel, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="data", index=False)
    entry = project.add_dataset(excel, dataset_id="excel", sheet_name="data")
    assert entry.rows == 2

    json_path = tmp_path / "data.json"
    frame.to_json(json_path, orient="records")
    entry = project.add_dataset(json_path, dataset_id="json")
    assert entry.rows == 2

    parquet = tmp_path / "data.parquet"
    frame.to_parquet(parquet, index=False)
    entry = project.add_dataset(parquet, dataset_id="parquet")
    assert entry.rows == 2
    assert project.dataset_ids() == ("excel", "json", "parquet")


def test_dataset_tables_excludes_the_base(project: Project, csv_file: Path) -> None:
    project.add_dataset(csv_file, dataset_id="sales")
    project.add_dataset(csv_file, dataset_id="other")
    tables = project.dataset_tables(exclude="sales")
    assert set(tables) == {"other"}


def test_remove_dataset_deletes_its_file(project: Project, csv_file: Path) -> None:
    entry = project.add_dataset(csv_file, dataset_id="sales")
    stored = project.root / entry.path
    project.remove_dataset("sales")
    assert not stored.exists()
    assert project.dataset_ids() == ()
    assert project.manifest.active_dataset is None
    with pytest.raises(KeyError):
        project.remove_dataset("sales")


def test_missing_dataset_file_raises(tmp_path: Path, csv_file: Path) -> None:
    root = tmp_path / "Demo"
    project = Project.create(root)
    entry = project.add_dataset(csv_file, dataset_id="sales")
    project.close()
    (root / entry.path).unlink()
    reopened = Project.open(root)
    try:
        with pytest.raises(ProjectError, match="missing its file"):
            reopened.dataset_table("sales")
    finally:
        reopened.close()


# -- pipeline --------------------------------------------------------------


def test_load_pipeline_defaults_to_the_active_dataset(
    project: Project, csv_file: Path
) -> None:
    pipeline = project.load_pipeline()
    assert pipeline.steps == ()
    assert pipeline.dataset is None
    project.add_dataset(csv_file, dataset_id="sales")
    assert project.load_pipeline().dataset == "sales"


def test_pipeline_roundtrip(project: Project, csv_file: Path) -> None:
    project.add_dataset(csv_file, dataset_id="sales")
    add_clean_steps(project)
    assert project.pipeline_path.is_file()
    pipeline = project.load_pipeline()
    assert [step.operation for step in pipeline.steps] == [
        "trim_text",
        "normalize_text",
        "cast",
        "deduplicate",
    ]
    saved = json.loads(project.pipeline_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "1.0"
    assert saved["dataset"] == "sales"

    project.undo(project.load_pipeline())
    assert [step.operation for step in project.load_pipeline().steps] == [
        "trim_text",
        "normalize_text",
        "cast",
    ]
    project.remove_step(project.load_pipeline(), "cast")
    assert [step.operation for step in project.load_pipeline().steps] == [
        "trim_text",
        "normalize_text",
    ]


def test_invalid_pipeline_document_is_reported(project: Project) -> None:
    project.pipeline_path.parent.mkdir(parents=True, exist_ok=True)
    project.pipeline_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProjectError, match="Invalid pipeline"):
        project.load_pipeline()


def test_save_pipeline_type_check(project: Project) -> None:
    with pytest.raises(TypeError):
        project.save_pipeline("nope")  # type: ignore[arg-type]


# -- run and export --------------------------------------------------------


def test_run_pipeline_and_export(project: Project, csv_file: Path, tmp_path: Path) -> None:
    import_sales(project, csv_file)
    add_clean_steps(project)
    result = project.run_pipeline()
    assert result.rows == 3
    assert result.dataset == "sales"
    assert result.cache_hits == 0
    records = result.preview(10).to_records()
    assert {row["region"] for row in records} == {"North", "South"}
    assert result.schema.column("amount").dtype is DataType.FLOAT

    again = project.run_pipeline()
    assert again.cache_hits == 4

    destination = tmp_path / "out.parquet"
    written = project.export(result, destination)
    assert written == destination.resolve()
    assert destination.is_file()
    assert project.verify() == []

    with pytest.raises(TypeError):
        project.export("nope", tmp_path / "x.parquet")  # type: ignore[arg-type]


def test_run_without_a_dataset_fails(project: Project) -> None:
    with pytest.raises(ProjectError, match="no active dataset"):
        project.run_pipeline()


def test_run_can_override_the_dataset(project: Project, csv_file: Path) -> None:
    project.add_dataset(csv_file, dataset_id="sales")
    project.add_dataset(csv_file, dataset_id="other")
    result = project.run_pipeline(dataset_id="other", count_rows=False)
    assert result.dataset == "other"
    assert result.rows is None


def test_reopen_reproduces_the_pipeline_result(
    tmp_path: Path, csv_file: Path
) -> None:
    root = tmp_path / "Demo"
    project = Project.create(root)
    import_sales(project, csv_file)
    add_clean_steps(project)
    expected = project.run_pipeline().preview(10).to_records()
    project.close()

    reopened = Project.open(root, verify=True)
    try:
        pipeline = reopened.load_pipeline()
        assert [step.operation for step in pipeline.steps] == [
            "trim_text",
            "normalize_text",
            "cast",
            "deduplicate",
        ]
        actual = reopened.run_pipeline(pipeline).preview(10).to_records()
        assert sorted(actual, key=lambda row: str(row.get("id"))) == sorted(
            expected, key=lambda row: str(row.get("id"))
        )
    finally:
        reopened.close()


# -- integrity -------------------------------------------------------------


def test_verify_detects_tampering(project: Project, csv_file: Path) -> None:
    entry = project.add_dataset(csv_file, dataset_id="sales")
    stored = project.root / entry.path
    stored.write_bytes(b"tampered")
    problems = project.verify()
    assert any("checksum mismatch" in problem for problem in problems)
    with pytest.raises(ProjectError, match="integrity"):
        project.verify_or_raise()


def test_verify_reports_missing_files(project: Project, csv_file: Path) -> None:
    entry = project.add_dataset(csv_file, dataset_id="sales")
    (project.root / entry.path).unlink()
    assert any("missing" in problem for problem in project.verify())


def test_open_with_verify_rejects_a_bad_checksum(
    tmp_path: Path, csv_file: Path
) -> None:
    root = tmp_path / "Demo"
    project = Project.create(root)
    entry = project.add_dataset(csv_file, dataset_id="sales")
    project.close()
    (root / entry.path).write_bytes(b"tampered")
    with pytest.raises(ProjectError, match="integrity"):
        Project.open(root, verify=True)
    # Opening without verification still works so the user can repair the project.
    repaired = Project.open(root, verify=False)
    try:
        assert repaired.dataset_ids() == ("sales",)
    finally:
        repaired.close()


def test_schema_roles_survive_import(project: Project, csv_file: Path) -> None:
    entry = project.add_dataset(csv_file, dataset_id="sales")
    assert entry.schema.column("region").role is Role.DIMENSION
    assert entry.schema.column("amount").role is Role.MEASURE
    assert isinstance(entry.schema.column("id"), Column)
    assert TableSchema.from_dict(entry.schema.to_dict()) == entry.schema
