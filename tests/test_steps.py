"""Tests for the step registry, expression compiler and built-in operations."""

from __future__ import annotations

import pandas as pd
import pytest

from data_studio.engine.duckdb_engine import DuckDBEngine, DuckDBTable
from data_studio.kernel.schema import DataType, Role, TableSchema
from data_studio.steps import (
    DEFAULT_REGISTRY,
    StepContext,
    StepDefinition,
    StepError,
    StepParam,
    StepRegistry,
    UnknownStepError,
    build_registry,
)
from data_studio.steps.expr import compile_expression, validate_expression
from data_studio.steps.sql import sql_type


@pytest.fixture
def engine() -> DuckDBEngine:
    instance = DuckDBEngine()
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def context(engine: DuckDBEngine) -> StepContext:
    return StepContext(engine)


@pytest.fixture
def base(engine: DuckDBEngine) -> DuckDBTable:
    return engine.from_records(
        [
            {"id": 1, "region": " North ", "amount": 10.0, "day": "2024-01-02", "qty": 2},
            {"id": 2, "region": "north", "amount": None, "day": "2024-03-04", "qty": 3},
            {"id": 3, "region": "South", "amount": 7.0, "day": "bad", "qty": 1},
            {"id": 3, "region": "South", "amount": 7.0, "day": "bad", "qty": 1},
        ],
        name="base",
    )


def apply(
    context: StepContext,
    table: DuckDBTable,
    operation: str,
    params: dict[str, object] | None = None,
) -> DuckDBTable:
    return DEFAULT_REGISTRY.apply(operation, context, table, params or {})


def values(table: DuckDBTable, column: str) -> list[object]:
    return table.to_pandas()[column].tolist()


def test_registry_exposes_the_phase_one_operations() -> None:
    names = DEFAULT_REGISTRY.names()
    assert len(DEFAULT_REGISTRY) >= 25
    for operation in (
        "filter",
        "select",
        "drop",
        "rename",
        "derive",
        "fill_missing",
        "drop_missing",
        "deduplicate",
        "cast",
        "trim_text",
        "replace_text",
        "date_parse",
        "date_extract",
        "join",
        "union",
        "group_aggregate",
        "pivot",
        "unpivot",
        "sort",
        "sample",
        "window",
    ):
        assert operation in names


def test_build_registry_is_independent() -> None:
    fresh = build_registry()
    assert fresh is not DEFAULT_REGISTRY
    assert fresh.names() == DEFAULT_REGISTRY.names()


def test_registry_catalog_is_json_safe() -> None:
    catalog = DEFAULT_REGISTRY.catalog()
    assert len(catalog) >= 25
    assert all(
        {"name", "title", "category", "description", "params"} <= set(item)
        for item in catalog
    )
    categories = {item["category"] for item in catalog}
    assert {"clean", "transform", "text", "datetime", "aggregate", "join"} <= categories


def test_registry_rejects_unknown_operation() -> None:
    with pytest.raises(UnknownStepError):
        DEFAULT_REGISTRY.get("not_a_step")


def test_registry_rejects_duplicate_registration() -> None:
    registry = StepRegistry()
    definition = StepDefinition(
        name="x",
        title="X",
        category="test",
        description="Test step.",
        compile=lambda *_args: None,  # type: ignore[arg-type,return-value]
    )
    registry.register(definition)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition)


def test_step_param_coercion_and_validation() -> None:
    assert StepParam("c", "columns").coerce("a, b") == ("a", "b")
    assert StepParam("n", "integer").coerce(3) == 3
    assert StepParam("n", "number").coerce(3) == 3.0
    assert StepParam("b", "boolean").coerce(True) is True
    assert StepParam("e", "enum", choices=("a", "b")).coerce("a") == "a"
    with pytest.raises(StepError):
        StepParam("n", "integer").coerce(True)
    with pytest.raises(StepError):
        StepParam("n", "integer").coerce("3")
    with pytest.raises(StepError):
        StepParam("e", "enum", choices=("a",)).coerce("b")
    with pytest.raises(ValueError):
        StepParam("e", "enum")


def test_step_definition_validates_parameters(context: StepContext, base: DuckDBTable) -> None:
    definition = DEFAULT_REGISTRY.get("select")
    with pytest.raises(StepError, match="Missing required"):
        definition.apply(context, base, {})
    with pytest.raises(StepError, match="Unknown parameter"):
        definition.apply(context, base, {"columns": ["id"], "bogus": 1})


def test_unknown_columns_raise(context: StepContext, base: DuckDBTable) -> None:
    with pytest.raises(StepError, match="Unknown column"):
        apply(context, base, "select", {"columns": ["nope"]})


def test_duplicate_columns_raise(context: StepContext, base: DuckDBTable) -> None:
    with pytest.raises(StepError, match="Duplicate"):
        apply(context, base, "select", {"columns": ["id", "id"]})


def test_expression_compiler_basics(base: DuckDBTable) -> None:
    assert compile_expression("amount * 2 + 1", base.schema)
    assert compile_expression("amount > 1 and region == 'North'", base.schema)
    assert compile_expression("if_else(amount > 1, 'big', 'small')", base.schema)
    assert compile_expression("col('amount') * 2", base.schema)
    assert compile_expression("try_cast(day, 'date')", base.schema)
    assert compile_expression("lower(region) == 'north'", base.schema)
    assert compile_expression("id in [1, 2, 3]", base.schema)
    assert compile_expression("amount is None", base.schema)
    assert compile_expression("date('2024-01-01')", base.schema)


@pytest.mark.parametrize(
    "expression",
    [
        "missing_column",
        "unknown_function(amount)",
        "amount.__class__",
        "lower(column='region')",
        "[1, 2][0]",
        "amount if",
        "'x' * 3",
    ],
)
def test_expression_compiler_rejects_unsafe_input(
    base: DuckDBTable, expression: str
) -> None:
    with pytest.raises(StepError):
        compile_expression(expression, base.schema)


def test_validate_expression_accepts_and_rejects(base: DuckDBTable) -> None:
    validate_expression("amount + 1", base.schema)
    with pytest.raises(StepError):
        validate_expression("nope + 1", base.schema)


def test_sql_type_mapping() -> None:
    assert sql_type("integer") == "BIGINT"
    assert sql_type("float") == "DOUBLE"
    assert sql_type("date") == "DATE"
    assert sql_type("Text") == "VARCHAR"
    with pytest.raises(StepError):
        sql_type("money")


def test_drop_missing_any_and_all(context: StepContext, base: DuckDBTable) -> None:
    any_missing = apply(context, base, "drop_missing", {"columns": ["amount"], "how": "any"})
    assert any_missing.count() == 3
    all_missing = apply(
        context, base, "drop_missing", {"columns": ["region", "amount"], "how": "all"}
    )
    assert all_missing.count() == 4


def test_fill_missing_strategies(context: StepContext, base: DuckDBTable) -> None:
    constant = apply(
        context, base, "fill_missing", {"columns": ["amount"], "method": "constant", "value": 0}
    )
    assert None not in values(constant, "amount")

    mean = apply(context, base, "fill_missing", {"columns": ["amount"], "method": "mean"})
    assert values(mean, "amount")[1] == pytest.approx(8.0)

    median = apply(context, base, "fill_missing", {"columns": ["amount"], "method": "median"})
    assert values(median, "amount")[1] == pytest.approx(7.0)

    mode = apply(context, base, "fill_missing", {"columns": ["amount"], "method": "mode"})
    assert values(mode, "amount")[1] == pytest.approx(7.0)

    zero = apply(context, base, "fill_missing", {"columns": ["amount"], "method": "zero"})
    assert values(zero, "amount")[1] == pytest.approx(0.0)


def test_fill_missing_forward_and_backward(
    context: StepContext, engine: DuckDBEngine
) -> None:
    table = engine.from_records(
        [{"id": 1, "v": 10.0}, {"id": 2, "v": None}, {"id": 3, "v": None}, {"id": 4, "v": 7.0}]
    )
    filled = apply(
        context,
        table,
        "fill_missing",
        {"columns": ["v"], "method": "ffill", "order_by": ["id"]},
    )
    assert {row["id"]: row["v"] for row in filled.collect().to_records()} == {
        1: 10.0,
        2: 10.0,
        3: 10.0,
        4: 7.0,
    }
    backfilled = apply(
        context,
        table,
        "fill_missing",
        {"columns": ["v"], "method": "bfill", "order_by": ["id"]},
    )
    assert {row["id"]: row["v"] for row in backfilled.collect().to_records()} == {
        1: 10.0,
        2: 7.0,
        3: 7.0,
        4: 7.0,
    }


def test_deduplicate_keeps_one_row_per_key(
    context: StepContext, base: DuckDBTable
) -> None:
    deduped = apply(context, base, "deduplicate", {"columns": ["id"]})
    assert deduped.count() == 3
    assert sorted(values(deduped, "id")) == [1, 2, 3]


def test_deduplicate_keep_last_uses_order(
    context: StepContext, engine: DuckDBEngine
) -> None:
    table = engine.from_records(
        [{"id": 1, "v": "old"}, {"id": 1, "v": "new"}, {"id": 2, "v": "x"}]
    )
    deduped = apply(
        context,
        table,
        "deduplicate",
        {"columns": ["id"], "keep": "last", "order_by": ["v"]},
    )
    records = {row["id"]: row["v"] for row in deduped.collect().to_records()}
    assert records == {1: "old", 2: "x"}


def test_flag_outliers_iqr_and_zscore(
    context: StepContext, engine: DuckDBEngine
) -> None:
    table = engine.from_records([{"v": 1}, {"v": 2}, {"v": 3}, {"v": 4}, {"v": 100}])
    iqr = apply(context, table, "flag_outliers", {"column": "v", "method": "iqr"})
    assert values(iqr, "v_outlier")[-1] is True
    zscore = apply(
        context, table, "flag_outliers", {"column": "v", "method": "zscore", "threshold": 1.5}
    )
    assert values(zscore, "v_outlier")[-1] is True


def test_flag_outliers_rejects_non_numeric(
    context: StepContext, base: DuckDBTable
) -> None:
    with pytest.raises(StepError, match="numeric"):
        apply(context, base, "flag_outliers", {"column": "region"})


def test_select_drop_and_rename(context: StepContext, base: DuckDBTable) -> None:
    selected = apply(context, base, "select", {"columns": ["id", "amount"]})
    assert selected.schema.names == ("id", "amount")
    dropped = apply(context, base, "drop", {"columns": ["day", "qty"]})
    assert dropped.schema.names == ("id", "region", "amount")
    renamed = apply(context, base, "rename", {"columns": {"region": "area"}})
    assert "area" in renamed.schema
    assert "region" not in renamed.schema


def test_drop_cannot_empty_the_table(context: StepContext, base: DuckDBTable) -> None:
    with pytest.raises(StepError, match="empty"):
        apply(context, base, "drop", {"columns": list(base.schema.names)})


def test_rename_rejects_collisions(context: StepContext, base: DuckDBTable) -> None:
    with pytest.raises(StepError, match="duplicate"):
        apply(context, base, "rename", {"columns": {"region": "amount"}})


def test_cast_updates_type_and_role(context: StepContext, base: DuckDBTable) -> None:
    casted = apply(
        context,
        base,
        "cast",
        {"column": "day", "dtype": "date", "try_cast": True, "output": "day_date"},
    )
    assert casted.schema.column("day_date").dtype is DataType.DATE
    assert values(casted, "day_date")[0] is not None
    assert pd.isna(values(casted, "day_date")[2])


def test_derive_and_filter(context: StepContext, base: DuckDBTable) -> None:
    derived = apply(
        context, base, "derive", {"column": "double", "expression": "amount * 2"}
    )
    assert derived.schema.column("double").dtype is DataType.FLOAT
    filtered = apply(context, derived, "filter", {"expression": "double > 10"})
    assert filtered.count() == 3


def test_derive_rejects_existing_column_without_replace(
    context: StepContext, base: DuckDBTable
) -> None:
    with pytest.raises(StepError, match="replace"):
        apply(context, base, "derive", {"column": "amount", "expression": "1"})
    replaced = apply(
        context, base, "derive", {"column": "amount", "expression": "1", "replace": True}
    )
    assert set(values(replaced, "amount")) == {1.0}


def test_sort_and_distinct(context: StepContext, base: DuckDBTable) -> None:
    sorted_table = apply(context, base, "sort", {"columns": ["amount"], "descending": True})
    assert values(sorted_table, "amount")[0] == pytest.approx(10.0)
    distinct = apply(context, base, "distinct")
    assert distinct.count() == 3
    distinct_regions = apply(context, base, "distinct", {"columns": ["region"]})
    assert distinct_regions.count() == 3


def test_text_trim_replace_and_case(context: StepContext, base: DuckDBTable) -> None:
    trimmed = apply(context, base, "trim_text", {"columns": ["region"]})
    assert values(trimmed, "region")[0] == "North"
    lowered = apply(context, base, "normalize_text", {"columns": ["region"], "case": "lower"})
    assert values(lowered, "region")[0] == " north "
    upper = apply(context, base, "normalize_text", {"columns": ["region"], "case": "upper"})
    assert values(upper, "region")[0] == " NORTH "
    title = apply(context, base, "normalize_text", {"columns": ["region"], "case": "title"})
    assert values(title, "region")[0] == " North "


def test_text_replace_extract_split_and_concat(
    context: StepContext, base: DuckDBTable
) -> None:
    replaced = apply(
        context,
        base,
        "replace_text",
        {"column": "region", "pattern": "South", "replacement": "S", "regex": False},
    )
    assert "S" in values(replaced, "region")

    extracted = apply(
        context,
        base,
        "extract_text",
        {"column": "day", "pattern": r"(\d{4})", "output": "year"},
    )
    assert values(extracted, "year")[0] == "2024"

    split = apply(
        context,
        base,
        "split_text",
        {"column": "day", "delimiter": "-", "index": 1, "output": "year_part"},
    )
    assert values(split, "year_part")[0] == "2024"

    concatenated = apply(
        context,
        base,
        "concat_columns",
        {"columns": ["region", "day"], "column": "label", "separator": "|"},
    )
    assert "|" in values(concatenated, "label")[0]


def test_split_text_rejects_zero_index(context: StepContext, base: DuckDBTable) -> None:
    with pytest.raises(StepError, match="1-based"):
        apply(context, base, "split_text", {"column": "day", "delimiter": "-", "index": 0})


def test_datetime_steps(context: StepContext, base: DuckDBTable) -> None:
    parsed = apply(
        context,
        base,
        "date_parse",
        {"column": "day", "format": "%Y-%m-%d", "output": "day_ts"},
    )
    assert parsed.schema.column("day_ts").dtype is DataType.DATETIME
    assert pd.isna(values(parsed, "day_ts")[2])

    extracted = apply(
        context, parsed, "date_extract", {"column": "day_ts", "part": "year"}
    )
    assert values(extracted, "day_ts_year")[0] == 2024

    weekday = apply(
        context, parsed, "date_extract", {"column": "day_ts", "part": "weekday"}
    )
    assert values(weekday, "day_ts_weekday")[0] == "Tuesday"

    truncated = apply(
        context, parsed, "date_trunc", {"column": "day_ts", "unit": "month"}
    )
    assert values(truncated, "day_ts_month")[0].day == 1

    shifted = apply(
        context,
        parsed,
        "date_shift",
        {"column": "day_ts", "amount": 3, "unit": "day"},
    )
    assert values(shifted, "day_ts_shifted")[0].day == 5


def test_date_parse_accepts_an_already_temporal_column(
    context: StepContext, base: DuckDBTable
) -> None:
    casted = apply(
        context,
        base,
        "cast",
        {"column": "day", "dtype": "date", "try_cast": True, "output": "day_date"},
    )
    parsed = apply(
        context,
        casted,
        "date_parse",
        {"column": "day_date", "format": "%Y-%m-%d", "output": "day_ts"},
    )
    assert parsed.schema.column("day_ts").dtype is DataType.DATETIME
    assert values(parsed, "day_ts")[0] is not None
    assert pd.isna(values(parsed, "day_ts")[2])


def test_group_aggregate_and_value_counts(
    context: StepContext, base: DuckDBTable
) -> None:
    grouped = apply(
        context,
        base,
        "group_aggregate",
        {
            "group_by": ["region"],
            "aggregations": [
                {"column": "amount", "function": "sum", "output": "total"},
                {"column": "*", "function": "count", "output": "n"},
            ],
        },
    )
    assert {"region", "total", "n"} <= set(grouped.schema.names)
    assert grouped.count() == 3

    counts = apply(context, base, "value_counts", {"columns": ["region"]})
    assert set(counts.schema.names) == {"region", "count", "percent"}
    assert counts.count() == 3


def test_group_aggregate_rejects_bad_input(
    context: StepContext, base: DuckDBTable
) -> None:
    with pytest.raises(StepError, match="aggregation"):
        apply(context, base, "group_aggregate", {"group_by": [], "aggregations": []})
    with pytest.raises(StepError, match="Unsupported aggregate"):
        apply(
            context,
            base,
            "group_aggregate",
            {"group_by": ["region"], "aggregations": [{"column": "amount", "function": "wat"}]},
        )


def test_join_left_and_suffix(engine: DuckDBEngine) -> None:
    left = engine.from_records(
        [{"id": 1, "region": "North"}, {"id": 2, "region": "South"}, {"id": 3, "region": "West"}]
    )
    right = engine.from_records(
        [{"id": 1, "zone": "N"}, {"id": 2, "zone": "S"}], name="zones"
    )
    context = StepContext(engine, datasets={"zones": right})
    joined = apply(
        context,
        left,
        "join",
        {"dataset": "zones", "how": "left", "left_on": ["id"], "right_on": ["id"]},
    )
    assert joined.count() == 3
    assert joined.schema.names == ("id", "region", "zone")
    assert joined.collect().to_records()[0]["zone"] == "N"


def test_join_cardinality_check_fails(engine: DuckDBEngine) -> None:
    left = engine.from_records([{"id": 1}])
    right = engine.from_records([{"id": 1}, {"id": 1}], name="dupes")
    context = StepContext(engine, datasets={"dupes": right})
    with pytest.raises(StepError, match="cardinality"):
        apply(
            context,
            left,
            "join",
            {
                "dataset": "dupes",
                "how": "left",
                "left_on": ["id"],
                "right_on": ["id"],
                "cardinality": "one_to_one",
            },
        )


def test_union(engine: DuckDBEngine) -> None:
    left = engine.from_records([{"id": 1}, {"id": 2}])
    right = engine.from_records([{"id": 3}], name="more")
    context = StepContext(engine, datasets={"more": right})
    unioned = apply(context, left, "union", {"datasets": ["more"], "all": True})
    assert unioned.count() == 3


def test_union_rejects_column_mismatch(engine: DuckDBEngine) -> None:
    left = engine.from_records([{"id": 1}])
    right = engine.from_records([{"other": 2}], name="other")
    context = StepContext(engine, datasets={"other": right})
    with pytest.raises(StepError, match="mismatch"):
        apply(context, left, "union", {"datasets": ["other"]})


def test_pivot_and_unpivot(context: StepContext, engine: DuckDBEngine) -> None:
    table = engine.from_records(
        [
            {"id": 1, "kind": "a", "value": 10},
            {"id": 1, "kind": "b", "value": 20},
            {"id": 2, "kind": "a", "value": 30},
        ]
    )
    pivoted = apply(
        context,
        table,
        "pivot",
        {"index": ["id"], "columns": "kind", "values": "value", "aggregate": "sum"},
    )
    assert pivoted.count() == 2
    assert len(pivoted.schema) >= 3

    unpivoted = apply(
        context,
        engine.from_records([{"id": 1, "a": 10, "b": 20}]),
        "unpivot",
        {"keep": ["id"]},
    )
    assert unpivoted.count() == 2
    assert set(unpivoted.schema.names) == {"id", "variable", "value"}


def test_window_functions(context: StepContext, engine: DuckDBEngine) -> None:
    table = engine.from_records(
        [{"id": 1, "v": 1.0}, {"id": 2, "v": 2.0}, {"id": 3, "v": 3.0}, {"id": 4, "v": 4.0}]
    )
    lagged = apply(
        context, table, "window", {"function": "lag", "column": "v", "order_by": ["id"]}
    )
    assert [row["lag_v"] for row in lagged.collect().to_records()] == [None, 1.0, 2.0, 3.0]

    led = apply(
        context, table, "window", {"function": "lead", "column": "v", "order_by": ["id"]}
    )
    assert [row["lead_v"] for row in led.collect().to_records()] == [2.0, 3.0, 4.0, None]

    numbered = apply(context, table, "window", {"function": "row_number", "order_by": ["id"]})
    assert values(numbered, "row_number") == [1, 2, 3, 4]

    cumulative = apply(
        context, table, "window", {"function": "cumsum", "column": "v", "order_by": ["id"]}
    )
    assert values(cumulative, "cumsum_v") == [1.0, 3.0, 6.0, 10.0]

    rolling = apply(
        context,
        table,
        "window",
        {"function": "rolling_sum", "column": "v", "order_by": ["id"], "window": 2},
    )
    assert values(rolling, "rolling_sum_v") == [1.0, 3.0, 5.0, 7.0]


def test_window_requires_order(context: StepContext, base: DuckDBTable) -> None:
    with pytest.raises(StepError, match="order_by"):
        apply(context, base, "window", {"function": "row_number"})


def test_sample_and_limit(context: StepContext, base: DuckDBTable) -> None:
    sampled = apply(context, base, "sample", {"rows": 2})
    assert sampled.count() == 2
    full = apply(context, base, "sample", {"fraction": 1.0})
    assert full.count() == 4
    limited = apply(context, base, "limit", {"n": 2, "offset": 1})
    assert limited.count() == 2
    with pytest.raises(StepError):
        apply(context, base, "sample", {"rows": 2, "fraction": 0.5})
    with pytest.raises(StepError):
        apply(context, base, "sample", {})
    with pytest.raises(StepError):
        apply(context, base, "sample", {"fraction": 0})
    with pytest.raises(StepError):
        apply(context, base, "limit", {"n": -1})


def test_step_context_unknown_dataset() -> None:
    with pytest.raises(StepError, match="Unknown dataset"):
        StepContext(DuckDBEngine()).dataset("missing")


def test_schema_role_survives_selection(base: DuckDBTable) -> None:
    selected = base.schema.select("id", "amount")
    assert selected.column("id").role is Role.IDENTIFIER
    assert selected.column("amount").role is Role.MEASURE
    assert isinstance(selected, TableSchema)
