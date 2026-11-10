"""Command line interface for the Data Studio kernel."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from ..analysis import DEFAULT_ANALYSIS_REGISTRY, AnalysisContext
from ..analysis.saved import SCHEDULES, SavedAnalysisStore
from ..dashboard import (
    DashboardBookmark,
    DashboardParameter,
    DashboardRenderer,
    DashboardSlicer,
    DashboardStore,
    DashboardTile,
    slugify,
    write_dashboard_report,
)
from ..engine.duckdb_engine import EngineError
from ..io.sniff import CsvOptions
from ..kernel.table import TableData
from ..pipeline import PipelineRunError, make_step
from ..project.manifest import (
    MANIFEST_FILENAME,
    init_project,
    load_manifest,
    safe_project_path,
    verify_integrity,
)
from ..project.store import Project, ProjectError
from ..reporting import write_html_report
from ..semantic import (
    CalendarError,
    QueryFilter,
    QueryOrder,
    SemanticEngine,
    SemanticModel,
    SemanticModelStore,
    generate_calendar,
    SemanticQuery,
)
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


def _read_json(value: str | None) -> Any:
    if value is None:
        return None
    text = value
    if value == "-":
        text = sys.stdin.read()
    elif value.startswith("@"):
        text = Path(value[1:]).read_text(encoding="utf-8")
    return json.loads(text or "null")


def _coerce(value: str) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _csv_list(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


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


def _cmd_analyses(args: argparse.Namespace) -> int:
    catalog = DEFAULT_ANALYSIS_REGISTRY.catalog()
    if args.json:
        _print_json(catalog)
        return 0
    _print_table(
        ("name", "category", "title"),
        [(item["name"], item["category"], item["title"]) for item in catalog],
    )
    return 0


def _analysis_context(
    project: Project, dataset: str | None, raw: bool
) -> AnalysisContext:
    return project.analysis_context(dataset_id=dataset, use_pipeline=not raw)


def _cmd_analyze(args: argparse.Namespace) -> int:
    try:
        params = _read_params(args.params)
        with _open(args) as project:
            context = _analysis_context(project, args.dataset, args.raw)
            analysis = DEFAULT_ANALYSIS_REGISTRY.run(args.analysis, context, params)
            if args.json:
                _print_json(analysis.to_dict())
                return 0
            print(analysis.title)
            if analysis.narrative:
                print(analysis.narrative)
                print("")
            for name, value in analysis.metrics.items():
                print(f"  {name:<18} {_cell(value)}")
            for table in analysis.tables:
                print("")
                print(table.title)
                _print_table(table.columns, table.rows)
                if table.note:
                    print(table.note)
            if analysis.diagnostics:
                print("")
                for diagnostic in analysis.diagnostics:
                    print(f"  [{diagnostic.severity}] {diagnostic.message}")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_report(args: argparse.Namespace) -> int:
    try:
        params = _read_params(args.params)
        with _open(args) as project:
            context = _analysis_context(project, args.dataset, args.raw)
            analysis = DEFAULT_ANALYSIS_REGISTRY.run(args.analysis, context, params)
            destination = args.destination
            if not destination:
                stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                destination = f"reports/report-{stamp}.html"
            path = Path(destination)
            if not path.is_absolute():
                path = safe_project_path(
                    project.root, destination.replace("\\", "/")
                )
            written = write_html_report(
                analysis,
                path,
                title=args.title or analysis.title,
                theme=args.theme,
            )
            if args.json:
                _print_json(
                    {
                        "destination": str(written),
                        "bytes": written.stat().st_size,
                        "result_id": analysis.result_id,
                    }
                )
                return 0
            print(f"Wrote {analysis.title} report to {written}")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _saved_text_row(item: dict[str, Any]) -> str:
    schedule = item.get("schedule") or "manual"
    status = item.get("status") or "unknown"
    refreshed = item.get("refreshed_at") or "-"
    return (
        f"{item['id']:<28} {status:<8} {schedule:<8} {refreshed:<25} "
        f"{item['title']}"
    )


def _cmd_save_analysis(args: argparse.Namespace) -> int:
    try:
        params = _read_params(args.params)
        report_path: str | bool | None = None
        if args.report_path:
            report_path = args.report_path
        elif args.report:
            report_path = True
        with _open(args) as project:
            store = SavedAnalysisStore(project)
            saved = store.save(
                name=args.name,
                analysis=args.analysis,
                params=params,
                dataset=args.dataset,
                use_pipeline=not args.raw,
                schedule=args.schedule,
                title=args.title,
                report_path=report_path,
                overwrite=args.overwrite,
            )
            view = store.describe(saved)
            if args.json:
                _print_json(view)
                return 0
            print(f"Saved {saved.title!r} as {saved.id}")
            print(f"  analysis   {saved.analysis}")
            print(f"  schedule   {saved.schedule}")
            print(f"  refreshed  {saved.refreshed_at}")
            if saved.report_path:
                print(f"  report     {saved.report_path}")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_saved(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            store = SavedAnalysisStore(project)
            if args.saved_id:
                saved = store.get(args.saved_id)
                view = store.describe(saved)
                if args.json:
                    payload = dict(view)
                    payload["result"] = saved.result.to_dict() if saved.result else None
                    _print_json(payload)
                    return 0
                print(f"{saved.title} ({saved.id})")
                print(f"  analysis    {saved.analysis}")
                print(f"  schedule    {saved.schedule}")
                print(f"  status      {view['status']}")
                print(f"  refreshed   {saved.refreshed_at}")
                if view.get("next_refresh"):
                    print(f"  next        {view['next_refresh']}")
                if saved.report_path:
                    print(f"  report      {saved.report_path}")
                if saved.result:
                    print(f"  result      {saved.result.result_id}")
                    print(f"  narrative   {saved.result.narrative}")
                    for name, value in saved.result.metrics.items():
                        print(f"  {name:<18} {_cell(value)}")
                return 0

            summaries = store.summaries()
            if args.json:
                _print_json(
                    {"analyses": summaries, "errors": store.last_errors}
                )
                return 0
            if not summaries:
                print("No saved analyses.")
            else:
                print(
                    f"{'ID':<28} {'STATUS':<8} {'SCHEDULE':<8} "
                    f"{'REFRESHED':<25} TITLE"
                )
                print("-" * 96)
                for item in summaries:
                    print(_saved_text_row(item))
                print("")
                print(
                    "Refresh due: data-studio refresh PROJECT --due "
                    "(or schedules run when the workspace is open)."
                )
            for error in store.last_errors:
                print(f"WARNING: {error}", file=sys.stderr)
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_refresh(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            store = SavedAnalysisStore(project)
            if args.saved_id:
                refreshed = [store.refresh(args.saved_id)]
                failures: list[dict[str, str]] = []
            elif args.all:
                refreshed, failures = store.refresh_all()
            else:
                refreshed, failures = store.refresh_due()
            # Dashboards share the schedule vocabulary: refresh them alongside
            # saved analyses. Passing a saved analysis id scopes this command to
            # that analysis only.
            dashboards: list[Any] = []
            dashboard_failures: list[dict[str, str]] = []
            dash_store = DashboardStore(project)
            if args.dashboard_id:
                dashboards = [dash_store.refresh(args.dashboard_id)]
            elif not args.saved_id:
                if args.all:
                    dashboards, dashboard_failures = dash_store.refresh_all()
                else:
                    dashboards, dashboard_failures = dash_store.refresh_due()
            failures = list(failures) + list(dashboard_failures)
            if args.json:
                _print_json(
                    {
                        "refreshed": [store.describe(item) for item in refreshed],
                        "failed": failures,
                        "dashboards": [
                            dash_store.describe(item) for item in dashboards
                        ],
                        "dashboard_failures": dashboard_failures,
                    }
                )
            else:
                print(f"Refreshed {len(refreshed)} saved analysis(es).")
                for item in refreshed:
                    print(f"  {item.id:<28} {item.refreshed_at}  {item.title}")
                print(f"Refreshed {len(dashboards)} dashboard(s).")
                for item in dashboards:
                    print(f"  {item.id:<28} {item.refreshed_at}  {item.title}")
                for failure in failures:
                    print(
                        f"  FAILED {failure['id']}: {failure['error']}",
                        file=sys.stderr,
                    )
        return 1 if failures else 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_remove_saved(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            SavedAnalysisStore(project).remove(args.saved_id)
            if args.json:
                _print_json({"removed": args.saved_id})
            else:
                print(f"Removed saved analysis {args.saved_id!r}.")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_model_show(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            payload = SemanticModelStore(project).to_dict()
            if args.json:
                _print_json(payload)
                return 0
            if not payload["exists"]:
                print("No semantic model. Run: data-studio model-derive PROJECT")
                return 0
            model = payload["model"]
            print(f"{model['name']}  (default table: {model['default_table']})")
            print("")
            print("TABLES")
            for table in model["tables"]:
                print(f"  {table['name']:<24} {table['dataset']}")
            if model["relationships"]:
                print("")
                print("RELATIONSHIPS")
                for item in model["relationships"]:
                    print(
                        f"  {item['from_table']}.{item['from_column']} -> "
                        f"{item['to_table']}.{item['to_column']}  ({item['kind']})"
                    )
            print("")
            print(f"DIMENSIONS ({len(model['dimensions'])})")
            for item in model["dimensions"]:
                print(f"  {item['name']:<24} {item['table']}.{item['column']}")
            print("")
            print(f"MEASURES ({len(model['measures'])})")
            for item in model["measures"]:
                print(
                    f"  {item['name']:<24} {item['format']:<9} {item['expression']}"
                )
            if payload["problems"]:
                print("")
                print("PROBLEMS")
                for problem in payload["problems"]:
                    print(f"  - {problem}")
            else:
                print("")
                print("Model matches the project schemas.")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_model_derive(args: argparse.Namespace) -> int:
    try:
        datasets = _csv_list(args.datasets)
        with _open(args) as project:
            store = SemanticModelStore(project)
            model = store.derive(
                name=args.name,
                base_table=args.base,
                dataset_ids=datasets or None,
            )
            if args.json:
                _print_json(store.to_dict(model))
                return 0
            print(
                f"Derived {model.name!r}: {len(model.tables)} table(s), "
                f"{len(model.dimensions)} dimension(s), "
                f"{len(model.measures)} measure(s)."
            )
            print(f"Saved to {store.path}")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_model_set(args: argparse.Namespace) -> int:
    try:
        payload = _read_json(args.model)
        if not isinstance(payload, dict):
            raise ValueError("--model must be a JSON object, @file or -.")
        with _open(args) as project:
            store = SemanticModelStore(project)
            model = SemanticModel.from_dict(payload.get("model", payload))
            saved = store.save(model)
            if args.json:
                _print_json(store.to_dict(saved))
                return 0
            print(f"Saved semantic model {saved.name!r} to {store.path}")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_model_remove(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            SemanticModelStore(project).remove()
            if args.json:
                _print_json({"removed": "semantic"})
            else:
                print("Removed the semantic model.")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_date_table(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            try:
                entry = generate_calendar(
                    project,
                    start=args.start,
                    end=args.end,
                    dataset_id=args.dataset,
                    overwrite=args.overwrite,
                )
            except CalendarError as exc:
                raise ValueError(str(exc)) from exc
            payload: dict[str, Any] = {
                "dataset": entry.id,
                "rows": entry.rows,
                "checksum_sha256": entry.checksum_sha256,
            }
            if args.rederive:
                store = SemanticModelStore(project)
                model = store.derive(name=f"{project.name} model")
                payload["model"] = store.to_dict(model)
            if args.json:
                _print_json(payload)
            else:
                print(
                    f"Calendar dataset {entry.id!r} stored "
                    f"({entry.rows} rows, {args.start}..{args.end})."
                )
                print(
                    "Re-derive the semantic model to expose its year/quarter/month/"
                    "week/day dimensions (data-studio model-derive)."
                )
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _parse_order(value: str) -> QueryOrder:
    parts = value.replace(":", " ").split()
    if not parts:
        raise ValueError("--order needs a field name.")
    return QueryOrder(parts[0], parts[1] if len(parts) > 1 else "asc")


def _cmd_query(args: argparse.Namespace) -> int:
    try:
        filters: list[QueryFilter] = []
        for field, operator, raw in args.filter or []:
            if operator in {"in", "not_in", "between"}:
                value: Any = [_coerce(part) for part in raw.split(",")]
            else:
                value = _coerce(raw)
            filters.append(QueryFilter(field, operator, value))
        orders = tuple(_parse_order(item) for item in (args.order or []))
        query = SemanticQuery(
            dimensions=_csv_list(args.dimensions),
            measures=_csv_list(args.measures),
            filters=tuple(filters),
            order_by=orders,
            limit=args.limit,
            table=args.table,
        )
        with _open(args) as project:
            model = SemanticModelStore(project).get()
            engine = SemanticEngine(
                project, model, use_pipeline=not args.raw
            )
            result = engine.query(query)
            if args.json:
                _print_json(result.to_dict())
                return 0
            if not result.rows:
                print("No rows.")
                return 0
            _print_table(
                [column.title for column in result.columns],
                [
                    [row.get(column.name) for column in result.columns]
                    for row in result.rows
                ],
            )
            print("")
            print(
                f"{result.row_count} row(s) in {result.elapsed_ms:.1f} ms"
                + (" (limited)" if result.truncated else "")
            )
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_dashboard_list(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            payload = DashboardStore(project).to_dict()
            if args.json:
                _print_json(payload)
                return 0
            dashboards = payload["dashboards"]
            if not dashboards:
                print("No dashboards.")
            else:
                print(f"{'ID':<28} {'TILES':>5} {'SLICERS':>7}  TITLE")
                print("-" * 84)
                for item in dashboards:
                    print(
                        f"{item['id']:<28} {len(item['tiles']):>5} "
                        f"{len(item['slicers']):>7}  {item['title']}"
                    )
            for error in payload["errors"]:
                print(f"WARNING: {error}", file=sys.stderr)
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_dashboard_show(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            if not args.dashboard_id:
                return _cmd_dashboard_list(args)
            dashboard = DashboardStore(project).get(args.dashboard_id)
            if args.json:
                _print_json(dashboard.to_dict())
                return 0
            print(f"{dashboard.title} ({dashboard.id})")
            if dashboard.description:
                print(f"  {dashboard.description}")
            print(f"  updated {dashboard.updated_at or 'never'}")
            print(f"  schedule {dashboard.schedule}")
            if dashboard.refreshed_at:
                print(f"  refreshed {dashboard.refreshed_at}")
            if dashboard.parameters:
                print("  parameters")
                for parameter in dashboard.parameters:
                    options = (
                        f" options={','.join(str(item) for item in parameter.options)}"
                        if parameter.options
                        else ""
                    )
                    print(
                        f"    {parameter.name:<20} {parameter.type:<7} "
                        f"default={parameter.default}{options}"
                    )
            if dashboard.slicers:
                print("  slicers")
                for slicer in dashboard.slicers:
                    print(
                        f"    {slicer.id:<20} {slicer.dimension:<24} "
                        f"{'multi' if slicer.multi else 'single'}"
                    )
            if dashboard.bookmarks:
                print("  views")
                for bookmark in dashboard.bookmarks:
                    detail = ", ".join(
                        f"{key}={','.join(str(value) for value in values)}"
                        for key, values in bookmark.selections.items()
                    )
                    print(f"    {bookmark.id:<20} {bookmark.title:<24} {detail or 'all data'}")
            if dashboard.tiles:
                print("  tiles")
                for tile in dashboard.tiles:
                    detail = ", ".join(tile.dimensions + tile.measures)
                    print(
                        f"    {tile.id:<20} {tile.kind:<7} "
                        f"width {tile.width}  {detail}"
                    )
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_dashboard_save(args: argparse.Namespace) -> int:
    try:
        tiles_raw = _read_json(args.tiles) or []
        slicers_raw = _read_json(args.slicers) or []
        bookmarks_raw = _read_json(args.bookmarks) if args.bookmarks else None
        if not isinstance(tiles_raw, list):
            raise ValueError("--tiles must be a JSON list.")
        if not isinstance(slicers_raw, list):
            raise ValueError("--slicers must be a JSON list.")
        if bookmarks_raw is not None and not isinstance(bookmarks_raw, list):
            raise ValueError("--bookmarks must be a JSON list.")
        parameters_raw = _read_json(args.parameters) if args.parameters else None
        if parameters_raw is not None and not isinstance(parameters_raw, list):
            raise ValueError("--parameters must be a JSON list.")
        tiles = tuple(DashboardTile.from_dict(item) for item in tiles_raw)
        slicers = tuple(
            DashboardSlicer.from_dict(item) for item in slicers_raw
        )
        bookmarks = tuple(
            DashboardBookmark.from_dict(item) for item in (bookmarks_raw or [])
        )
        parameters = tuple(
            DashboardParameter.from_dict(item) for item in (parameters_raw or [])
        )
        with _open(args) as project:
            model = SemanticModelStore(project).load()
            store = DashboardStore(project)
            if bookmarks_raw is None and args.overwrite:
                target = args.dashboard_id or slugify(args.title)
                with contextlib.suppress(KeyError):
                    bookmarks = store.get(target).bookmarks
            dashboard = store.create(
                title=args.title,
                description=args.description or "",
                tiles=tiles,
                slicers=slicers,
                bookmarks=bookmarks,
                parameters=parameters,
                dashboard_id=args.dashboard_id,
                model=model,
                overwrite=args.overwrite,
                schedule=args.schedule,
                report_path=True if args.report else None,
            )
            if args.json:
                _print_json(dashboard.to_dict())
                return 0
            print(f"Saved dashboard {dashboard.title!r} as {dashboard.id}")
            print(f"  tiles    {len(dashboard.tiles)}")
            print(f"  slicers  {len(dashboard.slicers)}")
            print(f"  views    {len(dashboard.bookmarks)}")
            print(f"  params   {len(dashboard.parameters)}")
            print(f"  schedule {dashboard.schedule}")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_dashboard_remove(args: argparse.Namespace) -> int:
    try:
        with _open(args) as project:
            DashboardStore(project).remove(args.dashboard_id)
            if args.json:
                _print_json({"removed": args.dashboard_id})
            else:
                print(f"Removed dashboard {args.dashboard_id!r}.")
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _parse_selection(value: str) -> tuple[str, list[Any]]:
    key, _, raw = value.partition("=")
    key = key.strip()
    if not key:
        raise ValueError("--select needs SLICER=VALUE.")
    if not raw.strip():
        return key, []
    return key, [_coerce(item.strip()) for item in raw.split(",")]


def _parse_dashboard_filters(values: list[str] | None) -> list[QueryFilter]:
    filters: list[QueryFilter] = []
    for item in values or []:
        field, _, raw = item.partition("=")
        field = field.strip()
        if not field:
            raise ValueError("--filter needs FIELD=VALUE.")
        filters.append(QueryFilter(field, "=", _coerce(raw.strip())))
    return filters


def _parse_parameters(values: list[str] | None) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for item in values or []:
        name, _, raw = item.partition("=")
        name = name.strip().lstrip("@").strip()
        if not name:
            raise ValueError("--param needs NAME=VALUE.")
        parameters[name] = _coerce(raw.strip())
    return parameters


def _cmd_dashboard_render(args: argparse.Namespace) -> int:
    try:
        selections = dict(_parse_selection(item) for item in (args.select or []))
        cross_filters = _parse_dashboard_filters(args.filter)
        parameters = _parse_parameters(args.param)
        with _open(args) as project:
            model = SemanticModelStore(project).get()
            dashboard = DashboardStore(project).get(args.dashboard_id)
            view = DashboardRenderer(
                SemanticEngine(project, model, use_pipeline=not args.raw)
            ).render(
                dashboard,
                selections=selections,
                filters=cross_filters,
                parameters=parameters,
                theme=args.theme,
            )
            if args.json:
                _print_json(view.to_dict())
                return 0
            print(f"{dashboard.title} ({dashboard.id})")
            print(f"  generated {view.generated_at}")
            for tile in view.tiles:
                print("")
                print(f"{tile.tile.title} [{tile.tile.kind}]")
                if tile.error:
                    print(f"  ERROR {tile.error}")
                    continue
                result = tile.result
                if result is None:
                    print("  no result")
                    continue
                if tile.tile.kind == "kpi":
                    row = result.rows[0] if result.rows else {}
                    for column in result.columns:
                        print(f"  {column.title:<24} {_cell(row.get(column.name))}")
                elif tile.tile.kind == "table":
                    _print_table(
                        [column.title for column in result.columns],
                        [
                            [row.get(column.name) for column in result.columns]
                            for row in result.rows
                        ],
                    )
                else:
                    print(
                        f"  {result.row_count} row(s), chart {tile.tile.chart}"
                        + (", SVG rendered" if tile.svg else "")
                    )
        return 0
    except _ERRORS as exc:
        return _fail(exc)


def _cmd_dashboard_report(args: argparse.Namespace) -> int:
    try:
        selections = dict(_parse_selection(item) for item in (args.select or []))
        cross_filters = _parse_dashboard_filters(args.filter)
        parameters = _parse_parameters(args.param)
        with _open(args) as project:
            model = SemanticModelStore(project).get()
            dashboard = DashboardStore(project).get(args.dashboard_id)
            view = DashboardRenderer(
                SemanticEngine(project, model, use_pipeline=not args.raw)
            ).render(
                dashboard,
                selections=selections,
                filters=cross_filters,
                parameters=parameters,
                theme=args.theme,
            )
            destination = args.destination or f"reports/dashboard-{dashboard.id}.html"
            path = Path(destination)
            if not path.is_absolute():
                path = safe_project_path(
                    project.root, destination.replace("\\", "/")
                )
            written = write_dashboard_report(
                view,
                path,
                title=args.title,
                theme=args.theme,
            )
            if args.json:
                _print_json(
                    {
                        "destination": str(written),
                        "bytes": written.stat().st_size,
                        "tiles": len(view.tiles),
                    }
                )
                return 0
            print(f"Wrote dashboard report to {written}")
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

    analyses = subparsers.add_parser("analyses", help="List available analyses.")
    analyses.add_argument("--json", action="store_true", help="Emit JSON.")
    analyses.set_defaults(handler=_cmd_analyses)

    analyze = subparsers.add_parser(
        "analyze", help="Run an analysis on the prepared pipeline output."
    )
    analyze.add_argument("project", help="Project directory.")
    analyze.add_argument("analysis", help="Registered analysis name.")
    analyze.add_argument("--params", help="Analysis parameters as JSON, @file or -.")
    analyze.add_argument("--dataset", help="Override the dataset.")
    analyze.add_argument(
        "--raw",
        action="store_true",
        help="Analyse the raw dataset instead of the pipeline output.",
    )
    analyze.add_argument("--json", action="store_true", help="Emit JSON.")
    analyze.set_defaults(handler=_cmd_analyze)

    report = subparsers.add_parser(
        "report", help="Run an analysis and export a self-contained HTML report."
    )
    report.add_argument("project", help="Project directory.")
    report.add_argument("analysis", help="Registered analysis name.")
    report.add_argument("--params", help="Analysis parameters as JSON, @file or -.")
    report.add_argument("--dataset", help="Override the dataset.")
    report.add_argument(
        "--raw",
        action="store_true",
        help="Analyse the raw dataset instead of the pipeline output.",
    )
    report.add_argument(
        "--destination",
        help="Output HTML path (defaults to reports/report-<timestamp>.html).",
    )
    report.add_argument("--title", help="Report title (defaults to the analysis title).")
    report.add_argument(
        "--theme",
        choices=("light", "dark"),
        default="light",
        help="Report colour theme.",
    )
    report.add_argument("--json", action="store_true", help="Emit JSON.")
    report.set_defaults(handler=_cmd_report)

    save_analysis = subparsers.add_parser(
        "save-analysis",
        help="Run an analysis and persist it under analyses/ for refresh and reuse.",
    )
    save_analysis.add_argument("project", help="Project directory.")
    save_analysis.add_argument("name", help="Saved analysis name (used for its file id).")
    save_analysis.add_argument("analysis", help="Registered analysis name.")
    save_analysis.add_argument("--params", help="Analysis parameters as JSON, @file or -.")
    save_analysis.add_argument("--dataset", help="Override the dataset.")
    save_analysis.add_argument(
        "--raw",
        action="store_true",
        help="Analyse the raw dataset instead of the pipeline output.",
    )
    save_analysis.add_argument(
        "--schedule",
        choices=SCHEDULES,
        default="manual",
        help="Refresh policy: manual, on_open, hourly or daily.",
    )
    save_analysis.add_argument("--title", help="Display title (defaults to the name).")
    save_analysis.add_argument(
        "--report",
        action="store_true",
        help="Write reports/<name>.html on every save and refresh.",
    )
    save_analysis.add_argument(
        "--report-path",
        help="Custom report path under reports/ (implies --report).",
    )
    save_analysis.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing saved analysis with the same name.",
    )
    save_analysis.add_argument("--json", action="store_true", help="Emit JSON.")
    save_analysis.set_defaults(handler=_cmd_save_analysis)

    saved = subparsers.add_parser(
        "saved",
        help="List saved analyses or show one with its last result.",
    )
    saved.add_argument("project", help="Project directory.")
    saved.add_argument("saved_id", nargs="?", help="Saved analysis id to show.")
    saved.add_argument("--json", action="store_true", help="Emit JSON.")
    saved.set_defaults(handler=_cmd_saved)

    refresh = subparsers.add_parser(
        "refresh",
        help="Refresh due (or all) saved analyses and dashboards; suitable for Task Scheduler/cron.",
    )
    refresh.add_argument("project", help="Project directory.")
    refresh.add_argument("saved_id", nargs="?", help="Refresh only this saved analysis.")
    refresh.add_argument(
        "--dashboard-id",
        help="Refresh only this dashboard (instead of all due dashboards).",
    )
    refresh.add_argument(
        "--due",
        action="store_true",
        help="Refresh only items whose schedule is due (default).",
    )
    refresh.add_argument(
        "--all",
        action="store_true",
        help="Refresh every saved analysis and dashboard regardless of schedule.",
    )
    refresh.add_argument("--json", action="store_true", help="Emit JSON.")
    refresh.set_defaults(handler=_cmd_refresh)

    remove_saved = subparsers.add_parser(
        "remove-saved", help="Delete a saved analysis document."
    )
    remove_saved.add_argument("project", help="Project directory.")
    remove_saved.add_argument("saved_id", help="Saved analysis id to remove.")
    remove_saved.add_argument("--json", action="store_true", help="Emit JSON.")
    remove_saved.set_defaults(handler=_cmd_remove_saved)

    model_show = subparsers.add_parser(
        "model-show", help="Show the semantic model, dimensions and measures."
    )
    model_show.add_argument("project", help="Project directory.")
    model_show.add_argument("--json", action="store_true", help="Emit JSON.")
    model_show.set_defaults(handler=_cmd_model_show)

    model_derive = subparsers.add_parser(
        "model-derive", help="Derive and save a starter semantic model from datasets."
    )
    model_derive.add_argument("project", help="Project directory.")
    model_derive.add_argument("--name", default="Semantic model", help="Model name.")
    model_derive.add_argument(
        "--base", help="Dataset id to use as the default (fact) table."
    )
    model_derive.add_argument(
        "--datasets", help="Comma-separated dataset ids (defaults to all)."
    )
    model_derive.add_argument("--json", action="store_true", help="Emit JSON.")
    model_derive.set_defaults(handler=_cmd_model_derive)

    model_set = subparsers.add_parser(
        "model-set", help="Replace the semantic model from a JSON document."
    )
    model_set.add_argument("project", help="Project directory.")
    model_set.add_argument(
        "--model", required=True, help="Model JSON, @file or - for stdin."
    )
    model_set.add_argument("--json", action="store_true", help="Emit JSON.")
    model_set.set_defaults(handler=_cmd_model_set)

    model_remove = subparsers.add_parser(
        "model-remove", help="Delete the project's semantic model."
    )
    model_remove.add_argument("project", help="Project directory.")
    model_remove.add_argument("--json", action="store_true", help="Emit JSON.")
    model_remove.set_defaults(handler=_cmd_model_remove)

    date_table = subparsers.add_parser(
        "date-table",
        help="Generate a one-row-per-day calendar dataset for time intelligence.",
    )
    date_table.add_argument("project", help="Project directory.")
    date_table.add_argument("--start", required=True, help="First day (YYYY-MM-DD).")
    date_table.add_argument("--end", required=True, help="Last day (YYYY-MM-DD).")
    date_table.add_argument(
        "--dataset", default="calendar", help="Dataset id to store (default: calendar)."
    )
    date_table.add_argument(
        "--overwrite", action="store_true", help="Replace an existing calendar dataset."
    )
    date_table.add_argument(
        "--rederive",
        action="store_true",
        help="Re-derive the semantic model afterwards (replaces the model).",
    )
    date_table.add_argument("--json", action="store_true", help="Emit JSON.")
    date_table.set_defaults(handler=_cmd_date_table)

    query = subparsers.add_parser(
        "query", help="Run a semantic query (dimensions, measures and filters)."
    )
    query.add_argument("project", help="Project directory.")
    query.add_argument("--dimensions", help="Comma-separated dimension names.")
    query.add_argument("--measures", help="Comma-separated measure names.")
    query.add_argument(
        "--filter",
        nargs=3,
        action="append",
        metavar=("FIELD", "OP", "VALUE"),
        help="Filter, for example: --filter region = North.",
    )
    query.add_argument(
        "--order", action="append", help="Order by a field, for example: total desc."
    )
    query.add_argument("--limit", type=int, help="Maximum rows.")
    query.add_argument("--table", help="Override the semantic base table.")
    query.add_argument(
        "--raw",
        action="store_true",
        help="Query raw datasets instead of the pipeline output for the base table.",
    )
    query.add_argument("--json", action="store_true", help="Emit JSON.")
    query.set_defaults(handler=_cmd_query)

    dashboard_list = subparsers.add_parser(
        "dashboard-list", help="List dashboards in a project."
    )
    dashboard_list.add_argument("project", help="Project directory.")
    dashboard_list.add_argument("--json", action="store_true", help="Emit JSON.")
    dashboard_list.set_defaults(handler=_cmd_dashboard_list)

    dashboard_save = subparsers.add_parser(
        "dashboard-save", help="Create or replace a dashboard definition."
    )
    dashboard_save.add_argument("project", help="Project directory.")
    dashboard_save.add_argument("--title", required=True, help="Dashboard title.")
    dashboard_save.add_argument("--id", dest="dashboard_id", help="Dashboard id.")
    dashboard_save.add_argument("--description", help="Dashboard description.")
    dashboard_save.add_argument(
        "--tiles", help="Tiles as a JSON list, @file or - for stdin."
    )
    dashboard_save.add_argument(
        "--slicers", help="Slicers as a JSON list, @file or - for stdin."
    )
    dashboard_save.add_argument(
        "--bookmarks", help="Saved views as a JSON list, @file or - for stdin."
    )
    dashboard_save.add_argument(
        "--parameters",
        help="What-if parameters as a JSON list, @file or - for stdin.",
    )
    dashboard_save.add_argument(
        "--schedule",
        choices=("manual", "on_open", "hourly", "daily"),
        default="manual",
        help="Refresh schedule (default: manual).",
    )
    dashboard_save.add_argument(
        "--report",
        action="store_true",
        help="Rewrite reports/dashboard-<id>.html on every scheduled refresh.",
    )
    dashboard_save.add_argument(
        "--overwrite", action="store_true", help="Replace an existing dashboard id."
    )
    dashboard_save.add_argument("--json", action="store_true", help="Emit JSON.")
    dashboard_save.set_defaults(handler=_cmd_dashboard_save)

    dashboard_show = subparsers.add_parser(
        "dashboard-show", help="Show a dashboard definition."
    )
    dashboard_show.add_argument("project", help="Project directory.")
    dashboard_show.add_argument("dashboard_id", nargs="?", help="Dashboard id.")
    dashboard_show.add_argument("--json", action="store_true", help="Emit JSON.")
    dashboard_show.set_defaults(handler=_cmd_dashboard_show)

    dashboard_render = subparsers.add_parser(
        "dashboard-render", help="Render a dashboard and print tile results."
    )
    dashboard_render.add_argument("project", help="Project directory.")
    dashboard_render.add_argument("dashboard_id", help="Dashboard id.")
    dashboard_render.add_argument(
        "--select",
        action="append",
        metavar="SLICER=VALUE",
        help="Slicer selection, for example: --select region=North.",
    )
    dashboard_render.add_argument(
        "--filter",
        action="append",
        metavar="FIELD=VALUE",
        help="Cross-filter every tile, for example: --filter region=North.",
    )
    dashboard_render.add_argument(
        "--param",
        action="append",
        metavar="NAME=VALUE",
        help="What-if parameter value, for example: --param top_n=5. Repeatable.",
    )
    dashboard_render.add_argument(
        "--theme",
        choices=("light", "dark"),
        default="light",
        help="Chart theme.",
    )
    dashboard_render.add_argument(
        "--raw",
        action="store_true",
        help="Query raw datasets instead of the pipeline output for the base table.",
    )
    dashboard_render.add_argument("--json", action="store_true", help="Emit JSON.")
    dashboard_render.set_defaults(handler=_cmd_dashboard_render)

    dashboard_remove = subparsers.add_parser(
        "dashboard-remove", help="Delete a dashboard definition."
    )
    dashboard_remove.add_argument("project", help="Project directory.")
    dashboard_remove.add_argument("dashboard_id", help="Dashboard id.")
    dashboard_remove.add_argument("--json", action="store_true", help="Emit JSON.")
    dashboard_remove.set_defaults(handler=_cmd_dashboard_remove)

    dashboard_report = subparsers.add_parser(
        "dashboard-report", help="Export a dashboard as a self-contained HTML report."
    )
    dashboard_report.add_argument("project", help="Project directory.")
    dashboard_report.add_argument("dashboard_id", help="Dashboard id.")
    dashboard_report.add_argument(
        "--destination",
        help="Output HTML path (defaults to reports/dashboard-<id>.html).",
    )
    dashboard_report.add_argument("--title", help="Report title.")
    dashboard_report.add_argument(
        "--select",
        action="append",
        metavar="SLICER=VALUE",
        help="Slicer selection, for example: --select region=North.",
    )
    dashboard_report.add_argument(
        "--filter",
        action="append",
        metavar="FIELD=VALUE",
        help="Cross-filter every tile, for example: --filter region=North.",
    )
    dashboard_report.add_argument(
        "--param",
        action="append",
        metavar="NAME=VALUE",
        help="What-if parameter value, for example: --param top_n=5. Repeatable.",
    )
    dashboard_report.add_argument(
        "--theme",
        choices=("light", "dark"),
        default="light",
        help="Report colour theme.",
    )
    dashboard_report.add_argument(
        "--raw",
        action="store_true",
        help="Query raw datasets instead of the pipeline output for the base table.",
    )
    dashboard_report.add_argument("--json", action="store_true", help="Emit JSON.")
    dashboard_report.set_defaults(handler=_cmd_dashboard_report)

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
