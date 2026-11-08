"""Tests for the folder project format and integrity checks."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from data_studio.kernel.schema import Column, DataType, TableSchema
from data_studio.project.manifest import (
    CURRENT_PROJECT_MANIFEST_VERSION,
    MANIFEST_FILENAME,
    DatasetEntry,
    DatasetFormat,
    ProjectManifest,
    init_project,
    load_manifest,
    safe_project_path,
    save_manifest,
    sha256_file,
    validate_relative_path,
    verify_integrity,
)

NOW = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def schema() -> TableSchema:
    return TableSchema(
        columns=(
            Column("id", DataType.INTEGER, nullable=False),
            Column("amount", DataType.FLOAT),
        ),
        primary_key=("id",),
    )


def make_dataset(root: Path, schema: TableSchema, dataset_id: str = "sales") -> DatasetEntry:
    relative = f"datasets/{dataset_id}.parquet"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PAR1 test payload")
    return DatasetEntry(
        id=dataset_id,
        path=relative,
        format=DatasetFormat.PARQUET,
        schema=schema,
        checksum_sha256=sha256_file(path),
        rows=1,
        source="unit-test",
    )


def test_validate_relative_path_accepts_canonical_paths() -> None:
    assert validate_relative_path("datasets/sales.parquet") == "datasets/sales.parquet"
    assert validate_relative_path("a/b/c.json") == "a/b/c.json"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/absolute/path",
        "C:/drive/path",
        "a\\b",
        "../escape",
        "a/../b",
        "a//b",
        "a/",
        "a/./b",
    ],
)
def test_validate_relative_path_rejects_unsafe_paths(value: str) -> None:
    with pytest.raises((ValueError, TypeError)):
        validate_relative_path(value)


def test_safe_project_path_stays_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    assert safe_project_path(root, "datasets/a.parquet") == (root / "datasets/a.parquet").resolve()
    with pytest.raises(ValueError):
        safe_project_path(root, "../a.parquet")


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"
    payload = b"data studio" * 1000
    path.write_bytes(payload)
    assert sha256_file(path) == sha256(payload).hexdigest()


def test_dataset_entry_validation(tmp_path: Path, schema: TableSchema) -> None:
    with pytest.raises(ValueError, match="must end with"):
        DatasetEntry(
            id="x",
            path="datasets/x.csv",
            format=DatasetFormat.PARQUET,
            schema=schema,
            checksum_sha256="a" * 64,
        )
    with pytest.raises(ValueError, match="checksum"):
        DatasetEntry(
            id="x",
            path="datasets/x.parquet",
            format=DatasetFormat.PARQUET,
            schema=schema,
            checksum_sha256="not-a-checksum",
        )
    with pytest.raises(ValueError, match="rows"):
        DatasetEntry(
            id="x",
            path="datasets/x.parquet",
            format=DatasetFormat.PARQUET,
            schema=schema,
            checksum_sha256="a" * 64,
            rows=-1,
        )
    with pytest.raises(ValueError, match="Dataset id"):
        DatasetEntry(
            id="bad id!",
            path="datasets/x.parquet",
            format=DatasetFormat.PARQUET,
            schema=schema,
            checksum_sha256="a" * 64,
        )


def test_init_project_creates_layout_and_manifest(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    manifest = init_project(root, "Demo", project_id=str(uuid4()), now=NOW)
    assert manifest.name == "Demo"
    assert manifest.active_dataset is None
    assert manifest.manifest_version == CURRENT_PROJECT_MANIFEST_VERSION
    assert (root / MANIFEST_FILENAME).is_file()
    for directory in ("datasets", "steps", "analyses", "dashboards", "models"):
        assert (root / directory).is_dir()
    loaded = load_manifest(root)
    assert loaded.to_dict() == manifest.to_dict()
    with pytest.raises(FileExistsError):
        init_project(root, "Again")


def test_add_dataset_roundtrip_and_integrity(tmp_path: Path, schema: TableSchema) -> None:
    root = tmp_path / "demo"
    manifest = init_project(root, "Demo", project_id=str(uuid4()), now=NOW)
    entry = make_dataset(root, schema)
    updated = manifest.add_dataset(entry)
    assert updated.active_dataset == "sales"
    save_manifest(root, updated, touch=False)
    loaded = load_manifest(root)
    assert loaded.dataset("sales") == entry
    assert verify_integrity(root, loaded) == []


def test_add_dataset_rejects_duplicates(tmp_path: Path, schema: TableSchema) -> None:
    root = tmp_path / "demo"
    manifest = init_project(root, "Demo", project_id=str(uuid4()), now=NOW)
    entry = make_dataset(root, schema)
    manifest = manifest.add_dataset(entry)
    with pytest.raises(ValueError, match="id already exists"):
        manifest.add_dataset(entry)
    other = make_dataset(root, schema, "other")
    object.__setattr__(other, "path", entry.path)
    with pytest.raises(ValueError, match="path already exists"):
        manifest.add_dataset(other)


def test_replace_and_remove_dataset(tmp_path: Path, schema: TableSchema) -> None:
    root = tmp_path / "demo"
    manifest = init_project(root, "Demo", project_id=str(uuid4()), now=NOW)
    first = make_dataset(root, schema)
    manifest = manifest.add_dataset(first)
    replacement = DatasetEntry(
        id=first.id,
        path=first.path,
        format=first.format,
        schema=first.schema,
        checksum_sha256="b" * 64,
        rows=2,
    )
    manifest = manifest.replace_dataset(replacement)
    assert manifest.dataset("sales").rows == 2
    with pytest.raises(ValueError):
        manifest.replace_dataset(make_dataset(root, schema, "missing"))
    manifest = manifest.remove_dataset("sales")
    assert manifest.datasets == ()
    assert manifest.active_dataset is None


def test_remove_active_dataset_selects_next(tmp_path: Path, schema: TableSchema) -> None:
    root = tmp_path / "demo"
    manifest = init_project(root, "Demo", project_id=str(uuid4()), now=NOW)
    first = manifest.add_dataset(make_dataset(root, schema, "first"))
    second = first.add_dataset(make_dataset(root, schema, "second"))
    updated = second.remove_dataset("first")
    assert updated.active_dataset in {entry.id for entry in updated.datasets}
    assert updated.active_dataset == "second"


def test_set_active_dataset_validates(tmp_path: Path, schema: TableSchema) -> None:
    root = tmp_path / "demo"
    manifest = init_project(root, "Demo", project_id=str(uuid4()), now=NOW)
    manifest = manifest.add_dataset(make_dataset(root, schema))
    assert manifest.set_active_dataset("sales").active_dataset == "sales"
    with pytest.raises(KeyError):
        manifest.set_active_dataset("missing")


def test_verify_integrity_detects_tampering_and_missing_files(
    tmp_path: Path, schema: TableSchema
) -> None:
    root = tmp_path / "demo"
    manifest = init_project(root, "Demo", project_id=str(uuid4()), now=NOW)
    entry = make_dataset(root, schema)
    manifest = manifest.add_dataset(entry)
    (root / entry.path).write_bytes(b"tampered")
    problems = verify_integrity(root, manifest)
    assert any("checksum mismatch" in problem for problem in problems)

    (root / entry.path).unlink()
    problems = verify_integrity(root, manifest)
    assert any("missing file" in problem for problem in problems)


def test_manifest_validation(tmp_path: Path, schema: TableSchema) -> None:
    with pytest.raises(ValueError, match="UUID"):
        ProjectManifest(project_id="not-a-uuid", name="x")
    with pytest.raises(ValueError, match="name"):
        ProjectManifest(project_id=str(uuid4()), name="  ")
    with pytest.raises(ValueError, match="Unsupported manifest version"):
        ProjectManifest(
            project_id=str(uuid4()),
            name="x",
            manifest_version="9.9",
        )
    with pytest.raises(ValueError, match="active_dataset"):
        ProjectManifest(project_id=str(uuid4()), name="x", active_dataset="missing")
    entry = make_dataset(tmp_path, schema)
    with pytest.raises(ValueError, match="active_dataset"):
        ProjectManifest(project_id=str(uuid4()), name="x", datasets=(entry,))


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    manifest = init_project(root, "Demo", project_id=str(uuid4()), now=NOW)
    save_manifest(root, manifest)
    assert not list(root.glob(".project-*.tmp"))
    assert json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))["name"] == "Demo"


def test_load_manifest_reports_invalid_json(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    (root / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid project manifest"):
        load_manifest(root)
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "missing")


def test_manifest_roundtrip_preserves_settings(tmp_path: Path) -> None:
    manifest = ProjectManifest(
        project_id=str(uuid4()),
        name="Demo",
        settings={"theme": "dark", "preview_rows": 500},
    )
    restored = ProjectManifest.from_dict(manifest.to_dict())
    assert restored == manifest
    assert restored.settings["preview_rows"] == 500
