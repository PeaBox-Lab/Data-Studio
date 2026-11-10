"""Project store: the single API every surface uses to work with a project.

A `Project` owns one DuckDB engine and one folder on disk. It imports sources into
project-owned Parquet files, records validated schemas in the manifest, loads pipelines
from ``steps/pipeline.json``, runs them through the incremental runner and exports
results atomically.

The store contains no UI, HTTP or argument-parsing code, so the CLI, FastAPI app and
tests all exercise exactly the same behaviour.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..engine.duckdb_engine import (
    DuckDBEngine,
    DuckDBTable,
    SourceRef,
    sql_path,
)
from ..io.readers import read_source
from ..io.sniff import CsvOptions, SourceFormat
from ..io.writers import write_table
from ..kernel.schema import TableSchema
from ..kernel.table import TableData
from ..pipeline.model import Pipeline
from ..pipeline.runner import PipelineRunner, RunResult

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..analysis import AnalysisContext

from .manifest import (
    DatasetEntry,
    DatasetFormat,
    ProjectManifest,
    init_project,
    load_manifest,
    safe_project_path,
    save_manifest,
    sha256_file,
    verify_integrity,
)

PIPELINE_FILENAME = "pipeline.json"
_DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


class ProjectError(RuntimeError):
    """A project could not be created, opened or modified safely."""


def _sanitise_dataset_id(value: str) -> str:
    candidate = _UNSAFE_ID_CHARS.sub("_", str(value).strip())
    candidate = candidate.lstrip("_.-") or "dataset"
    candidate = candidate[:128]
    if not _DATASET_ID.match(candidate):
        candidate = "dataset"
    return candidate


def _unique_dataset_id(candidate: str, existing: Sequence[str]) -> str:
    taken = {name.casefold() for name in existing}
    if candidate.casefold() not in taken:
        return candidate
    counter = 2
    while True:
        suffix = f"_{counter}"
        unique = f"{candidate[: 128 - len(suffix)]}{suffix}"
        if unique.casefold() not in taken:
            return unique
        counter += 1


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    """A JSON-safe dataset description for the CLI and API."""

    id: str
    path: str
    format: str
    rows: int | None
    columns: int
    schema: TableSchema
    checksum_sha256: str
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "format": self.format,
            "rows": self.rows,
            "columns": self.columns,
            "schema": self.schema.to_dict(),
            "checksum_sha256": self.checksum_sha256,
            "source": self.source,
        }


class Project:
    """An open project directory plus its engine and pipeline runner."""

    def __init__(
        self,
        root: str | Path,
        manifest: ProjectManifest,
        *,
        engine: DuckDBEngine | None = None,
        runner: PipelineRunner | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.manifest = manifest
        self.engine = engine or DuckDBEngine()
        self.runner = runner or PipelineRunner()
        self._tables: dict[str, DuckDBTable] = {}

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def create(
        cls,
        root: str | Path,
        name: str | None = None,
        *,
        engine: DuckDBEngine | None = None,
    ) -> Project:
        path = Path(root)
        manifest = init_project(path, name or path.name)
        return cls(path, manifest, engine=engine)

    @classmethod
    def open(
        cls,
        root: str | Path,
        *,
        engine: DuckDBEngine | None = None,
        verify: bool = False,
        check_checksums: bool = True,
    ) -> Project:
        path = Path(root)
        try:
            manifest = load_manifest(path)
        except FileNotFoundError as exc:
            raise ProjectError(f"No project found at {path}.") from exc
        if verify:
            problems = verify_integrity(path, manifest, check_checksums=check_checksums)
            if problems:
                raise ProjectError(
                    "Project integrity check failed: " + "; ".join(problems)
                )
        return cls(path, manifest, engine=engine)

    def close(self) -> None:
        self._tables.clear()
        self.engine.close()

    def __enter__(self) -> Project:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def project_id(self) -> str:
        return self.manifest.project_id

    # -- datasets ----------------------------------------------------------

    def datasets(self) -> list[DatasetSummary]:
        return [
            DatasetSummary(
                id=entry.id,
                path=entry.path,
                format=entry.format.value,
                rows=entry.rows,
                columns=len(entry.schema),
                schema=entry.schema,
                checksum_sha256=entry.checksum_sha256,
                source=entry.source,
            )
            for entry in self.manifest.datasets
        ]

    def dataset_ids(self) -> tuple[str, ...]:
        return tuple(entry.id for entry in self.manifest.datasets)

    def dataset_entry(self, dataset_id: str) -> DatasetEntry:
        return self.manifest.dataset(dataset_id)

    def dataset_schema(self, dataset_id: str) -> TableSchema:
        return self.dataset_entry(dataset_id).schema

    def add_dataset(
        self,
        source: str | Path,
        *,
        dataset_id: str | None = None,
        source_format: str | SourceFormat | None = None,
        csv_options: CsvOptions | Mapping[str, Any] | None = None,
        sheet_name: str | int = 0,
        table: str | None = None,
        replace: bool = False,
        source_name: str | None = None,
    ) -> DatasetEntry:
        """Import a file, normalise it to project Parquet and record its schema."""

        resolved = Path(source).resolve()
        if not resolved.is_file():
            raise ProjectError(f"Source file not found: {resolved}")

        source_table = read_source(
            self.engine,
            resolved,
            name=dataset_id or resolved.stem,
            source_format=source_format,
            csv_options=csv_options,
            sheet_name=sheet_name,
            table=table,
        )

        requested = _sanitise_dataset_id(dataset_id or resolved.stem)
        if replace:
            if requested not in self.dataset_ids():
                raise ProjectError(f"Cannot replace unknown dataset {requested!r}.")
            final_id = requested
        else:
            final_id = _unique_dataset_id(requested, self.dataset_ids())

        relative = f"datasets/{final_id}.parquet"
        destination = safe_project_path(self.root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_table(
            self.engine,
            source_table,
            destination,
            overwrite=True,
        )

        stored = self._table_for_parquet(destination, final_id, source_table.schema)
        rows = stored.count()
        entry = DatasetEntry(
            id=final_id,
            path=relative,
            format=DatasetFormat.PARQUET,
            schema=stored.schema,
            checksum_sha256=sha256_file(destination),
            rows=rows,
            source=source_name or str(resolved),
        )

        manifest = (
            self.manifest.replace_dataset(entry)
            if replace
            else self.manifest.add_dataset(entry)
        )
        self.manifest = save_manifest(self.root, manifest)
        self._tables.pop(final_id, None)
        return entry

    def add_table(
        self,
        table: DuckDBTable,
        *,
        dataset_id: str,
        replace: bool = False,
        source_name: str = "generated",
    ) -> DatasetEntry:
        """Store an engine-created table as a project dataset (e.g. a calendar)."""

        if not isinstance(table, DuckDBTable):
            raise TypeError("add_table expects a DuckDBTable.")
        requested = _sanitise_dataset_id(dataset_id)
        if replace:
            if requested not in self.dataset_ids():
                raise ProjectError(f"Cannot replace unknown dataset {requested!r}.")
            final_id = requested
        else:
            final_id = _unique_dataset_id(requested, self.dataset_ids())

        relative = f"datasets/{final_id}.parquet"
        destination = safe_project_path(self.root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_table(self.engine, table, destination, overwrite=True)
        sql = f"SELECT * FROM read_parquet({sql_path(destination)})"
        schema = self.engine.describe(sql)
        stored = self._table_for_parquet(destination, final_id, schema)
        rows = stored.count()
        entry = DatasetEntry(
            id=final_id,
            path=relative,
            format=DatasetFormat.PARQUET,
            schema=stored.schema,
            checksum_sha256=sha256_file(destination),
            rows=rows,
            source=source_name,
        )
        manifest = (
            self.manifest.replace_dataset(entry)
            if replace
            else self.manifest.add_dataset(entry)
        )
        self.manifest = save_manifest(self.root, manifest)
        self._tables.pop(final_id, None)
        return entry

    def add_dataset_bytes(
        self,
        data: bytes,
        filename: str,
        *,
        dataset_id: str | None = None,
        source_format: str | SourceFormat | None = None,
        csv_options: CsvOptions | Mapping[str, Any] | None = None,
        replace: bool = False,
    ) -> DatasetEntry:
        """Import an uploaded file without keeping the original bytes."""

        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("Uploaded data must be bytes.")
        suffix = Path(filename).suffix
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".upload-", suffix=suffix, dir=self.root
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(bytes(data))
            return self.add_dataset(
                temporary,
                dataset_id=dataset_id or Path(filename).stem,
                source_format=source_format,
                csv_options=csv_options,
                replace=replace,
                source_name=filename,
            )
        finally:
            temporary.unlink(missing_ok=True)

    def remove_dataset(self, dataset_id: str) -> None:
        entry = self.dataset_entry(dataset_id)
        manifest = self.manifest.remove_dataset(dataset_id)
        self.manifest = save_manifest(self.root, manifest)
        self._tables.pop(dataset_id, None)
        path = safe_project_path(self.root, entry.path)
        path.unlink(missing_ok=True)

    def dataset_table(self, dataset_id: str) -> DuckDBTable:
        """Return a lazy table over a stored dataset, preserving recorded roles."""

        if dataset_id in self._tables:
            return self._tables[dataset_id]
        entry = self.dataset_entry(dataset_id)
        path = safe_project_path(self.root, entry.path)
        if not path.is_file():
            raise ProjectError(
                f"Dataset {dataset_id!r} is missing its file {entry.path!r}."
            )
        if entry.format is DatasetFormat.PARQUET:
            sql = f"SELECT * FROM read_parquet({sql_path(path)})"
        else:
            sql = f"SELECT * FROM read_{entry.format.value}_auto({sql_path(path)})"
        source = SourceRef(
            name=dataset_id,
            kind=entry.format.value,
            fingerprint="sha256:" + entry.checksum_sha256,
            path=str(path),
        )
        table = self.engine.table(
            sql,
            name=dataset_id,
            sources=(source,),
            base_schema=entry.schema,
        )
        self._tables[dataset_id] = table
        return table

    def dataset_tables(self, *, exclude: str | None = None) -> dict[str, DuckDBTable]:
        return {
            dataset_id: self.dataset_table(dataset_id)
            for dataset_id in self.dataset_ids()
            if dataset_id != exclude
        }

    def preview(self, dataset_id: str, limit: int = 100) -> TableData:
        return self.dataset_table(dataset_id).preview(limit)

    def count(self, dataset_id: str) -> int:
        return self.dataset_table(dataset_id).count()

    # -- pipeline ----------------------------------------------------------

    @property
    def pipeline_path(self) -> Path:
        return self.root / "steps" / PIPELINE_FILENAME

    def load_pipeline(self) -> Pipeline:
        path = self.pipeline_path
        if not path.is_file():
            return Pipeline(dataset=self.manifest.active_dataset)
        try:
            return Pipeline.from_json(path.read_text(encoding="utf-8"))
        except (ValueError, TypeError) as exc:
            raise ProjectError(f"Invalid pipeline document: {exc}") from exc

    def save_pipeline(self, pipeline: Pipeline) -> Pipeline:
        if not isinstance(pipeline, Pipeline):
            raise TypeError("save_pipeline expects a Pipeline.")
        _atomic_write_text(self.pipeline_path, pipeline.to_json())
        return pipeline

    def add_step(self, pipeline: Pipeline, step: Any) -> Pipeline:
        updated = pipeline.add_step(step)
        return self.save_pipeline(updated)

    def remove_step(self, pipeline: Pipeline, step_id: str) -> Pipeline:
        return self.save_pipeline(pipeline.remove_step(step_id))

    def undo(self, pipeline: Pipeline) -> Pipeline:
        return self.save_pipeline(pipeline.undo())

    # -- run and export ----------------------------------------------------

    def run_pipeline(
        self,
        pipeline: Pipeline | None = None,
        *,
        dataset_id: str | None = None,
        use_cache: bool = True,
        count_rows: bool = True,
    ) -> RunResult:
        active = pipeline or self.load_pipeline()
        target = dataset_id or active.dataset or self.manifest.active_dataset
        if not target:
            raise ProjectError("The project has no active dataset to run.")
        base = self.dataset_table(target)
        datasets = self.dataset_tables(exclude=target)
        return self.runner.run(
            active,
            base,
            datasets=datasets,
            dataset_id=target,
            use_cache=use_cache,
            count_rows=count_rows,
        )

    # -- analyses ----------------------------------------------------------

    def analysis_context(
        self,
        dataset_id: str | None = None,
        use_pipeline: bool = True,
    ) -> AnalysisContext:
        """Build the typed context every analysis runs against.

        The default is the prepared pipeline output, so analysis numbers match the
        cleaned preview. ``use_pipeline=False`` exposes the raw stored dataset.
        """

        from ..analysis import AnalysisContext

        if use_pipeline:
            run = self.run_pipeline(dataset_id=dataset_id, count_rows=False)
            return AnalysisContext(
                dataset=run.dataset,
                table=run.table,
                step_ids=tuple(step.step_id for step in run.steps),
                engine="duckdb",
            )
        target = dataset_id or self.manifest.active_dataset
        if not target:
            raise ProjectError("The project has no active dataset to analyse.")
        return AnalysisContext(
            dataset=target,
            table=self.dataset_table(target),
            engine="duckdb",
        )

    # -- export ------------------------------------------------------------

    def export(
        self,
        result: RunResult | DuckDBTable,
        destination: str | Path,
        *,
        source_format: str | SourceFormat | None = None,
        overwrite: bool = True,
    ) -> Path:
        table = result.table if isinstance(result, RunResult) else result
        if not isinstance(table, DuckDBTable):
            raise TypeError("export expects a RunResult or DuckDBTable.")
        return write_table(
            self.engine,
            table,
            destination,
            source_format=source_format,
            overwrite=overwrite,
        )

    # -- integrity ---------------------------------------------------------

    def verify(self, *, check_checksums: bool = True) -> list[str]:
        return verify_integrity(self.root, self.manifest, check_checksums=check_checksums)

    def verify_or_raise(self, *, check_checksums: bool = True) -> None:
        problems = self.verify(check_checksums=check_checksums)
        if problems:
            raise ProjectError("Project integrity check failed: " + "; ".join(problems))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.manifest.name,
            "project_id": self.manifest.project_id,
            "root": str(self.root),
            "active_dataset": self.manifest.active_dataset,
            "datasets": [summary.to_dict() for summary in self.datasets()],
        }

    # -- internals ---------------------------------------------------------

    def _table_for_parquet(
        self,
        path: Path,
        name: str,
        base_schema: TableSchema,
    ) -> DuckDBTable:
        sql = f"SELECT * FROM read_parquet({sql_path(path)})"
        source = SourceRef(
            name=name,
            kind="parquet",
            fingerprint="sha256:" + sha256_file(path),
            path=str(path),
        )
        return self.engine.table(
            sql,
            name=name,
            sources=(source,),
            base_schema=base_schema,
        )


def open_project(root: str | Path, *, verify: bool = False) -> Project:
    """Convenience wrapper used by the CLI and API."""

    return Project.open(root, verify=verify)


__all__ = [
    "PIPELINE_FILENAME",
    "DatasetSummary",
    "Project",
    "ProjectError",
    "open_project",
]
