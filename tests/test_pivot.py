"""Tests for the pivot engine and API."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_studio.io.sniff import CsvOptions
from data_studio.pivot import PivotError, PivotQuery, PivotValue, pivot_query
from data_studio.project.store import Project
from data_studio.semantic import QueryFilter

CSV_TEXT = (
    "region,product,amount,units\n"
    "North,A,10,1\n"
    "South,A,20,2\n"
    "North,B,30,3\n"
    "East,A,5,1\n"
    "South,B,40,4\n"
    "North,A,15,2\n"
)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    instance = Project.create(tmp_path / "PivotDemo", "Pivots")
    source = tmp_path / "sales.csv"
    source.write_text(CSV_TEXT, encoding="utf-8")
    instance.add_dataset(source, dataset_id="sales", csv_options=CsvOptions(header=True))
    try:
        yield instance
    finally:
        instance.close()


def _table(project: Project):
    return project.dataset_table("sales")


def test_single_row_dimension_with_pivot_column(project: Project) -> None:
    query = PivotQuery(
        rows=("region",),
        columns="product",
        values=(PivotValue("amount", "sum", "revenue"),),
    )
    result = pivot_query(project.engine, _table(project), query)
    aliases = [column.name for column in result.columns]
    assert aliases == ["region", "revenue | A", "revenue | B"]
    by_region = {row["region"]: row for row in result.rows}
    assert by_region["North"]["revenue | A"] == 25
    assert by_region["North"]["revenue | B"] == 30
    assert by_region["South"]["revenue | A"] == 20
    assert by_region["East"]["revenue | A"] == 5


def test_multiple_aggregations_per_value(project: Project) -> None:
    query = PivotQuery(
        rows=("product",),
        values=(
            PivotValue("amount", "sum", "total"),
            PivotValue("amount", "avg", "average"),
            PivotValue(None, "count", "rows"),
        ),
    )
    result = pivot_query(project.engine, _table(project), query)
    assert [column.name for column in result.columns] == [
        "product",
        "total",
        "average",
        "rows",
    ]
    by_product = {row["product"]: row for row in result.rows}
    assert by_product["A"]["rows"] == 4
    assert by_product["A"]["total"] == 50
    assert by_product["B"]["average"] == 35


def test_pivot_with_filters(project: Project) -> None:
    query = PivotQuery(
        rows=("region",),
        values=(PivotValue("amount", "sum", "total"),),
        filters=(QueryFilter("product", "=", "A"),),
    )
    result = pivot_query(project.engine, _table(project), query)
    by_region = {row["region"]: row for row in result.rows}
    assert by_region["North"]["total"] == 25
    assert by_region["South"]["total"] == 20


def test_non_numeric_value_rejected(project: Project) -> None:
    query = PivotQuery(
        rows=("region",),
        values=(PivotValue("region", "sum", "total"),),
    )
    with pytest.raises(PivotError, match="numeric"):
        pivot_query(project.engine, _table(project), query)


def test_unknown_columns_rejected(project: Project) -> None:
    query = PivotQuery(rows=("nope",), values=(PivotValue("amount", "sum"),))
    with pytest.raises(PivotError, match="does not exist"):
        pivot_query(project.engine, _table(project), query)


def test_duplicate_labels_rejected(project: Project) -> None:
    query = PivotQuery(
        rows=("region",),
        values=(
            PivotValue("amount", "sum", "total"),
            PivotValue("units", "sum", "total"),
        ),
    )
    with pytest.raises(PivotError, match="duplicate"):
        pivot_query(project.engine, _table(project), query)


def test_api_pivot_roundtrip(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from data_studio.api.app import create_app
    from data_studio.api.state import ProjectManager

    root = tmp_path / "PivotApi"
    source = tmp_path / "sales.csv"
    source.write_text(CSV_TEXT, encoding="utf-8")
    manager = ProjectManager()
    app = create_app(manager=manager)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/project/create", json={"path": str(root), "name": "PivotApi"}
        )
        assert created.status_code == 200
        upload = client.post(
            "/api/v1/datasets",
            params={"filename": "sales.csv", "dataset_id": "sales"},
            content=CSV_TEXT.encode("utf-8"),
            headers={"Content-Type": "application/octet-stream"},
        )
        assert upload.status_code == 201
        response = client.post(
            "/api/v1/pivot",
            json={
                "rows": ["region"],
                "columns": "product",
                "values": [{"column": "amount", "aggregation": "sum", "label": "revenue"}],
                "use_pipeline": False,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        headers = [column["name"] for column in payload["columns"]]
        assert headers == ["region", "revenue | A", "revenue | B"]
        assert payload["rows"][0]["region"] in {"East", "North", "South"}
    manager.close()


def test_pivot_query_roundtrip(project: Project) -> None:
    table = _table(project)
    direct = pivot_query(
        project.engine,
        table,
        PivotQuery(
            rows=("region",),
            values=(PivotValue("amount", "sum", "total"),),
        ),
    )
    via_dict = PivotQuery.from_dict(direct.query.to_dict())
    assert via_dict == direct.query
