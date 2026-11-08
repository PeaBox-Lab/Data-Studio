"""Declarative analysis registry.

An analysis is data too: a name, a typed parameter list and a function that turns an
`AnalysisContext` into a typed `AnalysisResult`. The registry validates parameters before
anything touches the data and serialises itself for the API and the workspace UI, which
generates its forms from the same catalog.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..engine.duckdb_engine import DuckDBTable
from ..kernel.provenance import Provenance
from ..kernel.results import AnalysisResult
from ..steps.registry import StepParam


class AnalysisError(ValueError):
    """An analysis definition, parameter or execution was invalid."""


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """The data and lineage an analysis runs against."""

    dataset: str
    table: DuckDBTable = field(compare=False, repr=False)
    step_ids: tuple[str, ...] = ()
    engine: str = "duckdb"

    def __post_init__(self) -> None:
        dataset = self.dataset.strip() if isinstance(self.dataset, str) else ""
        if not dataset:
            raise ValueError("AnalysisContext dataset must not be empty.")
        object.__setattr__(self, "dataset", dataset)

        if not isinstance(self.table, DuckDBTable):
            raise TypeError("AnalysisContext table must be a DuckDBTable.")

        steps = tuple(self.step_ids)
        if any(not isinstance(step, str) or not step.strip() for step in steps):
            raise ValueError("AnalysisContext step_ids must be non-empty strings.")
        object.__setattr__(self, "step_ids", steps)

        engine = self.engine.strip() if isinstance(self.engine, str) else ""
        if not engine:
            raise ValueError("AnalysisContext engine must not be empty.")
        object.__setattr__(self, "engine", engine)

    def provenance(self, parameters: Mapping[str, Any]) -> Provenance:
        return Provenance.create(
            dataset=self.dataset,
            dataset_fingerprint=self.table.fingerprint(),
            step_ids=self.step_ids,
            engine=self.engine,
            parameters=parameters,
        )


@dataclass(frozen=True, slots=True)
class AnalysisDefinition:
    """One registered analysis."""

    name: str
    title: str
    category: str
    description: str
    run: Callable[[AnalysisContext, Mapping[str, Any]], AnalysisResult] = field(
        repr=False,
        compare=False,
    )
    params: tuple[StepParam, ...] = ()

    def __post_init__(self) -> None:
        for value, label in (
            (self.name, "name"),
            (self.title, "title"),
            (self.category, "category"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"AnalysisDefinition {label} must not be empty.")
        params = tuple(self.params)
        if any(not isinstance(param, StepParam) for param in params):
            raise TypeError("AnalysisDefinition params must be StepParam instances.")
        names = [param.name for param in params]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate parameter names in analysis {self.name!r}.")
        object.__setattr__(self, "params", params)
        if not callable(self.run):
            raise TypeError(f"Analysis {self.name!r} run must be callable.")

    def validate(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            raise AnalysisError(f"Parameters for {self.name!r} must be an object.")
        known = {param.name for param in self.params}
        unknown = sorted(set(params) - known)
        if unknown:
            raise AnalysisError(
                f"Unknown parameter(s) for {self.name!r}: {', '.join(unknown)}."
            )
        resolved: dict[str, Any] = {}
        for param in self.params:
            if param.name in params:
                resolved[param.name] = param.coerce(params[param.name])
            elif param.required:
                raise AnalysisError(
                    f"Missing required parameter {param.name!r} for {self.name!r}."
                )
            else:
                resolved[param.name] = param.default
        return resolved

    def execute(
        self,
        context: AnalysisContext,
        params: Mapping[str, Any] | None = None,
    ) -> AnalysisResult:
        if not isinstance(context, AnalysisContext):
            raise TypeError("execute expects an AnalysisContext.")
        resolved = self.validate(params)
        try:
            result = self.run(context, resolved)
        except AnalysisError:
            raise
        except Exception as exc:
            raise AnalysisError(f"Analysis {self.name!r} failed: {exc}") from exc
        if not isinstance(result, AnalysisResult):
            raise AnalysisError(f"Analysis {self.name!r} did not return an AnalysisResult.")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "params": [param.to_dict() for param in self.params],
        }


class AnalysisRegistry:
    """An ordered collection of analysis definitions."""

    def __init__(self, definitions: Sequence[AnalysisDefinition] = ()) -> None:
        self._definitions: dict[str, AnalysisDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: AnalysisDefinition) -> AnalysisDefinition:
        if not isinstance(definition, AnalysisDefinition):
            raise TypeError("register expects an AnalysisDefinition.")
        if definition.name in self._definitions:
            raise ValueError(f"Analysis {definition.name!r} is already registered.")
        self._definitions[definition.name] = definition
        return definition

    def get(self, name: str) -> AnalysisDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            available = ", ".join(self.names()) or "(none)"
            raise AnalysisError(
                f"Unknown analysis {name!r}. Available: {available}."
            ) from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def __len__(self) -> int:
        return len(self._definitions)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._definitions

    def run(
        self,
        analysis: str,
        context: AnalysisContext,
        params: Mapping[str, Any] | None = None,
    ) -> AnalysisResult:
        return self.get(analysis).execute(context, params)

    def catalog(self) -> list[dict[str, Any]]:
        definitions = sorted(
            self._definitions.values(), key=lambda item: (item.category, item.name)
        )
        return [definition.to_dict() for definition in definitions]


__all__ = [
    "AnalysisContext",
    "AnalysisDefinition",
    "AnalysisError",
    "AnalysisRegistry",
]
