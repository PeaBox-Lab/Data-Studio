"""Tests for calendar generation and time-intelligence measures."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from data_studio.io.sniff import CsvOptions
from data_studio.project.store import Project
from data_studio.semantic import (
    GRAIN_PERIODS,
    GRAINS,
    QueryOrder,
    Relationship,
    SemanticDimension,
    SemanticEngine,
    SemanticMeasure,
    SemanticModel,
    SemanticQuery,
    SemanticTable,
    calendar_sql,
    generate_calendar,
)
from data_studio.semantic.calendar import CalendarError
from data_studio.semantic.dsl import MeasureError, compile_measure
from data_studio.semantic.model import SemanticError


@pytest.fixture
def project(tmp_path: Path) -> Project:
    instance = Project.create(tmp_path / "Demo", "Calendar demo")
    try:
        yield instance
    finally:
        instance.close()


SALES_ROWS = [
    (f"{year}-{month:02d}-15", 10 * index)
    for index, (year, month) in enumerate(
        [(2023, month) for month in range(1, 13)]
        + [(2024, month) for month in range(1, 3)],
        start=1,
    )
]


def _write_sales(tmp_path: Path) -> Path:
    source = tmp_path / "sales.csv"
    source.write_text(
        "date,amount\n"
        + "\n".join(f"{day},{amount}" for day, amount in SALES_ROWS)
        + "\n",
        encoding="utf-8",
    )
    return source


def _model(engine_project: Project) -> SemanticModel:
    return SemanticModel(
        name="Calendar sales",
        tables=(
            SemanticTable("sales", "sales"),
            SemanticTable("calendar", "calendar"),
        ),
        relationships=(
            Relationship("sales", "date", "calendar", "date"),
        ),
        dimensions=(
            SemanticDimension(
                "month",
                "calendar",
                "month_start",
                title="Month",
                grain="month",
            ),
            SemanticDimension(
                "day",
                "calendar",
                "date",
                title="Day",
                grain="day",
            ),
        ),
        measures=(
            SemanticMeasure("total_amount", "sum(amount)", "Total amount"),
            SemanticMeasure(
                "running_amount",
                "running_total(sum(amount))",
                "Running total",
            ),
            SemanticMeasure(
                "yoy_amount",
                "yoy(sum(amount))",
                "YoY change",
            ),
            SemanticMeasure(
                "yoy_amount_pct",
                "yoy_pct(sum(amount))",
                "YoY percent",
                "percent",
            ),
        ),
        default_table="sales",
    )


@pytest.fixture
def calendar_project(project: Project, tmp_path: Path) -> Project:
    sales = _write_sales(tmp_path)
    project.add_dataset(
        sales, dataset_id="sales", csv_options=CsvOptions(header=True)
    )
    generate_calendar(project, start="2023-01-01", end="2024-12-31")
    return project


def test_calendar_sql_validates_range() -> None:
    with pytest.raises(CalendarError):
        calendar_sql("2024-13-01", "2024-12-31")
    with pytest.raises(CalendarError):
        calendar_sql("2024-12-31", "2024-01-01")


def test_generate_calendar_registers_dataset(calendar_project: Project) -> None:
    entry = calendar_project.dataset_entry("calendar")
    assert entry.rows == (date(2024, 12, 31) - date(2023, 1, 1)).days + 1
    schema = calendar_project.dataset_schema("calendar")
    assert "date" in schema
    assert "month_start" in schema
    assert "quarter_start" in schema


def test_calendar_derivation_marks_grains_and_hierarchy(
    calendar_project: Project,
) -> None:
    model = SemanticModel.derive(
        {
            "calendar": calendar_project.dataset_schema("calendar"),
        },
        name="Calendar only",
    )
    grains = {
        dimension.name: dimension.grain
        for dimension in model.dimensions
        if dimension.grain
    }
    assert grains.get("date") == "day"
    assert grains.get("month_start") == "month"
    assert grains.get("quarter_start") == "quarter"
    assert grains.get("year_start") == "year"
    hierarchies = {item.name: item for item in model.hierarchies}
    assert "calendar" in hierarchies
    assert hierarchies["calendar"].levels == (
        "year_start",
        "quarter_start",
        "month_start",
        "date",
    )


def test_operation_constants() -> None:
    assert GRAINS == ("year", "quarter", "month", "week", "day")
    assert GRAIN_PERIODS["month"] == 12
    assert GRAIN_PERIODS["day"] == 365


def test_compile_running_total_and_yoy() -> None:
    def resolve(name: str):
        from data_studio.semantic.dsl import ResolvedColumn

        return ResolvedColumn(
            sql='"t"."amount"', table="t", name="amount", numeric=True
        )

    compiled = compile_measure(
        "running_total(sum(amount))",
        resolve,
        time_axis=('"t_calendar"."month_start"', 12),
    )
    assert compiled.needs_time_axis
    assert "OVER (ORDER BY" in compiled.sql
    yoy = compile_measure(
        "yoy(sum(amount))", resolve, time_axis=('"t_calendar"."month_start"', 12)
    )
    assert "LAG(SUM(\"t\".\"amount\"), 12)" in yoy.sql
    default = compile_measure("running_total(sum(amount))", resolve)
    assert default.needs_time_axis  # compiles for validation, flagged for the engine
    with pytest.raises(MeasureError):
        compile_measure("running_total(amount)", resolve)


def test_time_intelligence_end_to_end(calendar_project: Project) -> None:
    model = _model(calendar_project)
    engine = SemanticEngine(calendar_project, model)
    result = engine.query(
        SemanticQuery(
            dimensions=("month",),
            measures=("total_amount", "running_amount", "yoy_amount", "yoy_amount_pct"),
            order_by=(QueryOrder("month", "asc"),),
        )
    )
    rows = {
        row["month"]: row
        for row in result.rows
        if row["month"].startswith(("2023-01", "2023-02", "2024-01", "2024-02"))
    }
    amounts = dict(SALES_ROWS)
    assert rows["2023-01-01"]["total_amount"] == amounts["2023-01-15"]
    assert rows["2023-02-01"]["total_amount"] == amounts["2023-02-15"]
    # YoY: first 11 monthly rows have no prior-year period inside the data.
    assert rows["2024-01-01"]["yoy_amount"] == pytest.approx(
        amounts["2024-01-15"] - amounts["2023-01-15"]
    )
    assert rows["2024-01-01"]["yoy_amount_pct"] == pytest.approx(
        (amounts["2024-01-15"] - amounts["2023-01-15"])
        / amounts["2023-01-15"]
        * 100
    )
    running = [
        row["running_amount"]
        for row in result.rows
        if row["month"] <= "2023-03-01"
    ]
    assert running == sorted(running)
    assert running[-1] == pytest.approx(
        amounts["2023-01-15"] + amounts["2023-02-15"] + amounts["2023-03-15"]
    )


def test_time_intelligence_requires_grain(
    calendar_project: Project,
) -> None:
    model = _model(calendar_project)
    engine = SemanticEngine(calendar_project, model)
    with pytest.raises(SemanticError, match="grain"):
        engine.query(
            SemanticQuery(
                measures=("running_amount",),
            )
        )


def test_time_intelligence_measures_cannot_filter(
    calendar_project: Project,
) -> None:
    from data_studio.semantic import QueryFilter

    model = _model(calendar_project)
    engine = SemanticEngine(calendar_project, model)
    with pytest.raises(SemanticError, match="filter"):
        engine.query(
            SemanticQuery(
                dimensions=("month",),
                measures=("total_amount",),
                filters=(QueryFilter("running_amount", ">", 10),),
            )
        )


def test_grain_requires_date_like_column(calendar_project: Project) -> None:
    base = _model(calendar_project)
    problems = base.validate(
        {
            "sales": calendar_project.dataset_schema("sales"),
            "calendar": calendar_project.dataset_schema("calendar"),
        }
    )
    assert not any("grain" in problem for problem in problems)
    bad = SemanticModel(
        name="Bad grains",
        tables=(SemanticTable("sales", "sales"),),
        dimensions=(
            SemanticDimension(
                "amount", "sales", "amount", title="Amount", grain="month"
            ),
        ),
        default_table="sales",
    )
    problems = bad.validate(
        {"sales": calendar_project.dataset_schema("sales")}
    )
    assert any("grain" in problem and "date-like" in problem for problem in problems)
