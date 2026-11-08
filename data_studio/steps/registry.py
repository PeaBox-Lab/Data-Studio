"""Declarative step registry.

A step is data: a name, a typed parameter list and a compile function that turns an
input table into a new lazy table. The registry validates parameters before any SQL is
built, and serialises itself for the API/UI catalog.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..engine.duckdb_engine import DuckDBEngine, DuckDBTable


class StepError(ValueError):
    """A step definition or parameter was invalid, or compilation failed."""


class UnknownStepError(KeyError):
    """The requested operation is not registered."""


@dataclass(frozen=True, slots=True)
class StepContext:
    """Inputs available to a step beyond the table being transformed."""

    engine: DuckDBEngine
    datasets: Mapping[str, DuckDBTable] = field(default_factory=dict)

    def dataset(self, name: str) -> DuckDBTable:
        try:
            return self.datasets[name]
        except KeyError as exc:
            available = ", ".join(sorted(self.datasets)) or "(none)"
            raise StepError(f"Unknown dataset {name!r}. Available datasets: {available}.") from exc


@dataclass(frozen=True, slots=True)
class StepParam:
    """One declared step parameter."""

    name: str
    kind: str = "any"
    required: bool = True
    default: Any = None
    choices: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("StepParam name must not be empty.")
        allowed = {
            "any",
            "boolean",
            "column",
            "columns",
            "dataset",
            "enum",
            "expression",
            "integer",
            "mapping",
            "number",
            "string",
            "aggregations",
        }
        if self.kind not in allowed:
            raise ValueError(f"Unknown StepParam kind: {self.kind!r}.")
        choices = tuple(str(choice) for choice in self.choices)
        object.__setattr__(self, "choices", choices)
        if self.kind == "enum":
            if not choices:
                raise ValueError(f"Enum parameter {self.name!r} needs choices.")
            if self.default is not None and str(self.default) not in choices:
                raise ValueError(f"Default {self.default!r} is not a choice for {self.name!r}.")

    def coerce(self, value: Any) -> Any:
        kind = self.kind
        if kind in {"column", "string", "text", "dataset", "expression"}:
            if not isinstance(value, str) or not value.strip():
                raise StepError(f"Parameter {self.name!r} must be a non-empty string.")
            return value.strip()
        if kind == "columns":
            if isinstance(value, str):
                value = [part.strip() for part in value.split(",")]
            if not isinstance(value, Sequence) or isinstance(value, (bytes, str)):
                raise StepError(f"Parameter {self.name!r} must be a list of columns.")
            columns = tuple(str(item).strip() for item in value if str(item).strip())
            return columns
        if kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise StepError(f"Parameter {self.name!r} must be an integer.")
            return value
        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise StepError(f"Parameter {self.name!r} must be a number.")
            return float(value)
        if kind == "boolean":
            if not isinstance(value, bool):
                raise StepError(f"Parameter {self.name!r} must be true or false.")
            return value
        if kind == "enum":
            text = str(value)
            if text not in self.choices:
                raise StepError(
                    f"Parameter {self.name!r} must be one of: {', '.join(self.choices)}."
                )
            return text
        if kind == "mapping":
            if not isinstance(value, Mapping):
                raise StepError(f"Parameter {self.name!r} must be an object.")
            return dict(value)
        if kind == "aggregations":
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                raise StepError(f"Parameter {self.name!r} must be a list of aggregation objects.")
            result = []
            for item in value:
                if not isinstance(item, Mapping):
                    raise StepError(f"Each aggregation in {self.name!r} must be an object.")
                result.append(dict(item))
            return tuple(result)
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "required": self.required,
            "default": self.default,
            "choices": list(self.choices),
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class StepDefinition:
    """A registered operation."""

    name: str
    title: str
    category: str
    description: str
    compile: Callable[[StepContext, DuckDBTable, Mapping[str, Any]], DuckDBTable] = field(
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
                raise ValueError(f"StepDefinition {label} must not be empty.")
        params = tuple(self.params)
        if any(not isinstance(param, StepParam) for param in params):
            raise TypeError("StepDefinition params must be StepParam instances.")
        names = [param.name for param in params]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate parameter names in step {self.name!r}.")
        object.__setattr__(self, "params", params)
        if not callable(self.compile):
            raise TypeError(f"Step {self.name!r} compile must be callable.")

    def validate(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            raise StepError(f"Parameters for {self.name!r} must be an object.")
        known = {param.name for param in self.params}
        unknown = sorted(set(params) - known)
        if unknown:
            raise StepError(f"Unknown parameter(s) for {self.name!r}: {', '.join(unknown)}.")
        resolved: dict[str, Any] = {}
        for param in self.params:
            if param.name in params:
                resolved[param.name] = param.coerce(params[param.name])
            elif param.required:
                raise StepError(f"Missing required parameter {param.name!r} for {self.name!r}.")
            else:
                resolved[param.name] = param.default
        return resolved

    def apply(
        self,
        context: StepContext,
        table: DuckDBTable,
        params: Mapping[str, Any] | None,
    ) -> DuckDBTable:
        resolved = self.validate(params)
        try:
            result = self.compile(context, table, resolved)
        except StepError:
            raise
        except Exception as exc:
            raise StepError(f"Step {self.name!r} failed: {exc}") from exc
        if not isinstance(result, DuckDBTable):
            raise StepError(f"Step {self.name!r} did not return a DuckDBTable.")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "params": [param.to_dict() for param in self.params],
        }


class StepRegistry:
    """An ordered collection of step definitions."""

    def __init__(self, definitions: Sequence[StepDefinition] = ()) -> None:
        self._definitions: dict[str, StepDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: StepDefinition) -> StepDefinition:
        if not isinstance(definition, StepDefinition):
            raise TypeError("register expects a StepDefinition.")
        if definition.name in self._definitions:
            raise ValueError(f"Step {definition.name!r} is already registered.")
        self._definitions[definition.name] = definition
        return definition

    def get(self, name: str) -> StepDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            available = ", ".join(self.names()) or "(none)"
            raise UnknownStepError(f"Unknown operation {name!r}. Available: {available}.") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def __len__(self) -> int:
        return len(self._definitions)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._definitions

    def apply(
        self,
        operation: str,
        context: StepContext,
        table: DuckDBTable,
        params: Mapping[str, Any] | None = None,
    ) -> DuckDBTable:
        return self.get(operation).apply(context, table, params)

    def catalog(self) -> list[dict[str, Any]]:
        definitions = sorted(
            self._definitions.values(), key=lambda item: (item.category, item.name)
        )
        return [definition.to_dict() for definition in definitions]


__all__ = [
    "StepContext",
    "StepDefinition",
    "StepError",
    "StepParam",
    "StepRegistry",
    "UnknownStepError",
]
