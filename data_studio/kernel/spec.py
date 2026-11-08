"""Declarative chart and dashboard specifications.

A specification is the contract between the analytics kernel and any renderer. The
desktop UI, a future React client, a PDF report and the API all render the same spec,
so a visual is never locked inside a widget.

The shape intentionally mirrors a small subset of Vega-Lite: data + mark + encoding +
transforms. Phase 1 compiles these specs to matplotlib/PNG; Phase 3 adds an interactive
renderer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..version import CHART_SCHEMA_VERSION

_TILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None.")
    stripped = value.strip()
    return stripped or None


class ChartType(StrEnum):
    """Chart marks supported by the renderer contract."""

    LINE = "line"
    BAR = "bar"
    COLUMN = "column"
    AREA = "area"
    SCATTER = "scatter"
    HISTOGRAM = "histogram"
    BOX = "box"
    HEATMAP = "heatmap"
    PIE = "pie"
    DONUT = "donut"
    WATERFALL = "waterfall"


class FilterKind(StrEnum):
    CATEGORICAL = "categorical"
    NUMERIC = "numeric"
    DATE = "date"
    BOOLEAN = "boolean"


class TileKind(StrEnum):
    CHART = "chart"
    KPI = "kpi"
    TABLE = "table"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class Encoding:
    """Channel-to-field mapping for a chart."""

    x: str | None = None
    y: str | None = None
    color: str | None = None
    size: str | None = None
    shape: str | None = None
    facet: str | None = None
    text: str | None = None
    tooltip: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("x", "y", "color", "size", "shape", "facet", "text"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        tooltip = tuple(self.tooltip)
        if any(not isinstance(field_name, str) or not field_name.strip() for field_name in tooltip):
            raise ValueError("Encoding tooltip fields must be non-empty strings.")
        object.__setattr__(self, "tooltip", tuple(f.strip() for f in tooltip))

    def fields(self) -> tuple[str, ...]:
        """All data fields referenced by this encoding, without duplicates."""
        seen: list[str] = []
        for name in ("x", "y", "color", "size", "shape", "facet", "text"):
            value = getattr(self, name)
            if value and value not in seen:
                seen.append(value)
        for value in self.tooltip:
            if value not in seen:
                seen.append(value)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "color": self.color,
            "size": self.size,
            "shape": self.shape,
            "facet": self.facet,
            "text": self.text,
            "tooltip": list(self.tooltip),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Encoding:
        return cls(
            x=payload.get("x"),
            y=payload.get("y"),
            color=payload.get("color"),
            size=payload.get("size"),
            shape=payload.get("shape"),
            facet=payload.get("facet"),
            text=payload.get("text"),
            tooltip=tuple(payload.get("tooltip", [])),
        )


@dataclass(frozen=True, slots=True)
class ChartSpec:
    """A renderer-independent description of one chart."""

    chart_type: ChartType
    title: str
    encoding: Encoding = field(default_factory=Encoding)
    options: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)
    transforms: tuple[Mapping[str, Any], ...] = ()
    schema_version: str = CHART_SCHEMA_VERSION

    def __post_init__(self) -> None:
        chart_type = (
            self.chart_type
            if isinstance(self.chart_type, ChartType)
            else ChartType(str(self.chart_type))
        )
        object.__setattr__(self, "chart_type", chart_type)

        title = self.title.strip()
        if not title:
            raise ValueError("Chart title must not be empty.")
        object.__setattr__(self, "title", title)

        if not isinstance(self.encoding, Encoding):
            raise TypeError("Chart encoding must be an Encoding instance.")
        if not isinstance(self.options, Mapping):
            raise TypeError("Chart options must be a mapping.")
        object.__setattr__(self, "options", dict(self.options))

        transforms = tuple(dict(transform) for transform in self.transforms)
        object.__setattr__(self, "transforms", transforms)

        if not str(self.schema_version).strip():
            raise ValueError("Chart schema_version must not be empty.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart_type": self.chart_type.value,
            "title": self.title,
            "encoding": self.encoding.to_dict(),
            "options": dict(self.options),
            "transforms": [dict(transform) for transform in self.transforms],
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ChartSpec:
        return cls(
            chart_type=ChartType(str(payload["chart_type"])),
            title=str(payload["title"]),
            encoding=Encoding.from_dict(payload.get("encoding", {})),
            options=dict(payload.get("options", {})),
            transforms=tuple(payload.get("transforms", [])),
            schema_version=str(payload.get("schema_version", CHART_SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class DashboardFilter:
    """A dashboard-level slicer."""

    column: str
    kind: FilterKind = FilterKind.CATEGORICAL
    default: Any = None
    values: tuple[Any, ...] = ()
    multi: bool = False

    def __post_init__(self) -> None:
        column = self.column.strip()
        if not column:
            raise ValueError("Filter column must not be empty.")
        object.__setattr__(self, "column", column)
        kind = self.kind if isinstance(self.kind, FilterKind) else FilterKind(str(self.kind))
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "values", tuple(self.values))

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "kind": self.kind.value,
            "default": self.default,
            "values": list(self.values),
            "multi": self.multi,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DashboardFilter:
        return cls(
            column=str(payload["column"]),
            kind=FilterKind(str(payload.get("kind", FilterKind.CATEGORICAL.value))),
            default=payload.get("default"),
            values=tuple(payload.get("values", [])),
            multi=bool(payload.get("multi", False)),
        )


@dataclass(frozen=True, slots=True)
class DashboardTile:
    """One positioned element on a dashboard grid."""

    id: str
    kind: TileKind
    title: str
    chart: ChartSpec | None = None
    measure: str | None = None
    columns: tuple[str, ...] = ()
    text: str | None = None
    x: int = 0
    y: int = 0
    w: int = 1
    h: int = 1

    def __post_init__(self) -> None:
        if not _TILE_ID.match(self.id):
            raise ValueError(
                "Tile id must be 1-64 characters of letters, digits, '_' or '-', "
                "and start with a letter or digit."
            )
        kind = self.kind if isinstance(self.kind, TileKind) else TileKind(str(self.kind))
        object.__setattr__(self, "kind", kind)

        title = self.title.strip()
        if not title:
            raise ValueError("Tile title must not be empty.")
        object.__setattr__(self, "title", title)

        if self.chart is not None and not isinstance(self.chart, ChartSpec):
            raise TypeError("Tile chart must be a ChartSpec.")

        columns = tuple(self.columns)
        if any(not isinstance(column, str) or not column.strip() for column in columns):
            raise ValueError("Tile columns must be non-empty strings.")
        object.__setattr__(self, "columns", columns)

        for name in ("x", "y", "w", "h"):
            value = getattr(self, name)
            if not isinstance(value, int):
                raise TypeError(f"Tile {name} must be an integer.")
            if value < 0:
                raise ValueError(f"Tile {name} must be >= 0.")
        if self.w < 1 or self.h < 1:
            raise ValueError("Tile width and height must be at least 1.")

        if kind is TileKind.CHART and self.chart is None:
            raise ValueError("A chart tile requires a ChartSpec.")
        if kind is TileKind.KPI and not (self.measure and self.measure.strip()):
            raise ValueError("A KPI tile requires a measure.")
        if kind is TileKind.TABLE and not columns:
            raise ValueError("A table tile requires at least one column.")
        if kind is TileKind.TEXT and not (self.text and self.text.strip()):
            raise ValueError("A text tile requires text.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "title": self.title,
            "chart": self.chart.to_dict() if self.chart else None,
            "measure": self.measure,
            "columns": list(self.columns),
            "text": self.text,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DashboardTile:
        chart = payload.get("chart")
        return cls(
            id=str(payload["id"]),
            kind=TileKind(str(payload["kind"])),
            title=str(payload["title"]),
            chart=ChartSpec.from_dict(chart) if chart else None,
            measure=payload.get("measure"),
            columns=tuple(payload.get("columns", [])),
            text=payload.get("text"),
            x=int(payload.get("x", 0)),
            y=int(payload.get("y", 0)),
            w=int(payload.get("w", 1)),
            h=int(payload.get("h", 1)),
        )


@dataclass(frozen=True, slots=True)
class DashboardSpec:
    """A page of tiles over one dataset."""

    name: str
    dataset: str
    tiles: tuple[DashboardTile, ...] = ()
    filters: tuple[DashboardFilter, ...] = ()
    theme: str = "light"
    schema_version: str = CHART_SCHEMA_VERSION

    def __post_init__(self) -> None:
        name = self.name.strip()
        dataset = self.dataset.strip()
        if not name:
            raise ValueError("Dashboard name must not be empty.")
        if not dataset:
            raise ValueError("Dashboard dataset must not be empty.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "dataset", dataset)

        tiles = tuple(self.tiles)
        if any(not isinstance(tile, DashboardTile) for tile in tiles):
            raise TypeError("Dashboard tiles must be DashboardTile instances.")
        ids = [tile.id for tile in tiles]
        if len(set(ids)) != len(ids):
            raise ValueError("Dashboard tile ids must be unique.")
        object.__setattr__(self, "tiles", tiles)

        filters = tuple(self.filters)
        if any(not isinstance(item, DashboardFilter) for item in filters):
            raise TypeError("Dashboard filters must be DashboardFilter instances.")
        columns = [item.column for item in filters]
        if len(set(columns)) != len(columns):
            raise ValueError("Dashboard filters must target distinct columns.")
        object.__setattr__(self, "filters", filters)

        if not self.theme.strip():
            raise ValueError("Dashboard theme must not be empty.")
        if not self.schema_version.strip():
            raise ValueError("Dashboard schema_version must not be empty.")

    def tile(self, tile_id: str) -> DashboardTile:
        for tile in self.tiles:
            if tile.id == tile_id:
                return tile
        raise KeyError(f"Unknown dashboard tile {tile_id!r}.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dataset": self.dataset,
            "tiles": [tile.to_dict() for tile in self.tiles],
            "filters": [item.to_dict() for item in self.filters],
            "theme": self.theme,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DashboardSpec:
        return cls(
            name=str(payload["name"]),
            dataset=str(payload["dataset"]),
            tiles=tuple(
                DashboardTile.from_dict(tile) for tile in payload.get("tiles", [])
            ),
            filters=tuple(
                DashboardFilter.from_dict(item) for item in payload.get("filters", [])
            ),
            theme=str(payload.get("theme", "light")),
            schema_version=str(payload.get("schema_version", CHART_SCHEMA_VERSION)),
        )


def validate_chart_fields(chart: ChartSpec, available: Sequence[str]) -> list[str]:
    """Return field references that are not present in the dataset schema."""
    available_set = set(available)
    return [name for name in chart.encoding.fields() if name not in available_set]
