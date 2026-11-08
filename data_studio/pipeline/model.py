"""Versioned, ordered pipeline definitions.

A pipeline is a list of steps over one dataset. It is data, not code: every step is an
operation name plus JSON parameters. The model is immutable; editing returns a new
pipeline, which makes undo and persistence trivial.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

PIPELINE_SCHEMA_VERSION = "1.0"
_STEP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PIPELINE_NAME = re.compile(r"^.{1,200}$", re.DOTALL)


def _canonical(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _validate_params(params: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(params, Mapping):
        raise TypeError("Step params must be a mapping.")
    clean: dict[str, Any] = {}
    for key, value in params.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Step parameter names must be non-empty strings.")
        clean[key] = value
    try:
        json.dumps(clean, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Step parameters must be JSON-serialisable.") from exc
    return clean


@dataclass(frozen=True, slots=True)
class PipelineStep:
    """One operation invocation inside a pipeline."""

    id: str
    operation: str
    params: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)
    title: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _STEP_ID.match(self.id):
            raise ValueError(
                "Step id must be 1-64 characters of letters, digits, '_' or '-', "
                "and start with a letter or digit."
            )
        operation = self.operation.strip()
        if not operation:
            raise ValueError("Step operation must not be empty.")
        object.__setattr__(self, "operation", operation)

        params = _validate_params(self.params)
        object.__setattr__(self, "params", MappingProxyType(params))

        if self.title is not None:
            title = self.title.strip()
            object.__setattr__(self, "title", title or None)
        if not isinstance(self.enabled, bool):
            raise TypeError("Step enabled must be a boolean.")

    @property
    def label(self) -> str:
        return self.title or self.operation.replace("_", " ").title()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "operation": self.operation,
            "params": dict(self.params),
            "title": self.title,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PipelineStep:
        if not isinstance(payload, Mapping):
            raise TypeError("Step payload must be an object.")
        return cls(
            id=str(payload["id"]),
            operation=str(payload["operation"]),
            params=dict(payload.get("params") or {}),
            title=payload.get("title"),
            enabled=bool(payload.get("enabled", True)),
        )


def make_step_id(operation: str, existing: Sequence[str] = ()) -> str:
    """Return a unique, human-readable step id."""

    base = re.sub(r"[^A-Za-z0-9_-]", "_", operation).strip("_") or "step"
    if base[0].isdigit():
        base = f"step_{base}"
    candidate = base[:64]
    if candidate not in existing:
        return candidate
    counter = 2
    while True:
        suffix = f"_{counter}"
        candidate = f"{base[: 64 - len(suffix)]}{suffix}"
        if candidate not in existing:
            return candidate
        counter += 1


def make_step(
    operation: str,
    params: Mapping[str, Any] | None = None,
    *,
    step_id: str | None = None,
    existing: Sequence[str] = (),
    title: str | None = None,
    enabled: bool = True,
) -> PipelineStep:
    return PipelineStep(
        id=step_id or make_step_id(operation, existing),
        operation=operation,
        params=dict(params or {}),
        title=title,
        enabled=enabled,
    )


@dataclass(frozen=True, slots=True)
class Pipeline:
    """An ordered, immutable list of steps over one dataset."""

    name: str = "Pipeline"
    dataset: str | None = None
    steps: tuple[PipelineStep, ...] = ()
    schema_version: str = PIPELINE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name or not _PIPELINE_NAME.match(name):
            raise ValueError("Pipeline name must be 1-200 characters.")
        object.__setattr__(self, "name", name)

        if self.dataset is not None:
            dataset = str(self.dataset).strip()
            if not dataset:
                raise ValueError("Pipeline dataset must not be empty.")
            object.__setattr__(self, "dataset", dataset)

        if self.schema_version != PIPELINE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported pipeline schema version {self.schema_version!r}; "
                f"expected {PIPELINE_SCHEMA_VERSION!r}."
            )

        steps = tuple(self.steps)
        if any(not isinstance(step, PipelineStep) for step in steps):
            raise TypeError("Pipeline steps must be PipelineStep instances.")
        ids = [step.id for step in steps]
        if len(set(ids)) != len(ids):
            raise ValueError("Pipeline step ids must be unique.")
        object.__setattr__(self, "steps", steps)

    @property
    def enabled_steps(self) -> tuple[PipelineStep, ...]:
        return tuple(step for step in self.steps if step.enabled)

    def __len__(self) -> int:
        return len(self.steps)

    def step(self, step_id: str) -> PipelineStep:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(f"Unknown step {step_id!r}.")

    def index_of(self, step_id: str) -> int:
        for position, step in enumerate(self.steps):
            if step.id == step_id:
                return position
        raise KeyError(f"Unknown step {step_id!r}.")

    def add_step(self, step: PipelineStep, *, position: int | None = None) -> Pipeline:
        if any(existing.id == step.id for existing in self.steps):
            raise ValueError(f"Step id already exists: {step.id!r}.")
        steps = list(self.steps)
        if position is None:
            steps.append(step)
        else:
            if not 0 <= position <= len(steps):
                raise ValueError("Step position is out of range.")
            steps.insert(position, step)
        return replace(self, steps=tuple(steps))

    def remove_step(self, step_id: str) -> Pipeline:
        self.step(step_id)
        return replace(self, steps=tuple(step for step in self.steps if step.id != step_id))

    def replace_step(self, step_id: str, step: PipelineStep) -> Pipeline:
        self.step(step_id)
        if step.id != step_id and any(item.id == step.id for item in self.steps):
            raise ValueError(f"Step id already exists: {step.id!r}.")
        steps = tuple(step if item.id == step_id else item for item in self.steps)
        return replace(self, steps=steps)

    def move_step(self, step_id: str, position: int) -> Pipeline:
        step = self.step(step_id)
        steps = [item for item in self.steps if item.id != step_id]
        if not 0 <= position <= len(steps):
            raise ValueError("Step position is out of range.")
        steps.insert(position, step)
        return replace(self, steps=tuple(steps))

    def set_enabled(self, step_id: str, enabled: bool) -> Pipeline:
        step = self.step(step_id)
        return self.replace_step(step_id, replace(step, enabled=enabled))

    def undo(self) -> Pipeline:
        if not self.steps:
            raise ValueError("The pipeline has no steps to undo.")
        return replace(self, steps=self.steps[:-1])

    def rename(self, name: str) -> Pipeline:
        return replace(self, name=name)

    def with_dataset(self, dataset: str) -> Pipeline:
        return replace(self, dataset=dataset)

    def clear(self) -> Pipeline:
        return replace(self, steps=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "dataset": self.dataset,
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Pipeline:
        if not isinstance(payload, Mapping):
            raise TypeError("Pipeline payload must be an object.")
        return cls(
            name=str(payload.get("name", "Pipeline")),
            dataset=payload.get("dataset"),
            steps=tuple(
                PipelineStep.from_dict(step) for step in payload.get("steps", [])
            ),
            schema_version=str(
                payload.get("schema_version", PIPELINE_SCHEMA_VERSION)
            ),
        )

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Pipeline:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid pipeline JSON: {exc}") from exc
        return cls.from_dict(payload)


__all__ = [
    "PIPELINE_SCHEMA_VERSION",
    "Pipeline",
    "PipelineStep",
    "make_step",
    "make_step_id",
]
