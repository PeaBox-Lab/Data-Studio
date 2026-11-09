"""FastAPI application for the local Data Studio client.

Phase 1 exposes the project store, dataset import, schema/preview, the step catalog,
pipeline editing, execution and export. Phase 2 adds the analysis catalog and typed
result execution. Every endpoint is a thin wrapper over the kernel: no business logic
lives here, so the CLI and the workspace UI behave identically.

The app owns one open project (see ``state.ProjectManager``). It starts with a project if
``create_app(project_root=...)`` is given or ``DATA_STUDIO_PROJECT`` is set; otherwise the
workspace asks the user to create or open one.
"""

from __future__ import annotations

import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from ..analysis import DEFAULT_ANALYSIS_REGISTRY, AnalysisContext, AnalysisError
from ..analysis.saved import (
    SCHEDULES,
    SavedAnalysisStore,
)
from ..engine.duckdb_engine import EngineError
from ..kernel.chart_render import DEFAULT_HEIGHT, DEFAULT_WIDTH, render_chart
from ..kernel.spec import ChartSpec
from ..pipeline import Pipeline, PipelineRunError, make_step
from ..pipeline.runner import PipelineRunner
from ..project.manifest import safe_project_path
from ..project.store import Project, ProjectError
from ..reporting import write_html_report
from ..steps import DEFAULT_REGISTRY
from ..steps.registry import StepError, UnknownStepError
from ..version import API_VERSION, PROJECT_FORMAT_VERSION, __version__
from .state import NoProjectError, ProjectManager

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_FALLBACK_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Data Studio</title></head>
<body style="font-family: system-ui, sans-serif; padding: 40px">
<h1>Data Studio</h1>
<p>The workspace UI is not installed. The API is available at <a href="/docs">/docs</a>.</p>
</body></html>
"""


def _workspace_html() -> str:
    path = _STATIC_DIR / "index.html"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return _FALLBACK_HTML


def _initial_root(project_root: str | Path | None) -> str | None:
    if project_root is not None:
        return str(Path(project_root).resolve())
    return os.environ.get("DATA_STUDIO_PROJECT") or None


def create_app(
    project_root: str | Path | None = None,
    *,
    manager: ProjectManager | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Data Studio API",
        version=__version__,
        description=(
            "Local API for the Data Studio workbench: project storage, dataset import, "
            "schemas, previews, pipeline editing, execution and export."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    state = manager or ProjectManager(_initial_root(project_root))
    app.state.project_manager = state
    app.state.pipeline_runner = PipelineRunner(DEFAULT_REGISTRY)

    # -- error handling ----------------------------------------------------

    @app.exception_handler(NoProjectError)
    async def no_project(_: Request, exc: NoProjectError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ProjectError)
    async def project_error(_: Request, exc: ProjectError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(PipelineRunError)
    async def run_error(_: Request, exc: PipelineRunError) -> JSONResponse:
        payload = {"detail": str(exc), **exc.to_dict()}
        return JSONResponse(payload, status_code=400)

    @app.exception_handler(UnknownStepError)
    async def unknown_step(_: Request, exc: UnknownStepError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(StepError)
    async def step_error(_: Request, exc: StepError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(AnalysisError)
    async def analysis_error(_: Request, exc: AnalysisError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing_file(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(FileExistsError)
    async def exists(_: Request, exc: FileExistsError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing_key(_: Request, exc: KeyError) -> JSONResponse:
        detail = exc.args[0] if exc.args else str(exc)
        return JSONResponse({"detail": str(detail)}, status_code=404)

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(TypeError)
    async def type_error(_: Request, exc: TypeError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(EngineError)
    async def engine_error(_: Request, exc: EngineError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def project() -> Project:
        return state.current()

    def analysis_context(
        dataset_id: str | None, use_pipeline: bool
    ) -> AnalysisContext:
        return project().analysis_context(
            dataset_id=dataset_id, use_pipeline=use_pipeline
        )

    def saved_store() -> SavedAnalysisStore:
        return SavedAnalysisStore(project())

    def refresh_on_open(active: Project) -> None:
        # A scheduled refresh must never stop a project from opening.
        with suppress(OSError, ValueError, TypeError, KeyError, RuntimeError):
            SavedAnalysisStore(active).refresh_due(only_on_open=True)

    def parse_requested_dataset(payload: dict[str, Any]) -> str | None:
        dataset_id = payload.get("dataset")
        if dataset_id is not None and not isinstance(dataset_id, str):
            raise ValueError("Analysis 'dataset' must be a string or null.")
        return dataset_id.strip() if dataset_id else None

    def report_results(payload: dict[str, Any]) -> list[Any]:
        raw = payload.get("analyses")
        if raw is None:
            if "analysis" not in payload:
                raise ValueError(
                    "A report needs an 'analysis' name or an 'analyses' list."
                )
            raw = [payload]
        if not isinstance(raw, list) or not raw:
            raise ValueError("'analyses' must be a non-empty list of analysis requests.")
        results = []
        for index, entry in enumerate(raw):
            if not isinstance(entry, dict):
                raise ValueError(f"Analysis request {index} must be an object.")
            name = entry.get("analysis")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    f"Analysis request {index} needs a non-empty 'analysis' name."
                )
            params = entry.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError(f"Analysis request {index} 'params' must be an object.")
            context = analysis_context(
                parse_requested_dataset(entry),
                bool(entry.get("use_pipeline", True)),
            )
            results.append(DEFAULT_ANALYSIS_REGISTRY.run(name, context, params))
        return results

    # -- system ------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        return _workspace_html()

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "api": API_VERSION}

    @app.get("/api/v1/meta", tags=["system"])
    def meta() -> dict[str, object]:
        return {
            "name": "Data Studio",
            "version": __version__,
            "api_version": API_VERSION,
            "project_format_version": PROJECT_FORMAT_VERSION,
            "phase": 2,
            "capabilities": {
                "projects": True,
                "datasets": True,
                "pipelines": True,
                "exports": True,
                "analyses": True,
                "dashboards": False,
            },
            "operations": len(DEFAULT_REGISTRY),
            "analysis_operations": len(DEFAULT_ANALYSIS_REGISTRY),
        }

    # -- project lifecycle -------------------------------------------------

    @app.get("/api/v1/project", tags=["project"])
    def get_project() -> dict[str, Any]:
        if not state.is_open and state.root is None:
            return {"open": False, "root": None, "project": None}
        active = project()
        return {
            "open": True,
            "root": str(active.root),
            "project": active.to_dict(),
        }

    @app.post("/api/v1/project/open", tags=["project"])
    def open_project(payload: dict[str, Any]) -> dict[str, Any]:
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("An object with a 'path' string is required.")
        active = state.open(path, verify=bool(payload.get("verify", False)))
        refresh_on_open(active)
        return {"open": True, "root": str(active.root), "project": active.to_dict()}

    @app.post("/api/v1/project/create", tags=["project"])
    def create_project(payload: dict[str, Any]) -> dict[str, Any]:
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("An object with a 'path' string is required.")
        name = payload.get("name")
        active = state.create(path, str(name) if name else None)
        refresh_on_open(active)
        return {"open": True, "root": str(active.root), "project": active.to_dict()}

    @app.post("/api/v1/project/close", tags=["project"])
    def close_project() -> dict[str, bool]:
        state.close()
        return {"open": False}

    # -- datasets ----------------------------------------------------------

    @app.get("/api/v1/datasets", tags=["datasets"])
    def list_datasets() -> list[dict[str, Any]]:
        return [summary.to_dict() for summary in project().datasets()]

    @app.post("/api/v1/datasets", status_code=201, tags=["datasets"])
    async def upload_dataset(
        request: Request,
        filename: str = Query(..., description="Original file name, including suffix."),
        dataset_id: str | None = Query(None),
        format: str | None = Query(None),
        replace: bool = Query(False),
    ) -> dict[str, Any]:
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="The request body is empty.")
        entry = project().add_dataset_bytes(
            data,
            filename,
            dataset_id=dataset_id,
            source_format=format,
            replace=replace,
        )
        return entry.to_dict()

    @app.delete("/api/v1/datasets/{dataset_id}", tags=["datasets"])
    def remove_dataset(dataset_id: str) -> dict[str, Any]:
        active = project()
        active.remove_dataset(dataset_id)
        return {"removed": dataset_id}

    @app.get("/api/v1/datasets/{dataset_id}/schema", tags=["datasets"])
    def dataset_schema(dataset_id: str) -> dict[str, Any]:
        return project().dataset_schema(dataset_id).to_dict()

    @app.get("/api/v1/datasets/{dataset_id}/preview", tags=["datasets"])
    def dataset_preview(
        dataset_id: str,
        limit: int = Query(50, ge=0, le=5000),
    ) -> dict[str, Any]:
        data = project().preview(dataset_id, limit)
        return {
            "schema": data.schema.to_dict(),
            "rows": [list(row) for row in data.rows],
        }

    # -- steps and pipeline ------------------------------------------------

    @app.get("/api/v1/operations", tags=["pipeline"])
    def operations() -> list[dict[str, Any]]:
        return DEFAULT_REGISTRY.catalog()

    @app.get("/api/v1/pipeline", tags=["pipeline"])
    def get_pipeline() -> dict[str, Any]:
        return project().load_pipeline().to_dict()

    @app.put("/api/v1/pipeline", tags=["pipeline"])
    def put_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
        active = project()
        pipeline = Pipeline.from_dict(payload)
        return active.save_pipeline(pipeline).to_dict()

    @app.post("/api/v1/pipeline/steps", status_code=201, tags=["pipeline"])
    def add_step(payload: dict[str, Any]) -> dict[str, Any]:
        active = project()
        operation = payload.get("operation")
        if not isinstance(operation, str) or not operation.strip():
            raise ValueError("A step needs an 'operation' string.")
        params = payload.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError("Step 'params' must be an object.")
        pipeline = active.load_pipeline()
        step = make_step(
            operation,
            params,
            step_id=payload.get("id"),
            existing=[item.id for item in pipeline.steps],
            title=payload.get("title"),
            enabled=bool(payload.get("enabled", True)),
        )
        position = payload.get("position")
        pipeline = active.save_pipeline(pipeline.add_step(step, position=position))
        return {"step": step.to_dict(), "pipeline": pipeline.to_dict()}

    @app.delete("/api/v1/pipeline/steps/{step_id}", tags=["pipeline"])
    def remove_step(step_id: str) -> dict[str, Any]:
        active = project()
        pipeline = active.save_pipeline(active.load_pipeline().remove_step(step_id))
        return pipeline.to_dict()

    @app.post("/api/v1/pipeline/undo", tags=["pipeline"])
    def undo_step() -> dict[str, Any]:
        active = project()
        pipeline = active.undo(active.load_pipeline())
        return pipeline.to_dict()

    # -- run and export ----------------------------------------------------

    @app.post("/api/v1/run", tags=["pipeline"])
    def run_pipeline(
        dataset: str | None = Query(None),
        use_cache: bool = Query(True),
        count_rows: bool = Query(True),
    ) -> dict[str, Any]:
        result = project().run_pipeline(
            dataset_id=dataset,
            use_cache=use_cache,
            count_rows=count_rows,
        )
        return result.to_dict()

    @app.post("/api/v1/run/preview", tags=["pipeline"])
    def run_preview(
        limit: int = Query(50, ge=0, le=5000),
        dataset: str | None = Query(None),
    ) -> dict[str, Any]:
        result = project().run_pipeline(dataset_id=dataset)
        data = result.preview(limit)
        return {
            "run": result.to_dict(),
            "schema": data.schema.to_dict(),
            "rows": [list(row) for row in data.rows],
        }

    @app.post("/api/v1/export", tags=["pipeline"])
    def export(payload: dict[str, Any]) -> dict[str, Any]:
        destination = payload.get("destination")
        if not isinstance(destination, str) or not destination.strip():
            raise ValueError("An object with a 'destination' string is required.")
        active = project()
        result = active.run_pipeline(
            dataset_id=payload.get("dataset"),
            use_cache=bool(payload.get("use_cache", True)),
        )
        path = Path(destination)
        if not path.is_absolute():
            path = active.root / path
        written = active.export(
            result,
            path,
            source_format=payload.get("format"),
            overwrite=bool(payload.get("overwrite", True)),
        )
        return {
            "destination": str(written),
            "rows": result.rows,
            "schema": result.schema.to_dict(),
        }

    # -- analyses ----------------------------------------------------------

    @app.get("/api/v1/analyses", tags=["analyses"])
    def analyses_catalog() -> list[dict[str, Any]]:
        return DEFAULT_ANALYSIS_REGISTRY.catalog()

    @app.post("/api/v1/analyses/run", tags=["analyses"])
    def run_analysis(payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("analysis")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("An object with an 'analysis' name is required.")
        params = payload.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError("Analysis 'params' must be an object.")
        context = analysis_context(
            parse_requested_dataset(payload),
            bool(payload.get("use_pipeline", True)),
        )
        return DEFAULT_ANALYSIS_REGISTRY.run(name, context, params).to_dict()

    # -- saved analyses ----------------------------------------------------

    def saved_view(saved: Any, store: SavedAnalysisStore) -> dict[str, Any]:
        view = store.describe(saved)
        view["result"] = saved.result.to_dict() if saved.result else None
        return view

    @app.get("/api/v1/saved-analyses", tags=["analyses"])
    def list_saved_analyses() -> dict[str, Any]:
        store = saved_store()
        return {
            "analyses": store.summaries(),
            "errors": store.last_errors,
            "schedules": list(SCHEDULES),
        }

    @app.post("/api/v1/saved-analyses", status_code=201, tags=["analyses"])
    def save_analysis(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("A saved analysis request object is required.")
        name = payload.get("name") or payload.get("title") or payload.get("id")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("A saved analysis needs a non-empty 'name'.")
        analysis = payload.get("analysis")
        if not isinstance(analysis, str) or not analysis.strip():
            raise ValueError("A saved analysis needs an 'analysis' name.")
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("Saved analysis 'params' must be an object.")
        schedule = payload.get("schedule") or "manual"
        if schedule not in SCHEDULES:
            raise ValueError(f"Schedule must be one of: {', '.join(SCHEDULES)}.")
        report_path = payload.get("report_path")
        if report_path is None and payload.get("report"):
            report_path = True
        store = saved_store()
        saved = store.save(
            name=name,
            analysis=analysis,
            params=params,
            dataset=parse_requested_dataset(payload),
            use_pipeline=bool(payload.get("use_pipeline", True)),
            schedule=str(schedule),
            title=payload.get("title"),
            report_path=report_path,
            overwrite=bool(payload.get("overwrite", False)),
        )
        return saved_view(saved, store)

    @app.post("/api/v1/saved-analyses/refresh", tags=["analyses"])
    def refresh_saved_analyses(
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = payload or {}
        if not isinstance(request, dict):
            raise ValueError("A refresh request must be an object.")
        store = saved_store()
        saved_id = request.get("id") or request.get("name")
        if saved_id is not None:
            refreshed = [store.refresh(str(saved_id))]
            failures: list[dict[str, str]] = []
        elif bool(request.get("due", True)):
            refreshed, failures = store.refresh_due()
        else:
            refreshed, failures = store.refresh_all()
        return {
            "analyses": [saved_view(item, store) for item in refreshed],
            "failed": failures,
        }

    @app.get("/api/v1/saved-analyses/{saved_id}", tags=["analyses"])
    def get_saved_analysis(saved_id: str) -> dict[str, Any]:
        store = saved_store()
        return saved_view(store.get(saved_id), store)

    @app.delete("/api/v1/saved-analyses/{saved_id}", tags=["analyses"])
    def remove_saved_analysis(saved_id: str) -> dict[str, str]:
        saved_store().remove(saved_id)
        return {"removed": saved_id}

    @app.post("/api/v1/saved-analyses/{saved_id}/refresh", tags=["analyses"])
    def refresh_saved_analysis(saved_id: str) -> dict[str, Any]:
        store = saved_store()
        return saved_view(store.refresh(saved_id), store)

    @app.get(
        "/api/v1/saved-analyses/{saved_id}/report",
        response_class=HTMLResponse,
        tags=["analyses"],
    )
    def saved_analysis_report(saved_id: str) -> HTMLResponse:
        saved = saved_store().get(saved_id)
        if not saved.report_path:
            raise ValueError("This saved analysis has no report artifact.")
        path = safe_project_path(project().root, saved.report_path)
        if not path.is_file():
            raise KeyError("Report artifact not found; refresh the saved analysis.")
        return HTMLResponse(path.read_text(encoding="utf-8"))

    # -- charts and reports ------------------------------------------------

    @app.post("/api/v1/charts/render", tags=["charts"])
    def render_chart_spec(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("A chart specification object is required.")
        theme = payload.get("theme") or "light"
        width = int(payload.get("width") or DEFAULT_WIDTH)
        height = int(payload.get("height") or DEFAULT_HEIGHT)
        if "charts" in payload:
            raw = payload["charts"]
            if not isinstance(raw, list) or not raw:
                raise ValueError("'charts' must be a non-empty list of specifications.")
            specs = [ChartSpec.from_dict(item) for item in raw]
            return {
                "svgs": [
                    render_chart(spec, width=width, height=height, theme=theme)
                    for spec in specs
                ]
            }
        chart_payload = payload.get("chart", payload)
        spec = ChartSpec.from_dict(chart_payload)
        return {
            "title": spec.title,
            "chart_type": spec.chart_type.value,
            "svg": render_chart(spec, width=width, height=height, theme=theme),
        }

    @app.post("/api/v1/analyses/report", tags=["analyses"])
    def analysis_report(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("A report request object is required.")
        results = report_results(payload)
        active = project()
        title = payload.get("title") or "Data Studio report"
        if not isinstance(title, str):
            raise ValueError("Report 'title' must be a string.")
        theme = payload.get("theme") or "light"
        if theme not in ("light", "dark"):
            raise ValueError("Report theme must be 'light' or 'dark'.")
        destination = payload.get("destination")
        if destination is None:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            destination = f"reports/report-{stamp}.html"
        if not isinstance(destination, str) or not destination.strip():
            raise ValueError("Report 'destination' must be a non-empty string.")
        relative = destination.strip().replace("\\", "/")
        path = safe_project_path(active.root, relative)
        written = write_html_report(
            results,
            path,
            title=title,
            subtitle=payload.get("subtitle"),
            theme=theme,
            chart_width=int(payload.get("width") or DEFAULT_WIDTH),
            chart_height=int(payload.get("height") or DEFAULT_HEIGHT),
        )
        return {
            "title": title,
            "destination": str(written),
            "relative_path": relative,
            "bytes": written.stat().st_size,
            "results": [item.result_id for item in results],
            "html": written.read_text(encoding="utf-8"),
        }

    return app


app = create_app()
