from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "controller_ab_batch.py"
SPEC = importlib.util.spec_from_file_location("controller_ab_batch", MODULE_PATH)
batch = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(batch)


class ControllerAbBatchTests(unittest.TestCase):
    def test_preflight_schedule_has_one_pair_per_mode_and_is_non_scored(self) -> None:
        contract = batch.validate_task_contract(
            json.loads(
                (ROOT / "benchmarks" / "evaluation_tasks.json").read_text(
                    encoding="utf-8"
                )
            )
        )
        schedule = batch.build_preflight_schedule(contract)
        self.assertEqual(len(schedule["jobs"]), 4)
        self.assertEqual(len(schedule["sessions"]), 8)
        self.assertTrue(schedule["non_scored"])
        self.assertEqual(
            {job["job_id"] for job in schedule["jobs"]},
            {
                "preflight-solo",
                "preflight-parallel-read-only",
                "preflight-parallel-packets",
                "preflight-staged-verify",
            },
        )
        self.assertTrue(all(job["non_scored"] for job in schedule["jobs"]))
        self.assertEqual(
            {job["runner_seed"] for job in schedule["jobs"]},
            {contract["rounds"]["pilot"]["arm_order"]["seed"]},
        )

    def _contract(self) -> dict[str, object]:
        return {
            "task_set_id": "controller-test",
            "rounds": {
                "pilot": {
                    "task_ids": ["p-a", "p-b"],
                    "repetitions_per_task": 3,
                    "arm_order": {"seed": "pilot-seed"},
                },
                "promotion": {
                    "task_ids": ["x-a"],
                    "repetitions_per_task": 4,
                    "arm_order": {"seed": "promotion-seed"},
                },
            },
            "tasks": {
                "p-a": {"prompt": "p-a"},
                "p-b": {"prompt": "p-b"},
                "x-a": {"prompt": "x-a"},
            },
        }

    def _write_batch_inputs(self, root: Path) -> tuple[Path, Path, Path]:
        contract = root / "tasks.json"
        protocol = root / "protocol.json"
        runner = root / "runner.py"
        contract.write_text("{}\n", encoding="utf-8")
        protocol.write_text("{}\n", encoding="utf-8")
        runner.write_text("# runner\n", encoding="utf-8")
        (root / "baseline").mkdir()
        (root / "candidate").mkdir()
        tasks = {}
        for task_id in ("p-a", "p-b", "x-a"):
            fixture = root / task_id
            fixture.mkdir()
            tasks[task_id] = {
                "fixture": str(fixture),
                "hidden_check": ["python", "hidden.py"],
                "quality_check": ["python", "quality.py"],
                "allow_changes": ["src/**"],
            }
        config = root / "batch.json"
        config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_contract": str(contract),
                    "controller_protocol": str(protocol),
                    "baseline_home": str(root / "baseline"),
                    "candidate_home": str(root / "candidate"),
                    "model": "test-model",
                    "reasoning_effort": "medium",
                    "available_tools": ["shell", "agents"],
                    "agent_slots": 4,
                    "tasks": tasks,
                }
            ),
            encoding="utf-8",
        )
        return config, protocol, runner

    def _fake_runner_output(self, command: list[str]) -> None:
        destination = Path(command[command.index("--output") + 1])
        task_id = command[command.index("--task-id") + 1]
        repetitions = int(command[command.index("--repetitions") + 1])
        scheduled_repetitions = (
            [int(command[command.index("--batch-repetition") + 1])]
            if "--batch-repetition" in command
            else list(range(1, repetitions + 1))
        )
        destination.mkdir(parents=True, exist_ok=True)
        receipts = []
        results = []
        for repetition in scheduled_repetitions:
            for variant, mode in (
                ("baseline", "forced-solo"),
                ("candidate", "adaptive"),
            ):
                case_id = f"{task_id}-rep{repetition}"
                telemetry = {
                    "schema_version": 3,
                    "complete": True,
                    "controller_mode": mode,
                    "actual_mode": "solo",
                    "agent_execution_receipt": {
                        "schema_version": 3,
                        "complete": True,
                        "selected_mode": "solo",
                        "executed_mode": "solo",
                        "outcome": "completed",
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
                        "allowed_paths": ["src/a.py"],
                        "read_only_unchanged": True,
                        "provenance": "pre-evaluator-tree-diff",
                    },
                }
                receipts.append(
                    {
                        "case_id": case_id,
                        "variant": variant,
                        "controller_protocol_sha256": "a" * 64,
                        "experiment_sha256": "b" * 64,
                        "host_identity": {"version": "codex-test"},
                        "agent_telemetry": telemetry,
                    }
                )
                results.append(
                    {
                        "case_id": case_id,
                        "variant": variant,
                        "agent_telemetry": telemetry,
                        "hidden_exit": 0,
                        "quality_score": 1.0,
                        "quality_evidence": ["ok"],
                        "critical_errors": [],
                        "changed_paths": ["src/a.py"],
                        "pre_evaluation_diff_sha256": "c" * 64,
                    }
                )
        (destination / "summary.json").write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "repetitions": repetitions,
                    "controller_modes": {
                        "baseline": "forced-solo",
                        "candidate": "adaptive",
                    },
                    "host_identity": {"version": "codex-test"},
                }
            ),
            encoding="utf-8",
        )
        (destination / "receipts.json").write_text(
            json.dumps(receipts), encoding="utf-8"
        )
        (destination / "results.json").write_text(json.dumps(results), encoding="utf-8")

    def test_schedule_is_deterministic_complete_and_counterbalanced(self) -> None:
        first = batch.build_schedule(self._contract())
        second = batch.build_schedule(self._contract())
        self.assertEqual(first, second)
        self.assertEqual(len(first["jobs"]), 10)
        self.assertEqual(len(first["sessions"]), (3 * 2 * 2) + (4 * 2))
        self.assertEqual(first["execution"], "sequential-randomized-pairs")
        for task_id in ("p-a", "p-b", "x-a"):
            task_jobs = [job for job in first["jobs"] if job["task_id"] == task_id]
            baseline_first = sum(
                job["arm_orders"][0][0] == "baseline" for job in task_jobs
            )
            self.assertLessEqual(
                abs(baseline_first - (len(task_jobs) - baseline_first)), 1
            )

    def test_v2_round_schedule_does_not_mix_pilot_and_promotion(self) -> None:
        pilot = batch.build_schedule(self._contract(), "pilot")
        promotion = batch.build_schedule(self._contract(), "promotion")
        self.assertEqual({job["split"] for job in pilot["jobs"]}, {"pilot"})
        self.assertEqual({job["split"] for job in promotion["jobs"]}, {"promotion"})
        self.assertTrue(
            {job["task_id"] for job in pilot["jobs"]}.isdisjoint(
                {job["task_id"] for job in promotion["jobs"]}
            )
        )

    def test_journal_rejects_duplicates_and_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = Path(temporary) / "journal.jsonl"
            journal.write_text(
                '{"job_id":"p-a","state":"started"}\n'
                '{"job_id":"p-a","state":"started"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(batch.BatchError, "duplicate"):
                batch.read_journal(journal, {"p-a"})
            journal.write_text("not-json\n", encoding="utf-8")
            with self.assertRaisesRegex(batch.BatchError, "corrupt"):
                batch.read_journal(journal, {"p-a"})

    def test_resume_skips_completed_jobs_without_duplicate_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, runner = self._write_batch_inputs(root)
            output = root / "output"
            calls: list[list[str]] = []

            def invoke(
                command: list[str], **_: object
            ) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                self._fake_runner_output(command)
                return subprocess.CompletedProcess(command, 0)

            protocol = {"protocol_id": "test", "sha256": "d" * 64}
            with (
                mock.patch.object(
                    batch, "validate_task_contract", return_value=self._contract()
                ),
                mock.patch.object(
                    batch, "load_controller_protocol", return_value=protocol
                ),
                mock.patch.object(
                    batch, "compare", return_value={"verdict": "not-proven"}
                ),
            ):
                first = batch.run_batch(config, output, runner, invoke=invoke)
                count = len(calls)
                second = batch.run_batch(config, output, runner, invoke=invoke)
            self.assertTrue(first["complete"])
            self.assertEqual(second, first)
            self.assertEqual(len(calls), count)
            self.assertEqual(count, 10)

    def test_interrupted_job_blocks_automatic_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, runner = self._write_batch_inputs(root)
            output = root / "output"
            protocol = {"protocol_id": "test", "sha256": "d" * 64}
            patches = (
                mock.patch.object(
                    batch, "validate_task_contract", return_value=self._contract()
                ),
                mock.patch.object(
                    batch, "load_controller_protocol", return_value=protocol
                ),
                mock.patch.object(
                    batch, "compare", return_value={"verdict": "not-proven"}
                ),
            )
            with patches[0], patches[1], patches[2]:
                with self.assertRaises(KeyboardInterrupt):
                    batch.run_batch(
                        config,
                        output,
                        runner,
                        invoke=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            KeyboardInterrupt()
                        ),
                    )
                with self.assertRaisesRegex(batch.BatchError, "refusing duplicate"):
                    batch.run_batch(
                        config,
                        output,
                        runner,
                        invoke=lambda *_args, **_kwargs: self.fail("must not invoke"),
                    )

    def test_output_validation_rejects_duplicate_receipts_and_missing_telemetry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = [
                "python",
                "runner.py",
                "--output",
                str(root),
                "--task-id",
                "p-a",
                "--repetitions",
                "2",
            ]
            self._fake_runner_output(command)
            job = {"job_id": "pilot-p-a", "task_id": "p-a", "repetitions": 2}
            receipts_path = root / "receipts.json"
            receipts = json.loads(receipts_path.read_text(encoding="utf-8"))
            receipts[1] = receipts[0]
            receipts_path.write_text(json.dumps(receipts), encoding="utf-8")
            with self.assertRaisesRegex(batch.BatchError, "duplicates"):
                batch.validate_job_output(root, job)

            self._fake_runner_output(command)
            receipts = json.loads(receipts_path.read_text(encoding="utf-8"))
            receipts[0].pop("agent_telemetry")
            receipts_path.write_text(json.dumps(receipts), encoding="utf-8")
            with self.assertRaisesRegex(batch.BatchError, "telemetry"):
                batch.validate_job_output(root, job)

            self._fake_runner_output(command)
            receipts = json.loads(receipts_path.read_text(encoding="utf-8"))
            execution = receipts[0]["agent_telemetry"]["agent_execution_receipt"]
            execution["selected_mode"] = "parallel-packets"
            execution["outcome"] = "degraded"
            receipts_path.write_text(json.dumps(receipts), encoding="utf-8")
            with self.assertRaisesRegex(batch.BatchError, "degraded"):
                batch.validate_job_output(root, job)

            self._fake_runner_output(command)
            receipts = json.loads(receipts_path.read_text(encoding="utf-8"))
            execution = receipts[0]["agent_telemetry"]["agent_execution_receipt"]
            execution["semantic_binding"] = False
            receipts_path.write_text(json.dumps(receipts), encoding="utf-8")
            with self.assertRaisesRegex(batch.BatchError, "semantic binding"):
                batch.validate_job_output(root, job)

            self._fake_runner_output(command)
            receipts = json.loads(receipts_path.read_text(encoding="utf-8"))
            execution = receipts[0]["agent_telemetry"]["agent_execution_receipt"]
            execution["lifecycle_bindings"] = [
                {
                    "assignment_id": "unplanned",
                    "actor_id": "actor",
                    "role": "verifier",
                    "parent_id": "root",
                    "delegation_depth": 1,
                }
            ]
            receipts_path.write_text(json.dumps(receipts), encoding="utf-8")
            with self.assertRaisesRegex(batch.BatchError, "semantic identities"):
                batch.validate_job_output(root, job)

    def test_schema_v1_is_not_claim_eligible_for_v2_confirmatory_inputs(self) -> None:
        legacy = {"schema_version": 1, "claim_eligible": False}
        self.assertFalse(
            batch.validate_confirmatory_schema_binding(
                legacy, {"schema_version": 2}, {"schema_version": 1}
            )
        )
        with self.assertRaisesRegex(batch.BatchError, "not claim-eligible"):
            batch.validate_confirmatory_schema_binding(
                legacy, {"schema_version": 3}, {"schema_version": 2}
            )

    def test_incomplete_receipt_closes_invalid_bundle_with_sha256_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "frozen-manifest.json").write_text("{}\n", encoding="utf-8")
            (output / "randomized-schedule.json").write_text("{}\n", encoding="utf-8")
            (output / "session-receipts.jsonl").write_text(
                '{"agent_telemetry":{"complete":false}}\n', encoding="utf-8"
            )
            (output / "batch-journal.jsonl").write_text(
                '{"job_id":"pilot-a","state":"started"}\n', encoding="utf-8"
            )
            status = batch.materialize_invalid_bundle(
                output, "runner receipt lacks telemetry"
            )
            self.assertEqual(status["verdict"], "invalid")
            self.assertEqual(status["attempted_session_count"], 2)
            verdict = json.loads(
                (output / "independent-verdict.json").read_text(encoding="utf-8")
            )
            index = json.loads(
                (output / "sha256-index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(verdict["verdict"], "invalid")
            self.assertEqual(index["verdict"], "invalid")
            self.assertIn("session-receipts.jsonl", index["artifacts"])


if __name__ == "__main__":
    unittest.main()
