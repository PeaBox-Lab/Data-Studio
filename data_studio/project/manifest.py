"""Versioned, folder-based project storage.

A project is a directory:

    MyProject/
      project.json
      datasets/*.parquet
      steps/*.json
      analyses/*.json
      dashboards/*.json
      models/
      reports/*.html

Guarantees:

* No pickle, ever. Opening a project cannot execute code.
* Dataset paths in the manifest are relative, canonical POSIX paths that must stay
  inside the project directory (no absolute paths, no `..`, no drive letters).
* Every dataset carries a SHA-256 checksum that can be verified on open.
* The manifest is written atomically (temp file + fsync + replace).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from ..kernel.provenance import utc_now_iso
from ..kernel.schema import TableSchema
from ..version import PROJECT_FORMAT_VERSION, __version__

MANIFEST_FILENAME = "project.json"
CURRENT_PROJECT_MANIFEST_VERSION = PROJECT_FORMAT_VERSION
PROJECT_DIRECTORIES = (
    "datasets",
    "steps",
    "analyses",
    "dashboards",
    "models",
    "reports",
)

_DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")
_FORMAT_SUFFIX = {
    "parquet": ".parquet",
    "csv": ".csv",
    "json": ".json",
    "xlsx": ".xlsx",
}
_FORBIDDEN_PATH_PARTS = ("", ".", "..")


class DatasetFormat(StrEnum):
    PARQUET = "parquet"
    CSV = "csv"
    JSON = "json"
    EXCEL = "xlsx"


def _utc_timestamp(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO-8601 string.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}: {value!r}.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone.")
    return parsed.isoformat(timespec="seconds")


def validate_relative_path(value: str) -> str:
    """Validate and return a canonical, project-relative POSIX path.

    Rejects absolute paths, Windows drive letters, backslashes, `..`, `.`, empty
    segments and non-canonical forms. This is deliberately strict: manifests are data,
    and data must never be able to point outside the project.
    """

    if not isinstance(value, str):
        raise TypeError("Path must be a string.")
    if not value:
        raise ValueError("Path must not be empty.")
    if "\\" in value:
        raise ValueError("Paths must use '/' separators, not backslashes.")
    if ":" in value:
        raise ValueError("Paths must not contain a drive letter or colon.")
    candidate = PurePosixPath(value)
    if candidate.is_absolute():
        raise ValueError("Paths must be relative to the project directory.")
    if any(part in _FORBIDDEN_PATH_PARTS for part in candidate.parts):
        raise ValueError("Paths must not contain empty, '.' or '..' segments.")
    if str(candidate) != value:
        raise ValueError("Paths must be canonical (no duplicate or trailing separators).")
    return value


def safe_project_path(root: Path, relative: str) -> Path:
    """Resolve a manifest path under `root`, refusing any escape attempt."""

    validate_relative_path(relative)
    root_resolved = Path(root).resolve()
    candidate = (root_resolved / relative).resolve()
    if candidate == root_resolved or not candidate.is_relative_to(root_resolved):
        raise ValueError(f"Path escapes the project directory: {relative!r}.")
    return candidate


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class DatasetEntry:
    """One dataset file inside a project."""

    id: str
    path: str
    format: DatasetFormat
    schema: TableSchema
    checksum_sha256: str
    created_at: str = field(default_factory=utc_now_iso)
    rows: int | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _DATASET_ID.match(self.id):
            raise ValueError("Dataset id must be 1-128 letters, digits, '_', '.' or '-'.")
        path = validate_relative_path(self.path)
        fmt = (
            self.format
            if isinstance(self.format, DatasetFormat)
            else DatasetFormat(str(self.format))
        )
        suffix = _FORMAT_SUFFIX[fmt.value]
        if not path.lower().endswith(suffix):
            raise ValueError(f"A {fmt.value} dataset path must end with {suffix!r}.")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "format", fmt)

        if not isinstance(self.schema, TableSchema):
            raise TypeError("Dataset schema must be a TableSchema.")
        checksum = str(self.checksum_sha256).lower()
        if not _CHECKSUM.match(checksum):
            raise ValueError("Dataset checksum must be 64 lowercase hex characters.")
        object.__setattr__(self, "checksum_sha256", checksum)

        object.__setattr__(self, "created_at", _utc_timestamp(self.created_at, "created_at"))

        if self.rows is not None and (not isinstance(self.rows, int) or self.rows < 0):
            raise ValueError("Dataset rows must be a non-negative integer or None.")
        if self.source is not None:
            source = self.source.strip()
            object.__setattr__(self, "source", source or None)

    @property
    def columns(self) -> tuple[str, ...]:
        return self.schema.names

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "format": self.format.value,
            "schema": self.schema.to_dict(),
            "checksum_sha256": self.checksum_sha256,
            "created_at": self.created_at,
            "rows": self.rows,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DatasetEntry:
        return cls(
            id=str(payload["id"]),
            path=str(payload["path"]),
            format=DatasetFormat(str(payload["format"])),
            schema=TableSchema.from_dict(payload["schema"]),
            checksum_sha256=str(payload["checksum_sha256"]),
            created_at=str(payload.get("created_at", utc_now_iso())),
            rows=payload.get("rows"),
            source=payload.get("source"),
        )


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    """The root document of a project."""

    project_id: str
    name: str
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    app_version: str = __version__
    manifest_version: str = CURRENT_PROJECT_MANIFEST_VERSION
    datasets: tuple[DatasetEntry, ...] = ()
    active_dataset: str | None = None
    directories: tuple[str, ...] = PROJECT_DIRECTORIES
    settings: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        try:
            UUID(str(self.project_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError("project_id must be a UUID.") from exc

        name = self.name.strip()
        if not name:
            raise ValueError("Project name must not be empty.")
        object.__setattr__(self, "name", name)

        if self.manifest_version != CURRENT_PROJECT_MANIFEST_VERSION:
            raise ValueError(
                f"Unsupported manifest version {self.manifest_version!r}; "
                f"expected {CURRENT_PROJECT_MANIFEST_VERSION!r}."
            )
        if not self.app_version.strip():
            raise ValueError("app_version must not be empty.")

        object.__setattr__(self, "created_at", _utc_timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc_timestamp(self.updated_at, "updated_at"))

        datasets = tuple(self.datasets)
        if any(not isinstance(entry, DatasetEntry) for entry in datasets):
            raise TypeError("datasets must contain DatasetEntry instances.")
        ids = [entry.id for entry in datasets]
        if len(set(ids)) != len(ids):
            raise ValueError("Dataset ids must be unique.")
        paths = [entry.path for entry in datasets]
        if len(set(paths)) != len(paths):
            raise ValueError("Dataset paths must be unique.")
        object.__setattr__(self, "datasets", datasets)

        if datasets:
            if self.active_dataset not in ids:
                raise ValueError("active_dataset must reference an existing dataset.")
        elif self.active_dataset is not None:
            raise ValueError("active_dataset must be None when the project has no datasets.")

        directories = tuple(self.directories)
        if len(set(directories)) != len(directories):
            raise ValueError("Project directories must be unique.")
        for directory in directories:
            validate_relative_path(directory)
        object.__setattr__(self, "directories", directories)

        if not isinstance(self.settings, Mapping):
            raise TypeError("settings must be a mapping.")
        object.__setattr__(self, "settings", dict(self.settings))

    def dataset(self, dataset_id: str) -> DatasetEntry:
        for entry in self.datasets:
            if entry.id == dataset_id:
                return entry
        raise KeyError(f"Unknown dataset {dataset_id!r}.")

    def add_dataset(self, entry: DatasetEntry) -> ProjectManifest:
        if any(existing.id == entry.id for existing in self.datasets):
            raise ValueError(f"Dataset id already exists: {entry.id!r}.")
        if any(existing.path == entry.path for existing in self.datasets):
            raise ValueError(f"Dataset path already exists: {entry.path!r}.")
        active = self.active_dataset or entry.id
        return replace(
            self,
            datasets=(*self.datasets, entry),
            active_dataset=active,
            updated_at=utc_now_iso(),
        )

    def replace_dataset(self, entry: DatasetEntry) -> ProjectManifest:
        if not any(existing.id == entry.id for existing in self.datasets):
            raise ValueError(f"Dataset id does not exist: {entry.id!r}.")
        datasets = tuple(
            entry if existing.id == entry.id else existing for existing in self.datasets
        )
        return replace(self, datasets=datasets, updated_at=utc_now_iso())

    def remove_dataset(self, dataset_id: str) -> ProjectManifest:
        if not any(entry.id == dataset_id for entry in self.datasets):
            raise ValueError(f"Dataset id does not exist: {dataset_id!r}.")
        datasets = tuple(entry for entry in self.datasets if entry.id != dataset_id)
        active = self.active_dataset
        if active == dataset_id:
            active = datasets[0].id if datasets else None
        return replace(self, datasets=datasets, active_dataset=active, updated_at=utc_now_iso())

    def set_active_dataset(self, dataset_id: str) -> ProjectManifest:
        self.dataset(dataset_id)
        return replace(self, active_dataset=dataset_id, updated_at=utc_now_iso())

    def touch(self, now: str | None = None) -> ProjectManifest:
        return replace(self, updated_at=now or utc_now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "project_id": self.project_id,
            "name": self.name,
            "app_version": self.app_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "datasets": [entry.to_dict() for entry in self.datasets],
            "active_dataset": self.active_dataset,
            "directories": list(self.directories),
            "settings": dict(self.settings),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProjectManifest:
        if not isinstance(payload, Mapping):
            raise TypeError("Manifest must be a JSON object.")
        return cls(
            project_id=str(payload["project_id"]),
            name=str(payload["name"]),
            created_at=str(payload.get("created_at", utc_now_iso())),
            updated_at=str(payload.get("updated_at", utc_now_iso())),
            app_version=str(payload.get("app_version", __version__)),
            manifest_version=str(
                payload.get("manifest_version", CURRENT_PROJECT_MANIFEST_VERSION)
            ),
            datasets=tuple(
                DatasetEntry.from_dict(entry) for entry in payload.get("datasets", [])
            ),
            active_dataset=payload.get("active_dataset"),
            directories=tuple(payload.get("directories", PROJECT_DIRECTORIES)),
            settings=dict(payload.get("settings") or {}),
        )


def init_project(
    root: Path | str,
    name: str,
    *,
    app_version: str = __version__,
    project_id: str | None = None,
    now: str | None = None,
) -> ProjectManifest:
    """Create an empty project directory and manifest."""

    root_path = Path(root)
    if (root_path / MANIFEST_FILENAME).exists():
        raise FileExistsError(f"A project already exists at {root_path}.")
    root_path.mkdir(parents=True, exist_ok=True)
    timestamp = now or utc_now_iso()
    manifest = ProjectManifest(
        project_id=project_id or str(uuid4()),
        name=name,
        created_at=timestamp,
        updated_at=timestamp,
        app_version=app_version,
    )
    for directory in manifest.directories:
        (root_path / directory).mkdir(parents=True, exist_ok=True)
    save_manifest(root_path, manifest, touch=False)
    return manifest


def save_manifest(
    root: Path | str,
    manifest: ProjectManifest,
    *,
    touch: bool = True,
) -> ProjectManifest:
    """Atomically write the manifest, returning the version that was written."""

    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    stored = manifest.touch() if touch else manifest
    payload = json.dumps(
        stored.to_dict(),
        indent=2,
        ensure_ascii=False,
        default=str,
    )
    target = root_path / MANIFEST_FILENAME
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".project-", suffix=".tmp", dir=root_path
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return stored


def load_manifest(root: Path | str) -> ProjectManifest:
    root_path = Path(root)
    path = root_path / MANIFEST_FILENAME
    if not path.is_file():
        raise FileNotFoundError(f"No {MANIFEST_FILENAME} found in {root_path}.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid project manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid project manifest: root must be an object.")
    return ProjectManifest.from_dict(payload)


def verify_integrity(
    root: Path | str,
    manifest: ProjectManifest,
    *,
    check_checksums: bool = True,
) -> list[str]:
    """Return a list of integrity problems; an empty list means the project is sound."""

    root_path = Path(root)
    problems: list[str] = []
    for entry in manifest.datasets:
        try:
            path = safe_project_path(root_path, entry.path)
        except ValueError as exc:
            problems.append(f"{entry.id}: {exc}")
            continue
        if not path.exists():
            problems.append(f"{entry.id}: missing file {entry.path!r}.")
            continue
        if not path.is_file():
            problems.append(f"{entry.id}: {entry.path!r} is not a file.")
            continue
        if check_checksums:
            actual = sha256_file(path)
            if actual != entry.checksum_sha256:
                problems.append(
                    f"{entry.id}: checksum mismatch (expected {entry.checksum_sha256}, "
                    f"found {actual})."
                )
    return problems
