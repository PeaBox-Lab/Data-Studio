"""Conditional-formatting rules persisted per dataset in the project folder.

Rules are tiny, versioned JSON documents under ``formats/<dataset>.json`` — never
executable content. They drive data bars, heatmaps and threshold highlighting in the
workspace preview grid; the render is a pure function of the stored values.

Supported rules:

* ``data_bars`` — horizontal bar scaled to the column's range (numeric columns).
* ``heatmap`` — background alpha scaled to the column's range (numeric columns).
* ``highlight`` — colour cells matching ``op``/``value`` (numeric columns; optional
  ``value2`` for the ``between`` operator).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .kernel.schema import NUMERIC_TYPES, TableSchema

FORMAT_SCHEMA_VERSION = "1"
FORMAT_KINDS = ("data_bars", "heatmap", "highlight")
FORMAT_OPS = (">", ">=", "<", "<=", "=", "!=", "between")
FORMAT_COLORS = ("red", "amber", "green", "blue")
_DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class FormatError(ValueError):
    """A formatting rule or document was invalid."""


def _validate_dataset_id(dataset_id: str) -> str:
    text = str(dataset_id).strip()
    if not _DATASET_ID.match(text):
        raise FormatError(f"Invalid dataset id {dataset_id!r}.")
    return text


@dataclass(frozen=True, slots=True)
class FormatRule:
    """One conditional-formatting rule on a single column."""

    column: str
    kind: str
    op: str | None = None
    value: float | str | None = None
    value2: float | None = None
    color: str = "amber"

    def __post_init__(self) -> None:
        column = str(self.column).strip()
        if not column:
            raise FormatError("Format rule column must not be empty.")
        object.__setattr__(self, "column", column)
        kind = str(self.kind).strip().lower()
        if kind not in FORMAT_KINDS:
            raise FormatError(
                f"Format rule kind must be one of: {', '.join(FORMAT_KINDS)}."
            )
        object.__setattr__(self, "kind", kind)
        op = self.op
        if op is not None:
            op = str(op).strip().lower()
            aliases = {"==": "=", "eq": "=", "ne": "!=", "neq": "!=", "gt": ">",
                       "gte": ">=", "lt": "<", "lte": "<="}
            op = aliases.get(op, op)
            if op not in FORMAT_OPS:
                raise FormatError(
                    f"Format rule operator must be one of: {', '.join(FORMAT_OPS)}."
                )
        object.__setattr__(self, "op", op)
        color = str(self.color).strip().lower()
        if color not in FORMAT_COLORS:
            raise FormatError(
                f"Format rule color must be one of: {', '.join(FORMAT_COLORS)}."
            )
        object.__setattr__(self, "color", color)
        if kind == "highlight":
            if op is None:
                raise FormatError("Highlight rules need an operator.")
            value = self.value
            if value is None:
                raise FormatError("Highlight rules need a comparison value.")
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise FormatError(
                    f"Highlight value must be a number; got {self.value!r}."
                ) from exc
            object.__setattr__(self, "value", value)
            if op == "between":
                if self.value2 is None:
                    raise FormatError("The 'between' operator needs value2.")
                try:
                    object.__setattr__(self, "value2", float(self.value2))
                except (TypeError, ValueError) as exc:
                    raise FormatError("Highlight value2 must be a number.") from exc
                if float(self.value) > float(self.value2):
                    raise FormatError("Highlight 'between' needs value <= value2.")
            else:
                object.__setattr__(self, "value2", None)
        else:
            if any(
                item is not None for item in (self.op, self.value, self.value2)
            ):
                raise FormatError(
                    f"{kind!r} rules do not take an operator, value or value2."
                )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "column": self.column,
            "kind": self.kind,
            "color": self.color,
        }
        if self.op is not None:
            payload["op"] = self.op
        if self.value is not None:
            payload["value"] = self.value
        if self.value2 is not None:
            payload["value2"] = self.value2
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FormatRule:
        if not isinstance(payload, Mapping):
            raise FormatError("A format rule must be an object.")
        return cls(
            column=str(payload.get("column", "")),
            kind=str(payload.get("kind", "")),
            op=payload.get("op"),
            value=payload.get("value"),
            value2=payload.get("value2"),
            color=str(payload.get("color", "amber")),
        )


def validate_rules(
    rules: Sequence[FormatRule], schema: TableSchema
) -> list[FormatRule]:
    names = {column.name for column in schema.columns}
    numeric = {
        column.name for column in schema.columns if column.dtype in NUMERIC_TYPES
    }
    seen: set[tuple[str, str]] = set()
    validated: list[FormatRule] = []
    for rule in rules:
        if rule.column not in names:
            raise FormatError(f"Format rule column {rule.column!r} does not exist.")
        if rule.kind in {"data_bars", "heatmap"} and rule.column not in numeric:
            raise FormatError(
                f"{rule.kind} needs a numeric column; {rule.column!r} is not numeric."
            )
        key = (rule.column, rule.kind)
        if key in seen:
            raise FormatError(
                f"Duplicate {rule.kind} rule for column {rule.column!r}."
            )
        seen.add(key)
        validated.append(rule)
    return validated


def _path(project: Any, dataset_id: str):
    from .project.manifest import safe_project_path

    dataset_id = _validate_dataset_id(dataset_id)
    return safe_project_path(project.root, f"formats/{dataset_id}.json")


def load_formats(project: Any, dataset_id: str) -> list[FormatRule]:
    path = _path(project, dataset_id)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FormatError(
            f"Format document for {dataset_id!r} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise FormatError(f"Format document for {dataset_id!r} must be an object.")
    raw_rules = payload.get("rules", [])
    if not isinstance(raw_rules, Sequence):
        raise FormatError(f"Format document for {dataset_id!r} needs a 'rules' list.")
    return [FormatRule.from_dict(item) for item in raw_rules]


def save_formats(
    project: Any, dataset_id: str, rules: Sequence[FormatRule]
) -> list[FormatRule]:
    # dataset must exist
    project.dataset_entry(dataset_id)
    schema = project.dataset_schema(dataset_id)
    validated = validate_rules(list(rules), schema)
    path = _path(project, dataset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": FORMAT_SCHEMA_VERSION,
        "dataset": _validate_dataset_id(dataset_id),
        "rules": [rule.to_dict() for rule in validated],
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return validated


__all__ = [
    "FORMAT_COLORS",
    "FORMAT_KINDS",
    "FORMAT_OPS",
    "FORMAT_SCHEMA_VERSION",
    "FormatError",
    "FormatRule",
    "load_formats",
    "save_formats",
    "validate_rules",
]
