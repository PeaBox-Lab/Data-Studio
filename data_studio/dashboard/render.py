"""Render dashboard tiles through the semantic engine.

A tile is a semantic query plus a presentation choice. The renderer executes each tile,
turns chart tiles into inline SVG with the kernel renderer, and returns a typed view that
the API, the workspace UI and the HTML report all share.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..kernel.chart_render import DEFAULT_HEIGHT, DEFAULT_WIDTH, render_chart
from ..kernel.spec import ChartSpec, Encoding
from ..semantic.dsl import MeasureError
from ..semantic.engine import (
    QueryFilter,
    SemanticEngine,
    SemanticQuery,
    SemanticResult,
)
from ..semantic.model import SemanticError
from .model import Dashboard, DashboardError, DashboardTile


@dataclass(frozen=True, slots=True)
class DashboardDrill:
    """One tile's drill state: the active level and its ancestor values."""

    level: int = 0
    values: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        level = int(self.level)
        if level < 0:
            raise DashboardError("Drill level must not be negative.")
        object.__setattr__(self, "level", level)
        raw = self.values
        if raw is None:
            values: tuple[Any, ...] = ()
        elif isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            values = (raw,)
        else:
            values = tuple(raw)
        for value in values:
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise DashboardError(
                    "Drill values must be text, numbers, booleans or null."
                )
        object.__setattr__(self, "values", values)

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "values": list(self.values)}


@dataclass(frozen=True, slots=True)
class DashboardTileView:
    tile: DashboardTile
    result: SemanticResult | None = None
    svg: str | None = None
    error: str | None = None
    percentages: tuple[tuple[float | None, ...], ...] = ()
    level: int = 0
    path: tuple[tuple[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tile": self.tile.to_dict(),
            "result": self.result.to_dict() if self.result is not None else None,
            "svg": self.svg,
            "error": self.error,
            "percentages": [list(row) for row in self.percentages],
            "level": self.level,
            "path": [
                {"dimension": dimension, "value": value}
                for dimension, value in self.path
            ],
        }


@dataclass(frozen=True, slots=True)
class DashboardView:
    dashboard: Dashboard
    tiles: tuple[DashboardTileView, ...]
    filters: tuple[QueryFilter, ...]
    model: str
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dashboard": self.dashboard.to_dict(),
            "model": self.model,
            "filters": [item.to_dict() for item in self.filters],
            "tiles": [tile.to_dict() for tile in self.tiles],
            "generated_at": self.generated_at,
        }


def _selections(
    dashboard: Dashboard, selections: Mapping[str, Sequence[Any] | Any] | None
) -> tuple[QueryFilter, ...]:
    if not selections:
        return ()
    by_id = {slicer.id: slicer for slicer in dashboard.slicers}
    filters: list[QueryFilter] = []
    for key, value in selections.items():
        slicer = by_id.get(str(key))
        if slicer is None:
            raise DashboardError(f"Unknown slicer {key!r}.")
        if value is None:
            continue
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            values: tuple[Any, ...] = (value,)
        else:
            values = tuple(value)
        if not values:
            continue
        if not slicer.multi and len(values) > 1:
            values = values[:1]
        filters.append(QueryFilter(slicer.dimension, "in", values))
    return tuple(filters)


def _filter_from(value: Any) -> QueryFilter:
    if isinstance(value, QueryFilter):
        return value
    if isinstance(value, Mapping):
        return QueryFilter(
            str(value.get("field", "")),
            str(value.get("operator", "=")),
            value.get("value"),
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = list(value)
        if len(items) not in {2, 3}:
            raise DashboardError(
                "Filter lists need [field, operator] or [field, operator, value]."
            )
        return QueryFilter(
            str(items[0]), str(items[1]), items[2] if len(items) == 3 else None
        )
    raise DashboardError("Cross-filters must be objects or [field, operator, value] lists.")


def _cross_filters(
    values: Sequence[QueryFilter | Mapping[str, Any]] | None,
) -> tuple[QueryFilter, ...]:
    if not values:
        return ()
    return tuple(_filter_from(item) for item in values)


def _drill_state(value: Any) -> DashboardDrill:
    if isinstance(value, DashboardDrill):
        return value
    if isinstance(value, Mapping):
        return DashboardDrill(
            level=int(value.get("level", 0) or 0),
            values=tuple(value.get("values") or ()),
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = list(value)
        if len(items) != 2:
            raise DashboardError(
                "A drill entry needs [level, values] or an object with level and values."
            )
        return DashboardDrill(level=int(items[0]), values=tuple(items[1] or ()))
    raise DashboardError("Drill entries must be objects.")


def _drills(
    model: Any, dashboard: Dashboard, drill: Mapping[str, Any] | None
) -> dict[str, DashboardDrill]:
    if not drill:
        return {}
    tiles = {tile.id: tile for tile in dashboard.tiles}
    states: dict[str, DashboardDrill] = {}
    for key, value in drill.items():
        tile = tiles.get(str(key))
        if tile is None:
            raise DashboardError(f"Drill references unknown tile {key!r}.")
        if not tile.hierarchy:
            raise DashboardError(f"Tile {tile.id!r} has no hierarchy to drill.")
        hierarchy = model.hierarchy(tile.hierarchy)
        state = _drill_state(value)
        if state.level >= len(hierarchy.levels):
            raise DashboardError(
                f"Tile {tile.id!r} drills to level {state.level}, but hierarchy "
                f"{hierarchy.name!r} only has {len(hierarchy.levels)} level(s)."
            )
        if len(state.values) != state.level:
            raise DashboardError(
                f"Tile {tile.id!r} at level {state.level} needs "
                f"{state.level} ancestor value(s), got {len(state.values)}."
            )
        states[tile.id] = state
    return states


def _drill_path(
    model: Any,
    tile: DashboardTile,
    state: DashboardDrill | None,
) -> tuple[tuple[str, ...], tuple[QueryFilter, ...], int, tuple[tuple[str, Any], ...]]:
    if not tile.hierarchy:
        return tile.dimensions, (), 0, ()
    hierarchy = model.hierarchy(tile.hierarchy)
    active = state or DashboardDrill()
    if active.level >= len(hierarchy.levels):
        raise DashboardError(
            f"Tile {tile.id!r} drills to level {active.level}, but hierarchy "
            f"{hierarchy.name!r} only has {len(hierarchy.levels)} level(s)."
        )
    if len(active.values) != active.level:
        raise DashboardError(
            f"Tile {tile.id!r} at level {active.level} needs "
            f"{active.level} ancestor value(s), got {len(active.values)}."
        )
    path = tuple(
        (hierarchy.levels[index], active.values[index])
        for index in range(active.level)
    )
    filters = tuple(
        QueryFilter(dimension, "=", value) for dimension, value in path
    )
    return (hierarchy.levels[active.level],), filters, active.level, path


def _chart_spec(
    tile: DashboardTile, result: SemanticResult, dimensions: Sequence[str]
) -> ChartSpec | None:
    if not dimensions or not tile.measures:
        return None
    encoding = Encoding(
        x=dimensions[0],
        y=tile.measures[0],
        color=dimensions[1] if len(dimensions) > 1 else None,
    )
    return ChartSpec(
        chart_type=tile.chart,
        title=tile.title,
        encoding=encoding,
        data=result.rows,
        options={"subtitle": tile.subtitle} if tile.subtitle else {},
    )


def _percentages(
    result: SemanticResult,
) -> tuple[tuple[float | None, ...], ...]:
    """Normalise each numeric measure column to 0..1 for table formatting."""

    ranges: dict[int, tuple[float, float]] = {}
    for index, column in enumerate(result.columns):
        if column.kind != "measure":
            continue
        values = [
            float(row[column.name])
            for row in result.rows
            if isinstance(row.get(column.name), (int, float))
            and not isinstance(row.get(column.name), bool)
        ]
        if values:
            low, high = min(values), max(values)
            ranges[index] = (low, high)
    rows: list[tuple[float | None, ...]] = []
    for row in result.rows:
        cells: list[float | None] = []
        for index, column in enumerate(result.columns):
            value = row.get(column.name)
            span = ranges.get(index)
            if (
                span is None
                or not isinstance(value, (int, float))
                or isinstance(value, bool)
            ):
                cells.append(None)
                continue
            low, high = span
            if high <= low:
                cells.append(1.0)
            else:
                cells.append(max(0.0, min(1.0, (float(value) - low) / (high - low))))
        rows.append(tuple(cells))
    return tuple(rows)


class DashboardRenderer:
    """Execute and render one dashboard against a semantic engine."""

    def __init__(self, engine: SemanticEngine) -> None:
        if not isinstance(engine, SemanticEngine):
            raise TypeError("DashboardRenderer expects a SemanticEngine.")
        self.engine = engine

    def render(
        self,
        dashboard: Dashboard,
        *,
        selections: Mapping[str, Sequence[Any] | Any] | None = None,
        filters: Sequence[QueryFilter | Mapping[str, Any]] | None = None,
        drill: Mapping[str, Any] | None = None,
        theme: str = "light",
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
    ) -> DashboardView:
        if not isinstance(dashboard, Dashboard):
            raise TypeError("render expects a Dashboard.")
        slicer_filters = _selections(dashboard, selections)
        cross_filters = _cross_filters(filters)
        applied = slicer_filters + cross_filters
        states = _drills(self.engine.model, dashboard, drill)
        views: list[DashboardTileView] = []
        for tile in dashboard.tiles:
            views.append(
                self._render_tile(
                    tile,
                    applied,
                    states.get(tile.id),
                    theme=theme,
                    width=width,
                    height=height,
                )
            )
        return DashboardView(
            dashboard=dashboard,
            tiles=tuple(views),
            filters=applied,
            model=self.engine.model.name,
            generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )

    def _render_tile(
        self,
        tile: DashboardTile,
        slicer_filters: Sequence[QueryFilter],
        drill: DashboardDrill | None,
        *,
        theme: str,
        width: int,
        height: int,
    ) -> DashboardTileView:
        try:
            dimensions, drill_filters, level, path = _drill_path(
                self.engine.model, tile, drill
            )
            query = SemanticQuery(
                dimensions=dimensions,
                measures=tile.measures,
                filters=tuple(tile.filters) + tuple(slicer_filters) + drill_filters,
                order_by=tile.order_by,
                limit=tile.limit,
            )
            result = self.engine.query(query)
            svg = None
            if tile.kind == "chart":
                spec = _chart_spec(tile, result, dimensions)
                svg = (
                    render_chart(spec, width=width, height=height, theme=theme)
                    if spec is not None
                    else None
                )
            percentages = (
                _percentages(result)
                if tile.kind == "table" and tile.table_style != "plain"
                else ()
            )
            return DashboardTileView(
                tile=tile,
                result=result,
                svg=svg,
                percentages=percentages,
                level=level,
                path=path,
            )
        except (SemanticError, MeasureError, KeyError, ValueError, TypeError) as exc:
            return DashboardTileView(
                tile=tile, error=str(exc), level=drill.level if drill else 0
            )


__all__ = [
    "DashboardDrill",
    "DashboardRenderer",
    "DashboardTileView",
    "DashboardView",
]
