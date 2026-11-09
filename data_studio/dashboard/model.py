"""Dashboard definitions: tiles, slicers and a renderable layout.

A dashboard is declarative data. It names semantic dimensions and measures, not SQL, so
the same definition can be rendered by the API, exported to HTML and reopened after a
restart. Tile queries run through the semantic engine; the dashboard never stores
pre-baked numbers.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..kernel.spec import ChartType
from ..semantic.engine import QueryFilter, QueryOrder

DASHBOARD_SCHEMA_VERSION = "1"
TILE_KINDS = ("chart", "kpi", "table")
TABLE_STYLES = ("plain", "data_bars", "heatmap")
GRID_WIDTHS = (1, 2, 3, 4)
_DASHBOARD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_TILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_UNSAFE = re.compile(r"[^a-z0-9-]+")


class DashboardError(ValueError):
    """A dashboard definition is invalid."""


def slugify(value: str) -> str:
    text = str(value or "").strip().lower()
    text = _UNSAFE.sub("-", text.replace("_", "-").replace(" ", "-"))
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return (text or "dashboard")[:64]


def _humanize(value: str) -> str:
    return str(value).replace("_", " ").replace("-", " ").strip().title()


def _required_text(value: str, label: str) -> str:
    text = str(value).strip() if isinstance(value, str) else ""
    if not text:
        raise DashboardError(f"{label} must not be empty.")
    return text


def _chart_type(value: str) -> str:
    text = str(value).strip().lower()
    try:
        return ChartType(text).value
    except ValueError as exc:
        raise DashboardError(
            "Chart type must be one of: "
            + ", ".join(item.value for item in ChartType)
            + "."
        ) from exc


def _filters(values: Sequence[Any]) -> tuple[QueryFilter, ...]:
    return tuple(
        item if isinstance(item, QueryFilter) else QueryFilter.from_dict(item)
        for item in values
    )


def _orders(values: Sequence[Any]) -> tuple[QueryOrder, ...]:
    parsed: list[QueryOrder] = []
    for item in values:
        if isinstance(item, QueryOrder):
            parsed.append(item)
        elif isinstance(item, str):
            parts = item.replace(":", " ").split()
            parsed.append(QueryOrder(parts[0], parts[1] if len(parts) > 1 else "asc"))
        elif isinstance(item, Mapping):
            parsed.append(
                QueryOrder(
                    str(item.get("field", "")), str(item.get("direction", "asc"))
                )
            )
        else:
            raise DashboardError(
                "Tile order entries must be objects or 'field asc' strings."
            )
    return tuple(parsed)


def _bookmark_values(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        values: tuple[Any, ...] = (value,)
    else:
        values = tuple(value)
    for item in values:
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise DashboardError(
                "Bookmark selection values must be text, numbers, booleans or null."
            )
    return values


@dataclass(frozen=True, slots=True)
class DashboardTile:
    """One KPI, chart or table on a dashboard."""

    id: str
    title: str
    kind: str = "chart"
    dimensions: tuple[str, ...] = ()
    measures: tuple[str, ...] = ()
    chart: str = "column"
    filters: tuple[QueryFilter, ...] = ()
    order_by: tuple[QueryOrder, ...] = ()
    limit: int | None = None
    subtitle: str = ""
    width: int = 1
    table_style: str = "plain"

    def __post_init__(self) -> None:
        identifier = str(self.id).strip()
        if not _TILE_ID.match(identifier):
            raise DashboardError(
                "Tile id must be 1-64 lowercase letters, digits, '_' or '-' and start "
                "with a letter or digit."
            )
        object.__setattr__(self, "id", identifier)
        object.__setattr__(self, "title", _required_text(self.title, "Tile title"))
        kind = str(self.kind).strip().lower()
        if kind not in TILE_KINDS:
            raise DashboardError(
                f"Tile kind must be one of: {', '.join(TILE_KINDS)}."
            )
        object.__setattr__(self, "kind", kind)
        dimensions = tuple(str(item).strip() for item in self.dimensions)
        measures = tuple(str(item).strip() for item in self.measures)
        if any(not item for item in dimensions + measures):
            raise DashboardError("Tile dimensions and measures must not be empty names.")
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "measures", measures)
        object.__setattr__(self, "chart", _chart_type(self.chart))
        object.__setattr__(self, "filters", _filters(self.filters))
        object.__setattr__(self, "order_by", _orders(self.order_by))
        if self.limit is not None:
            limit = int(self.limit)
            if limit < 1:
                raise DashboardError("Tile limit must be at least 1.")
            object.__setattr__(self, "limit", limit)
        object.__setattr__(self, "subtitle", str(self.subtitle or ""))
        width = int(self.width)
        if width not in GRID_WIDTHS:
            raise DashboardError(
                f"Tile width must be one of: {', '.join(map(str, GRID_WIDTHS))}."
            )
        object.__setattr__(self, "width", width)
        table_style = str(self.table_style or "plain").strip().lower()
        if table_style not in TABLE_STYLES:
            raise DashboardError(
                "Table style must be one of: " + ", ".join(TABLE_STYLES) + "."
            )
        if table_style != "plain" and kind != "table":
            raise DashboardError("Only table tiles can use a table style.")
        object.__setattr__(self, "table_style", table_style)

        if kind == "kpi":
            if len(measures) != 1:
                raise DashboardError("A KPI tile needs exactly one measure.")
            if dimensions:
                raise DashboardError("A KPI tile cannot group by a dimension.")
        elif kind == "chart":
            if not measures:
                raise DashboardError("A chart tile needs at least one measure.")
            if not dimensions:
                raise DashboardError("A chart tile needs at least one dimension.")
        elif not dimensions and not measures:
            raise DashboardError("A table tile needs a dimension or a measure.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "dimensions": list(self.dimensions),
            "measures": list(self.measures),
            "chart": self.chart,
            "filters": [item.to_dict() for item in self.filters],
            "order_by": [item.to_dict() for item in self.order_by],
            "limit": self.limit,
            "subtitle": self.subtitle,
            "width": self.width,
            "table_style": self.table_style,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DashboardTile:
        return cls(
            id=str(payload.get("id", "")),
            title=str(payload.get("title", "")),
            kind=str(payload.get("kind", "chart")),
            dimensions=tuple(payload.get("dimensions", ())),
            measures=tuple(payload.get("measures", ())),
            chart=str(payload.get("chart", "column")),
            filters=tuple(payload.get("filters", ())),
            order_by=tuple(payload.get("order_by", ())),
            limit=payload.get("limit"),
            subtitle=str(payload.get("subtitle", "")),
            width=payload.get("width", 1),
            table_style=str(payload.get("table_style", "plain")),
        )


@dataclass(frozen=True, slots=True)
class DashboardSlicer:
    """A dashboard-level filter on one semantic dimension."""

    id: str
    dimension: str
    title: str | None = None
    multi: bool = True

    def __post_init__(self) -> None:
        identifier = str(self.id).strip()
        if not _TILE_ID.match(identifier):
            raise DashboardError(
                "Slicer id must be 1-64 lowercase letters, digits, '_' or '-' and "
                "start with a letter or digit."
            )
        object.__setattr__(self, "id", identifier)
        dimension = _required_text(self.dimension, "Slicer dimension")
        object.__setattr__(self, "dimension", dimension)
        title = str(self.title).strip() if isinstance(self.title, str) else ""
        object.__setattr__(self, "title", title or _humanize(dimension))
        object.__setattr__(self, "multi", bool(self.multi))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dimension": self.dimension,
            "title": self.title,
            "multi": self.multi,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DashboardSlicer:
        return cls(
            id=str(payload.get("id", "")),
            dimension=str(payload.get("dimension", "")),
            title=payload.get("title"),
            multi=bool(payload.get("multi", True)),
        )


@dataclass(frozen=True, slots=True)
class DashboardBookmark:
    """A named set of slicer selections that can be restored later."""

    id: str
    title: str
    selections: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        identifier = str(self.id).strip()
        if not _TILE_ID.match(identifier):
            raise DashboardError(
                "Bookmark id must be 1-64 lowercase letters, digits, '_' or '-' and "
                "start with a letter or digit."
            )
        object.__setattr__(self, "id", identifier)
        object.__setattr__(self, "title", _required_text(self.title, "Bookmark title"))
        raw = self.selections or {}
        if not isinstance(raw, Mapping):
            raise DashboardError("Bookmark selections must be an object.")
        cleaned: dict[str, tuple[Any, ...]] = {}
        for key, value in raw.items():
            name = str(key).strip()
            if not name:
                raise DashboardError("Bookmark selection ids must not be empty.")
            cleaned[name] = _bookmark_values(value)
        object.__setattr__(self, "selections", cleaned)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "selections": {
                key: list(values) for key, values in self.selections.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DashboardBookmark:
        return cls(
            id=str(payload.get("id", "")),
            title=str(payload.get("title", "")),
            selections=payload.get("selections") or {},
        )


@dataclass(frozen=True, slots=True)
class Dashboard:
    """A named collection of tiles and slicers."""

    id: str
    title: str
    description: str = ""
    tiles: tuple[DashboardTile, ...] = ()
    slicers: tuple[DashboardSlicer, ...] = ()
    bookmarks: tuple[DashboardBookmark, ...] = ()
    created_at: str = ""
    updated_at: str = ""
    schema_version: str = DASHBOARD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        identifier = str(self.id).strip()
        if not _DASHBOARD_ID.match(identifier):
            raise DashboardError(
                "Dashboard id must be 1-64 lowercase letters, digits or '-' and start "
                "with a letter or digit."
            )
        object.__setattr__(self, "id", identifier)
        object.__setattr__(self, "title", _required_text(self.title, "Dashboard title"))
        object.__setattr__(self, "description", str(self.description or ""))

        tiles = tuple(self.tiles)
        if any(not isinstance(item, DashboardTile) for item in tiles):
            raise DashboardError("Dashboard tiles must be DashboardTile instances.")
        tile_ids = [item.id for item in tiles]
        if len(set(tile_ids)) != len(tile_ids):
            raise DashboardError("Dashboard tile ids must be unique.")
        object.__setattr__(self, "tiles", tiles)

        slicers = tuple(self.slicers)
        if any(not isinstance(item, DashboardSlicer) for item in slicers):
            raise DashboardError("Dashboard slicers must be DashboardSlicer instances.")
        slicer_ids = [item.id for item in slicers]
        if len(set(slicer_ids)) != len(slicer_ids):
            raise DashboardError("Dashboard slicer ids must be unique.")
        dimensions = [item.dimension for item in slicers]
        if len(set(dimensions)) != len(dimensions):
            raise DashboardError("A dimension can appear in only one dashboard slicer.")
        object.__setattr__(self, "slicers", slicers)

        bookmarks = tuple(self.bookmarks)
        if any(not isinstance(item, DashboardBookmark) for item in bookmarks):
            raise DashboardError(
                "Dashboard bookmarks must be DashboardBookmark instances."
            )
        bookmark_ids = [item.id for item in bookmarks]
        if len(set(bookmark_ids)) != len(bookmark_ids):
            raise DashboardError("Dashboard bookmark ids must be unique.")
        slicer_ids = set(slicer_ids)
        for bookmark in bookmarks:
            unknown = sorted(set(bookmark.selections) - slicer_ids)
            if unknown:
                raise DashboardError(
                    f"Bookmark {bookmark.id!r} uses unknown slicer(s): "
                    + ", ".join(unknown)
                    + "."
                )
        object.__setattr__(self, "bookmarks", bookmarks)

        for name in ("created_at", "updated_at"):
            if not isinstance(getattr(self, name), str):
                raise DashboardError(f"Dashboard {name} must be a string.")
        if not str(self.schema_version).strip():
            raise DashboardError("Dashboard schema_version must not be empty.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "tiles": [tile.to_dict() for tile in self.tiles],
            "slicers": [slicer.to_dict() for slicer in self.slicers],
            "bookmarks": [bookmark.to_dict() for bookmark in self.bookmarks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Dashboard:
        if not isinstance(payload, Mapping):
            raise DashboardError("A dashboard document must be an object.")
        title = str(payload.get("title", ""))
        identifier = payload.get("id")
        dashboard_id = (
            str(identifier).strip() if isinstance(identifier, str) and identifier.strip()
            else slugify(title)
        )
        return cls(
            id=dashboard_id,
            title=title,
            description=str(payload.get("description", "")),
            tiles=tuple(
                item if isinstance(item, DashboardTile) else DashboardTile.from_dict(item)
                for item in payload.get("tiles", ())
            ),
            slicers=tuple(
                item
                if isinstance(item, DashboardSlicer)
                else DashboardSlicer.from_dict(item)
                for item in payload.get("slicers", ())
            ),
            bookmarks=tuple(
                item
                if isinstance(item, DashboardBookmark)
                else DashboardBookmark.from_dict(item)
                for item in payload.get("bookmarks", ())
            ),
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
            schema_version=str(
                payload.get("schema_version", DASHBOARD_SCHEMA_VERSION)
            ),
        )


def validate_dashboard(dashboard: Dashboard, model: Any | None) -> list[str]:
    """Return human-readable problems against a semantic model."""

    problems: list[str] = []
    if model is None:
        return problems

    dimensions = {item.name for item in model.dimensions}
    measures = {item.name for item in model.measures}
    for slicer in dashboard.slicers:
        if slicer.dimension not in dimensions:
            problems.append(
                f"Slicer {slicer.id!r} uses unknown dimension {slicer.dimension!r}."
            )
    for tile in dashboard.tiles:
        for name in tile.dimensions:
            if name not in dimensions:
                problems.append(
                    f"Tile {tile.id!r} uses unknown dimension {name!r}."
                )
        for name in tile.measures:
            if name not in measures:
                problems.append(f"Tile {tile.id!r} uses unknown measure {name!r}.")
        for item in tile.filters:
            if item.field not in dimensions and item.field not in measures:
                problems.append(
                    f"Tile {tile.id!r} filters on unknown field {item.field!r}."
                )
        for order in tile.order_by:
            if order.field not in tile.dimensions + tile.measures:
                problems.append(
                    f"Tile {tile.id!r} orders by {order.field!r}, which is not in the "
                    "tile output."
                )
    return problems


__all__ = [
    "DASHBOARD_SCHEMA_VERSION",
    "GRID_WIDTHS",
    "TABLE_STYLES",
    "TILE_KINDS",
    "Dashboard",
    "DashboardBookmark",
    "DashboardError",
    "DashboardSlicer",
    "DashboardTile",
    "slugify",
    "validate_dashboard",
]
