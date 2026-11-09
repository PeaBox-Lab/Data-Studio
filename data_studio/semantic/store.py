"""Persistence for the semantic model.

One project owns at most one semantic model, stored as readable JSON at
``models/semantic.json``. The store validates the model against the project's recorded
dataset schemas before writing, so a model that refers to a missing table or column can
never reach a dashboard query.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..kernel.schema import TableSchema
from ..project.manifest import safe_project_path
from .model import SemanticError, SemanticModel

if TYPE_CHECKING:
    from ..project.store import Project

MODEL_PATH = "models/semantic.json"
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


class SemanticModelStore:
    """Read and write the project's semantic model."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.path = safe_project_path(project.root, MODEL_PATH)

    # -- files -------------------------------------------------------------

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> SemanticModel | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SemanticError(
                f"Semantic model file is not valid JSON: {exc}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise SemanticError("A semantic model document must be an object.")
        return SemanticModel.from_dict(payload)

    def get(self) -> SemanticModel:
        model = self.load()
        if model is None:
            raise KeyError(
                "This project has no semantic model yet. Derive one from its datasets."
            )
        return model

    def remove(self) -> None:
        with _LOCK:
            if not self.path.is_file():
                raise KeyError("This project has no semantic model to remove.")
            self.path.unlink()

    # -- schema validation -------------------------------------------------

    def dataset_schemas(self) -> dict[str, TableSchema]:
        """Recorded schemas keyed by dataset id."""

        return {
            dataset_id: self.project.dataset_schema(dataset_id)
            for dataset_id in self.project.dataset_ids()
        }

    def schemas(self, model: SemanticModel) -> dict[str, TableSchema]:
        """Recorded schemas keyed by *semantic table name*."""

        resolved: dict[str, TableSchema] = {}
        for table in model.tables:
            try:
                resolved[table.name] = self.project.dataset_schema(table.dataset)
            except KeyError as exc:
                raise SemanticError(
                    f"Table {table.name!r} references unknown dataset "
                    f"{table.dataset!r}."
                ) from exc
        return resolved

    def validate(self, model: SemanticModel) -> list[str]:
        """Return schema problems for a model, without raising."""

        problems: list[str] = []
        schemas: dict[str, TableSchema] = {}
        for table in model.tables:
            try:
                schemas[table.name] = self.project.dataset_schema(table.dataset)
            except KeyError:
                problems.append(
                    f"Table {table.name!r} references unknown dataset "
                    f"{table.dataset!r}."
                )
        if problems:
            return problems
        return model.validate(schemas)

    # -- save and derive ---------------------------------------------------

    def save(self, model: SemanticModel) -> SemanticModel:
        if not isinstance(model, SemanticModel):
            raise TypeError("save expects a SemanticModel.")
        problems = self.validate(model)
        if problems:
            raise SemanticError(
                "Semantic model does not match the project schemas: "
                + "; ".join(problems)
            )
        with _LOCK:
            _atomic_write_text(
                self.path,
                json.dumps(model.to_dict(), indent=2, ensure_ascii=False) + "\n",
            )
        return model

    def derive(
        self,
        *,
        name: str = "Semantic model",
        base_table: str | None = None,
        dataset_ids: Sequence[str] | None = None,
        save: bool = True,
    ) -> SemanticModel:
        """Derive a starter model from the project's recorded schemas."""

        schemas = self.dataset_schemas()
        model = SemanticModel.derive(
            schemas,
            name=name,
            base_table=base_table,
            dataset_ids=dataset_ids,
        )
        return self.save(model) if save else model

    # -- serialisation -----------------------------------------------------

    def to_dict(self, model: SemanticModel | None = None) -> dict[str, Any]:
        """A UI-ready view of the model plus validation problems."""

        current = model if model is not None else self.load()
        payload: dict[str, Any] = {
            "exists": current is not None,
            "path": MODEL_PATH,
            "model": current.to_dict() if current is not None else None,
            "problems": self.validate(current) if current is not None else [],
            "datasets": [summary.to_dict() for summary in self.project.datasets()],
        }
        return payload


__all__ = ["MODEL_PATH", "SemanticModelStore"]
