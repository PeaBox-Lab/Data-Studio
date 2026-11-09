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
class DashboardTileView:
    tile: DashboardTile
    result: SemanticResult | None = None
    svg: str | None = None
    error: str | None = None
    percentages: tuple[tuple[float | None, ...], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tile": self.tile.to_dict(),
            "result": self.result.to_dict() if self.result is not None else None,
            "svg": self.svg,
            "error": self.error,
            "percentages": [list(row) for row in self.percentages],
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


def _chart_spec(tile: DashboardTile, result: SemanticResult) -> ChartSpec | None:
    if not tile.dimensions or not tile.measures:
        return None
    encoding = Encoding(
        x=tile.dimensions[0],
        y=tile.measures[0],
        color=tile.dimensions[1] if len(tile.dimensions) > 1 else None,
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
        theme: str = "light",
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
    ) -> DashboardView:
        if not isinstance(dashboard, Dashboard):
            raise TypeError("render expects a Dashboard.")
        slicer_filters = _selections(dashboard, selections)
        views: list[DashboardTileView] = []
        for tile in dashboard.tiles:
            views.append(
                self._render_tile(
                    tile,
                    slicer_filters,
                    theme=theme,
                    width=width,
                    height=height,
                )
            )
        return DashboardView(
            dashboard=dashboard,
            tiles=tuple(views),
            filters=slicer_filters,
            model=self.engine.model.name,
            generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )

    def _render_tile(
        self,
        tile: DashboardTile,
        slicer_filters: Sequence[QueryFilter],
        *,
        theme: str,
        width: int,
        height: int,
    ) -> DashboardTileView:
        query = SemanticQuery(
            dimensions=tile.dimensions,
            measures=tile.measures,
            filters=tuple(tile.filters) + tuple(slicer_filters),
            order_by=tile.order_by,
            limit=tile.limit,
        )
        try:
            result = self.engine.query(query)
            svg = None
            if tile.kind == "chart":
                spec = _chart_spec(tile, result)
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
            )
        except (SemanticError, MeasureError, KeyError, ValueError, TypeError) as exc:
            return DashboardTileView(tile=tile, error=str(exc))


__all__ = ["DashboardRenderer", "DashboardTileView", "DashboardView"]
