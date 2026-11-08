"""Folder-based project format: manifest, integrity and safe paths."""

from __future__ import annotations

from .manifest import (
    CURRENT_PROJECT_MANIFEST_VERSION,
    MANIFEST_FILENAME,
    PROJECT_DIRECTORIES,
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
from .store import (
    PIPELINE_FILENAME,
    DatasetSummary,
    Project,
    ProjectError,
    open_project,
)

__all__ = [
    "CURRENT_PROJECT_MANIFEST_VERSION",
    "MANIFEST_FILENAME",
    "PIPELINE_FILENAME",
    "PROJECT_DIRECTORIES",
    "DatasetEntry",
    "DatasetFormat",
    "DatasetSummary",
    "Project",
    "ProjectError",
    "ProjectManifest",
    "init_project",
    "load_manifest",
    "open_project",
    "safe_project_path",
    "save_manifest",
    "sha256_file",
    "validate_relative_path",
    "verify_integrity",
]
