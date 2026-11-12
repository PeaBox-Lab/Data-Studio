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
from ..schedule import SCHEDULES
from ..semantic.engine import QueryFilter, QueryOrder

DASHBOARD_SCHEMA_VERSION = "1"
TILE_KINDS = ("chart", "kpi", "table")
TABLE_STYLES = ("plain", "data_bars", "heatmap")
GRID_WIDTHS = (1, 2, 3, 4)
PARAMETER_TYPES = ("number", "text", "choice", "boolean")
_PARAM_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
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
    limit: int | str | None = None
    subtitle: str = ""
    width: int = 1
    table_style: str = "plain"
    hierarchy: str | None = None

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
            limit = self.limit
            if isinstance(limit, str):
                text = limit.strip()
                if not (
                    text.startswith("@")
                    and _PARAM_NAME.match(text[1:])
                ):
                    raise DashboardError(
                        "Tile limit must be a positive integer or '@parameter'."
                    )
                limit = text
            else:
                limit = int(limit)
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

        hierarchy = self.hierarchy
        if hierarchy is not None:
            hierarchy = str(hierarchy).strip()
            if not hierarchy:
                hierarchy = None
            elif kind != "chart":
                raise DashboardError("Only chart tiles can use a hierarchy.")
            elif dimensions:
                raise DashboardError(
                    "A chart tile with a hierarchy draws its dimensions from the "
                    "hierarchy; leave dimensions empty."
                )
        object.__setattr__(self, "hierarchy", hierarchy)

        if kind == "kpi":
            if len(measures) != 1:
                raise DashboardError("A KPI tile needs exactly one measure.")
            if dimensions:
                raise DashboardError("A KPI tile cannot group by a dimension.")
        elif kind == "chart":
            if not measures:
                raise DashboardError("A chart tile needs at least one measure.")
            if not dimensions and hierarchy is None:
                raise DashboardError(
                    "A chart tile needs at least one dimension or a hierarchy."
                )
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
            "hierarchy": self.hierarchy,
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
            hierarchy=payload.get("hierarchy"),
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


def _parameter_refs(value: Any) -> list[str]:
    """Return the parameter names referenced by a filter value tree."""

    refs: list[str] = []
    if isinstance(value, str):
        if value.startswith("@"):
            refs.append(value[1:])
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            refs.extend(_parameter_refs(item))
    return refs


def validate_parameter_references(
    tiles: Sequence[DashboardTile], parameter_names: Sequence[str]
) -> None:
    """Reject tiles that reference parameters the dashboard does not declare."""

    known = set(parameter_names)
    for tile in tiles:
        candidates: list[str] = []
        if isinstance(tile.limit, str):
            candidates.append(tile.limit[1:])
        for item in tile.filters:
            candidates.extend(_parameter_refs(item.value))
        for name in candidates:
            if name not in known:
                raise DashboardError(
                    f"Tile {tile.id!r} references unknown parameter @{name}; "
                    "declare it in the dashboard parameters."
                )


@dataclass(frozen=True, slots=True)
class DashboardParameter:
    """A what-if parameter the viewer can change without editing the dashboard.

    A parameter is referenced from a tile filter value or the tile ``limit`` as
    ``"@name"``; the renderer substitutes the chosen value (or the default) before
    the query runs. Parameters are plain data — they never carry SQL or code.
    """

    name: str
    type: str = "number"
    title: str | None = None
    default: Any = None
    options: tuple[Any, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not _PARAM_NAME.match(name):
            raise DashboardError(
                "Parameter name must be 1-64 letters, digits or underscores and "
                f"start with a letter or underscore; got {self.name!r}."
            )
        object.__setattr__(self, "name", name)
        kind = str(self.type).strip().lower()
        if kind not in PARAMETER_TYPES:
            raise DashboardError(
                f"Parameter type must be one of: {', '.join(PARAMETER_TYPES)}."
            )
        object.__setattr__(self, "type", kind)
        title = str(self.title).strip() if isinstance(self.title, str) else ""
        object.__setattr__(self, "title", title or _humanize(name))

        options: tuple[Any, ...] = ()
        if kind == "choice":
            raw = self.options
            if isinstance(raw, str) or not isinstance(raw, Sequence):
                raw = (raw,) if raw else ()
            options = tuple(raw)
            if not options:
                raise DashboardError(
                    f"Parameter {name!r} of type 'choice' needs at least one option."
                )
            for option in options:
                if not isinstance(option, (str, int, float, bool)):
                    raise DashboardError(
                        f"Parameter {name!r} options must be text, numbers or booleans."
                    )
        object.__setattr__(self, "options", options)

        default = self.default
        if kind == "number":
            if default is None:
                default = 0.0
            if isinstance(default, bool) or not isinstance(default, (int, float)):
                raise DashboardError(f"Parameter {name!r} default must be a number.")
            default = float(default)
            minimum = self._bound("minimum", name)
            maximum = self._bound("maximum", name)
            if minimum is not None and maximum is not None and minimum > maximum:
                raise DashboardError(f"Parameter {name!r} minimum is above maximum.")
            step = self._bound("step", name)
            if step is not None and step <= 0:
                raise DashboardError(f"Parameter {name!r} step must be positive.")
            if minimum is not None and default < minimum:
                raise DashboardError(f"Parameter {name!r} default is below minimum.")
            if maximum is not None and default > maximum:
                raise DashboardError(f"Parameter {name!r} default is above maximum.")
            object.__setattr__(self, "minimum", minimum)
            object.__setattr__(self, "maximum", maximum)
            object.__setattr__(self, "step", step)
        elif kind == "text":
            if default is None:
                default = ""
            if not isinstance(default, str):
                raise DashboardError(f"Parameter {name!r} default must be text.")
            object.__setattr__(self, "minimum", None)
            object.__setattr__(self, "maximum", None)
            object.__setattr__(self, "step", None)
        elif kind == "boolean":
            if default is None:
                default = False
            if not isinstance(default, bool):
                raise DashboardError(f"Parameter {name!r} default must be true/false.")
            object.__setattr__(self, "minimum", None)
            object.__setattr__(self, "maximum", None)
            object.__setattr__(self, "step", None)
        else:  # choice
            if default is None:
                default = options[0]
            if default not in options:
                raise DashboardError(
                    f"Parameter {name!r} default must be one of its options."
                )
            object.__setattr__(self, "minimum", None)
            object.__setattr__(self, "maximum", None)
            object.__setattr__(self, "step", None)
        object.__setattr__(self, "default", default)

    def _bound(self, label: str, name: str) -> float | None:
        value = getattr(self, label)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DashboardError(f"Parameter {name!r} {label} must be a number.")
        return float(value)

    def coerce(self, value: Any) -> Any:
        """Validate and normalise one incoming value for this parameter."""

        if value is None:
            return self.default
        kind = self.type
        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise DashboardError(f"Parameter {self.name!r} expects a number.")
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise DashboardError(
                    f"Parameter {self.name!r} expects a number; got {value!r}."
                ) from exc
            if self.minimum is not None:
                number = max(self.minimum, number)
            if self.maximum is not None:
                number = min(self.maximum, number)
            return number
        if kind == "text":
            return str(value)
        if kind == "boolean":
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in {"true", "1", "yes", "on"}:
                return True
            if text in {"false", "0", "no", "off"}:
                return False
            raise DashboardError(f"Parameter {self.name!r} expects true or false.")
        if value not in self.options:
            raise DashboardError(
                f"Parameter {self.name!r} must be one of: "
                + ", ".join(str(option) for option in self.options)
                + "."
            )
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "title": self.title,
            "default": self.default,
            "options": list(self.options),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DashboardParameter:
        if not isinstance(payload, Mapping):
            raise DashboardError("A parameter document must be an object.")
        return cls(
            name=str(payload.get("name", "")),
            type=str(payload.get("type", "number")),
            title=payload.get("title"),
            default=payload.get("default"),
            options=tuple(payload.get("options", ())),
            minimum=payload.get("minimum"),
            maximum=payload.get("maximum"),
            step=payload.get("step"),
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
    parameters: tuple[DashboardParameter, ...] = ()
    schedule: str = "manual"
    refreshed_at: str = ""
    input_fingerprint: str = ""
    report_path: str | None = None
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

        parameters = tuple(self.parameters)
        if any(not isinstance(item, DashboardParameter) for item in parameters):
            raise DashboardError(
                "Dashboard parameters must be DashboardParameter instances."
            )
        parameter_names = [item.name for item in parameters]
        if len(set(parameter_names)) != len(parameter_names):
            raise DashboardError("Dashboard parameter names must be unique.")
        object.__setattr__(self, "parameters", parameters)

        validate_parameter_references(self.tiles, parameter_names)

        schedule = str(self.schedule).strip().lower()
        if schedule not in SCHEDULES:
            raise DashboardError(
                f"Schedule must be one of: {', '.join(SCHEDULES)}."
            )
        object.__setattr__(self, "schedule", schedule)
        for name in ("refreshed_at", "input_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise DashboardError(f"Dashboard {name} must be a string.")
        if self.report_path is not None:
            relative = str(self.report_path).replace("\\", "/")
            if not relative.startswith("reports/"):
                raise DashboardError(
                    "Dashboard report artifacts must live under 'reports/'."
                )
            object.__setattr__(self, "report_path", relative)

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
            "parameters": [item.to_dict() for item in self.parameters],
            "schedule": self.schedule,
            "refreshed_at": self.refreshed_at,
            "input_fingerprint": self.input_fingerprint,
            "report_path": self.report_path,
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
            parameters=tuple(
                item
                if isinstance(item, DashboardParameter)
                else DashboardParameter.from_dict(item)
                for item in payload.get("parameters", ())
            ),
            schedule=str(payload.get("schedule", "manual")),
            refreshed_at=str(payload.get("refreshed_at", "")),
            input_fingerprint=str(payload.get("input_fingerprint", "")),
            report_path=payload.get("report_path"),
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
    hierarchies = {item.name: item for item in model.hierarchies}
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
        output_fields = tile.dimensions + tile.measures
        if tile.hierarchy:
            hierarchy = hierarchies.get(tile.hierarchy)
            if hierarchy is None:
                problems.append(
                    f"Tile {tile.id!r} uses unknown hierarchy {tile.hierarchy!r}."
                )
            else:
                output_fields = hierarchy.levels + tile.measures
        for order in tile.order_by:
            if order.field not in output_fields:
                problems.append(
                    f"Tile {tile.id!r} orders by {order.field!r}, which is not in the "
                    "tile output."
                )
    return problems


__all__ = [
    "DASHBOARD_SCHEMA_VERSION",
    "GRID_WIDTHS",
    "PARAMETER_TYPES",
    "TABLE_STYLES",
    "TILE_KINDS",
    "Dashboard",
    "DashboardBookmark",
    "DashboardError",
    "DashboardParameter",
    "DashboardSlicer",
    "DashboardTile",
    "slugify",
    "validate_dashboard",
    "validate_parameter_references",
]
