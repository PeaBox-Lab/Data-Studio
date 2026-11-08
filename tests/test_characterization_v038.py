"""Characterization tests for the pure legacy v0.38 modules.

The desktop monolith is frozen, but these modules are still the reference for
scheduling, task execution, governance and diagnostics behaviour. These tests pin the
behaviour the new kernel must preserve while it is migrated behind contracts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data_studio_core_v0_38 import (
    DeploymentHealth,
    calculate_deployment_health,
    infer_validation_rules,
    promotion_readiness,
    safe_math_eval,
    validate_dataframe_expression,
    validate_expression,
    validate_frame,
    workspace_health,
)
from data_studio_diagnostics_v0_38 import DiagnosticLog
from data_studio_jobs_v0_38 import (
    dependency_status,
    execute_with_retry,
    next_schedule_time,
    parse_utc,
)
from data_studio_state_v0_38 import CURRENT_VERSION, migrate_project, validate_project
from data_studio_tasks_v0_38 import TaskManager


class TestProjectState:
    def test_migrate_fills_defaults_and_repairs_active_dataset(self) -> None:
        payload, changes = migrate_project(
            {"version": "0.37", "datasets": {"x": {}}, "active_dataset": "missing"}
        )
        assert payload["version"] == CURRENT_VERSION
        assert payload["project_schema_version"] == 1
        assert payload["active_dataset"] == "x"
        assert changes[0] == "migrated project 0.37 to 0.38"
        assert any(change.startswith("initialized ") for change in changes)
        assert validate_project(payload) == []

    def test_migrate_is_idempotent(self) -> None:
        first, _ = migrate_project({"datasets": {"x": {}}, "active_dataset": "x"})
        second, changes = migrate_project(first)
        assert second == first
        assert changes == []

    def test_migrate_rejects_non_mapping(self) -> None:
        with pytest.raises(ValueError, match="dictionary"):
            migrate_project([])  # type: ignore[arg-type]

    def test_validate_project_reports_structural_problems(self) -> None:
        assert validate_project(None) == ["payload is not a dictionary"]  # type: ignore[arg-type]
        assert "project contains no datasets" in validate_project(
            {"datasets": {}}
        )
        assert "active_dataset is not present in datasets" in validate_project(
            {"datasets": {"x": {}}, "active_dataset": "y"}
        )
        errors = validate_project(
            {
                "datasets": {"x": {}},
                "active_dataset": "x",
                "scheduled_jobs": [],
                "job_runs": {},
                "environments": [],
            }
        )
        assert "scheduled_jobs must be a dictionary" in errors
        assert "job_runs must be a list" in errors
        assert "environments must be a dictionary" in errors

    def test_validate_project_can_skip_dataset_requirement(self) -> None:
        assert validate_project({"datasets": {}}, require_datasets=False) == []


class TestSchedulesAndRetries:
    def test_parse_utc_normalises_timestamps(self) -> None:
        assert parse_utc(None) is None
        assert parse_utc("") is None
        assert parse_utc("2026-01-01T12:00:00") == datetime(
            2026, 1, 1, 12, tzinfo=UTC
        )
        assert parse_utc("2026-01-01T12:00:00Z") == datetime(
            2026, 1, 1, 12, tzinfo=UTC
        )
        assert parse_utc("2026-01-01T12:00:00+02:00") == datetime(
            2026, 1, 1, 10, tzinfo=UTC
        )

    @pytest.mark.parametrize(
        ("schedule", "delta"),
        [
            ("Hourly", timedelta(hours=1)),
            ("Daily", timedelta(days=1)),
            ("Weekly", timedelta(days=7)),
        ],
    )
    def test_fixed_schedules_advance(self, schedule: str, delta: timedelta) -> None:
        anchor = datetime(2026, 1, 1, tzinfo=UTC)
        assert next_schedule_time(schedule, anchor) == anchor + delta

    def test_monthly_schedule_clamps_to_last_day(self) -> None:
        january = datetime(2026, 1, 31, tzinfo=UTC)
        assert next_schedule_time("Monthly", january) == datetime(
            2026, 2, 28, tzinfo=UTC
        )
        december = datetime(2026, 12, 31, tzinfo=UTC)
        assert next_schedule_time("Monthly", december) == datetime(
            2027, 1, 31, tzinfo=UTC
        )

    def test_unknown_schedule_has_no_next_time(self) -> None:
        assert next_schedule_time("Fortnightly", datetime.now(UTC)) is None

    def test_dependency_status(self) -> None:
        assert dependency_status({}, {}) == (True, [])
        assert dependency_status({"depends_on": ["a"]}, {"a": {"last_status": "success"}}) == (
            True,
            [],
        )
        ok, blocked = dependency_status(
            {"depends_on": ["a", "b", "c"]},
            {"a": {"last_status": "failed"}, "b": {"last_status": "success"}},
        )
        assert ok is False
        assert blocked == ["a: failed", "c: missing"]
        ok, blocked = dependency_status({"depends_on": ["a"]}, {"a": {}})
        assert ok is False
        assert blocked == ["a: never run"]

    def test_execute_with_retry_succeeds_after_failures(self) -> None:
        calls: list[int] = []

        def action() -> int:
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("retry")
            return 7

        value, attempts, error = execute_with_retry(action, max_retries=2, retry_delay_seconds=0)
        assert value == 7
        assert error is None
        assert [attempt["status"] for attempt in attempts] == [
            "failed",
            "failed",
            "success",
        ]
        assert all("duration_seconds" in attempt for attempt in attempts)

    def test_execute_with_retry_returns_last_error(self) -> None:
        sleeps: list[float] = []

        def action() -> None:
            raise RuntimeError("nope")

        value, attempts, error = execute_with_retry(
            action, max_retries=1, retry_delay_seconds=0.5, sleep=sleeps.append
        )
        assert value is None
        assert error == "nope"
        assert len(attempts) == 2
        assert sleeps == [0.5]

    def test_execute_with_retry_negative_retries_still_runs_once(self) -> None:
        value, attempts, error = execute_with_retry(lambda: 3, max_retries=-5)
        assert (value, error) == (3, None)
        assert len(attempts) == 1


class TestTaskManager:
    def test_task_success_reports_progress(self) -> None:
        manager = TaskManager(max_workers=1)
        try:
            def work(progress, cancel_event) -> int:  # type: ignore[no-untyped-def]
                progress(50, "half")
                return 42

            record, future = manager.submit("work", work)
            assert future.result(timeout=5) == 42
            assert record.status == "completed"
            assert record.progress == 100
            assert record.message == "half"
            assert record.started_utc is not None
            assert record.finished_utc is not None
        finally:
            manager.shutdown()

    def test_task_failure_records_error(self) -> None:
        manager = TaskManager(max_workers=1)
        try:
            def work(**_: object) -> None:
                raise ValueError("boom")

            record, future = manager.submit("work", work)
            with pytest.raises(ValueError, match="boom"):
                future.result(timeout=5)
            assert record.status == "failed"
            assert record.error == "boom"
        finally:
            manager.shutdown()

    def test_cancel_marks_a_cooperative_task_cancelled(self) -> None:
        manager = TaskManager(max_workers=1)
        try:
            def work(progress, cancel_event) -> int:  # type: ignore[no-untyped-def]
                cancel_event.wait(timeout=5)
                return 7

            record, future = manager.submit("work", work)
            assert manager.cancel(record.id) is True
            assert future.result(timeout=5) == 7
            assert record.status == "cancelled"
        finally:
            manager.shutdown()

    def test_cancel_unknown_task_returns_false(self) -> None:
        manager = TaskManager(max_workers=1)
        try:
            assert manager.cancel("missing") is False
        finally:
            manager.shutdown()


class TestGovernance:
    def test_healthy_deployment_by_default(self) -> None:
        health = calculate_deployment_health({})
        assert isinstance(health, DeploymentHealth)
        assert health.score == 100
        assert health.status == "HEALTHY"
        assert health.rollback_recommended is False
        assert health.reasons == ()

    def test_catastrophic_deployment_recommends_rollback(self) -> None:
        health = calculate_deployment_health(
            {
                "api_error_rate": 1.0,
                "drift_alerts": 3,
                "critical_drift_alerts": 2,
                "failed_jobs": 5,
                "ci_passed": False,
                "contracts_passed": False,
            }
        )
        assert health.score == 0
        assert health.status == "UNHEALTHY"
        assert health.rollback_recommended is True
        reasons = " ".join(health.reasons)
        assert "API error rate" in reasons
        assert "drift alerts" in reasons
        assert "CI evidence failed" in reasons
        assert "contract-test evidence failed" in reasons
        assert isinstance(health.to_dict()["reasons"], list)

    def test_thresholds_are_configurable(self) -> None:
        health = calculate_deployment_health(
            {"failed_jobs": 1}, {"healthy": 95, "degraded": 80}
        )
        assert health.score == 95
        assert health.status == "HEALTHY"

    def test_promotion_readiness_only_gates_production(self) -> None:
        assert promotion_readiness("PROD", True, True, True) == (True, [])
        ready, blockers = promotion_readiness("prod", False, False, False)
        assert ready is False
        assert len(blockers) == 3
        assert promotion_readiness("DEV", False, False, False) == (True, [])
        assert promotion_readiness("TEST", False, False, False) == (True, [])

    def test_infer_and_validate_rules(self) -> None:
        frame = pd.DataFrame(
            {
                "id": [1, 2, 3],
                "status": ["A", "B", "A"],
                "amount": [1.0, 2.0, None],
            }
        )
        rules = infer_validation_rules(frame)
        assert {"column": "id", "type": "not_null"} in rules
        assert {"column": "id", "type": "unique"} in rules
        assert any(rule["column"] == "id" and rule["type"] == "range" for rule in rules)
        assert any(rule["column"] == "status" and rule["type"] == "domain" for rule in rules)
        assert not any(rule["column"] == "amount" and rule["type"] == "not_null" for rule in rules)

        results = validate_frame(frame, rules)
        assert results
        assert all(result["status"] == "PASS" for result in results)

        mutated = frame.copy()
        mutated.loc[0, "status"] = "Z"
        results = validate_frame(mutated, rules)
        domain = next(result for result in results if result["rule"] == "domain")
        assert domain["status"] == "FAIL"
        assert domain["violations"] == 1

    def test_validate_frame_reports_missing_columns_and_unknown_rules(self) -> None:
        frame = pd.DataFrame({"id": [1, 2, 3]})
        results = validate_frame(
            frame,
            [
                {"column": "ghost", "type": "not_null"},
                {"column": "id", "type": "made_up"},
            ],
        )
        assert results[0]["status"] == "FAIL"
        assert results[0]["violations"] == 3
        assert results[0]["message"] == "column missing"
        assert results[1]["status"] == "SKIP"
        assert results[1]["message"] == "unsupported rule"

    def test_workspace_health_explains_deductions(self) -> None:
        empty = workspace_health({})
        assert empty["score"] == 10
        assert empty["status"] == "AT RISK"
        assert len(empty["findings"]) == 6

        healthy = workspace_health(
            {
                "datasets": {"x": {}},
                "pipeline_definitions": {"p": {}},
                "contract_test_runs": [{}],
                "cicd_runs": [{}],
                "release_gate_runs": [{}],
                "current_project_path": "x",
            }
        )
        assert healthy == {"score": 100, "status": "READY", "findings": []}


class TestSafeExpressions:
    def test_arithmetic_is_allowed(self) -> None:
        validate_expression("amount * 2 + 1", {"amount"})
        assert safe_math_eval("x * 2 + 1", {"x": 3}, np) == 7

    def test_unknown_names_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown expression name"):
            validate_expression("missing + 1", {"amount"})

    def test_only_approved_numpy_attributes_are_allowed(self) -> None:
        validate_expression("np.sqrt(x) + 1", {"x"})
        with pytest.raises(ValueError, match="approved NumPy"):
            validate_expression("np.__class__", {"x"})
        with pytest.raises(ValueError, match="approved NumPy"):
            validate_expression("x.mean()", {"x"})

    def test_dataframe_expressions_disable_numpy(self) -> None:
        with pytest.raises(ValueError, match="approved NumPy"):
            validate_dataframe_expression("np.sqrt(x)", ["x"])

    def test_python_internals_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            safe_math_eval("__import__('os').system('echo unsafe')", {}, np)


class TestDiagnosticLog:
    def test_events_are_levelled_and_appended_to_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "logs" / "events.jsonl"
        log = DiagnosticLog([], path)
        info = log.info("engine", "run", "started", value=1)
        warning = log.warning("engine", "run", "slow")
        error = log.error("engine", "run", "failed")

        assert [info["level"], warning["level"], error["level"]] == [
            "INFO",
            "WARNING",
            "ERROR",
        ]
        assert info["correlation_id"] != error["correlation_id"]
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3

    def test_event_buffer_is_bounded(self) -> None:
        log = DiagnosticLog([], max_events=2)
        log.info("a", "b", "one")
        log.info("a", "b", "two")
        log.info("a", "b", "three")
        assert [event["message"] for event in log.events] == ["two", "three"]
