from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FINALIZER_PATH = ROOT / "scripts" / "finalize_controller_ab_evidence.py"
EVALUATION_PATH = ROOT / "scripts" / "integration_evaluation.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


finalizer = _load_module("controller_ab_evidence_finalizer", FINALIZER_PATH)
evaluation = _load_module("controller_ab_evidence_evaluation", EVALUATION_PATH)


class ControllerAbEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract_path = ROOT / "benchmarks" / "evaluation_tasks.json"
        cls.protocol_path = ROOT / "benchmarks" / "controller_ab_protocol.json"
        cls.contract = json.loads(cls.contract_path.read_text(encoding="utf-8"))
        cls.protocol = evaluation.load_controller_protocol(cls.protocol_path)

    def _task(self, split: str) -> dict[str, object]:
        return next(item for item in self.contract["tasks"] if item["split"] == split)

    def _pair(
        self,
        task: dict[str, object],
        *,
        quality_delta: float = 0.1,
    ) -> list[dict[str, object]]:
        split = str(task["split"])
        round_value = self.contract["rounds"][split]
        case_id = f"{task['task_id']}-rep1"
        order = ["baseline", "candidate"]
        diff_sha256 = hashlib.sha256(b"").hexdigest()
        shared: dict[str, object] = {
            "schema_version": 2,
            "case_id": case_id,
            "task": task["prompt"],
            "task_set_id": self.contract["task_set_id"],
            "task_id": task["task_id"],
            "task_version": task["version"],
            "split": split,
            "repetition": 1,
            "model": "synthetic-model",
            "reasoning_effort": "medium",
            "prompt": task["prompt"],
            "tools": ["shell", "apply_patch"],
            "permissions": ["workspace-write"],
            "fixture_id": task["fixture_id"],
            "source_sha256": "a" * 64,
            "randomization_seed": round_value["arm_order"]["seed"],
            "arm_order": order,
            "success": True,
            "critical_errors": [],
            "quality_score": 1.0,
            "elapsed_seconds": 10.0,
            "evidence": ["synthetic producer evidence"],
            "live_execution": False,
            "independent_tests_passed": True,
            "turns": 1,
            "tool_calls": 1,
            "retries": 0,
            "hidden_check_sha256": "b" * 64,
            "quality_check_sha256": "c" * 64,
            "pre_evaluation_diff_sha256": diff_sha256,
            "controller_protocol_id": self.protocol["protocol_id"],
            "controller_protocol_sha256": self.protocol["sha256"],
            "agent_slots": 4,
        }

        def telemetry(mode: str) -> dict[str, object]:
            return {
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
                "workspace_change_check": {
                    "changed_paths": [],
                    "allowed_paths": [],
                    "read_only_unchanged": True,
                    "provenance": "pre-evaluator-tree-diff",
                },
            }

        return [
            {
                **shared,
                "variant": "baseline",
                "provider": "synthetic-control",
                "controller_mode": "forced-solo",
                "agent_telemetry": telemetry("forced-solo"),
                "input_tokens": 100,
                "output_tokens": 20,
                "quality_score": 0.8,
            },
            {
                **shared,
                "variant": "candidate",
                "provider": "synthetic-candidate",
                "controller_mode": "adaptive",
                "agent_telemetry": telemetry("adaptive"),
                "input_tokens": 80,
                "output_tokens": 20,
                "quality_score": 0.8 + quality_delta,
            },
        ]

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
        path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )

    def _coordinator_output(
        self,
        parent: Path,
        name: str,
        task: dict[str, object],
        runner_id: str,
        *,
        quality_delta: float = 0.1,
    ) -> tuple[Path, list[dict[str, object]], str]:
        output = parent / name
        output.mkdir()
        receipts = self._pair(task, quality_delta=quality_delta)
        split = str(task["split"])
        round_contract = self.contract["rounds"][split]
        job_id = f"{split}-{task['task_id']}-rep1"
        job = {
            "job_id": job_id,
            "task_id": task["task_id"],
            "split": split,
            "seed": round_contract["arm_order"]["seed"],
            "runner_seed": round_contract["arm_order"]["seed"],
            "repetition": 1,
            "repetitions": 1,
            "declared_repetitions": round_contract["repetitions_per_task"],
            "arm_orders": [receipts[0]["arm_order"]],
        }
        schedule_payload = {
            "schema_version": 1,
            "task_set_id": self.contract["task_set_id"],
            "execution": "sequential-randomized-pairs",
            "jobs": [job],
            "sessions": [
                {
                    "ordinal": ordinal,
                    "job_id": job_id,
                    "task_id": task["task_id"],
                    "split": split,
                    "repetition": 1,
                    "arm": arm,
                }
                for ordinal, arm in enumerate(receipts[0]["arm_order"], start=1)
            ],
        }
        schedule = {
            **schedule_payload,
            "sha256": evaluation._canonical_sha256(schedule_payload),
        }
        manifest_payload = {
            "schema_version": 1,
            "task_set_id": self.contract["task_set_id"],
            "controller_protocol_id": self.protocol["protocol_id"],
            "controller_protocol_sha256": self.protocol["sha256"],
            "schedule_sha256": schedule["sha256"],
        }
        manifest = {
            **manifest_payload,
            "sha256": evaluation._canonical_sha256(manifest_payload),
        }
        self._write_json(output / "frozen-manifest.json", manifest)
        self._write_json(output / "randomized-schedule.json", schedule)
        self._write_jsonl(output / "session-receipts.jsonl", receipts)
        self._write_jsonl(
            output / "agent-events.jsonl",
            [
                {
                    "schema_version": 1,
                    "type": "agent.lifecycle",
                    "provenance": "host",
                    "scope": "experiment",
                    "actor_id": runner_id,
                    "role": "experiment-runner",
                }
            ],
        )
        self._write_jsonl(
            output / "hidden-check-results.jsonl",
            [
                {
                    "case_id": row["case_id"],
                    "variant": row["variant"],
                    "check_sha256": row["hidden_check_sha256"],
                    "passed": row["independent_tests_passed"],
                }
                for row in receipts
            ],
        )
        self._write_jsonl(
            output / "quality-check-results.jsonl",
            [
                {
                    "case_id": row["case_id"],
                    "variant": row["variant"],
                    "check_sha256": row["quality_check_sha256"],
                    "quality_score": row["quality_score"],
                }
                for row in receipts
            ],
        )
        self._write_json(
            output / "analysis-with-ci95.json",
            {"schema_version": 1, "round": task["split"]},
        )
        diff_root = output / "pre-evaluator-diffs"
        diff_root.mkdir()
        for row in receipts:
            self._write_json(
                diff_root / f"{row['case_id']}-{row['variant']}.json",
                {
                    "case_id": row["case_id"],
                    "variant": row["variant"],
                    "changed_paths": [],
                    "manifest": {},
                    "sha256": hashlib.sha256(b"").hexdigest(),
                },
            )
        (output / "batch-journal.jsonl").write_text(
            json.dumps({"job_id": name, "state": "completed"}) + "\n",
            encoding="utf-8",
        )
        indexed_names = {
            "frozen-manifest.json",
            "randomized-schedule.json",
            "batch-journal.jsonl",
            "session-receipts.jsonl",
            "agent-events.jsonl",
            "hidden-check-results.jsonl",
            "quality-check-results.jsonl",
            "analysis-with-ci95.json",
        }
        indexed_names.update(
            path.relative_to(output).as_posix() for path in diff_root.glob("*.json")
        )
        index_payload = {
            "schema_version": 1,
            "scope": "coordinator-evidence-before-independent-verification",
            "artifacts": {
                relative: hashlib.sha256((output / relative).read_bytes()).hexdigest()
                for relative in sorted(indexed_names)
            },
            "independent_verdict_included": False,
        }
        index = {
            **index_payload,
            "sha256": evaluation._canonical_sha256(index_payload),
        }
        self._write_json(output / "coordinator-sha256-index.json", index)
        self._write_json(
            output / "batch-status.json",
            {
                "schema_version": 1,
                "complete": True,
                "session_count": len(receipts),
                "manifest_sha256": manifest["sha256"],
                "schedule_sha256": schedule["sha256"],
                "coordinator_index_sha256": index["sha256"],
                "independent_verification_pending": True,
            },
        )
        return output, receipts, index["sha256"]

    def _verifier_receipt(
        self,
        root: Path,
        indexes: list[str],
        *,
        verifier_id: str = "fresh-independent-verifier",
        provenance: str = "host",
    ) -> Path:
        path = (
            root / f"verifier-{hashlib.sha256(verifier_id.encode()).hexdigest()}.json"
        )
        self._write_json(
            path,
            {
                "schema_version": 1,
                "kind": "controller-ab-independent-verifier-receipt",
                "provenance": provenance,
                "scope": "experiment",
                "role": "experiment-verifier",
                "verdict": "confirmed",
                "independent": True,
                "verifier_id": verifier_id,
                "coordinator_index_sha256s": sorted(indexes),
            },
        )
        return path

    @staticmethod
    def _snapshot(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def test_two_disjoint_outputs_finalize_and_public_consumer_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pilot, pilot_receipts, pilot_index = self._coordinator_output(
                root, "pilot", self._task("pilot"), "pilot-runner"
            )
            promotion, promotion_receipts, promotion_index = self._coordinator_output(
                root,
                "promotion",
                self._task("promotion"),
                "promotion-runner",
            )
            verifier = self._verifier_receipt(root, [pilot_index, promotion_index])
            before = {
                pilot: self._snapshot(pilot),
                promotion: self._snapshot(promotion),
            }
            bundle = root / "bundle"

            result = finalizer.finalize_evidence(
                [promotion, pilot],
                bundle,
                verifier,
                controller_protocol_path=self.protocol_path,
                task_contract_path=self.contract_path,
                minimum_live_pairs=1,
            )

            self.assertEqual(self._snapshot(pilot), before[pilot])
            self.assertEqual(self._snapshot(promotion), before[promotion])
            self.assertEqual(
                set(path.name for path in bundle.iterdir()),
                {value.rstrip("/") for value in evaluation.EXPECTED_REQUIRED_ARTIFACTS},
            )
            loaded = evaluation.load_artifact_bundle(
                bundle / "sha256-index.json", self.protocol
            )
            self.assertEqual(
                loaded["evidence_root_sha256"], result["evidence_root_sha256"]
            )
            report = evaluation.compare(
                [*pilot_receipts, *promotion_receipts],
                minimum_live_pairs=1,
                task_contract=self.contract,
                controller_protocol=self.protocol,
                artifact_index=bundle / "sha256-index.json",
            )
            self.assertIsInstance(report["artifact_bundle"]["semantic_binding"], dict)
            verdict = json.loads(
                (bundle / "independent-verdict.json").read_text(encoding="utf-8")
            )
            self.assertTrue(verdict["independent"])
            self.assertEqual(
                verdict["executor_ids"], ["pilot-runner", "promotion-runner"]
            )
            self.assertEqual(
                verdict["coordinator_index_sha256s"],
                sorted([pilot_index, promotion_index]),
            )

    def test_duplicate_and_divergent_sources_fail_without_partial_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pilot, _, pilot_index = self._coordinator_output(
                root, "pilot", self._task("pilot"), "pilot-runner"
            )
            verifier = self._verifier_receipt(root, [pilot_index])
            duplicate_bundle = root / "duplicate-bundle"
            with self.assertRaisesRegex(finalizer.EvidenceFinalizationError, "unique"):
                finalizer.finalize_evidence(
                    [pilot, pilot],
                    duplicate_bundle,
                    verifier,
                    controller_protocol_path=self.protocol_path,
                    task_contract_path=self.contract_path,
                    minimum_live_pairs=1,
                )
            self.assertFalse(duplicate_bundle.exists())

            divergent, _, divergent_index = self._coordinator_output(
                root,
                "divergent",
                self._task("pilot"),
                "other-runner",
                quality_delta=0.05,
            )
            divergent_verifier = self._verifier_receipt(
                root, [pilot_index, divergent_index], verifier_id="divergence-reviewer"
            )
            divergent_bundle = root / "divergent-bundle"
            with self.assertRaisesRegex(
                finalizer.EvidenceFinalizationError, "diverges"
            ):
                finalizer.finalize_evidence(
                    [pilot, divergent],
                    divergent_bundle,
                    divergent_verifier,
                    controller_protocol_path=self.protocol_path,
                    task_contract_path=self.contract_path,
                    minimum_live_pairs=1,
                )
            self.assertFalse(divergent_bundle.exists())

    def test_tamper_is_rejected_without_partial_verdict_or_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output, _, index_sha256 = self._coordinator_output(
                root, "pilot", self._task("pilot"), "runner"
            )
            verifier = self._verifier_receipt(root, [index_sha256])
            with (output / "session-receipts.jsonl").open("ab") as stream:
                stream.write(b"tamper")
            bundle = root / "bundle"

            with self.assertRaisesRegex(
                finalizer.EvidenceFinalizationError, "hash mismatch"
            ):
                finalizer.finalize_evidence(
                    [output],
                    bundle,
                    verifier,
                    controller_protocol_path=self.protocol_path,
                    task_contract_path=self.contract_path,
                    minimum_live_pairs=1,
                )

            self.assertFalse(bundle.exists())

    def test_self_verification_and_invalid_host_receipt_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output, _, index_sha256 = self._coordinator_output(
                root, "pilot", self._task("pilot"), "experiment-runner"
            )
            for verifier_id, provenance, message in (
                ("experiment-runner", "host", "must differ"),
                ("independent", "fixture", "invalid"),
            ):
                with self.subTest(verifier_id=verifier_id, provenance=provenance):
                    verifier = self._verifier_receipt(
                        root,
                        [index_sha256],
                        verifier_id=verifier_id,
                        provenance=provenance,
                    )
                    bundle = root / f"bundle-{verifier_id}"
                    with self.assertRaisesRegex(
                        finalizer.EvidenceFinalizationError, message
                    ):
                        finalizer.finalize_evidence(
                            [output],
                            bundle,
                            verifier,
                            controller_protocol_path=self.protocol_path,
                            task_contract_path=self.contract_path,
                            minimum_live_pairs=1,
                        )
                    self.assertFalse(bundle.exists())

    def test_cli_returns_one_and_leaves_no_output_on_invalid_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output, _, index_sha256 = self._coordinator_output(
                root, "pilot", self._task("pilot"), "runner"
            )
            verifier = self._verifier_receipt(
                root, [index_sha256], provenance="not-host"
            )
            bundle = root / "bundle"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = finalizer.main(
                    [
                        "--coordinator-output",
                        str(output),
                        "--bundle-output",
                        str(bundle),
                        "--verifier-receipt",
                        str(verifier),
                        "--controller-protocol",
                        str(self.protocol_path),
                        "--task-contract",
                        str(self.contract_path),
                        "--minimum-live-pairs",
                        "1",
                    ]
                )
            self.assertEqual(exit_code, 1)
            self.assertIn("error", json.loads(stdout.getvalue()))
            self.assertFalse(bundle.exists())


if __name__ == "__main__":
    unittest.main()
