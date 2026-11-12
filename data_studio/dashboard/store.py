"""Persistence for dashboards.

Each dashboard is a versioned JSON document under ``dashboards/``. Dashboards hold no
data and no SQL: they reference semantic dimensions and measures, so a dashboard stays
small and can be reviewed like any other project artifact.

Dashboards also carry a *schedule* (``manual``, ``on_open``, ``hourly``, ``daily``).
Scheduled refresh re-executes the tiles against the current engine and stamps the
document — and optionally rewrites the HTML report artifact under ``reports/``. A
fingerprint of the semantic model, the backing dataset checksums, the pipeline and the
app version marks a dashboard *stale* when its inputs change; staleness, not wall-clock
time alone, makes a scheduled dashboard due. Schedules run on demand — project open, a
workspace timer or ``data-studio refresh --due`` — so there is no background daemon.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..kernel.provenance import hash_parameters, utc_now_iso
from ..project.manifest import safe_project_path
from ..schedule import is_due, next_refresh
from ..version import __version__
from .model import (
    Dashboard,
    DashboardBookmark,
    DashboardError,
    DashboardParameter,
    DashboardSlicer,
    DashboardTile,
    slugify,
    validate_dashboard,
)

if TYPE_CHECKING:
    from ..project.store import Project
    from ..semantic.model import SemanticModel

_LOCK = threading.RLock()


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


def dashboard_fingerprint(project: Project, model: Any) -> str:
    """Fingerprint everything a dashboard depends on."""

    datasets: dict[str, str | None] = {}
    for table in model.tables:
        try:
            entry = project.dataset_entry(table.dataset)
            datasets[table.dataset] = entry.checksum_sha256
        except KeyError:
            datasets[table.dataset] = None
    try:
        pipeline = project.load_pipeline().to_dict()
    except Exception:  # a missing/unreadable pipeline must not block refresh
        pipeline = None
    return hash_parameters(
        {
            "datasets": datasets,
            "pipeline": pipeline,
            "model": model.to_dict(),
            "app_version": __version__,
        }
    )


class DashboardStore:
    """Read and write dashboards beneath one project folder."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.directory = project.root / "dashboards"
        self.last_errors: list[str] = []

    # -- files -------------------------------------------------------------

    def _path(self, dashboard_id: str) -> Path:
        return safe_project_path(
            self.project.root, f"dashboards/{dashboard_id}.json"
        )

    def get(self, dashboard_id: str) -> Dashboard:
        path = self._path(str(dashboard_id))
        if not path.is_file():
            raise KeyError(f"Dashboard {dashboard_id!r} does not exist.")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DashboardError(
                f"Dashboard {dashboard_id!r} is not valid JSON: {exc}"
            ) from exc
        dashboard = Dashboard.from_dict(payload)
        if dashboard.id != dashboard_id:
            raise DashboardError(
                f"Dashboard file {path.name!r} declares id {dashboard.id!r}."
            )
        return dashboard

    def list(self) -> list[Dashboard]:
        self.last_errors = []
        if not self.directory.is_dir():
            return []
        dashboards: list[Dashboard] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                item = Dashboard.from_dict(payload)
                if item.id != path.stem:
                    raise DashboardError(
                        f"File {path.name!r} declares id {item.id!r}."
                    )
                dashboards.append(item)
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
            ) as exc:  # corrupted documents must not hide the rest
                self.last_errors.append(f"{path.name}: {exc}")
        return dashboards

    def remove(self, dashboard_id: str) -> None:
        path = self._path(str(dashboard_id))
        if not path.is_file():
            raise KeyError(f"Dashboard {dashboard_id!r} does not exist.")
        path.unlink()

    # -- save --------------------------------------------------------------

    def save(
        self,
        dashboard: Dashboard,
        *,
        model: SemanticModel | None = None,
        overwrite: bool = True,
    ) -> Dashboard:
        if not isinstance(dashboard, Dashboard):
            raise TypeError("save expects a Dashboard.")
        problems = validate_dashboard(dashboard, model)
        if problems:
            raise DashboardError(
                "Dashboard does not match the semantic model: "
                + "; ".join(problems)
            )
        existing = self._load_optional(dashboard.id)
        if existing is not None and not overwrite:
            raise FileExistsError(
                f"Dashboard {dashboard.id!r} already exists."
            )
        now = utc_now_iso()
        created = dashboard.created_at or (
            existing.created_at if existing is not None else now
        )
        fingerprint = None
        model_obj = model
        if model_obj is None:
            model_obj = self._load_model_optional()
        if model_obj is not None:
            try:
                fingerprint = dashboard_fingerprint(self.project, model_obj)
            except Exception:
                fingerprint = None
        stored = Dashboard(
            id=dashboard.id,
            title=dashboard.title,
            description=dashboard.description,
            tiles=dashboard.tiles,
            slicers=dashboard.slicers,
            bookmarks=dashboard.bookmarks,
            parameters=dashboard.parameters,
            schedule=dashboard.schedule or (
                existing.schedule if existing is not None else "manual"
            ),
            refreshed_at=dashboard.refreshed_at or (
                existing.refreshed_at if existing is not None else now
            ),
            input_fingerprint=fingerprint or (
                dashboard.input_fingerprint or (
                    existing.input_fingerprint if existing is not None else ""
                )
            ),
            report_path=dashboard.report_path
            if dashboard.report_path is not None
            else (existing.report_path if existing is not None else None),
            created_at=created,
            updated_at=now,
            schema_version=dashboard.schema_version,
        )
        with _LOCK:
            _atomic_write_text(
                self._path(stored.id),
                json.dumps(stored.to_dict(), indent=2, ensure_ascii=False) + "\n",
            )
        return stored

    def create(
        self,
        *,
        title: str,
        tiles: Sequence[DashboardTile] = (),
        slicers: Sequence[DashboardSlicer] = (),
        bookmarks: Sequence[DashboardBookmark] = (),
        parameters: Sequence[DashboardParameter] = (),
        description: str = "",
        dashboard_id: str | None = None,
        model: SemanticModel | None = None,
        overwrite: bool = False,
        schedule: str = "manual",
        report_path: str | bool | None = None,
    ) -> Dashboard:
        base = slugify(dashboard_id or title)
        identifier = base
        if not overwrite:
            counter = 2
            while self._load_optional(identifier) is not None:
                suffix = f"-{counter}"
                identifier = f"{base[: 64 - len(suffix)]}{suffix}"
                counter += 1
        dashboard = Dashboard(
            id=identifier,
            title=title,
            description=description,
            tiles=tuple(tiles),
            slicers=tuple(slicers),
            bookmarks=tuple(bookmarks),
            parameters=tuple(parameters),
            schedule=schedule,
            report_path=(
                f"reports/dashboard-{identifier}.html"
                if report_path is True
                else report_path
            ),
        )
        return self.save(dashboard, model=model, overwrite=overwrite)

    # -- refresh and freshness -----------------------------------------------

    def describe(
        self,
        dashboard: Dashboard,
        *,
        model: Any | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        model_obj = model if model is not None else self._load_model_optional()
        status = "fresh"
        if model_obj is None:
            status = "missing"
        else:
            try:
                current = dashboard_fingerprint(self.project, model_obj)
            except Exception:
                current = None
                status = "missing"
            else:
                if not dashboard.input_fingerprint or current != dashboard.input_fingerprint:
                    status = "stale"
        due = is_due(dashboard.schedule, status, dashboard.refreshed_at, moment)
        payload = dashboard.to_dict()
        payload.update(
            {
                "status": status,
                "due": due,
                "next_refresh": next_refresh(dashboard.schedule, dashboard.refreshed_at),
            }
        )
        return payload

    def summaries(
        self, *, model: Any | None = None, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        moment = now or datetime.now(UTC)
        return [
            self.describe(item, model=model, now=moment) for item in self.list()
        ]

    def refresh(
        self,
        dashboard_id: str,
        *,
        selections: Any = None,
        parameters: Any = None,
        theme: str = "light",
    ) -> Dashboard:
        """Re-render every tile and stamp the document fresh.

        When the dashboard declares ``report_path`` (under ``reports/``), the HTML
        artifact is rewritten as well, so scheduled exports stay current.
        """

        from ..semantic.engine import SemanticEngine
        from .render import DashboardRenderer

        dashboard = self.get(dashboard_id)
        model_obj = self._load_model_optional()
        if model_obj is None:
            raise DashboardError(
                f"Dashboard {dashboard_id!r} cannot refresh: no semantic model."
            )
        engine = SemanticEngine(self.project, model_obj)
        view = DashboardRenderer(engine).render(
            dashboard,
            selections=selections,
            parameters=parameters,
            theme=theme,
        )
        errors = [tile.error for tile in view.tiles if tile.error]
        if errors:
            raise DashboardError(
                f"Dashboard {dashboard_id!r} failed to render: {'; '.join(errors)}"
            )
        fingerprint = dashboard_fingerprint(self.project, model_obj)
        refreshed = replace(
            dashboard,
            refreshed_at=utc_now_iso(),
            input_fingerprint=fingerprint,
        )
        with _LOCK:
            _atomic_write_text(
                self._path(refreshed.id),
                json.dumps(refreshed.to_dict(), indent=2, ensure_ascii=False) + "\n",
            )
        if refreshed.report_path:
            self._write_report(refreshed, view)
        return refreshed

    def refresh_due(
        self,
        *,
        now: datetime | None = None,
        only_on_open: bool = False,
    ) -> tuple[list[Dashboard], list[dict[str, str]]]:
        """Refresh every dashboard whose schedule and freshness require it."""

        moment = now or datetime.now(UTC)
        model_obj = self._load_model_optional()
        refreshed: list[Dashboard] = []
        failures: list[dict[str, str]] = []
        for dashboard in self.list():
            if only_on_open and dashboard.schedule != "on_open":
                continue
            try:
                summary = self.describe(dashboard, model=model_obj, now=moment)
            except (ValueError, KeyError) as exc:
                failures.append({"id": dashboard.id, "error": str(exc)})
                continue
            if dashboard.schedule == "manual":
                continue
            if not summary["due"]:
                continue
            try:
                refreshed.append(self.refresh(dashboard.id))
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                failures.append({"id": dashboard.id, "error": str(exc)})
        return refreshed, failures

    def refresh_all(self) -> tuple[list[Dashboard], list[dict[str, str]]]:
        refreshed: list[Dashboard] = []
        failures: list[dict[str, str]] = []
        for dashboard in self.list():
            try:
                refreshed.append(self.refresh(dashboard.id))
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                failures.append({"id": dashboard.id, "error": str(exc)})
        return refreshed, failures

    # -- report artifacts ------------------------------------------------------

    def _write_report(self, dashboard: Dashboard, view: Any) -> None:
        from .report import write_dashboard_report

        path = safe_project_path(self.project.root, str(dashboard.report_path))
        write_dashboard_report(view, path, title=dashboard.title)

    # -- model access ----------------------------------------------------------

    def load_model(self):
        """Return the project's semantic model, or None when there isn't one."""

        return self._load_model_optional()

    def _load_model_optional(self):
        try:
            from ..semantic.store import SemanticModelStore

            return SemanticModelStore(self.project).load()
        except Exception:
            return None

    # -- helpers -----------------------------------------------------------

    def _load_optional(self, dashboard_id: str) -> Dashboard | None:
        try:
            return self.get(dashboard_id)
        except (KeyError, DashboardError):
            return None

    def to_dict(self) -> dict[str, Any]:
        dashboards = self.list()
        return {
            "dashboards": [item.to_dict() for item in dashboards],
            "errors": list(self.last_errors),
        }


__all__ = ["DashboardStore"]
