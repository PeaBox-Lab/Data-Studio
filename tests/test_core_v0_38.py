from __future__ import annotations

import unittest

import pandas as pd

from data_studio_core_v0_38 import (
    calculate_deployment_health,
    infer_validation_rules,
    promotion_readiness,
    safe_math_eval,
    validate_frame,
    workspace_health,
)


class CoreTests(unittest.TestCase):
    def test_healthy_deployment(self) -> None:
        result = calculate_deployment_health(
            {
                "api_error_rate": 0.0,
                "drift_alerts": 0,
                "failed_jobs": 0,
                "ci_passed": True,
                "contracts_passed": True,
            }
        )
        self.assertEqual(result.score, 100)
        self.assertEqual(result.status, "HEALTHY")
        self.assertFalse(result.rollback_recommended)

    def test_rollback_thresholds(self) -> None:
        result = calculate_deployment_health({"api_error_rate": 0.05, "drift_alerts": 2})
        self.assertTrue(result.rollback_recommended)
        self.assertIn("API error rate", " ".join(result.reasons))

    def test_prod_gate(self) -> None:
        ready, blockers = promotion_readiness("PROD", True, False, True)
        self.assertFalse(ready)
        self.assertEqual(len(blockers), 1)

    def test_rules(self) -> None:
        frame = pd.DataFrame({"id": [1, 2, 3], "status": ["A", "B", "A"]})
        rules = infer_validation_rules(frame)
        results = validate_frame(frame, rules)
        self.assertTrue(rules)
        self.assertTrue(all(item["status"] == "PASS" for item in results))

    def test_workspace_health(self) -> None:
        result = workspace_health(
            {
                "datasets": {"x": 1},
                "pipeline_definitions": {},
                "contract_test_runs": [],
                "cicd_runs": [],
                "release_gate_runs": [],
                "current_project_path": None,
            }
        )
        self.assertEqual(result["score"], 50)

    def test_safe_expression(self) -> None:
        import numpy as np

        self.assertEqual(safe_math_eval("x * 2 + 1", {"x": 3}, np), 7)
        with self.assertRaises(ValueError):
            safe_math_eval("__import__('os').system('echo unsafe')", {}, np)


if __name__ == "__main__":
    unittest.main()
