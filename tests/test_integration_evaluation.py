from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "scripts" / "integration_evaluation.py"
SPEC = importlib.util.spec_from_file_location("integration_evaluation", MODULE_PATH)
evaluation = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(evaluation)


class IntegrationEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.receipts = json.loads(
            (
                PLUGIN_ROOT / "benchmarks" / "integration_evaluation_cases.json"
            ).read_text(encoding="utf-8")
        )

    def test_offline_contract_can_pass_without_claiming_end_to_end_improvement(
        self,
    ) -> None:
        report = evaluation.compare(self.receipts, minimum_live_pairs=1)
        self.assertTrue(report["all_quality_gates_passed"])
        self.assertFalse(report["end_to_end_improvement_proven"])
        self.assertEqual(report["live_pairs"], 0)

    def test_quality_regression_blocks_pair(self) -> None:
        self.receipts[1]["quality_score"] = 0.5
        report = evaluation.compare(self.receipts, minimum_live_pairs=1)
        self.assertFalse(report["pairs"][0]["passed"])
        self.assertFalse(report["end_to_end_improvement_proven"])

    def test_critical_error_blocks_pair_even_when_tokens_drop(self) -> None:
        self.receipts[1]["critical_errors"] = ["wrong project scope"]
        report = evaluation.compare(self.receipts, minimum_live_pairs=1)
        self.assertLess(report["pairs"][0]["token_delta"], 0)
        self.assertFalse(report["pairs"][0]["passed"])

    def test_unpaired_receipt_is_rejected(self) -> None:
        with self.assertRaisesRegex(evaluation.EvaluationError, "not paired"):
            evaluation.compare(self.receipts[:1])


if __name__ == "__main__":
    unittest.main()
