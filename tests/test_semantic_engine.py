"""Tests for semantic query planning and execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_studio.io.sniff import CsvOptions
from data_studio.project.store import Project
from data_studio.semantic import (
    QueryFilter,
    QueryOrder,
    Relationship,
    SemanticDimension,
    SemanticEngine,
    SemanticError,
    SemanticMeasure,
    SemanticModel,
    SemanticQuery,
    SemanticTable,
)

CSV_TEXT = "region,amount,units\nNorth,10,1\nSouth,20,2\nNorth,15,3\nEast,5,1\n"
REGIONS_TEXT = "region,manager,weight\nNorth,Ana,1\nSouth,Bo,2\nEast,Cyd,3\n"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    instance = Project.create(tmp_path / "Demo", "Semantic")
    sales = tmp_path / "sales.csv"
    sales.write_text(CSV_TEXT, encoding="utf-8")
    instance.add_dataset(
        sales, dataset_id="sales", csv_options=CsvOptions(header=True)
    )
    regions = tmp_path / "regions.csv"
    regions.write_text(REGIONS_TEXT, encoding="utf-8")
    instance.add_dataset(
        regions, dataset_id="regions", csv_options=CsvOptions(header=True)
    )
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def model() -> SemanticModel:
    return SemanticModel(
        name="Sales",
        tables=(
            SemanticTable("sales", "sales"),
            SemanticTable("regions", "regions"),
        ),
        relationships=(Relationship("sales", "region", "regions", "region"),),
        dimensions=(
            SemanticDimension("region", "sales", "region"),
            SemanticDimension("manager", "regions", "manager"),
        ),
        measures=(
            SemanticMeasure("total_amount", "sum(amount)", "Total amount", "currency"),
            SemanticMeasure("units", "sum(units)", "Units", "integer"),
            SemanticMeasure(
                "north_amount",
                "sum(amount) where sales.region = 'North'",
                "North amount",
                "currency",
            ),
        ),
        default_table="sales",
    )


@pytest.fixture
def engine(project: Project, model: SemanticModel) -> SemanticEngine:
    return SemanticEngine(project, model, use_pipeline=False)


def test_group_by_dimension_with_join(engine: SemanticEngine) -> None:
    result = engine.query(
        SemanticQuery(
            dimensions=("manager",),
            measures=("total_amount",),
            order_by=(QueryOrder("total_amount", "desc"),),
        )
    )
    assert result.rows == (
        {"manager": "Ana", "total_amount": 25},
        {"manager": "Bo", "total_amount": 20},
        {"manager": "Cyd", "total_amount": 5},
    )
    assert result.columns[1].kind == "measure"
    assert result.columns[1].format == "currency"
    assert "LEFT JOIN" in result.sql


def test_measure_filter_uses_having(engine: SemanticEngine) -> None:
    result = engine.query(
        SemanticQuery(
            dimensions=("region",),
            measures=("total_amount",),
            filters=(QueryFilter("total_amount", ">", 15),),
        )
    )
    assert [row["region"] for row in result.rows] == ["North", "South"]


def test_dimension_filter_and_limit(engine: SemanticEngine) -> None:
    result = engine.query(
        SemanticQuery(
            dimensions=("region",),
            measures=("total_amount",),
            filters=(QueryFilter("region", "in", ["North", "East"]),),
            order_by=(QueryOrder("region"),),
            limit=1,
        )
    )
    assert result.rows == ({"region": "East", "total_amount": 5},)
    assert result.truncated is True


def test_kpi_query_without_dimensions(engine: SemanticEngine) -> None:
    result = engine.query(SemanticQuery(measures=("total_amount", "north_amount")))
    assert result.rows == ({"total_amount": 50, "north_amount": 25},)
    assert result.row_count == 1


def test_qualified_column_filter(engine: SemanticEngine) -> None:
    result = engine.query(
        SemanticQuery(
            dimensions=("region",),
            measures=("total_amount",),
            filters=(QueryFilter("sales.region", "=", "North"),),
        )
    )
    assert result.rows == ({"region": "North", "total_amount": 25},)


def test_dimension_values(engine: SemanticEngine) -> None:
    values = engine.dimension_values("region")
    assert values["values"] == ["East", "North", "South"]
    filtered = engine.dimension_values("region", search="n")
    assert filtered["values"] == ["North"]


def test_unknown_dimension_and_measure(engine: SemanticEngine) -> None:
    with pytest.raises(SemanticError):
        engine.query(SemanticQuery(dimensions=("nope",)))
    with pytest.raises(SemanticError):
        engine.query(SemanticQuery(measures=("nope",)))


def test_unrelated_table_is_reported(project: Project, model: SemanticModel) -> None:
    extra = SemanticModel(
        name=model.name,
        tables=(*model.tables, SemanticTable("other", "regions")),
        relationships=model.relationships,
        dimensions=(
            *model.dimensions,
            SemanticDimension("weight", "other", "weight"),
        ),
        measures=model.measures,
        default_table="sales",
    )
    engine = SemanticEngine(project, extra, use_pipeline=False)
    with pytest.raises(SemanticError, match="cannot be joined"):
        engine.query(SemanticQuery(dimensions=("weight",), measures=("total_amount",)))


def test_ambiguous_unqualified_column(project: Project) -> None:
    model = SemanticModel(
        name="Sales",
        tables=(
            SemanticTable("sales", "sales"),
            SemanticTable("regions", "regions"),
        ),
        relationships=(Relationship("sales", "region", "regions", "region"),),
        measures=(
            SemanticMeasure("north_amount", "sum(amount) where region = 'North'"),
        ),
        default_table="sales",
    )
    with pytest.raises(SemanticError, match="Ambiguous"):
        SemanticEngine(project, model, use_pipeline=False)
