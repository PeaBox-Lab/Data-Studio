"""Tests for persisted analyses, fingerprint invalidation and schedules."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from data_studio.analysis.saved import (
    SavedAnalysisError,
    SavedAnalysisStore,
    input_fingerprint,
    slugify,
)
from data_studio.pipeline import make_step
from data_studio.project.store import Project

CSV_TEXT = "region,amount\nNorth,10\nSouth,20\nNorth,15\n"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    root = tmp_path / "Demo"
    instance = Project.create(root, "Saved")
    csv = tmp_path / "sales.csv"
    csv.write_text(CSV_TEXT, encoding="utf-8")
    instance.add_dataset(csv, dataset_id="sales")
    try:
        yield instance
    finally:
        instance.close()


def store(project: Project) -> SavedAnalysisStore:
    return SavedAnalysisStore(project)


def save(
    project: Project,
    *,
    name: str = "Revenue by region",
    schedule: str = "manual",
    report_path: str | bool | None = None,
    overwrite: bool = False,
):
    return store(project).save(
        name=name,
        analysis="descriptive_stats",
        params={"columns": ["amount"]},
        dataset="sales",
        use_pipeline=False,
        schedule=schedule,
        report_path=report_path,
        overwrite=overwrite,
    )


def test_saved_analysis_survives_reopen(project: Project) -> None:
    saved = save(project)
    assert saved.id == "revenue-by-region"

    reopened = Project.open(project.root)
    try:
        loaded = SavedAnalysisStore(reopened).get(saved.id)
        assert loaded.title == "Descriptive statistics"
        assert loaded.result is not None
        assert loaded.result.kind == "descriptive_stats"
        assert loaded.params == {"columns": ["amount"]}
    finally:
        reopened.close()


def test_fingerprint_changes_when_data_changes(project: Project) -> None:
    saved = save(project)
    before = input_fingerprint(project, "sales", False)

    csv = project.root.parent / "sales.csv"
    csv.write_text("region,amount\nNorth,11\nSouth,21\nNorth,16\n", encoding="utf-8")
    project.add_dataset(csv, dataset_id="sales", replace=True)

    after = input_fingerprint(project, "sales", False)
    assert before != after
    assert store(project).describe(store(project).get(saved.id))["status"] == "stale"


def test_fingerprint_changes_when_pipeline_changes(project: Project) -> None:
    saved = save(project, name="Pipeline result")
    store(project).save(
        name="Pipeline result",
        analysis="descriptive_stats",
        params={"columns": ["amount"]},
        dataset="sales",
        use_pipeline=True,
        overwrite=True,
    )
    assert store(project).describe(store(project).get(saved.id))["status"] == "fresh"

    pipeline = project.load_pipeline()
    project.save_pipeline(
        pipeline.add_step(make_step("trim_text", {"columns": ["region"]}))
    )
    assert store(project).describe(store(project).get(saved.id))["status"] == "stale"


def test_refresh_updates_result_and_artifact(project: Project) -> None:
    saved = save(project, schedule="daily", report_path=True)
    report = project.root / "reports" / "revenue-by-region.html"
    assert report.is_file()
    first = report.read_text(encoding="utf-8")

    csv = project.root.parent / "sales.csv"
    csv.write_text("region,amount\nNorth,11\nSouth,21\nNorth,16\n", encoding="utf-8")
    project.add_dataset(csv, dataset_id="sales", replace=True)
    refreshed, failures = store(project).refresh_due()
    assert failures == []
    assert [item.id for item in refreshed] == [saved.id]
    assert report.read_text(encoding="utf-8") != first
    assert "amount" in report.read_text(encoding="utf-8")
    assert store(project).describe(store(project).get(saved.id))["status"] == "fresh"


def test_due_follows_the_schedule(project: Project) -> None:
    daily = save(project, name="Daily", schedule="daily")
    manual = save(project, name="Manual", schedule="manual")

    fresh_store = store(project)
    assert fresh_store.describe(fresh_store.get(daily.id))["due"] is False
    assert fresh_store.describe(fresh_store.get(manual.id))["due"] is False

    later = datetime.now(UTC) + timedelta(days=1, minutes=1)
    assert fresh_store.describe(fresh_store.get(daily.id), now=later)["due"] is True
    assert fresh_store.describe(fresh_store.get(manual.id), now=later)["due"] is False


def test_on_open_refresh_only_touches_on_open_entries(project: Project) -> None:
    on_open = save(project, name="Open", schedule="on_open")
    daily = save(project, name="Daily", schedule="daily")

    csv = project.root.parent / "sales.csv"
    csv.write_text("region,amount\nNorth,12\nSouth,22\nNorth,17\n", encoding="utf-8")
    project.add_dataset(csv, dataset_id="sales", replace=True)

    refreshed, failures = store(project).refresh_due(only_on_open=True)
    assert failures == []
    assert [item.id for item in refreshed] == [on_open.id]
    assert store(project).describe(store(project).get(on_open.id))["status"] == "fresh"
    assert store(project).describe(store(project).get(daily.id))["status"] == "stale"


def test_manual_refresh_all(project: Project) -> None:
    save(project, name="One")
    save(project, name="Two")
    refreshed, failures = store(project).refresh_all()
    assert failures == []
    assert {item.id for item in refreshed} == {"one", "two"}


def test_duplicate_names_get_a_suffix_and_overwrite_replaces(project: Project) -> None:
    first = save(project, name="Revenue")
    second = save(project, name="Revenue")
    assert first.id == "revenue"
    assert second.id == "revenue-2"
    assert len(store(project).list()) == 2

    replaced = save(project, name="Revenue", overwrite=True)
    assert replaced.id == "revenue"
    assert len(store(project).list()) == 2


def test_corrupt_documents_are_reported_without_hiding_others(project: Project) -> None:
    save(project, name="Good")
    bad = project.root / "analyses" / "bad.json"
    bad.write_text("{not json", encoding="utf-8")

    instance = store(project)
    assert [item.id for item in instance.list()] == ["good"]
    assert instance.last_errors and "bad.json" in instance.last_errors[0]


def test_validation_rules(project: Project) -> None:
    with pytest.raises(SavedAnalysisError):
        save(project, schedule="sometimes")
    with pytest.raises(SavedAnalysisError):
        save(project, schedule="daily", report_path="exports/out.html")
    with pytest.raises(SavedAnalysisError):
        slugify("   ")
    with pytest.raises(KeyError):
        store(project).get("missing")
    with pytest.raises(ValueError):
        store(project).save(
            name="No dataset",
            analysis="descriptive_stats",
            dataset="missing",
            use_pipeline=False,
        )


def test_saved_document_is_plain_json(project: Project) -> None:
    saved = save(project)
    payload = json.loads(
        (project.root / "analyses" / f"{saved.id}.json").read_text(encoding="utf-8")
    )
    assert payload["analysis"] == "descriptive_stats"
    assert payload["result"]["kind"] == "descriptive_stats"
    assert "pickle" not in json.dumps(payload).lower()


def test_removing_a_saved_analysis_deletes_its_document(project: Project) -> None:
    saved = save(project)
    path = project.root / "analyses" / f"{saved.id}.json"
    assert path.is_file()
    store(project).remove(saved.id)
    assert not path.exists()
    with pytest.raises(KeyError):
        store(project).get(saved.id)
