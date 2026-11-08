"""End-to-end Phase 1 integration test: messy CSV -> clean/join -> export -> reopen.

The fixture is deliberately messy: inconsistent region casing and whitespace, missing
amounts, invalid dates, duplicate order ids and a lookup table that must be joined.
A 20,000-row frame keeps the test fast while exercising the same path a larger file uses.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from data_studio.kernel.schema import DataType
from data_studio.pipeline import make_step
from data_studio.project import Project

ROWS = 20_000
DUPLICATES = 500


def build_messy_csv(path: Path) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    frame = pd.DataFrame(
        {
            "order_id": np.arange(1, ROWS + 1),
            "customer_id": rng.integers(1, 500, ROWS),
            "region": rng.choice([" North ", "north", "SOUTH", "South ", "west"], ROWS),
            "amount": rng.normal(100.0, 25.0, ROWS).round(2),
            "order_date": pd.date_range("2024-01-01", periods=ROWS, freq="h").strftime(
                "%Y-%m-%d"
            ),
            "status": rng.choice(["new", "shipped", "cancelled"], ROWS),
            "revision": np.ones(ROWS, dtype=int),
        }
    )
    duplicates = frame.head(DUPLICATES).copy()
    duplicates["amount"] = (duplicates["amount"] + 5.0).round(2)
    duplicates["status"] = "shipped"
    duplicates["revision"] = 2

    combined = pd.concat([frame, duplicates], ignore_index=True)
    combined = combined.sample(frac=1.0, random_state=7).reset_index(drop=True)

    # Missing amounts and invalid dates are the kind of thing a spreadsheet export leaves.
    combined.loc[combined.index[:250], "amount"] = np.nan
    combined.loc[combined.index[250:300], "order_date"] = "not-a-date"
    combined.to_csv(path, index=False, na_rep="")
    return combined


def build_lookup(path: Path) -> None:
    path.write_text(
        "region,zone\nNorth,N\nSouth,S\nWest,W\n",
        encoding="utf-8",
    )


def expected_reference(frame: pd.DataFrame) -> tuple[int, float, set[str]]:
    cleaned = frame.copy()
    cleaned["region"] = cleaned["region"].str.strip().str.title()
    cleaned["amount"] = pd.to_numeric(cleaned["amount"], errors="coerce")
    cleaned["amount"] = cleaned["amount"].fillna(cleaned["amount"].mean())
    cleaned = cleaned.sort_values("revision", ascending=False).drop_duplicates(
        "order_id", keep="first"
    )
    cleaned = cleaned[cleaned["status"] != "cancelled"]
    return len(cleaned), float(cleaned["amount"].sum()), set(cleaned["region"])


def add_pipeline(project: Project) -> None:
    pipeline = project.load_pipeline()
    pipeline = pipeline.add_step(make_step("trim_text", {"columns": ["region"]}))
    pipeline = pipeline.add_step(
        make_step("normalize_text", {"columns": ["region"], "case": "title"})
    )
    pipeline = pipeline.add_step(
        make_step("cast", {"column": "amount", "dtype": "float", "try_cast": True})
    )
    pipeline = pipeline.add_step(
        make_step("fill_missing", {"columns": ["amount"], "method": "mean"})
    )
    pipeline = pipeline.add_step(
        make_step(
            "date_parse",
            {"column": "order_date", "format": "%Y-%m-%d", "output": "order_ts"},
        )
    )
    pipeline = pipeline.add_step(
        make_step(
            "deduplicate",
            {"columns": ["order_id"], "keep": "last", "order_by": ["revision"]},
        )
    )
    pipeline = pipeline.add_step(
        make_step("filter", {"expression": "status != 'cancelled'"})
    )
    pipeline = pipeline.add_step(
        make_step(
            "join",
            {
                "dataset": "regions",
                "how": "left",
                "left_on": ["region"],
                "right_on": ["region"],
                "cardinality": "many_to_one",
            },
        )
    )
    project.save_pipeline(pipeline)


def test_messy_csv_pipeline_export_and_reopen(tmp_path: Path) -> None:
    root = tmp_path / "Analytics"
    raw_path = tmp_path / "orders.csv"
    lookup_path = tmp_path / "regions.csv"
    frame = build_messy_csv(raw_path)
    build_lookup(lookup_path)

    expected_rows, expected_sum, expected_regions = expected_reference(frame)

    project = Project.create(root, "Analytics")
    try:
        project.add_dataset(raw_path, dataset_id="orders")
        project.add_dataset(lookup_path, dataset_id="regions")
        assert project.manifest.active_dataset == "orders"
        add_pipeline(project)

        result = project.run_pipeline()
        assert result.rows == expected_rows
        assert result.dataset == "orders"
        assert len(result.steps) == 8

        schema = result.schema
        assert "order_ts" in schema
        assert schema.column("order_ts").dtype is DataType.DATETIME
        assert schema.column("zone").dtype is DataType.STRING
        assert schema.column("region").role.value == "dimension"

        output = result.table.to_pandas()
        assert output.shape[0] == expected_rows
        assert math.isclose(float(output["amount"].sum()), expected_sum, rel_tol=1e-9)
        assert set(output["region"]) == expected_regions
        assert output["amount"].isna().sum() == 0
        assert output["zone"].isna().sum() == 0
        assert output["order_id"].is_unique

        # Incremental rerun: everything is cached on the second pass.
        cached = project.run_pipeline()
        assert cached.cache_hits == 8
        assert cached.rows == expected_rows

        parquet = tmp_path / "clean.parquet"
        project.export(result, parquet)
        exported = pd.read_parquet(parquet)
        assert exported.shape[0] == expected_rows
        assert "zone" in exported.columns
        assert exported["order_id"].is_unique

        csv = tmp_path / "clean.csv"
        project.export(result, csv)
        assert sum(1 for _ in csv.open(encoding="utf-8")) == expected_rows + 1

        assert project.verify() == []

        # Drop the join to prove undo + incremental rerun work after a normal run.
        undone = project.undo(project.load_pipeline())
        assert len(undone.steps) == 7
        without_join = project.run_pipeline(undone)
        assert without_join.rows == expected_rows
        assert without_join.cache_hits == 7
        assert "zone" not in without_join.schema
    finally:
        project.close()

    # Reopen from disk and reproduce the same rows, sums and schema.
    reopened = Project.open(root, verify=True)
    try:
        pipeline = reopened.load_pipeline()
        assert len(pipeline.steps) == 7  # undo was persisted
        rerun = reopened.run_pipeline(pipeline)
        assert rerun.rows == expected_rows
        assert "zone" not in rerun.schema
        assert math.isclose(
            float(rerun.table.to_pandas()["amount"].sum()), expected_sum, rel_tol=1e-9
        )
    finally:
        reopened.close()


def test_export_formats_roundtrip_from_a_project(tmp_path: Path) -> None:
    root = tmp_path / "Formats"
    raw_path = tmp_path / "orders.csv"
    build_messy_csv(raw_path)
    project = Project.create(root, "Formats")
    try:
        project.add_dataset(raw_path, dataset_id="orders")
        project.save_pipeline(
            project.load_pipeline().add_step(
                make_step("filter", {"expression": "amount > 90"})
            )
        )
        result = project.run_pipeline()
        assert result.rows and result.rows > 0
        for suffix in (".parquet", ".csv", ".json"):
            destination = tmp_path / f"out{suffix}"
            project.export(result, destination)
            assert destination.is_file()
            assert destination.stat().st_size > 0
        excel = tmp_path / "out.xlsx"
        project.export(result, excel)
        assert excel.is_file()
        assert pd.read_excel(excel).shape[0] == result.rows
    finally:
        project.close()
