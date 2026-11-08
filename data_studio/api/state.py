"""Process-wide project state for the local API.

The API keeps at most one open project. Opening or creating another project closes the
previous engine, which releases DuckDB file handles and caches. The manager is locked so
concurrent requests cannot open two engines for the same folder.
"""

from __future__ import annotations

import threading
from pathlib import Path

from ..project.store import Project


class NoProjectError(RuntimeError):
    """No project has been opened yet."""


class ProjectManager:
    """Own the single open project for the process."""

    def __init__(self, root: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._root = Path(root).resolve() if root else None
        self._project: Project | None = None

    @property
    def root(self) -> Path | None:
        return self._root

    @property
    def is_open(self) -> bool:
        return self._project is not None

    def current(self) -> Project:
        with self._lock:
            if self._project is not None:
                return self._project
            if self._root is None:
                raise NoProjectError(
                    "No project is open. Create or open a project first."
                )
            self._project = Project.open(self._root)
            return self._project

    def open(self, path: str | Path, *, verify: bool = False) -> Project:
        resolved = Path(path).resolve()
        with self._lock:
            self._close_locked()
            self._project = Project.open(resolved, verify=verify)
            self._root = resolved
            return self._project

    def create(self, path: str | Path, name: str | None = None) -> Project:
        resolved = Path(path).resolve()
        with self._lock:
            self._close_locked()
            self._project = Project.create(resolved, name)
            self._root = resolved
            return self._project

    def close(self) -> None:
        with self._lock:
            self._close_locked()
            self._root = None

    def _close_locked(self) -> None:
        if self._project is not None:
            self._project.close()
            self._project = None


__all__ = ["NoProjectError", "ProjectManager"]
