"""Tests for the immutable pipeline model and the incremental runner."""

from __future__ import annotations

import pytest

from data_studio.engine.duckdb_engine import DuckDBEngine, DuckDBTable
from data_studio.kernel.schema import DataType
from data_studio.pipeline import (
    PIPELINE_SCHEMA_VERSION,
    Pipeline,
    PipelineRunError,
    PipelineRunner,
    PipelineStep,
    make_step,
    make_step_id,
)
from data_studio.steps import StepRegistry


@pytest.fixture
def engine() -> DuckDBEngine:
    instance = DuckDBEngine()
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def base(engine: DuckDBEngine) -> DuckDBTable:
    return engine.from_records(
        [
            {"id": 1, "region": " North ", "amount": 10.0},
            {"id": 2, "region": "north", "amount": None},
            {"id": 3, "region": "South", "amount": 7.0},
            {"id": 3, "region": "South", "amount": 7.0},
        ],
        name="sales",
    )


@pytest.fixture
def lookup(engine: DuckDBEngine) -> DuckDBTable:
    return engine.from_records(
        [{"region": "North", "zone": "N"}, {"region": "South", "zone": "S"}],
        name="lookup",
    )


def simple_pipeline() -> Pipeline:
    pipeline = Pipeline(name="Demo", dataset="sales")
    pipeline = pipeline.add_step(make_step("trim_text", {"columns": ["region"]}))
    pipeline = pipeline.add_step(
        make_step("normalize_text", {"columns": ["region"], "case": "title"})
    )
    pipeline = pipeline.add_step(make_step("cast", {"column": "amount", "dtype": "float"}))
    pipeline = pipeline.add_step(make_step("deduplicate", {"columns": ["id"]}))
    return pipeline


# -- model -----------------------------------------------------------------


def test_default_pipeline() -> None:
    pipeline = Pipeline()
    assert pipeline.name == "Pipeline"
    assert pipeline.dataset is None
    assert pipeline.steps == ()
    assert pipeline.schema_version == PIPELINE_SCHEMA_VERSION
    assert len(pipeline) == 0
    assert pipeline.enabled_steps == ()


def test_pipeline_name_and_dataset_validation() -> None:
    with pytest.raises(ValueError, match="name"):
        Pipeline(name="   ")
    with pytest.raises(ValueError, match="dataset"):
        Pipeline(dataset="   ")
    with pytest.raises(ValueError, match="schema version"):
        Pipeline(schema_version="2.0")
    with pytest.raises(TypeError):
        Pipeline(steps=(object(),))  # type: ignore[arg-type]


def test_make_step_id_and_collisions() -> None:
    assert make_step_id("trim_text") == "trim_text"
    assert make_step_id("1 bad operation") == "step_1_bad_operation"
    assert make_step_id("trim_text", ["trim_text"]) == "trim_text_2"
    assert make_step_id("trim_text", ["trim_text", "trim_text_2"]) == "trim_text_3"
    assert make_step_id("!!!") == "step"
    long = make_step_id("x" * 100)
    assert len(long) == 64


def test_step_validation() -> None:
    step = make_step("trim_text", {"columns": ["region"]}, title="Trim region")
    assert step.label == "Trim region"
    assert make_step("drop_missing").label == "Drop Missing"
    with pytest.raises(ValueError, match="Step id"):
        PipelineStep(id=" bad", operation="trim_text")
    with pytest.raises(ValueError, match="operation"):
        PipelineStep(id="ok", operation="  ")
    with pytest.raises(ValueError, match="JSON"):
        PipelineStep(id="ok", operation="trim_text", params={"value": object()})
    with pytest.raises(TypeError, match="enabled"):
        PipelineStep(id="ok", operation="trim_text", enabled=1)  # type: ignore[arg-type]


def test_step_params_are_read_only() -> None:
    step = make_step("filter", {"expression": "amount > 1"})
    with pytest.raises(TypeError):
        step.params["expression"] = "amount > 2"  # type: ignore[index]


def test_add_remove_move_and_replace() -> None:
    pipeline = Pipeline()
    first = make_step("trim_text", {"columns": ["region"]})
    second = make_step("drop_missing")
    pipeline = pipeline.add_step(first).add_step(second)
    assert [step.id for step in pipeline.steps] == ["trim_text", "drop_missing"]

    with pytest.raises(ValueError, match="already exists"):
        pipeline.add_step(first)
    with pytest.raises(ValueError, match="position"):
        pipeline.add_step(make_step("sort", {"columns": ["id"]}), position=9)

    inserted = pipeline.add_step(make_step("sort", {"columns": ["id"]}), position=1)
    assert [step.id for step in inserted.steps] == ["trim_text", "sort", "drop_missing"]

    removed = inserted.remove_step("sort")
    assert [step.id for step in removed.steps] == ["trim_text", "drop_missing"]
    with pytest.raises(KeyError):
        removed.remove_step("missing")

    moved = inserted.move_step("sort", 0)
    assert [step.id for step in moved.steps] == ["sort", "trim_text", "drop_missing"]
    with pytest.raises(ValueError, match="position"):
        moved.move_step("sort", 9)

    replacement = make_step("deduplicate", {"columns": ["id"]}, step_id="drop_missing")
    replaced = moved.replace_step("drop_missing", replacement)
    assert replaced.step("drop_missing") is replacement
    with pytest.raises(ValueError, match="already exists"):
        replaced.replace_step("sort", make_step("cast", {}, step_id="trim_text"))


def test_enable_undo_rename_and_clear() -> None:
    pipeline = simple_pipeline()
    assert len(pipeline.enabled_steps) == 4

    disabled = pipeline.set_enabled("cast", False)
    assert len(disabled.enabled_steps) == 3
    assert disabled.step("cast").enabled is False
    assert disabled.set_enabled("cast", True).enabled_steps == pipeline.steps

    undone = pipeline.undo()
    assert [step.operation for step in undone.steps] == [
        "trim_text",
        "normalize_text",
        "cast",
    ]
    assert Pipeline().undo if True else False
    with pytest.raises(ValueError, match="no steps"):
        Pipeline().undo()

    renamed = pipeline.rename("Cleaner").with_dataset("other")
    assert renamed.name == "Cleaner"
    assert renamed.dataset == "other"
    assert pipeline.name == "Demo"
    assert pipeline.clear().steps == ()


def test_serialisation_roundtrip() -> None:
    pipeline = simple_pipeline()
    text = pipeline.to_json()
    restored = Pipeline.from_json(text)
    assert restored.to_dict() == pipeline.to_dict()
    assert restored.steps[0].params == {"columns": ["region"]}


def test_from_dict_and_bad_json() -> None:
    payload = {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "name": "P",
        "dataset": None,
        "steps": [
            {"id": "s1", "operation": "drop_missing", "params": {}, "enabled": True}
        ],
    }
    pipeline = Pipeline.from_dict(payload)
    assert pipeline.steps[0].id == "s1"
    with pytest.raises(ValueError, match="Invalid pipeline JSON"):
        Pipeline.from_json("{not json")
    with pytest.raises(ValueError, match="schema version"):
        Pipeline.from_json('{"schema_version": "9.9", "steps": []}')
    with pytest.raises(ValueError, match="unique"):
        Pipeline.from_json(
            '{"steps": [{"id": "a", "operation": "x"}, {"id": "a", "operation": "y"}]}'
        )
    with pytest.raises(TypeError):
        Pipeline.from_dict([])  # type: ignore[arg-type]


# -- runner ----------------------------------------------------------------


def test_runner_returns_the_base_table_for_an_empty_pipeline(
    base: DuckDBTable,
) -> None:
    result = PipelineRunner().run(Pipeline(dataset="sales"), base)
    assert result.rows == 4
    assert result.steps == ()
    assert result.cache_hits == 0
    assert result.table is base
    assert result.dataset == "sales"
    assert result.fingerprint == base.fingerprint()
    assert result.duration_ms >= 0


def test_runner_applies_steps_in_order(base: DuckDBTable) -> None:
    result = PipelineRunner().run(simple_pipeline(), base)
    assert result.rows == 3
    assert [step.operation for step in result.steps] == [
        "trim_text",
        "normalize_text",
        "cast",
        "deduplicate",
    ]
    assert result.schema.names == ("id", "region", "amount")
    assert result.schema.column("amount").dtype is DataType.FLOAT
    rows = result.preview(10).to_records()
    assert {row["region"] for row in rows} == {"North", "South"}
    assert all(step.cached is False for step in result.steps)
    assert result.cache_hits == 0


def test_runner_cache_hits_on_an_unchanged_pipeline(base: DuckDBTable) -> None:
    runner = PipelineRunner()
    pipeline = simple_pipeline()
    first = runner.run(pipeline, base)
    second = runner.run(pipeline, base)
    assert first.cache_hits == 0
    assert second.cache_hits == 4
    assert all(step.cached for step in second.steps)
    assert runner.cache_size == 4
    runner.clear_cache()
    assert runner.cache_size == 0
    assert runner.run(pipeline, base).cache_hits == 0


def test_changing_a_step_reruns_only_that_step_and_later_ones(
    base: DuckDBTable,
) -> None:
    runner = PipelineRunner()
    pipeline = simple_pipeline()
    runner.run(pipeline, base)

    changed = pipeline.replace_step(
        "cast",
        make_step("cast", {"column": "amount", "dtype": "float", "try_cast": True}, step_id="cast"),
    )
    result = runner.run(changed, base)
    assert [step.cached for step in result.steps] == [True, True, False, False]
    assert result.cache_hits == 2


def test_dataset_dependency_changes_invalidate_a_join(
    base: DuckDBTable, engine: DuckDBEngine, lookup: DuckDBTable
) -> None:
    pipeline = (
        Pipeline(dataset="sales")
        .add_step(make_step("trim_text", {"columns": ["region"]}))
        .add_step(
            make_step(
                "join",
                {
                    "dataset": "lookup",
                    "how": "left",
                    "left_on": ["region"],
                    "right_on": ["region"],
                },
            )
        )
    )
    runner = PipelineRunner()
    first = runner.run(pipeline, base, datasets={"lookup": lookup})
    assert first.cache_hits == 0
    second = runner.run(pipeline, base, datasets={"lookup": lookup})
    assert second.cache_hits == 2

    changed_lookup = engine.from_records(
        [{"region": "North", "zone": "NORTH"}, {"region": "South", "zone": "SOUTH"}],
        name="lookup",
    )
    third = runner.run(pipeline, base, datasets={"lookup": changed_lookup})
    assert third.cache_hits == 1
    assert [step.cached for step in third.steps] == [True, False]
    records = third.preview(10).to_records()
    assert {row["zone"] for row in records if row["zone"] is not None} == {"NORTH", "SOUTH"}


def test_missing_dataset_dependency_is_still_fingerprinted(
    base: DuckDBTable, lookup: DuckDBTable
) -> None:
    pipeline = Pipeline(dataset="sales").add_step(
        make_step(
            "join",
            {"dataset": "lookup", "how": "left", "left_on": ["region"], "right_on": ["region"]},
        )
    )
    with pytest.raises(PipelineRunError):
        PipelineRunner().run(pipeline, base, datasets={})
    result = PipelineRunner().run(pipeline, base, datasets={"lookup": lookup})
    assert result.rows == 4


def test_disabled_steps_are_skipped(base: DuckDBTable) -> None:
    pipeline = simple_pipeline()
    pipeline = pipeline.set_enabled("cast", False)
    result = PipelineRunner().run(pipeline, base)
    assert [step.step_id for step in result.steps] == [
        "trim_text",
        "normalize_text",
        "deduplicate",
    ]


def test_count_rows_can_be_skipped(base: DuckDBTable) -> None:
    result = PipelineRunner().run(simple_pipeline(), base, count_rows=False)
    assert result.rows is None


def test_failed_step_names_the_step(base: DuckDBTable) -> None:
    pipeline = Pipeline().add_step(
        make_step("select", {"columns": ["does_not_exist"]}, step_id="pick")
    )
    with pytest.raises(PipelineRunError) as excinfo:
        PipelineRunner().run(pipeline, base)
    error = excinfo.value
    assert error.step_id == "pick"
    assert error.operation == "select"
    assert "pick" in str(error)
    payload = error.to_dict()
    assert payload["step_id"] == "pick"
    assert payload["operation"] == "select"


def test_unknown_operation_fails_at_run_time(base: DuckDBTable) -> None:
    pipeline = Pipeline().add_step(make_step("not_a_real_step", {}))
    with pytest.raises(PipelineRunError, match="not_a_real_step"):
        PipelineRunner().run(pipeline, base)


def test_runner_type_checks(base: DuckDBTable) -> None:
    with pytest.raises(TypeError):
        PipelineRunner().run("nope", base)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        PipelineRunner().run(Pipeline(), "nope")  # type: ignore[arg-type]


def test_step_fingerprints_are_deterministic(base: DuckDBTable) -> None:
    pipeline = simple_pipeline()
    first = PipelineRunner().run(pipeline, base)
    second = PipelineRunner().run(pipeline, base)
    assert [step.fingerprint for step in first.steps] == [
        step.fingerprint for step in second.steps
    ]
    assert all(step.fingerprint.startswith("sha256:") for step in first.steps)


def test_runner_accepts_a_custom_registry(base: DuckDBTable) -> None:
    runner = PipelineRunner(StepRegistry())
    with pytest.raises(PipelineRunError, match="Unknown operation"):
        runner.run(simple_pipeline(), base)


def test_result_metadata_is_json_safe(base: DuckDBTable) -> None:
    result = PipelineRunner().run(simple_pipeline(), base)
    payload = result.to_dict()
    assert payload["rows"] == 3
    assert payload["dataset"] == "sales"
    assert payload["pipeline"] == "Demo"
    assert payload["cache_hits"] == 0
    assert len(payload["steps"]) == 4
    assert payload["schema"]["columns"][0]["name"] == "id"
    assert isinstance(payload["steps"][0]["duration_ms"], float)
