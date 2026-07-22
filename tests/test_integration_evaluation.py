from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import math
import tempfile
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
            60,
        )
        self.assertEqual(
            len(contract["rounds"]["promotion"]["task_ids"])
            * contract["rounds"]["promotion"]["repetitions_per_task"],
            180,
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
                pair = self._v2_pair("controller-pilot-solo-bug-fix-01", 1)
                pair[1][field] = replacement
                with self.assertRaisesRegex(evaluation.EvaluationError, field):
                    evaluation.compare(pair, task_contract=self.contract)

    def test_task_binding_and_held_out_split_are_fail_closed(self) -> None:
        pair = self._v2_pair(
            "controller-promotion-parallel-read-only-current-source-research-01", 1
        )
        pair[0]["split"] = "pilot"
        pair[1]["split"] = "pilot"
        with self.assertRaisesRegex(
            evaluation.EvaluationError, "split does not match task"
        ):
            evaluation.compare(pair, task_contract=self.contract)

        bad_contract = copy.deepcopy(self.contract)
        bad_contract["rounds"]["promotion"]["task_ids"][0] = (
            "controller-pilot-solo-bug-fix-01"
        )
        with self.assertRaisesRegex(evaluation.EvaluationError, "must be disjoint"):
            evaluation.validate_task_contract(bad_contract)

    def test_repeated_balanced_protocol_is_required(self) -> None:
        pair = self._v2_pair("controller-promotion-solo-bug-fix-01", 1)
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
        self.assertIsNone(pair["token_delta"])
        self.assertIsNone(pair["token_reduction_ratio"])
        self.assertTrue(pair["critical_failure"])
        self.assertEqual(pair["status"], "rejected")
        self.assertIsNone(pair["efficiency"])
        self.assertFalse(report["end_to_end_improvement_proven"])

    def test_cached_and_fresh_input_are_validated_and_reported(self) -> None:
        pair = self._v2_pair("controller-pilot-solo-bug-fix-01", 1)
        for receipt in pair:
            receipt["cached_input_tokens"] = 30
            receipt["fresh_input_tokens"] = receipt["input_tokens"] - 30
        report = evaluation.compare(pair, task_contract=self.contract)
        self.assertEqual(report["pairs"][0]["fresh_input_ratio"], 0.6)

        pair[1]["fresh_input_tokens"] = 1
        with self.assertRaisesRegex(evaluation.EvaluationError, "must sum"):
            evaluation.compare(pair, task_contract=self.contract)

    def test_live_v2_receipts_fail_closed_without_experiment_identity(self) -> None:
        pair = self._v2_pair("controller-pilot-solo-bug-fix-01", 1, live=True)
        with self.assertRaisesRegex(evaluation.EvaluationError, "frozen identity"):
            evaluation.compare(pair, task_contract=self.contract)

    def test_live_receipts_bind_the_frozen_controller_protocol(self) -> None:
        identity = evaluation.load_controller_protocol(
            PLUGIN_ROOT / "benchmarks" / "controller_ab_protocol.json"
        )
        pair = self._v2_pair("controller-pilot-solo-bug-fix-01", 1, live=True)
        for receipt, mode in zip(pair, ("forced-solo", "adaptive"), strict=True):
            receipt.update(
                {
                    "provider": "cognitive-powers-1.4.2",
                    "fixture_git_sha256": "b" * 64,
                    "experiment_sha256": "c" * 64,
                    "hidden_check_sha256": "d" * 64,
                    "quality_check_sha256": "e" * 64,
                    "allowed_changes_sha256": "f" * 64,
                    "pre_evaluation_diff_sha256": "1" * 64,
                    "controller_protocol_sha256": identity["sha256"],
                    "controller_protocol_id": identity["protocol_id"],
                    "agent_slots": 4,
                    "controller_mode": mode,
                    "agent_telemetry": {
                        "controller_mode": mode,
                        "spawn_count": 0,
                        "join_count": 0,
                        "complete": True,
                        "actual_mode": "solo",
                        "usage_includes_subagents": True,
                        "plan_receipts": [
                            {
                                "mode": "solo",
                                "waves": [],
                                "total_planned_agents": 0,
                                "max_depth": 0,
                            }
                        ],
                        "observed_assignments": [],
                    },
                }
            )
        report = evaluation.compare(
            pair, task_contract=self.contract, controller_protocol=identity
        )
        self.assertEqual(report["controller_protocol"]["sha256"], identity["sha256"])
        self.assertFalse(report["host_execution_receipts_eligible"])
        pair[1]["quality_score"] = 0.8
        report = evaluation.compare(
            pair, task_contract=self.contract, controller_protocol=identity
        )
        self.assertFalse(report["pairs"][0]["passed"])
        self.assertTrue(report["pairs"][0]["efficiency_eligible"])
        pair[1]["controller_protocol_sha256"] = "0" * 64
        with self.assertRaisesRegex(evaluation.EvaluationError, "live identity field"):
            evaluation.compare(
                pair, task_contract=self.contract, controller_protocol=identity
            )

    def test_host_execution_receipt_is_required_for_claim_eligibility(self) -> None:
        identity = evaluation.load_controller_protocol(
            PLUGIN_ROOT / "benchmarks" / "controller_ab_protocol.json"
        )
        receipt = self._v2_pair("controller-pilot-solo-bug-fix-01", 1, live=True)[0]
        receipt.update(
            {
                "fixture_git_sha256": "b" * 64,
                "experiment_sha256": "c" * 64,
                "hidden_check_sha256": "d" * 64,
                "quality_check_sha256": "e" * 64,
                "allowed_changes_sha256": "f" * 64,
                "pre_evaluation_diff_sha256": "1" * 64,
                "controller_protocol_sha256": identity["sha256"],
                "controller_protocol_id": identity["protocol_id"],
                "agent_slots": 4,
                "controller_mode": "forced-solo",
                "host_identity": {
                    "version": "codex-test",
                    "executable_sha256": "2" * 64,
                    "features": {"multi_agent": True},
                    "persistent_parent_thread": True,
                },
                "agent_telemetry": {
                    "schema_version": 3,
                    "controller_mode": "forced-solo",
                    "spawn_count": 0,
                    "join_count": 0,
                    "complete": True,
                    "actual_mode": "solo",
                    "observed_assignments": [],
                    "agent_execution_receipt": {
                        "schema_version": 3,
                        "complete": True,
                        "selected_mode": "solo",
                        "executed_mode": "solo",
                        "outcome": "completed",
                        "parent_thread_id": "thread-1",
                        "planned_assignment_ids": [],
                        "planned_assignments": [],
                        "lifecycle_bindings": [],
                        "semantic_binding": True,
                        "spawned_assignment_ids": [],
                        "joined_assignment_ids": [],
                        "result_assignment_ids": [],
                        "descendant_usage": {},
                    },
                    "workspace_change_check": {
                        "changed_paths": [],
                        "allowed_paths": [],
                        "read_only_unchanged": True,
                        "provenance": "pre-evaluator-tree-diff",
                    },
                },
            }
        )
        normalized = evaluation.normalize_receipt(receipt)
        self.assertTrue(normalized["agent_execution_claim_eligible"])
        receipt["agent_telemetry"]["agent_execution_receipt"]["outcome"] = "degraded"
        self.assertFalse(
            evaluation.normalize_receipt(receipt)["agent_execution_claim_eligible"]
        )

    def test_verifier_compliance_uses_observed_actor_identity(self) -> None:
        assignments = [
            {
                "assignment_id": "executor-assignment",
                "role": "executor",
                "permissions": "write-owned-paths",
                "ownership": ["src/feature.py"],
                "delegation_depth": 1,
                "may_spawn": False,
                "may_verify_parent": False,
            },
            {
                "assignment_id": "verifier-assignment",
                "role": "verifier",
                "permissions": "read-only",
                "ownership": [],
                "delegation_depth": 1,
                "may_spawn": False,
                "may_verify_parent": False,
            },
        ]
        telemetry = {
            "actual_mode": "staged-verify",
            "spawn_count": 2,
            "join_count": 2,
            "usage_includes_subagents": True,
            "plan_receipts": [
                {
                    "waves": [{"assignments": assignments}],
                    "total_planned_agents": 2,
                    "max_depth": 1,
                }
            ],
            "observed_assignments": [
                {
                    "assignment_id": "executor-assignment",
                    "actor_id": "same-actor",
                    "role": "executor",
                },
                {
                    "assignment_id": "verifier-assignment",
                    "actor_id": "same-actor",
                    "role": "verifier",
                },
            ],
        }
        self.assertFalse(evaluation._agent_plan_compliant(telemetry, "staged-verify"))
        telemetry["observed_assignments"][1]["actor_id"] = "fresh-actor"
        self.assertTrue(evaluation._agent_plan_compliant(telemetry, "staged-verify"))

    def test_controller_protocol_rejects_changed_gates_and_artifacts(self) -> None:
        original = json.loads(
            (PLUGIN_ROOT / "benchmarks" / "controller_ab_protocol.json").read_text(
                encoding="utf-8"
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "protocol.json"
            changed_gate = copy.deepcopy(original)
            changed_gate["promotion_gates"][0]["threshold"] = 1
            path.write_text(json.dumps(changed_gate), encoding="utf-8")
            with self.assertRaisesRegex(
                evaluation.EvaluationError, "confirmatory design"
            ):
                evaluation.load_controller_protocol(path)
            changed_artifact = copy.deepcopy(original)
            changed_artifact["required_artifacts"].pop()
            path.write_text(json.dumps(changed_artifact), encoding="utf-8")
            with self.assertRaisesRegex(
                evaluation.EvaluationError, "confirmatory design"
            ):
                evaluation.load_controller_protocol(path)
            changed_design = copy.deepcopy(original)
            changed_design["design"]["cells"] = 19
            path.write_text(json.dumps(changed_design), encoding="utf-8")
            with self.assertRaisesRegex(
                evaluation.EvaluationError, "confirmatory design"
            ):
                evaluation.load_controller_protocol(path)
            changed_telemetry = copy.deepcopy(original)
            changed_telemetry["fail_closed_requirements"]["telemetry"]["required"].pop()
            path.write_text(json.dumps(changed_telemetry), encoding="utf-8")
            with self.assertRaisesRegex(
                evaluation.EvaluationError, "confirmatory design"
            ):
                evaluation.load_controller_protocol(path)
            changed_round = copy.deepcopy(original)
            changed_round["design"]["rounds"]["promotion"]["held_out"] = False
            path.write_text(json.dumps(changed_round), encoding="utf-8")
            with self.assertRaisesRegex(
                evaluation.EvaluationError, "confirmatory design"
            ):
                evaluation.load_controller_protocol(path)

    def test_bootstrap_preserves_category_and_mode_strata(self) -> None:
        pairs = [
            {
                "fixture_id": "fixture-a",
                "category": "bug-fix",
                "expected_mode": "solo",
                "quality_delta": 0.0,
            },
            {
                "fixture_id": "fixture-b",
                "category": "delivery-verification",
                "expected_mode": "staged-verify",
                "quality_delta": 10.0,
            },
        ]
        self.assertEqual(
            evaluation._stratified_fixture_interval(
                pairs,
                "quality_delta",
                median=False,
                seed="controller-ab-bootstrap-v1",
                samples=100,
            ),
            [5.0, 5.0],
        )

    def test_artifact_bundle_is_hash_bound_and_independently_verified(self) -> None:
        protocol = evaluation.load_controller_protocol(
            PLUGIN_ROOT / "benchmarks" / "controller_ab_protocol.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_names = evaluation.EXPECTED_REQUIRED_ARTIFACTS - {
                "sha256-index.json",
                "independent-verdict.json",
            }
            for name in artifact_names:
                path = root / name
                if name.endswith("/"):
                    path.mkdir(parents=True)
                    (path / "diff.patch").write_text(
                        "diff evidence\n", encoding="utf-8"
                    )
                else:
                    path.write_text(f"{name} evidence\n", encoding="utf-8")
            evidence = {
                name: evaluation._artifact_sha256(root / name)
                for name in artifact_names
            }
            evidence_root = hashlib.sha256(
                json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            verdict = {
                "schema_version": 1,
                "verdict": "confirmed",
                "protocol_id": protocol["protocol_id"],
                "controller_protocol_sha256": protocol["sha256"],
                "evidence_root_sha256": evidence_root,
                "verifier_id": "fresh-verifier",
                "executor_ids": ["experiment-runner"],
            }
            verdict_path = root / "independent-verdict.json"
            verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
            artifacts = {
                name: {"path": name, "sha256": digest}
                for name, digest in evidence.items()
            }
            artifacts["independent-verdict.json"] = {
                "path": "independent-verdict.json",
                "sha256": evaluation._artifact_sha256(verdict_path),
            }
            index = root / "sha256-index.json"
            index.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "protocol_id": protocol["protocol_id"],
                        "controller_protocol_sha256": protocol["sha256"],
                        "evidence_root_sha256": evidence_root,
                        "artifacts": artifacts,
                    }
                ),
                encoding="utf-8",
            )
            loaded = evaluation.load_artifact_bundle(index, protocol)
            self.assertEqual(loaded["artifact_count"], 10)
            with self.assertRaisesRegex(evaluation.EvaluationError, "session receipts"):
                evaluation._validate_artifact_semantics(loaded, [], {}, protocol)
            (root / "frozen-manifest.json").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(evaluation.EvaluationError, "hash mismatch"):
                evaluation.load_artifact_bundle(index, protocol)

    def test_host_actor_binding_rejects_extra_lifecycle_and_untyped_ids(self) -> None:
        expected = {("case-1", "candidate", "a1", "worker-1", "executor")}
        task_event = {
            "type": "agent.lifecycle",
            "provenance": "host",
            "case_id": "case-1",
            "variant": "candidate",
            "assignment_id": "a1",
            "actor_id": "worker-1",
            "role": "executor",
        }
        runner_event = {
            "type": "agent.lifecycle",
            "provenance": "host",
            "scope": "experiment",
            "actor_id": "experiment-runner",
            "role": "experiment-runner",
        }
        verifier_event = {
            "type": "agent.lifecycle",
            "provenance": "host",
            "scope": "experiment",
            "actor_id": "experiment-verifier",
            "role": "experiment-verifier",
        }
        verdict = {
            "executor_ids": ["experiment-runner"],
            "verifier_id": "experiment-verifier",
        }
        self.assertEqual(
            evaluation._validate_host_actor_binding(
                expected, [task_event, runner_event, verifier_event], verdict
            ),
            1,
        )
        extra = {**task_event, "assignment_id": "undeclared"}
        with self.assertRaisesRegex(evaluation.EvaluationError, "lifecycle"):
            evaluation._validate_host_actor_binding(
                expected, [task_event, extra, runner_event, verifier_event], verdict
            )
        invalid_runner = {**runner_event, "actor_id": None}
        with self.assertRaisesRegex(evaluation.EvaluationError, "host-backed"):
            evaluation._validate_host_actor_binding(
                expected,
                [task_event, invalid_runner, verifier_event],
                {**verdict, "executor_ids": [None]},
            )
        second_verifier = {**verifier_event, "actor_id": "another-verifier"}
        with self.assertRaisesRegex(evaluation.EvaluationError, "host-backed"):
            evaluation._validate_host_actor_binding(
                expected,
                [task_event, runner_event, verifier_event, second_verifier],
                verdict,
            )

    def test_unpaired_and_duplicate_repetition_receipts_are_rejected(self) -> None:
        with self.assertRaisesRegex(evaluation.EvaluationError, "not paired"):
            evaluation.compare(self.receipts[:1])

        first = self._v2_pair("controller-pilot-solo-bug-fix-01", 1)
        duplicate = copy.deepcopy(first)
        for receipt in duplicate:
            receipt["case_id"] = "another-case-id"
        with self.assertRaisesRegex(
            evaluation.EvaluationError, "duplicate task repetition"
        ):
            evaluation.compare(first + duplicate, task_contract=self.contract)


if __name__ == "__main__":
    unittest.main()
