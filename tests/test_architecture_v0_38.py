from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from data_studio_diagnostics import DiagnosticLog
from data_studio_jobs import dependency_status, execute_with_retry, next_schedule_time
from data_studio_state import migrate_project, validate_project
from data_studio_tasks import TaskManager


class ArchitectureTests(unittest.TestCase):
    def test_migrate_v037(self) -> None:
        payload, changes = migrate_project(
            {"version": "0.37", "datasets": {"x": 1}, "active_dataset": "x"}
        )
        self.assertEqual(payload["version"], "0.38")
        self.assertIn("diagnostic_events", payload)
        self.assertFalse(validate_project(payload))
        self.assertTrue(changes)

    def test_repair_active_dataset(self) -> None:
        payload, _ = migrate_project(
            {"version": "0.36", "datasets": {"x": 1}, "active_dataset": "missing"}
        )
        self.assertEqual(payload["active_dataset"], "x")

    def test_diagnostics_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            log = DiagnosticLog([], path)
            event = log.info("test", "run", "hello", value=1)
            self.assertTrue(path.exists())
            self.assertIn(event["correlation_id"], path.read_text())

    def test_shared_dependency(self) -> None:
        ok, blocked = dependency_status(
            {"depends_on": ["a"]}, {"a": {"last_status": "failed"}}
        )
        self.assertFalse(ok)
        self.assertTrue(blocked)

    def test_shared_schedule(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        self.assertEqual(next_schedule_time("Daily", now).day, 2)

    def test_retry(self) -> None:
        calls: list[int] = []

        def action() -> int:
            calls.append(1)
            if len(calls) < 2:
                raise ValueError("retry")
            return 7

        value, attempts, error = execute_with_retry(action, 1, 0)
        self.assertEqual(value, 7)
        self.assertEqual(len(attempts), 2)
        self.assertIsNone(error)

    def test_background_task(self) -> None:
        manager = TaskManager(1)

        def work(progress, cancel_event) -> int:  # type: ignore[no-untyped-def]
            progress(50, "half")
            return 42

        record, future = manager.submit("work", work)
        self.assertEqual(future.result(timeout=5), 42)
        self.assertEqual(record.status, "completed")
        manager.shutdown()


if __name__ == "__main__":
    unittest.main()
