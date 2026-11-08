"""Pipeline execution: linear compile, per-step fingerprints, incremental cache.

The runner never materialises intermediate results. Each enabled step compiles a new
lazy query over the previous one. A step's fingerprint covers its operation, parameters,
input fingerprint and any dataset it joins to, so changing one step invalidates that
step and everything after it, while earlier steps stay cached.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..engine.duckdb_engine import DuckDBTable
from ..kernel.schema import TableSchema
from ..kernel.table import TableData
from ..steps import DEFAULT_REGISTRY, StepContext, StepRegistry
from ..steps.registry import StepError
from ..version import __version__
from .model import PIPELINE_SCHEMA_VERSION, Pipeline, PipelineStep

_DATASET_STEPS = {
    "join": ("dataset",),
    "union": ("datasets",),
}


class PipelineRunError(RuntimeError):
    """A step failed; the message names the step so the UI can point at it."""

    def __init__(self, step_id: str, operation: str, message: str) -> None:
        self.step_id = step_id
        self.operation = operation
        super().__init__(f"Step {step_id!r} ({operation}) failed: {message}")

    def to_dict(self) -> dict[str, str]:
        return {
            "step_id": self.step_id,
            "operation": self.operation,
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class StepRun:
    """Metadata about one executed (or cache-hit) step."""

    step_id: str
    operation: str
    fingerprint: str
    cached: bool
    duration_ms: float
    schema: TableSchema
    rows: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "operation": self.operation,
            "fingerprint": self.fingerprint,
            "cached": self.cached,
            "duration_ms": round(self.duration_ms, 3),
            "schema": self.schema.to_dict(),
            "rows": self.rows,
        }


@dataclass(frozen=True, slots=True)
class RunResult:
    """The outcome of running a pipeline."""

    dataset: str
    pipeline_name: str
    table: DuckDBTable = field(compare=False, repr=False)
    steps: tuple[StepRun, ...] = ()
    fingerprint: str = ""
    rows: int | None = None
    duration_ms: float = 0.0
    cache_hits: int = 0

    @property
    def schema(self) -> TableSchema:
        return self.table.schema

    def preview(self, limit: int = 100) -> TableData:
        return self.table.preview(limit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "pipeline": self.pipeline_name,
            "fingerprint": self.fingerprint,
            "rows": self.rows,
            "duration_ms": round(self.duration_ms, 3),
            "cache_hits": self.cache_hits,
            "schema": self.schema.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
        }


class PipelineRunner:
    """Compile a pipeline against a base table, with an in-memory step cache."""

    def __init__(self, registry: StepRegistry | None = None) -> None:
        self.registry = registry if registry is not None else DEFAULT_REGISTRY
        self._cache: dict[str, DuckDBTable] = {}

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    def clear_cache(self) -> None:
        self._cache.clear()

    def run(
        self,
        pipeline: Pipeline,
        base_table: DuckDBTable,
        *,
        datasets: Mapping[str, DuckDBTable] | None = None,
        dataset_id: str | None = None,
        use_cache: bool = True,
        count_rows: bool = True,
    ) -> RunResult:
        if not isinstance(pipeline, Pipeline):
            raise TypeError("run expects a Pipeline.")
        if not isinstance(base_table, DuckDBTable):
            raise TypeError("run expects a DuckDBTable base table.")
        context = StepContext(base_table.engine, dict(datasets or {}))
        started = time.perf_counter()
        input_fingerprint = base_table.fingerprint()
        current = base_table
        step_runs: list[StepRun] = []
        cache_hits = 0

        for step in pipeline.enabled_steps:
            fingerprint = _step_fingerprint(step, input_fingerprint, context)
            step_started = time.perf_counter()
            cached = use_cache and fingerprint in self._cache
            if cached:
                current = self._cache[fingerprint]
                cache_hits += 1
            else:
                try:
                    current = self.registry.apply(
                        step.operation, context, current, dict(step.params)
                    )
                except (StepError, KeyError, ValueError) as exc:
                    raise PipelineRunError(step.id, step.operation, str(exc)) from exc
                if use_cache:
                    self._cache[fingerprint] = current
            step_runs.append(
                StepRun(
                    step_id=step.id,
                    operation=step.operation,
                    fingerprint=fingerprint,
                    cached=cached,
                    duration_ms=(time.perf_counter() - step_started) * 1000.0,
                    schema=current.schema,
                )
            )
            input_fingerprint = fingerprint

        rows = current.count() if count_rows else None
        return RunResult(
            dataset=dataset_id or pipeline.dataset or current.name or "dataset",
            pipeline_name=pipeline.name,
            table=current,
            steps=tuple(step_runs),
            fingerprint=input_fingerprint,
            rows=rows,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            cache_hits=cache_hits,
        )


def _canonical(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _dataset_dependencies(
    step: PipelineStep,
    context: StepContext,
) -> dict[str, str]:
    fields = _DATASET_STEPS.get(step.operation)
    if not fields:
        return {}
    names: list[str] = []
    for field_name in fields:
        value = step.params.get(field_name)
        if isinstance(value, str):
            names.append(value)
        elif isinstance(value, (list, tuple)):
            names.extend(str(item) for item in value)
    dependencies: dict[str, str] = {}
    for name in names:
        table = context.datasets.get(name)
        dependencies[name] = table.fingerprint() if table is not None else "missing"
    return dependencies


def _step_fingerprint(
    step: PipelineStep,
    input_fingerprint: str,
    context: StepContext,
) -> str:
    payload = {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "app_version": __version__,
        "operation": step.operation,
        "params": dict(step.params),
        "input": input_fingerprint,
        "dependencies": _dataset_dependencies(step, context),
    }
    encoded = _canonical(payload)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["PipelineRunError", "PipelineRunner", "RunResult", "StepRun"]
