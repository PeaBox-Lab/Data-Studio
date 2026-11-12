"""Tests for per-dataset conditional-formatting rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_studio.formats import (
    FormatError,
    FormatRule,
    load_formats,
    save_formats,
    validate_rules,
)
from data_studio.io.sniff import CsvOptions
from data_studio.project.store import Project

CSV_TEXT = "region,amount\nNorth,10\nSouth,20\nNorth,15\n"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    instance = Project.create(tmp_path / "FormatDemo", "Formats")
    source = tmp_path / "sales.csv"
    source.write_text(CSV_TEXT, encoding="utf-8")
    instance.add_dataset(source, dataset_id="sales", csv_options=CsvOptions(header=True))
    try:
        yield instance
    finally:
        instance.close()


def test_save_and_load_roundtrip(project: Project) -> None:
    rules = [
        FormatRule("amount", "data_bars"),
        FormatRule("amount", "highlight", op=">=", value=15, color="green"),
    ]
    save_formats(project, "sales", rules)
    reloaded = load_formats(project, "sales")
    assert [rule.kind for rule in reloaded] == ["data_bars", "highlight"]
    assert reloaded[1].value == 15
    assert reloaded[1].color == "green"
    document = json.loads(
        (project.root / "formats" / "sales.json").read_text(encoding="utf-8")
    )
    assert document["dataset"] == "sales"
    assert len(document["rules"]) == 2


def test_validate_rejects_unknown_columns(project: Project) -> None:
    schema = project.dataset_schema("sales")
    with pytest.raises(FormatError, match="does not exist"):
        validate_rules([FormatRule("nope", "data_bars")], schema)
    with pytest.raises(FormatError, match="not numeric"):
        validate_rules([FormatRule("region", "data_bars")], schema)
    with pytest.raises(FormatError, match=r"[Dd]uplicate"):
        validate_rules(
            [FormatRule("amount", "heatmap"), FormatRule("amount", "heatmap")],
            schema,
        )


def test_highlight_between_and_color_validation() -> None:
    with pytest.raises(FormatError, match="between'"):
        FormatRule("amount", "highlight", op="between", value=1)
    with pytest.raises(FormatError, match="needs value <= value2"):
        FormatRule("amount", "highlight", op="between", value=20, value2=5)
    with pytest.raises(FormatError, match="color"):
        FormatRule("amount", "highlight", op=">", value=1, color="purple")
    with pytest.raises(FormatError, match="kind"):
        FormatRule("amount", "underline")
    with pytest.raises(FormatError, match="operator"):
        FormatRule("amount", "highlight")
    good = FormatRule("amount", "highlight", op="between", value=1, value2=10, color="amber")
    assert good.to_dict()["op"] == "between"


def test_rules_do_not_take_value_for_plain_kinds() -> None:
    with pytest.raises(FormatError, match="do not take"):
        FormatRule("amount", "data_bars", value=5)
    with pytest.raises(FormatError, match="do not take"):
        FormatRule("amount", "heatmap", value=5)


def test_load_missing_formats_is_empty(project: Project) -> None:
    assert load_formats(project, "sales") == []


def test_save_requires_known_dataset(project: Project) -> None:
    with pytest.raises(Exception, match="nope"):
        save_formats(project, "nope", [FormatRule("a", "data_bars")])


def test_api_format_roundtrip(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from data_studio.api.app import create_app
    from data_studio.api.state import ProjectManager

    root = tmp_path / "FormatApi"
    manager = ProjectManager()
    with TestClient(create_app(manager=manager)) as client:
        created = client.post(
            "/api/v1/project/create", json={"path": str(root), "name": "Fmt"}
        )
        assert created.status_code == 200
        upload = client.post(
            "/api/v1/datasets",
            params={"filename": "sales.csv", "dataset_id": "sales"},
            content=CSV_TEXT.encode("utf-8"),
            headers={"Content-Type": "application/octet-stream"},
        )
        assert upload.status_code == 201
        put = client.put(
            "/api/v1/datasets/sales/format",
            json={
                "rules": [
                    {"column": "amount", "kind": "data_bars"},
                    {
                        "column": "amount",
                        "kind": "highlight",
                        "op": ">",
                        "value": 12,
                        "color": "red",
                    },
                ]
            },
        )
        assert put.status_code == 200, put.text
        got = client.get("/api/v1/datasets/sales/format")
        assert [rule["kind"] for rule in got.json()["rules"]] == ["data_bars", "highlight"]
        bad = client.put(
            "/api/v1/datasets/sales/format",
            json={"rules": [{"column": "amount", "kind": "nope"}]},
        )
        assert bad.status_code == 400
    manager.close()
