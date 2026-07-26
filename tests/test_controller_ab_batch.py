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
    def test_cleanup_accounts_for_empty_residual_directories(self) -> None:
        work_root = Path("C:/temporary/controller-work")
        pre_cleanup = {"file_count": 30, "total_bytes": 4096}
        residual = {
            "path": str(work_root),
            "file_count": 0,
            "total_bytes": 0,
        }

        with mock.patch.object(
            batch,
            "finalize_workdir",
            return_value=residual,
        ):
            status = batch._cleanup_validated_workdir(work_root, pre_cleanup)

        self.assertEqual(
            status,
            {
                "status": "empty-directories-accounted-after-bounded-cleanup",
                "pre_cleanup_file_count": 30,
                "pre_cleanup_total_bytes": 4096,
                "persistent_file_count": 0,
                "persistent_total_bytes": 0,
                "path": str(work_root),
            },
        )

    def test_cleanup_rejects_material_residual_files_defensively(self) -> None:
        with mock.patch.object(
            batch,
            "finalize_workdir",
            return_value={
                "path": "C:/temporary/controller-work",
                "file_count": 1,
                "total_bytes": 8,
            },
        ):
            with self.assertRaisesRegex(batch.BatchError, "material files"):
                batch._cleanup_validated_workdir(
                    Path("C:/temporary/controller-work"),
                    {"file_count": 30, "total_bytes": 4096},
                )

    def test_v3_config_requires_clean_canonical_source_git_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self._write_batch_inputs(root)
            payload = json.loads(config.read_text(encoding="utf-8"))
            payload.update(
                {
                    "schema_version": 3,
                    "round_name": "pilot",
                    "plugin_source": str(root),
                    "source_commit": "a" * 40,
                    "source_git": {
                        "head": "a" * 40,
                        "status_sha256": batch.hashlib.sha256(b"").hexdigest(),
                    },
                }
            )
            payload["source_git"]["sha256"] = batch.canonical_sha256(
                payload["source_git"]
            )
            config.write_text(json.dumps(payload), encoding="utf-8")

            with mock.patch.object(
                batch, "git_identity", return_value=payload["source_git"]
            ):
                loaded = batch.load_config(config)
            self.assertEqual(loaded["source_commit"], "a" * 40)
            self.assertEqual(loaded["plugin_source"], str(root.resolve()))

            payload["source_git"]["status_sha256"] = "b" * 64
            payload["source_git"]["sha256"] = batch.canonical_sha256(
                {
                    "head": payload["source_git"]["head"],
                    "status_sha256": payload["source_git"]["status_sha256"],
                }
            )
            config.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(batch.BatchError, "source Git identity"):
                batch.load_config(config)

    def test_v3_config_requires_explicit_plugin_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self._write_batch_inputs(root)
            payload = json.loads(config.read_text(encoding="utf-8"))
            payload.update(
                {
                    "schema_version": 3,
                    "round_name": "pilot",
                    "source_commit": "a" * 40,
                    "source_git": {
                        "head": "a" * 40,
                        "status_sha256": batch.hashlib.sha256(b"").hexdigest(),
                    },
                }
            )
            payload["source_git"]["sha256"] = batch.canonical_sha256(
                payload["source_git"]
            )
            config.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(batch.BatchError, "plugin_source"):
                batch.load_config(config)

    def test_v3_runner_command_passes_explicit_plugin_source(self) -> None:
        plugin_source = Path("canonical-source").resolve()
        config = {
            "schema_version": 3,
            "baseline_home": "baseline",
            "candidate_home": "candidate",
            "task_contract": "contract.json",
            "controller_protocol": "protocol.json",
            "plugin_source": str(plugin_source),
            "source_commit": "a" * 40,
            "source_git": {"sha256": "b" * 64},
            "model": "test-model",
            "reasoning_effort": "medium",
            "task_prompts": {"task-a": "prompt"},
            "available_tools": ["shell_command"],
            "agent_slots": 4,
            "max_work_files": 10,
            "max_work_bytes": 100,
        }
        job = {
            "task_id": "task-a",
            "repetitions": 1,
            "repetition": 1,
            "runner_seed": "seed",
        }
        binding = {
            "fixture": "fixture",
            "hidden_check": ["python", "hidden.py"],
            "quality_check": ["python", "quality.py"],
            "allow_changes": ["src/**"],
            "guard_roots": [],
        }

        command = batch.runner_command(
            "python",
            Path("runner.py"),
            config,
            job,
            binding,
            Path("output"),
            Path("work"),
        )

        self.assertEqual(
            command[command.index("--plugin-source") + 1], str(plugin_source)
        )

    def test_verifier_only_staged_execution_is_valid(self) -> None:
        batch._validate_execution_semantics(
            {
                "semantic_binding": True,
                "selected_mode": "staged-verify",
                "planned_assignments": [
                    {
                        "assignment_id": "assignment-verifier",
                        "unit_id": "fresh-verifier",
                        "role": "verifier",
                        "wave_index": 0,
                        "wave_kind": "verification",
                        "wave_parallel": False,
                        "dependencies": [],
                        "ownership": [],
                        "permissions": "read-only",
                        "delegation_depth": 1,
                        "may_spawn": False,
                        "may_verify_parent": False,
                    }
                ],
                "lifecycle_bindings": [
                    {
                        "assignment_id": "assignment-verifier",
                        "actor_id": "actor-verifier",
                        "role_observed": None,
                        "parent_id": "actor-root",
                        "delegation_depth": 1,
                    }
                ],
            }
        )

    def test_read_only_assignments_may_declare_read_scope(self) -> None:
        planned = []
        bindings = []
        for index, unit_id in enumerate(("unit-a", "unit-b"), start=1):
            assignment_id = f"assignment-{index}"
            planned.append(
                {
                    "assignment_id": assignment_id,
                    "unit_id": unit_id,
                    "role": "investigator",
                    "wave_index": 0,
                    "wave_kind": "read-only-investigation",
                    "wave_parallel": True,
                    "dependencies": [],
                    "ownership": [f"evidence/{unit_id}.txt"],
                    "permissions": "read-only",
                    "delegation_depth": 1,
                    "may_spawn": False,
                    "may_verify_parent": False,
                    "must_be_distinct_from": [],
                }
            )
            bindings.append(
                {
                    "assignment_id": assignment_id,
                    "actor_id": f"actor-{index}",
                    "role_observed": None,
                    "parent_id": "parent",
                    "delegation_depth": 1,
                }
            )
        batch._validate_execution_semantics(
            {
                "semantic_binding": True,
                "selected_mode": "parallel-read-only",
                "planned_assignments": planned,
                "lifecycle_bindings": bindings,
            }
        )

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
            {job["expected_mode"] for job in schedule["jobs"]},
            {"solo", "parallel-read-only", "parallel-packets", "staged-verify"},
        )
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
        source_commit = (
            command[command.index("--source-commit") + 1]
            if "--source-commit" in command
            else None
        )
        source_git_sha256 = (
            command[command.index("--source-git-sha256") + 1]
            if "--source-git-sha256" in command
            else None
        )
        source_git = (
            {
                "head": source_commit,
                "status_sha256": batch.hashlib.sha256(b"").hexdigest(),
                "sha256": source_git_sha256,
            }
            if source_commit is not None
            else None
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
                    "telemetry_observation_complete": True,
                    "controller_mode": mode,
                    "actual_mode": "solo",
                    "agent_execution_receipt": {
                        "schema_version": 3,
                        "complete": True,
                        "telemetry_observation_complete": True,
                        "selected_mode": "solo",
                        "executed_mode": "solo",
                        "outcome": "completed",
                        "parent_thread_id": f"thread-{case_id}-{variant}",
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
                        "source_commit": source_commit,
                        "source_git_sha256": source_git_sha256,
                        "pre_evaluation_diff_sha256": batch.source_sha256({}),
                        "hidden_check_sha256": "c" * 64,
                        "quality_check_sha256": "d" * 64,
                        "independent_tests_passed": True,
                        "quality_score": 1.0,
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
                        "changed_paths": [],
                        "pre_evaluation_diff": {},
                        "pre_evaluation_diff_sha256": batch.source_sha256({}),
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
                    "source_git": source_git,
                    "candidate_plugin": {"source_git": source_git},
                }
            ),
            encoding="utf-8",
        )
        (destination / "receipts.json").write_text(
            json.dumps(receipts), encoding="utf-8"
        )
        (destination / "results.json").write_text(json.dumps(results), encoding="utf-8")

    def test_job_output_is_bound_to_exact_source_git_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_git = {
                "head": "a" * 40,
                "status_sha256": batch.hashlib.sha256(b"").hexdigest(),
            }
            source_git["sha256"] = batch.canonical_sha256(source_git)
            command = [
                "python",
                "runner.py",
                "--output",
                str(root),
                "--task-id",
                "task-a",
                "--repetitions",
                "1",
                "--source-commit",
                source_git["head"],
                "--source-git-sha256",
                source_git["sha256"],
            ]
            self._fake_runner_output(command)
            job = {"job_id": "job-a", "task_id": "task-a", "repetitions": 1}

            batch.validate_job_output(root, job, source_git)

            receipts = json.loads((root / "receipts.json").read_text(encoding="utf-8"))
            receipts[0]["source_commit"] = "b" * 40
            (root / "receipts.json").write_text(json.dumps(receipts), encoding="utf-8")
            with self.assertRaisesRegex(batch.BatchError, "identity or telemetry"):
                batch.validate_job_output(root, job, source_git)

    def test_job_output_rejects_unbound_pre_evaluator_diff_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = [
                "python",
                "runner.py",
                "--output",
                str(root),
                "--task-id",
                "task-a",
                "--repetitions",
                "1",
            ]
            self._fake_runner_output(command)
            job = {"job_id": "job-a", "task_id": "task-a", "repetitions": 1}
            results = json.loads((root / "results.json").read_text(encoding="utf-8"))
            results[0]["pre_evaluation_diff"] = {"src/a.py": "d" * 64}
            (root / "results.json").write_text(json.dumps(results), encoding="utf-8")
            with self.assertRaisesRegex(batch.BatchError, "diff is not bound"):
                batch.validate_job_output(root, job)

    def test_observed_noncompliance_is_retained_for_intention_to_treat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = [
                "python",
                "runner.py",
                "--output",
                str(root),
                "--task-id",
                "task-a",
                "--repetitions",
                "1",
            ]
            self._fake_runner_output(command)
            receipts = json.loads((root / "receipts.json").read_text(encoding="utf-8"))
            results = json.loads((root / "results.json").read_text(encoding="utf-8"))
            telemetry = receipts[1]["agent_telemetry"]
            execution = telemetry["agent_execution_receipt"]
            telemetry.update(
                {
                    "complete": False,
                    "selected_mode": "parallel-packets",
                    "executed_mode": "solo",
                    "outcome": "degraded",
                }
            )
            execution.update(
                {
                    "complete": False,
                    "controller_compliant": False,
                    "plan_adherent": False,
                    "selected_mode": "parallel-packets",
                    "executed_mode": "solo",
                    "outcome": "degraded",
                    "planned_assignment_ids": ["worker-1"],
                }
            )
            results[1]["agent_telemetry"] = telemetry
            results[1]["critical_errors"] = [
                "controller noncompliance: active plan was not executed"
            ]
            (root / "receipts.json").write_text(json.dumps(receipts), encoding="utf-8")
            (root / "results.json").write_text(json.dumps(results), encoding="utf-8")
            job = {"job_id": "job-a", "task_id": "task-a", "repetitions": 1}
            batch.validate_job_output(root, job)

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

    def test_success_compacts_evidence_and_removes_external_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, runner = self._write_batch_inputs(root)
            output = root / "output"
            work_root = root / "external-work"

            def invoke(
                command: list[str], **_: object
            ) -> subprocess.CompletedProcess[str]:
                destination = Path(command[command.index("--output") + 1])
                self.assertFalse(destination.is_relative_to(output))
                self.assertIn("--work-root", command)
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
                status = batch.run_batch(
                    config,
                    output,
                    runner,
                    work_root=work_root,
                    invoke=invoke,
                )

            self.assertTrue(status["complete"])
            self.assertFalse(work_root.exists())
            self.assertFalse((output / "sessions").exists())
            self.assertFalse((output / "debug-workdir.json").exists())
            self.assertTrue((output / "session-receipts.jsonl").is_file())
            self.assertTrue((output / "coordinator-sha256-index.json").is_file())
            hidden = [
                json.loads(line)
                for line in (output / "hidden-check-results.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            quality = [
                json.loads(line)
                for line in (output / "quality-check-results.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            agents = [
                json.loads(line)
                for line in (output / "agent-events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertTrue(hidden)
            self.assertTrue(quality)
            self.assertTrue(agents)
            self.assertTrue(
                all(
                    set(row)
                    == {
                        "case_id",
                        "variant",
                        "check_sha256",
                        "passed",
                    }
                    and row["check_sha256"] == "c" * 64
                    and row["passed"] is True
                    for row in hidden
                )
            )
            self.assertTrue(
                all(
                    set(row)
                    == {
                        "case_id",
                        "variant",
                        "check_sha256",
                        "quality_score",
                    }
                    and row["check_sha256"] == "d" * 64
                    and row["quality_score"] == 1.0
                    for row in quality
                )
            )
            self.assertEqual(
                agents,
                [
                    {
                        "type": "agent.lifecycle",
                        "provenance": "host",
                        "scope": "experiment",
                        "actor_id": row["actor_id"],
                        "role": "experiment-runner",
                    }
                    for row in agents
                ],
            )
            self.assertEqual(len({row["actor_id"] for row in agents}), len(agents))
            self.assertEqual(
                status["ephemeral_cleanup"],
                {
                    "status": "removed-after-validation",
                    "pre_cleanup_file_count": 30,
                    "pre_cleanup_total_bytes": status["ephemeral_cleanup"][
                        "pre_cleanup_total_bytes"
                    ],
                    "persistent_file_count": 0,
                    "persistent_total_bytes": 0,
                },
            )
            measured = batch.workdir_receipt(output)
            self.assertEqual(
                status["final_evidence_measurement"],
                {
                    "scope": "coordinator-evidence-before-independent-verification",
                    "file_count": measured["file_count"],
                    "total_bytes": measured["total_bytes"],
                },
            )

    def test_interrupted_job_blocks_automatic_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, runner = self._write_batch_inputs(root)
            output = root / "output"
            work_root = root / "external-work"
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
                        work_root=work_root,
                        invoke=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            KeyboardInterrupt()
                        ),
                    )
                debug = json.loads(
                    (output / "debug-workdir.json").read_text(encoding="utf-8")
                )
                self.assertEqual(debug["path"], str(work_root.resolve()))
                self.assertTrue(work_root.is_dir())
                with self.assertRaisesRegex(batch.BatchError, "refusing duplicate"):
                    batch.run_batch(
                        config,
                        output,
                        runner,
                        work_root=work_root,
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
            with self.assertRaisesRegex(batch.BatchError, "inconsistent"):
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

            self._fake_runner_output(command)
            preflight_job = {
                **job,
                "job_id": "preflight-parallel-read-only",
                "expected_mode": "parallel-read-only",
            }
            with self.assertRaisesRegex(batch.BatchError, "did not exercise"):
                batch.validate_job_output(root, preflight_job)

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
