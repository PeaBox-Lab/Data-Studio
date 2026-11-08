"""Command line interface for the Data Studio kernel."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from typing import Any

from ..engine.duckdb_engine import EngineError
from ..io.sniff import CsvOptions
from ..kernel.table import TableData
from ..pipeline import PipelineRunError, make_step
from ..project.manifest import (
    MANIFEST_FILENAME,
    init_project,
    load_manifest,
    verify_integrity,
)
from ..project.store import Project, ProjectError
from ..version import __version__

_CORE_PACKAGES = {
    "duckdb": "duckdb",
    "fastapi": "fastapi",
    "numpy": "numpy",
    "pandas": "pandas",
    "pyarrow": "pyarrow",
    "pydantic": "pydantic",
    "uvicorn": "uvicorn",
}

_ERRORS = (
    EngineError,
    PipelineRunError,
    ProjectError,
    FileNotFoundError,
    FileExistsError,
    ValueError,
    KeyError,
    TypeError,
)


def _package_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "MISSING"


def _fail(exc: BaseException) -> int:
    print(f"ERROR: {exc}", file=sys.stderr)
    return 1


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _print_table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    headers = [str(column) for column in columns]
    body = [[_cell(value) for value in row] for row in rows]
    widths = [
        max([len(headers[index])] + [len(row[index]) for row in body])
        for index in range(len(headers))
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in body:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _table_rows(data: TableData) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in data.rows]


def _read_params(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    text = value
    if value == "-":
        text = sys.stdin.read()
    elif value.startswith("@"):
        text = Path(value[1:]).read_text(encoding="utf-8")
    payload = json.loads(text or "{}")
    if not isinstance(payload, dict):
        raise ValueError("--params must be a JSON object.")
    return payload


def _open(args: argparse.Namespace) -> Project:
    return Project.open(Path(args.project), verify=getattr(args, "verify", False))


def _cmd_doctor(_: argparse.Namespace) -> int:
    print(f"data-studio {__version__}")
    print(f"python      {sys.version.split()[0]} ({sys.executable})")
    print("")
    print("core packages")
    missing = 0
    for distribution in _CORE_PACKAGES:
        version = _package_version(distribution)
        if version == "MISSING":
            missing += 1
        print(f"  {distribution:<12} {version}")
    print("")
    manifest = Path.cwd() / MANIFEST_FILENAME
    if manifest.exists():
        project = load_manifest(Path.cwd())
        print(f"project     {project.name} ({len(project.datasets)} datasets)")
    else:
        print("project     none in the current directory")
    if missing:
        print(f"\nERROR: {missing} core package(s) missing. Run: pip install -e .")
        return 1
    print("\nAll checks passed.")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    name = args.name or path.name
    try:
        manifest = init_project(path, name)
    except FileExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Created project {manifest.name!r} at {path.resolve()}")
    print(f"Manifest: {path / MANIFEST_FILENAME}")
    return 0


def _cmd_verify_project(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        manifest = load_manifest(path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    problems = verify_integrity(path, manifest)
    payload = {
        "project": manifest.name,
        "project_id": manifest.project_id,
        "manifest_version": manifest.manifest_version,
        "datasets": [entry.id for entry in manifest.datasets],
        "status": "ok" if not problems else "problems",
        "problems": problems,
    }
    print(json.dumps(payload, indent=2))
    return 0 if not problems else 1


def _csv_options(args: argparse.Namespace) -> CsvOptions | None:
    if not (args.delimiter or args.quote or args.encoding or args.no_header):
        return None
    return CsvOptions(
        delimiter=args.delimiter or ",",
        quote=args.quote or '"',
        header=not args.no_header,
        encoding=args.encoding or "utf-8-sig",
    )


def _cmd_add_dataset(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            sheet: str | int = args.sheet
            if isinstance(sheet, str) and sheet.isdigit():
                sheet = int(sheet)
            entry = project.add_dataset(
                Path(args.source),
                dataset_id=args.id,
                source_format=args.format,
                csv_options=_csv_options(args),
                sheet_name=sheet,
                table=args.table,
                replace=args.replace,
            )
            if args.json:
                _print_json(entry.to_dict())
            else:
                print(
                    f"Imported {entry.id!r}: {entry.rows or 0} rows, "
                    f"{len(entry.schema)} columns -> {entry.path}"
                )
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_datasets(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            summaries = project.datasets()
            if args.json:
                _print_json([summary.to_dict() for summary in summaries])
                return 0
            if not summaries:
                print("No datasets in this project.")
                return 0
            _print_table(
                ("id", "rows", "columns", "format", "path"),
                [
                    (
                        summary.id,
                        summary.rows,
                        summary.columns,
                        summary.format,
                        summary.path,
                    )
                    for summary in summaries
                ],
            )
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_schema(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            schema = project.dataset_schema(args.dataset)
            if args.json:
                _print_json(schema.to_dict())
                return 0
            _print_table(
                ("column", "type", "nullable", "role"),
                [
                    (
                        column.name,
                        column.dtype.value,
                        column.nullable,
                        column.role.value if column.role else "",
                    )
                    for column in schema
                ],
            )
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_preview(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            data = project.preview(args.dataset, args.limit)
            if args.json:
                _print_json(
                    {
                        "schema": data.schema.to_dict(),
                        "rows": [list(row) for row in data.rows],
                    }
                )
                return 0
            _print_table(data.schema.names, _table_rows(data))
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_steps(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            pipeline = project.load_pipeline()
            if args.json:
                _print_json(pipeline.to_dict())
                return 0
            if not pipeline.steps:
                print("Pipeline has no steps.")
                return 0
            _print_table(
                ("#", "id", "operation", "enabled"),
                [
                    (index + 1, step.id, step.operation, step.enabled)
                    for index, step in enumerate(pipeline.steps)
                ],
            )
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_add_step(args: argparse.Namespace) -> int:
    try:
        params = _read_params(args.params)
        with _open(args) as project:
            pipeline = project.load_pipeline()
            step = make_step(
                args.operation,
                params,
                step_id=args.id,
                existing=[item.id for item in pipeline.steps],
                title=args.title,
                enabled=not args.disabled,
            )
            pipeline = project.save_pipeline(
                pipeline.add_step(step, position=args.position)
            )
            if args.json:
                _print_json(step.to_dict())
            else:
                print(f"Added step {step.id!r} ({step.operation}).")
                print(f"Pipeline has {len(pipeline.steps)} step(s).")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_remove_step(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            pipeline = project.remove_step(project.load_pipeline(), args.step_id)
            if args.json:
                _print_json(pipeline.to_dict())
            else:
                print(f"Removed step {args.step_id!r}.")
                print(f"Pipeline has {len(pipeline.steps)} step(s).")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_undo(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            pipeline = project.undo(project.load_pipeline())
            if args.json:
                _print_json(pipeline.to_dict())
            else:
                print(f"Undid the last step; {len(pipeline.steps)} step(s) remain.")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            result = project.run_pipeline(
                dataset_id=args.dataset,
                use_cache=not args.no_cache,
                count_rows=not args.no_count,
            )
            if args.json:
                _print_json(result.to_dict())
                return 0
            print(
                f"Ran {result.pipeline_name!r} on {result.dataset!r}: "
                f"{result.rows if result.rows is not None else '?'} rows, "
                f"{len(result.schema)} columns in {result.duration_ms:.1f} ms"
            )
            print(f"Cache hits: {result.cache_hits}/{len(result.steps)}")
            for step in result.steps:
                marker = "cached" if step.cached else "ran"
                print(f"  {step.step_id:<20} {step.operation:<16} {marker}")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_export(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            result = project.run_pipeline(
                dataset_id=args.dataset,
                use_cache=not args.no_cache,
            )
            destination = project.export(
                result,
                Path(args.destination),
                source_format=args.format,
                overwrite=not args.no_overwrite,
            )
            if args.json:
                _print_json(
                    {
                        "destination": str(destination),
                        "rows": result.rows,
                        "columns": list(result.schema.names),
                    }
                )
                return 0
            print(
                f"Exported {result.rows if result.rows is not None else '?'} rows "
                f"to {destination}"
            )
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    if args.project:
        os.environ["DATA_STUDIO_PROJECT"] = str(Path(args.project).resolve())
    print(f"Data Studio {__version__} at http://{args.host}:{args.port}")
    uvicorn.run(
        "data_studio.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data-studio",
        description="Local-first analytics workbench.",
    )
    parser.add_argument("--version", action="version", version=f"data-studio {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check the environment.")
    doctor.set_defaults(handler=_cmd_doctor)

    init = subparsers.add_parser("init", help="Create an empty project.")
    init.add_argument("path", help="Directory for the new project.")
    init.add_argument("--name", help="Project name (defaults to the directory name).")
    init.set_defaults(handler=_cmd_init)

    verify = subparsers.add_parser(
        "verify-project",
        help="Validate a project manifest and checksums.",
    )
    verify.add_argument("path", help="Project directory.")
    verify.set_defaults(handler=_cmd_verify_project)

    add_dataset = subparsers.add_parser(
        "add-dataset", help="Import a CSV, Excel, JSON, Parquet or DuckDB file."
    )
    add_dataset.add_argument("project", help="Project directory.")
    add_dataset.add_argument("source", help="File to import.")
    add_dataset.add_argument("--id", help="Dataset id (defaults to the file name).")
    add_dataset.add_argument("--format", help="Force the source format.")
    add_dataset.add_argument("--sheet", default=0, help="Excel worksheet name or index.")
    add_dataset.add_argument("--table", help="Table name inside a DuckDB database.")
    add_dataset.add_argument("--delimiter", help="CSV delimiter override.")
    add_dataset.add_argument("--quote", help="CSV quote character override.")
    add_dataset.add_argument("--encoding", help="CSV encoding override.")
    add_dataset.add_argument(
        "--no-header", action="store_true", help="CSV file has no header row."
    )
    add_dataset.add_argument(
        "--replace", action="store_true", help="Replace an existing dataset id."
    )
    add_dataset.add_argument("--json", action="store_true", help="Emit JSON.")
    add_dataset.set_defaults(handler=_cmd_add_dataset)

    datasets = subparsers.add_parser("datasets", help="List project datasets.")
    datasets.add_argument("project", help="Project directory.")
    datasets.add_argument("--json", action="store_true", help="Emit JSON.")
    datasets.set_defaults(handler=_cmd_datasets)

    schema = subparsers.add_parser("schema", help="Show a dataset schema.")
    schema.add_argument("project", help="Project directory.")
    schema.add_argument("dataset", help="Dataset id.")
    schema.add_argument("--json", action="store_true", help="Emit JSON.")
    schema.set_defaults(handler=_cmd_schema)

    preview = subparsers.add_parser("preview", help="Preview dataset rows.")
    preview.add_argument("project", help="Project directory.")
    preview.add_argument("dataset", help="Dataset id.")
    preview.add_argument("--limit", type=int, default=20, help="Rows to show.")
    preview.add_argument("--json", action="store_true", help="Emit JSON.")
    preview.set_defaults(handler=_cmd_preview)

    steps = subparsers.add_parser("steps", help="List pipeline steps.")
    steps.add_argument("project", help="Project directory.")
    steps.add_argument("--json", action="store_true", help="Emit JSON.")
    steps.set_defaults(handler=_cmd_steps)

    add_step = subparsers.add_parser("add-step", help="Append a step to the pipeline.")
    add_step.add_argument("project", help="Project directory.")
    add_step.add_argument("operation", help="Registered operation name.")
    add_step.add_argument("--params", help="Step parameters as JSON, @file or -.")
    add_step.add_argument("--id", help="Explicit step id.")
    add_step.add_argument("--title", help="Human-readable step title.")
    add_step.add_argument("--position", type=int, help="Insert position (0-based).")
    add_step.add_argument(
        "--disabled", action="store_true", help="Add the step disabled."
    )
    add_step.add_argument("--json", action="store_true", help="Emit JSON.")
    add_step.set_defaults(handler=_cmd_add_step)

    remove_step = subparsers.add_parser("remove-step", help="Remove a pipeline step.")
    remove_step.add_argument("project", help="Project directory.")
    remove_step.add_argument("step_id", help="Step id to remove.")
    remove_step.add_argument("--json", action="store_true", help="Emit JSON.")
    remove_step.set_defaults(handler=_cmd_remove_step)

    undo = subparsers.add_parser("undo", help="Remove the last pipeline step.")
    undo.add_argument("project", help="Project directory.")
    undo.add_argument("--json", action="store_true", help="Emit JSON.")
    undo.set_defaults(handler=_cmd_undo)

    run = subparsers.add_parser("run", help="Run the project pipeline.")
    run.add_argument("project", help="Project directory.")
    run.add_argument("--dataset", help="Override the pipeline dataset.")
    run.add_argument("--no-cache", action="store_true", help="Ignore cached steps.")
    run.add_argument("--no-count", action="store_true", help="Skip the row count.")
    run.add_argument("--json", action="store_true", help="Emit JSON.")
    run.set_defaults(handler=_cmd_run)

    export = subparsers.add_parser("export", help="Run the pipeline and export a file.")
    export.add_argument("project", help="Project directory.")
    export.add_argument("destination", help="Output .parquet, .csv, .json or .xlsx file.")
    export.add_argument("--dataset", help="Override the pipeline dataset.")
    export.add_argument("--format", help="Force the output format.")
    export.add_argument("--no-cache", action="store_true", help="Ignore cached steps.")
    export.add_argument(
        "--no-overwrite", action="store_true", help="Refuse to replace an existing file."
    )
    export.add_argument("--json", action="store_true", help="Emit JSON.")
    export.set_defaults(handler=_cmd_export)

    serve = subparsers.add_parser("serve", help="Run the local web application.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--project", help="Open this project in the workspace.")
    serve.add_argument("--reload", action="store_true", help="Reload on Python changes.")
    serve.set_defaults(handler=_cmd_serve)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


def run() -> None:
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    run()
