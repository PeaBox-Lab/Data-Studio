"""Tests for the measure DSL, semantic model and model store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_studio.io.sniff import CsvOptions
from data_studio.kernel.schema import Column, DataType, Role, TableSchema
from data_studio.project.store import Project
from data_studio.semantic.dsl import (
    MeasureError,
    ResolvedColumn,
    compile_measure,
    referenced_columns,
)
from data_studio.semantic.model import (
    Relationship,
    SemanticDimension,
    SemanticError,
    SemanticMeasure,
    SemanticModel,
    SemanticTable,
)
from data_studio.semantic.store import SemanticModelStore

CSV_TEXT = "region,amount,units\nNorth,10,1\nSouth,20,2\nNorth,15,3\n"
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


def project_model(project: Project) -> SemanticModel:
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
            SemanticMeasure("total_amount", "sum(amount)", format="currency"),
            SemanticMeasure(
                "north_amount", "sum(amount) where sales.region = 'North'"
            ),
        ),
        default_table="sales",
    )


def schema(*columns: tuple[str, DataType]) -> TableSchema:
    return TableSchema(
        columns=tuple(
            Column(name=name, dtype=dtype, role=Role.MEASURE) for name, dtype in columns
        )
    )


def test_dsl_compiles_arithmetic_aggregates() -> None:
    table = schema(("amount", DataType.FLOAT), ("units", DataType.INTEGER))

    def resolve(name: str) -> ResolvedColumn:
        column = table.column(name)
        return ResolvedColumn(
            sql=f'"t"."{column.name}"',
            table="t",
            name=column.name,
            numeric=column.dtype in {DataType.INTEGER, DataType.FLOAT, DataType.DECIMAL},
        )

    compiled = compile_measure("sum(amount) / count()", resolve)
    assert compiled.sql == '(SUM("t"."amount") / COUNT(*))'
    assert compiled.tables == frozenset({"t"})
    assert compiled.numeric is True


def test_dsl_where_filter_and_references() -> None:
    def resolve(name: str) -> ResolvedColumn:
        if "." in name:
            table, column = name.split(".", 1)
        else:
            table, column = "t", name
        return ResolvedColumn(f'"{table}"."{column}"', table, column, True)

    compiled = compile_measure(
        "sum(amount) where region = 'North' and units >= 2", resolve
    )
    assert 'FILTER (WHERE ("t"."region" = \'North\'' in compiled.sql
    assert referenced_columns(
        "sum(amount) where region = 'North' and units >= 2"
    ) == ("amount", "region", "units")


@pytest.mark.parametrize(
    "expression",
    ["", "sum(", "sum(region)", "median(region)", "sum(amount) where region = 1"],
)
def test_dsl_rejects_invalid_expressions(expression: str, project: Project) -> None:
    resolver = project_model(project).column_resolver(
        {
            "sales": project.dataset_schema("sales"),
            "regions": project.dataset_schema("regions"),
        }
    )
    with pytest.raises((MeasureError, SemanticError)):
        compile_measure(expression, resolver)


def test_derive_creates_dimensions_and_measures(project: Project) -> None:
    schemas = {
        dataset: project.dataset_schema(dataset) for dataset in project.dataset_ids()
    }
    derived = SemanticModel.derive(schemas)
    assert [table.name for table in derived.tables] == ["sales", "regions"]
    dimension_names = {item.name for item in derived.dimensions}
    assert {"region", "manager"} <= dimension_names
    measure_names = {item.name for item in derived.measures}
    assert {"total_amount", "average_amount", "total_units"} <= measure_names
    assert derived.default_table == "sales"


def test_derive_formats_currency_and_percent() -> None:
    table = TableSchema(
        columns=(
            Column(name="amount", dtype=DataType.FLOAT, role=Role.MEASURE),
            Column(name="conversion_pct", dtype=DataType.FLOAT, role=Role.MEASURE),
            Column(name="label", dtype=DataType.STRING, role=Role.DIMENSION),
        )
    )
    derived = SemanticModel.derive({"sales": table})
    formats = {item.name: item.format for item in derived.measures}
    assert formats["total_amount"] == "currency"
    assert formats["total_conversion_pct"] == "percent"


def test_model_validation_reports_missing_columns(project: Project) -> None:
    broken = SemanticModel(
        name="Broken",
        tables=(SemanticTable("sales", "sales"),),
        dimensions=(SemanticDimension("region", "sales", "missing"),),
        measures=(SemanticMeasure("bad", "sum(nope)"),),
        default_table="sales",
    )
    with pytest.raises(SemanticError) as excinfo:
        broken.validate_or_raise({"sales": project.dataset_schema("sales")})
    message = str(excinfo.value)
    assert "missing" in message
    assert "nope" in message


def test_store_round_trip(project: Project) -> None:
    store = SemanticModelStore(project)
    assert store.exists() is False
    assert store.load() is None
    saved = store.derive(name="Sales model")
    assert store.exists()
    loaded = store.get()
    assert loaded.to_dict() == saved.to_dict()
    assert store.path == project.root / "models" / "semantic.json"
    store.remove()
    assert store.exists() is False


def test_store_refuses_invalid_model(project: Project) -> None:
    store = SemanticModelStore(project)
    broken = SemanticModel(
        name="Broken",
        tables=(SemanticTable("sales", "sales"),),
        dimensions=(SemanticDimension("region", "sales", "missing"),),
        default_table="sales",
    )
    with pytest.raises(SemanticError):
        store.save(broken)
    assert not store.exists()


def test_store_reports_unknown_dataset(project: Project) -> None:
    store = SemanticModelStore(project)
    broken = SemanticModel(
        name="Broken",
        tables=(SemanticTable("ghost", "nope"),),
        default_table="ghost",
    )
    problems = store.validate(broken)
    assert problems and "unknown dataset" in problems[0]
    with pytest.raises(SemanticError):
        store.save(broken)


def test_model_json_round_trip(project: Project) -> None:
    model = project_model(project)
    payload = model.to_dict()
    assert SemanticModel.from_dict(payload).to_dict() == payload
    assert json.loads(json.dumps(payload))["name"] == "Sales"
