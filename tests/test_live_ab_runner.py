from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "scripts" / "live_ab_runner.py"
SPEC = importlib.util.spec_from_file_location("live_ab_runner", MODULE_PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


class LiveAbRunnerTests(unittest.TestCase):
    def test_controller_protocol_is_bound_and_fails_closed_when_changed(self) -> None:
        canonical = runner.load_controller_protocol(
            PLUGIN_ROOT / "benchmarks" / "controller_ab_protocol.json"
        )
        self.assertEqual(canonical["protocol_id"], "cognitive-powers-controller-ab-v1")
        self.assertEqual(len(canonical["sha256"]), 64)
        with tempfile.TemporaryDirectory() as temporary:
            altered = Path(temporary) / "protocol.json"
            payload = json.loads(
                (PLUGIN_ROOT / "benchmarks" / "controller_ab_protocol.json").read_text(
                    encoding="utf-8"
                )
            )
            payload["comparison"]["control_arm"]["controller_mode"] = "adaptive"
            altered.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(runner.LiveEvaluationError):
                runner.load_controller_protocol(altered)

    def test_arm_order_is_deterministic_and_balanced(self) -> None:
        first = runner.arm_order(5, "stable-seed")
        second = runner.arm_order(5, "stable-seed")
        self.assertEqual(first, second)
        self.assertTrue(
            all(sorted(order) == ["baseline", "candidate"] for order in first)
        )
        baseline_first = sum(order[0] == "baseline" for order in first)
        self.assertLessEqual(abs(baseline_first - (len(first) - baseline_first)), 1)

    def test_layout_rejects_output_inside_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture"
            baseline = root / "baseline-home"
            candidate = root / "candidate-home"
            for path in (fixture, baseline, candidate):
                path.mkdir()
            with self.assertRaisesRegex(runner.LiveEvaluationError, "must not contain"):
                runner.validate_layout(
                    fixture, fixture / "results", baseline, candidate
                )

    def test_task_binding_requires_the_frozen_schedule(self) -> None:
        contract_path = PLUGIN_ROOT / "benchmarks" / "evaluation_tasks.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        task = contract["tasks"][0]
        prompt = task["prompt"]
        seed = contract["rounds"]["pilot"]["arm_order"]["seed"]

        binding = runner.load_task_binding(
            contract_path,
            task_id=task["task_id"],
            prompt=prompt,
            repetitions=3,
            seed=seed,
        )

        self.assertEqual(binding["split"], "pilot")
        self.assertEqual(binding["fixture_id"], task["fixture_id"])
        with self.assertRaisesRegex(runner.LiveEvaluationError, "prompt"):
            runner.load_task_binding(
                contract_path,
                task_id=task["task_id"],
                prompt="changed prompt",
                repetitions=3,
                seed=seed,
            )

        batched = runner.load_task_binding(
            contract_path,
            task_id=task["task_id"],
            prompt=prompt,
            repetitions=1,
            seed=seed,
            batch_repetition=2,
        )
        self.assertEqual(batched["batch_repetition"], 2)
        self.assertEqual(len(batched["batch_arm_order"]), 2)

    def test_aggregate_results_reports_medians_and_worst_pair(self) -> None:
        results = []
        for repetition, baseline, candidate in (
            (1, 100, 80),
            (2, 120, 126),
            (3, 90, 75),
        ):
            for variant, total in (("baseline", baseline), ("candidate", candidate)):
                results.append(
                    {
                        "repetition": repetition,
                        "variant": variant,
                        "success": True,
                        "total_tokens": total,
                        "fresh_input_tokens": total // 2,
                        "output_tokens": 10,
                        "tool_calls": 4,
                        "elapsed_seconds": 2.0,
                    }
                )

        report = runner.aggregate_results(results)

        self.assertEqual(report["pair_count"], 3)
        self.assertTrue(report["all_pairs_successful"])
        self.assertEqual(report["metrics"]["total_tokens"]["baseline_median"], 100)
        self.assertEqual(report["metrics"]["total_tokens"]["candidate_median"], 80)
        self.assertEqual(report["metrics"]["total_tokens"]["delta_percent"], -20.0)
        self.assertEqual(report["worst_pair_total_token_delta_percent"], 5.0)

    def test_aggregate_results_excludes_failed_pairs_from_token_metrics(self) -> None:
        results = []
        for repetition, success, baseline, candidate in (
            (1, True, 100, 80),
            (2, False, 10_000, 1),
        ):
            for variant, total in (("baseline", baseline), ("candidate", candidate)):
                results.append(
                    {
                        "repetition": repetition,
                        "variant": variant,
                        "success": success,
                        "total_tokens": total,
                        "fresh_input_tokens": total - 10,
                        "output_tokens": 10,
                        "tool_calls": 1,
                        "elapsed_seconds": 1.0,
                    }
                )

        report = runner.aggregate_results(results)
        self.assertEqual(report["successful_pair_count"], 1)
        self.assertEqual(report["failed_pair_count"], 1)
        self.assertEqual(report["metrics"]["total_tokens"]["candidate_median"], 80)

    def test_parse_events_extracts_observed_agent_plan_and_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events.jsonl"
            rows = [
                {
                    "type": "item.completed",
                    "item": {
                        "type": "function_call",
                        "name": "spawn_agent",
                        "output": json.dumps(
                            {
                                "agent_plan": {
                                    "schema_version": 2,
                                    "mode": "parallel-read-only",
                                    "waves": [],
                                }
                            }
                        ),
                    },
                },
                {
                    "type": "agent.lifecycle",
                    "provenance": "host",
                    "assignment_id": "assignment-1",
                    "actor_id": "worker-1",
                    "role": "researcher",
                },
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 2,
                        "output_tokens": 3,
                    },
                },
            ]
            events.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )
            parsed = runner.parse_events(events)
            self.assertEqual(parsed["agent_spawns"], 1)
            self.assertEqual(parsed["agent_plans"][0]["mode"], "parallel-read-only")
            self.assertEqual(parsed["observed_assignments"][0]["actor_id"], "worker-1")

    def test_no_agent_fast_path_is_observed_as_solo(self) -> None:
        parsed = {
            "agent_plans": [],
            "agent_spawns": 0,
            "agent_joins": 0,
            "observed_assignments": [],
            "usage_includes_subagents": False,
        }
        decision = runner.classify_agent_decision(parsed, "adaptive")
        self.assertEqual(decision["actual_mode"], "solo")
        self.assertEqual(
            decision["decision_observation"], "implicit-solo-no-agent-events"
        )
        self.assertTrue(decision["complete"])

    def test_spawn_without_plan_remains_incomplete(self) -> None:
        parsed = {
            "agent_plans": [],
            "agent_spawns": 1,
            "agent_joins": 0,
            "observed_assignments": [],
            "usage_includes_subagents": False,
        }
        decision = runner.classify_agent_decision(parsed, "adaptive")
        self.assertIsNone(decision["actual_mode"])
        self.assertEqual(decision["decision_observation"], "missing")
        self.assertFalse(decision["complete"])

    def test_parallel_plan_without_observed_agents_is_incomplete(self) -> None:
        parsed = {
            "agent_plans": [
                {
                    "mode": "parallel-packets",
                    "waves": [{"assignments": [{"assignment_id": "worker-1"}]}],
                }
            ],
            "agent_spawns": 0,
            "agent_joins": 0,
            "observed_assignments": [],
            "usage_includes_subagents": False,
        }
        decision = runner.classify_agent_decision(parsed, "adaptive")
        self.assertEqual(decision["planned_assignment_count"], 1)
        self.assertEqual(decision["actual_mode"], "parallel-packets")
        self.assertFalse(decision["complete"])

    def test_parse_events_rejects_self_reported_actor_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events.jsonl"
            rows = [
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "output": json.dumps(
                            {
                                "assignment_id": "fake-assignment",
                                "actor_id": "fake-actor",
                                "role": "verifier",
                            }
                        ),
                    },
                },
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 2,
                        "output_tokens": 3,
                    },
                },
            ]
            events.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )
            self.assertEqual(runner.parse_events(events)["observed_assignments"], [])

    def test_tree_identity_and_scope_detect_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            target = root / "src" / "feature.py"
            target.write_text("before\n", encoding="utf-8")
            before = runner.tree_hashes(root)
            target.write_text("after\n", encoding="utf-8")
            (root / "notes.txt").write_text("unexpected\n", encoding="utf-8")
            changes = runner.changed_paths(before, runner.tree_hashes(root))
            self.assertEqual(changes, ["notes.txt", "src/feature.py"])
            self.assertEqual(
                runner.unexpected_changes(changes, ["src/*.py"]), ["notes.txt"]
            )

    def test_command_identity_changes_with_evaluator_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evaluator = Path(temporary) / "evaluate.py"
            evaluator.write_text("print(1)\n", encoding="utf-8")
            before = runner.command_identity(["python", str(evaluator)])
            evaluator.write_text("print(2)\n", encoding="utf-8")
            after = runner.command_identity(["python", str(evaluator)])
            self.assertNotEqual(before["sha256"], after["sha256"])

    def test_protected_roots_include_fixture_source_and_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            explicit = root / "explicit"
            fixture = root / "fixture"
            source = root / "source"
            installed = root / "installed"
            for path in (explicit, fixture, source, installed):
                path.mkdir()

            roots = runner.protected_roots(
                [explicit, source],
                fixture,
                {"source_root": str(source), "installed_root": str(installed)},
            )

            self.assertEqual(roots, [explicit, source, fixture, installed])

    def test_guard_postflight_rejects_runtime_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "plugin.py"
            target.write_text("before\n", encoding="utf-8")
            before = runner.snapshot_guards([root])
            target.write_text("after\n", encoding="utf-8")

            with self.assertRaisesRegex(
                runner.LiveEvaluationError, "guarded roots changed"
            ):
                runner.verify_guards(before)

    def test_guard_postflight_records_stable_before_and_after_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plugin.py").write_text("stable\n", encoding="utf-8")

            receipts = runner.verify_guards(runner.snapshot_guards([root]))

            self.assertEqual(len(receipts), 1)
            self.assertTrue(receipts[0]["stable"])
            self.assertEqual(receipts[0]["before_sha256"], receipts[0]["after_sha256"])

    def test_candidate_identity_rejects_stale_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            home = root / "home"
            installed = home / "plugins" / "cache" / "personal" / "demo" / "1.0"
            source.mkdir()
            installed.mkdir(parents=True)
            (source / "plugin.txt").write_text("current\n", encoding="utf-8")
            (installed / "plugin.txt").write_text("stale\n", encoding="utf-8")
            item = {
                "version": "1.0",
                "name": "demo",
                "marketplaceName": "personal",
                "source": {"path": str(source)},
            }

            with self.assertRaisesRegex(
                runner.LiveEvaluationError, "differs from source"
            ):
                runner._candidate_identity(item, home)

            (installed / "plugin.txt").write_text("current\n", encoding="utf-8")
            identity = runner._candidate_identity(item, home)
            self.assertEqual(identity["source_sha256"], identity["installed_sha256"])

    def test_parse_events_requires_provider_usage_and_counts_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events.jsonl"
            rows = [
                {"type": "item.completed", "item": {"type": "agent_message"}},
                {"type": "item.completed", "item": {"type": "command_execution"}},
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 60,
                        "output_tokens": 20,
                    },
                },
            ]
            events.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )
            parsed = runner.parse_events(events)
            self.assertEqual(parsed["tool_calls"], 1)
            self.assertEqual(parsed["usage"]["input_tokens"], 100)

    def test_parse_events_rejects_missing_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events.jsonl"
            events.write_text('{"type":"turn.started"}\n', encoding="utf-8")
            with self.assertRaisesRegex(runner.LiveEvaluationError, "completed turn"):
                runner.parse_events(events)

    def test_parse_events_rejects_non_json_contamination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events.jsonl"
            events.write_text(
                'host warning\n{"type":"turn.completed","usage":'
                '{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(runner.LiveEvaluationError, "non-JSON"):
                runner.parse_events(events)

    def test_quality_payload_is_bounded_and_evidence_backed(self) -> None:
        payload = runner.normalize_quality_payload(
            {"score": 85, "evidence": ["focused check"], "critical_errors": []}
        )
        self.assertEqual(payload["score"], 85.0)
        with self.assertRaisesRegex(runner.LiveEvaluationError, "0 to 100"):
            runner.normalize_quality_payload(
                {"score": 101, "evidence": ["bad"], "critical_errors": []}
            )
        with self.assertRaisesRegex(runner.LiveEvaluationError, "evidence"):
            runner.normalize_quality_payload(
                {"score": 80, "evidence": [], "critical_errors": []}
            )


if __name__ == "__main__":
    unittest.main()
