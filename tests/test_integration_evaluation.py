from __future__ import annotations

import copy
import importlib.util
import io
import json
import math
import unittest
from contextlib import redirect_stdout
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "scripts" / "integration_evaluation.py"
SPEC = importlib.util.spec_from_file_location("integration_evaluation", MODULE_PATH)
evaluation = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(evaluation)


class IntegrationEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.receipts_path = (
            PLUGIN_ROOT / "benchmarks" / "integration_evaluation_cases.json"
        )
        self.tasks_path = PLUGIN_ROOT / "benchmarks" / "evaluation_tasks.json"
        self.receipts = json.loads(self.receipts_path.read_text(encoding="utf-8"))
        self.contract = json.loads(self.tasks_path.read_text(encoding="utf-8"))

    def _v2_pair(
        self,
        task_id: str,
        repetition: int,
        *,
        live: bool = False,
        candidate_tokens: int = 80,
    ) -> list[dict[str, object]]:
        task = next(
            item for item in self.contract["tasks"] if item["task_id"] == task_id
        )
        round_value = self.contract["rounds"][task["split"]]
        order = (
            ["baseline", "candidate"] if repetition % 2 else ["candidate", "baseline"]
        )
        shared = {
            "schema_version": 2,
            "case_id": f"{task_id}-{repetition}",
            "task": task["prompt"],
            "task_set_id": self.contract["task_set_id"],
            "task_id": task_id,
            "task_version": task["version"],
            "split": task["split"],
            "repetition": repetition,
            "model": "test-model-version",
            "reasoning_effort": "medium",
            "prompt": task["prompt"],
            "tools": ["shell", "apply_patch"],
            "permissions": ["workspace-write", "network-read"],
            "fixture_id": task["fixture_id"],
            "source_sha256": "a" * 64,
            "randomization_seed": round_value["arm_order"]["seed"],
            "arm_order": order,
            "success": True,
            "critical_errors": [],
            "quality_score": 1.0,
            "elapsed_seconds": 10.0,
            "evidence": ["synthetic gate-contract test evidence"],
            "live_execution": live,
            "independent_tests_passed": True,
            "turns": 2,
            "tool_calls": 3,
            "retries": 0,
        }
        baseline = {
            **shared,
            "variant": "baseline",
            "provider": "codex-base",
            "input_tokens": 80,
            "output_tokens": 20,
            "quality_score": 0.9,
        }
        candidate = {
            **shared,
            "variant": "candidate",
            "provider": "cognitive-powers",
            "input_tokens": candidate_tokens - 20,
            "output_tokens": 20,
            "elapsed_seconds": 9.0,
        }
        return [baseline, candidate]

    def _complete_protocol_receipts(self, *, live: bool = False) -> list[object]:
        receipts: list[object] = []
        for split in ("pilot", "promotion"):
            round_value = self.contract["rounds"][split]
            for task_id in round_value["task_ids"]:
                for repetition in range(1, round_value["repetitions_per_task"] + 1):
                    receipts.extend(self._v2_pair(task_id, repetition, live=live))
        return receipts

    def test_legacy_fixture_and_cli_remain_compatible_without_live_claim(self) -> None:
        report = evaluation.compare(self.receipts, minimum_live_pairs=1)
        self.assertTrue(report["all_quality_gates_passed"])
        self.assertFalse(report["end_to_end_improvement_proven"])
        self.assertEqual(report["live_pairs"], 0)

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = evaluation.main(
                ["--receipts", str(self.receipts_path), "--minimum-live-pairs", "1"]
            )
        self.assertEqual(exit_code, 0)
        self.assertFalse(json.loads(output.getvalue())["end_to_end_improvement_proven"])

    def test_legacy_live_receipts_cannot_bypass_the_versioned_protocol(self) -> None:
        receipts = []
        for index in range(3):
            pair = copy.deepcopy(self.receipts)
            for receipt in pair:
                receipt["case_id"] = f"legacy-live-{index}"
                receipt["live_execution"] = True
            receipts.extend(pair)

        report = evaluation.compare(receipts, minimum_live_pairs=3)

        self.assertEqual(report["live_pairs"], 3)
        self.assertFalse(report["end_to_end_improvement_proven"])
        self.assertIn("versioned", report["reason"])

    def test_non_finite_measurements_are_rejected(self) -> None:
        for field in (
            "quality_score",
            "input_tokens",
            "output_tokens",
            "elapsed_seconds",
        ):
            for value in (math.nan, math.inf, -math.inf, 10**10000):
                with self.subTest(field=field, value=value):
                    receipts = copy.deepcopy(self.receipts)
                    receipts[0][field] = value
                    with self.assertRaisesRegex(evaluation.EvaluationError, field):
                        evaluation.compare(receipts)

    def test_versioned_task_definitions_cover_both_disjoint_five_category_rounds(
        self,
    ) -> None:
        contract = evaluation.validate_task_contract(self.contract)
        self.assertFalse(contract["contains_run_results"])
        self.assertEqual(
            set(contract["tasks"]),
            set(contract["rounds"]["pilot"]["task_ids"])
            | set(contract["rounds"]["promotion"]["task_ids"]),
        )
        self.assertEqual(
            set(contract["rounds"]["pilot"]["task_ids"])
            & set(contract["rounds"]["promotion"]["task_ids"]),
            set(),
        )
        self.assertEqual(
            len(contract["rounds"]["pilot"]["task_ids"])
            * contract["rounds"]["pilot"]["repetitions_per_task"],
            20,
        )
        self.assertEqual(
            len(contract["rounds"]["promotion"]["task_ids"])
            * contract["rounds"]["promotion"]["repetitions_per_task"],
            50,
        )

    def test_locked_model_prompt_tools_permissions_and_source_must_match(self) -> None:
        for field, replacement in (
            ("model", "other-model"),
            ("reasoning_effort", "high"),
            ("prompt", "changed prompt"),
            ("tools", ["shell"]),
            ("permissions", ["workspace-write"]),
            ("fixture_id", "other-fixture"),
            ("source_sha256", "b" * 64),
        ):
            with self.subTest(field=field):
                pair = self._v2_pair("pilot-bug-fix", 1)
                pair[1][field] = replacement
                with self.assertRaisesRegex(evaluation.EvaluationError, field):
                    evaluation.compare(pair, task_contract=self.contract)

    def test_task_binding_and_held_out_split_are_fail_closed(self) -> None:
        pair = self._v2_pair("promotion-research", 1)
        pair[0]["split"] = "pilot"
        pair[1]["split"] = "pilot"
        with self.assertRaisesRegex(
            evaluation.EvaluationError, "split does not match task"
        ):
            evaluation.compare(pair, task_contract=self.contract)

        bad_contract = copy.deepcopy(self.contract)
        bad_contract["rounds"]["promotion"]["task_ids"][0] = "pilot-bug-fix"
        with self.assertRaisesRegex(evaluation.EvaluationError, "must be disjoint"):
            evaluation.validate_task_contract(bad_contract)

    def test_repeated_balanced_protocol_is_required(self) -> None:
        pair = self._v2_pair("promotion-bug-fix", 1)
        report = evaluation.compare(
            pair, minimum_live_pairs=1, task_contract=self.contract
        )
        self.assertTrue(report["all_quality_gates_passed"])
        self.assertFalse(report["protocol"]["complete"])
        self.assertFalse(report["end_to_end_improvement_proven"])
        self.assertIn("complete repeated", report["reason"])

    def test_complete_offline_schedule_never_proves_end_to_end_improvement(
        self,
    ) -> None:
        report = evaluation.compare(
            self._complete_protocol_receipts(),
            minimum_live_pairs=50,
            task_contract=self.contract,
        )
        self.assertTrue(report["protocol"]["complete"])
        self.assertTrue(report["all_quality_gates_passed"])
        self.assertTrue(report["aggregate_efficiency_improved"])
        self.assertEqual(report["live_pairs"], 0)
        self.assertFalse(report["end_to_end_improvement_proven"])

    def test_quality_regression_blocks_efficiency_evaluation(self) -> None:
        receipts = copy.deepcopy(self.receipts)
        receipts[1]["quality_score"] = 0.5
        report = evaluation.compare(receipts, minimum_live_pairs=1)
        pair = report["pairs"][0]
        self.assertFalse(pair["passed"])
        self.assertFalse(pair["efficiency_eligible"])
        self.assertIsNone(pair["efficiency"])
        self.assertFalse(report["efficiency_evaluated"])

    def test_critical_failure_rejects_pair_even_when_tokens_drop(self) -> None:
        receipts = copy.deepcopy(self.receipts)
        receipts[1]["critical_errors"] = ["wrong project scope"]
        report = evaluation.compare(receipts, minimum_live_pairs=1)
        pair = report["pairs"][0]
        self.assertLess(pair["token_delta"], 0)
        self.assertTrue(pair["critical_failure"])
        self.assertEqual(pair["status"], "rejected")
        self.assertIsNone(pair["efficiency"])
        self.assertFalse(report["end_to_end_improvement_proven"])

    def test_unpaired_and_duplicate_repetition_receipts_are_rejected(self) -> None:
        with self.assertRaisesRegex(evaluation.EvaluationError, "not paired"):
            evaluation.compare(self.receipts[:1])

        first = self._v2_pair("pilot-bug-fix", 1)
        duplicate = copy.deepcopy(first)
        for receipt in duplicate:
            receipt["case_id"] = "another-case-id"
        with self.assertRaisesRegex(
            evaluation.EvaluationError, "duplicate task repetition"
        ):
            evaluation.compare(first + duplicate, task_contract=self.contract)


if __name__ == "__main__":
    unittest.main()
