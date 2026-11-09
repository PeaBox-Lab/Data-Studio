"""Persistence for dashboards.

Each dashboard is a versioned JSON document under ``dashboards/``. Dashboards hold no
data and no SQL: they reference semantic dimensions and measures, so a dashboard stays
small and can be reviewed like any other project artifact.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..kernel.provenance import utc_now_iso
from ..project.manifest import safe_project_path
from .model import (
    Dashboard,
    DashboardError,
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
        stored = Dashboard(
            id=dashboard.id,
            title=dashboard.title,
            description=dashboard.description,
            tiles=dashboard.tiles,
            slicers=dashboard.slicers,
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
        description: str = "",
        dashboard_id: str | None = None,
        model: SemanticModel | None = None,
        overwrite: bool = False,
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
        )
        return self.save(dashboard, model=model, overwrite=overwrite)

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
