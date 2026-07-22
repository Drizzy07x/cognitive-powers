from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "scripts" / "live_ab_runner.py"
SPEC = importlib.util.spec_from_file_location("live_ab_runner", MODULE_PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


class LiveAbRunnerTests(unittest.TestCase):
    @staticmethod
    def _canonical_plan() -> dict[str, object]:
        assignments = []
        for unit_id in ("investigator-a", "investigator-b"):
            assignment = {
                "id": unit_id,
                "role": "investigator",
                "objective": f"Investigate {unit_id}",
                "context": ["bounded context"],
                "ownership": [f"evidence/{unit_id}.txt"],
                "permissions": "read-only",
                "expected_output": "Evidence-backed finding",
                "check": ["python", "-m", "unittest"],
                "dependencies": [],
                "stop_conditions": ["stop outside boundary"],
                "delegation_depth": 1,
                "may_spawn": False,
                "may_verify_parent": False,
            }
            encoded = json.dumps(
                assignment, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            assignment["assignment_id"] = (
                "assignment-" + runner.hashlib.sha256(encoded).hexdigest()
            )
            assignments.append(assignment)
        plan = {
            "schema_version": 2,
            "kind": "agent_plan",
            "profile": "auto-conservative",
            "valid_input": True,
            "mode": "parallel-read-only",
            "selected_mode": "parallel-read-only",
            "executed_mode": None,
            "outcome": "planned",
            "degradation": None,
            "spawn_count": 2,
            "total_planned_agents": 2,
            "max_concurrent_workers": 2,
            "max_depth": 1,
            "reserve_verifier_slot": False,
            "waves": [
                {
                    "kind": "read-only-investigation",
                    "parallel": True,
                    "assignments": assignments,
                }
            ],
            "reasons": ["two independent units"],
            "abstentions": ["undeclared writes"],
            "retry_policy": {
                "max_retries_per_assignment": 1,
                "retry_allowed": False,
                "target_assignment_id": None,
                "fallback": "main-agent-absorbs-or-reports-blocker",
            },
            "stop_conditions": ["stop when coordination has no value"],
            "receipt_policy": {
                "emit_json": True,
                "external_state_required": True,
                "end_to_end_improvement_proven": False,
            },
        }
        encoded = json.dumps(
            plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        plan["plan_id"] = "plan-" + runner.hashlib.sha256(encoded).hexdigest()
        return plan

    @staticmethod
    def _reidentify_plan(plan: dict[str, object]) -> dict[str, object]:
        plan.pop("plan_id", None)
        encoded = json.dumps(
            plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        plan["plan_id"] = "plan-" + runner.hashlib.sha256(encoded).hexdigest()
        return plan

    def _canonical_solo_plan(self) -> dict[str, object]:
        plan = self._canonical_plan()
        plan.update(
            {
                "mode": "solo",
                "selected_mode": "solo",
                "spawn_count": 0,
                "total_planned_agents": 0,
                "max_concurrent_workers": 0,
                "max_depth": 0,
                "waves": [],
            }
        )
        return self._reidentify_plan(plan)

    def _canonical_verifier_plan(self) -> dict[str, object]:
        plan = self._canonical_solo_plan()
        verifier = {
            "id": "fresh-verifier",
            "role": "verifier",
            "objective": "Independently verify the integrated result",
            "context": ["objective", "integrated diff", "criteria", "receipts"],
            "ownership": [],
            "permissions": "read-only",
            "expected_output": "confirmed, rejected, or inconclusive with evidence",
            "check": ["python", "verification/verify.py"],
            "dependencies": [],
            "stop_conditions": [
                "do not modify the workspace",
                "do not self-verify",
            ],
            "delegation_depth": 1,
            "may_spawn": False,
            "may_verify_parent": False,
            "must_be_distinct_from": [],
        }
        encoded = json.dumps(
            verifier, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        verifier["assignment_id"] = (
            "assignment-" + runner.hashlib.sha256(encoded).hexdigest()
        )
        plan.update(
            {
                "mode": "staged-verify",
                "selected_mode": "staged-verify",
                "total_planned_agents": 1,
                "max_concurrent_workers": 1,
                "max_depth": 1,
                "reserve_verifier_slot": True,
                "waves": [
                    {
                        "kind": "verification",
                        "parallel": False,
                        "assignments": [verifier],
                    }
                ],
            }
        )
        return self._reidentify_plan(plan)

    def _write_rollout_pair(
        self,
        home: Path,
        *,
        task_name: str = "unit_a",
        child_parent: str = "parent-1",
        include_child_usage: bool = True,
    ) -> None:
        sessions = home / "sessions" / "2026" / "07" / "22"
        sessions.mkdir(parents=True)
        parent = [
            {"type": "session_meta", "payload": {"id": "parent-1", "source": "exec"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "spawn_agent",
                    "arguments": json.dumps(
                        {"task_name": task_name, "message": "encrypted"}
                    ),
                    "call_id": "spawn-call",
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "sub_agent_activity",
                    "event_id": "spawn-call",
                    "agent_thread_id": "child-1",
                    "agent_path": f"/root/{task_name}",
                    "kind": "started",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "wait_agent",
                    "arguments": '{"timeout_ms":1000}',
                    "call_id": "wait-call",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "wait-call",
                    "output": '{"timed_out":false}',
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 2,
                            "output_tokens": 3,
                        }
                    },
                },
            },
        ]
        child = [
            {
                "type": "session_meta",
                "payload": {
                    "id": "child-1",
                    "parent_thread_id": child_parent,
                    "thread_source": "subagent",
                    "agent_path": f"/root/{task_name}",
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": child_parent,
                                "depth": 1,
                                "agent_path": f"/root/{task_name}",
                            }
                        }
                    },
                },
            },
            {
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "done"},
            },
        ]
        if include_child_usage:
            child.append(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 5,
                                "cached_input_tokens": 1,
                                "output_tokens": 2,
                            }
                        },
                    },
                }
            )
        for name, rows in (("parent.jsonl", parent), ("child.jsonl", child)):
            (sessions / name).write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )

    def test_rollout_v3_links_only_new_children_and_aggregates_usage_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self._write_rollout_pair(home)
            parsed = runner.parse_new_rollouts(home, {}, "parent-1")
            self.assertEqual(parsed["new_rollout_count"], 2)
            self.assertEqual(parsed["lifecycle"][0]["task_name"], "unit_a")
            self.assertEqual(
                parsed["lifecycle"][0]["binding_provenance"], "persistent-rollout-v3"
            )
            self.assertEqual(parsed["aggregate_usage"]["input_tokens"], 15)
            self.assertEqual(parsed["aggregate_usage"]["output_tokens"], 5)

    def test_rollout_v3_rejects_unlinked_child_and_missing_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self._write_rollout_pair(home, child_parent="other-parent")
            with self.assertRaisesRegex(runner.LiveEvaluationError, "not linked"):
                runner.parse_new_rollouts(home, {}, "parent-1")
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self._write_rollout_pair(home, include_child_usage=False)
            with self.assertRaisesRegex(
                runner.LiveEvaluationError, "final provider usage"
            ):
                runner.parse_new_rollouts(home, {}, "parent-1")

    def test_controller_directive_is_observable_and_mode_specific(self) -> None:
        base = "Do the task"
        forced, forced_receipt = runner.compose_controller_prompt(base, "forced-solo")
        adaptive, adaptive_receipt = runner.compose_controller_prompt(base, "adaptive")
        self.assertTrue(forced.startswith(base))
        self.assertTrue(adaptive.startswith(base))
        self.assertEqual(
            forced_receipt["template_sha256"], adaptive_receipt["template_sha256"]
        )
        self.assertNotEqual(
            forced_receipt["mode_sha256"], adaptive_receipt["mode_sha256"]
        )
        self.assertIn("Do not spawn", forced)
        self.assertIn("consult and execute", adaptive)
        self.assertIn("exactly one complete canonical v2 agent_plan", adaptive)

    def test_rollout_binding_rejects_wrong_task_name_and_forced_solo_spawn(
        self,
    ) -> None:
        plan = {
            "mode": "parallel-read-only",
            "waves": [
                {
                    "kind": "read-only-investigation",
                    "parallel": True,
                    "assignments": [
                        {
                            "id": unit_id,
                            "assignment_id": assignment_id,
                            "role": "investigator",
                            "permissions": "read-only",
                            "ownership": [],
                            "dependencies": [],
                            "delegation_depth": 1,
                            "may_spawn": False,
                            "may_verify_parent": False,
                        }
                        for unit_id, assignment_id in (
                            ("unit-a", "a-1"),
                            ("unit-b", "b-1"),
                        )
                    ],
                }
            ],
        }
        lifecycle = [
            {
                "assignment_id": None,
                "task_name": "wrong_name",
                "actor_id": "child-1",
                "role": None,
                "parent_id": "parent-1",
                "delegation_depth": 1,
                "phases": ["spawned", "joined", "result"],
                "usage": {
                    "input_tokens": 1,
                    "cached_input_tokens": 0,
                    "output_tokens": 1,
                },
                "binding_provenance": "persistent-rollout-v3",
            }
        ]
        bound = runner._bind_rollout_assignments(plan, lifecycle)
        self.assertIsNone(bound[0]["assignment_id"])
        forced = runner.classify_agent_decision(
            {
                "agent_plans": [],
                "agent_spawns": 1,
                "agent_joins": 1,
                "agent_lifecycle": lifecycle,
                "usage_includes_subagents": True,
                "parent_thread_id": "parent-1",
                "host_errors": [],
            },
            "forced-solo",
        )
        self.assertFalse(forced["complete"])

    def test_controller_protocol_is_bound_and_fails_closed_when_changed(self) -> None:
        canonical = runner.load_controller_protocol(
            PLUGIN_ROOT / "benchmarks" / "controller_ab_protocol.json"
        )
        self.assertEqual(canonical["protocol_id"], "cognitive-powers-controller-ab-v15")
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
            plan = self._canonical_plan()
            rows = [
                {"type": "thread.started", "thread_id": "thread-1"},
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "Selected plan:\n```json\n"
                        + json.dumps({"agent_plan": plan})
                        + "\n```",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {"type": "function_call", "name": "spawn_agent"},
                },
                {
                    "type": "agent.lifecycle",
                    "provenance": "host",
                    "assignment_id": "assignment-1",
                    "actor_id": "worker-1",
                    "role": "researcher",
                    "event": "spawned",
                },
                {
                    "type": "agent.lifecycle",
                    "provenance": "host",
                    "assignment_id": "assignment-1",
                    "actor_id": "worker-1",
                    "role": "researcher",
                    "event": "joined",
                },
                {
                    "type": "agent.lifecycle",
                    "provenance": "host",
                    "assignment_id": "assignment-1",
                    "actor_id": "worker-1",
                    "role": "researcher",
                    "event": "completed",
                    "usage": {"input_tokens": 4, "output_tokens": 2},
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
            self.assertEqual(parsed["parent_thread_id"], "thread-1")

    def test_parse_events_accepts_solo_to_fresh_verification_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events.jsonl"
            rows = [
                {"type": "thread.started", "thread_id": "thread-1"},
                *[
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps({"agent_plan": plan}),
                        },
                    }
                    for plan in (
                        self._canonical_solo_plan(),
                        self._canonical_verifier_plan(),
                    )
                ],
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

            self.assertEqual(parsed["plan_receipt_count"], 2)
            self.assertEqual(parsed["plan_transition"], "solo-to-fresh-verification")
            self.assertEqual(parsed["agent_plans"][-1]["mode"], "staged-verify")

    def test_parse_events_rejects_non_solo_plan_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events.jsonl"
            rows = [
                *[
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps({"agent_plan": plan}),
                        },
                    }
                    for plan in (
                        self._canonical_plan(),
                        self._canonical_verifier_plan(),
                    )
                ],
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

            with self.assertRaisesRegex(
                runner.LiveEvaluationError,
                "multiple distinct agent_plan receipts were emitted",
            ):
                runner.parse_events(events)

    def test_parse_events_does_not_infer_plan_from_spawn_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events.jsonl"
            rows = [
                {"type": "thread.started", "thread_id": "thread-1"},
                {
                    "type": "item.completed",
                    "item": {
                        "type": "function_call",
                        "name": "spawn_agent",
                        "arguments": json.dumps({"agent_plan": self._canonical_plan()}),
                        "output": json.dumps({"agent_plan": self._canonical_plan()}),
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
            parsed = runner.parse_events(events)
            self.assertEqual(parsed["agent_spawns"], 1)
            self.assertEqual(parsed["agent_plans"], [])

    def test_parse_events_rejects_malformed_or_noncanonical_emitted_plan(self) -> None:
        for message in (
            '```json\n{"agent_plan": {broken}\n```',
            "```json\n"
            + json.dumps(
                {
                    "agent_plan": {
                        "schema_version": 2,
                        "kind": "agent_plan",
                        "mode": "solo",
                        "selected_mode": "solo",
                        "executed_mode": None,
                        "outcome": "planned",
                        "waves": [],
                        "plan_id": "plan-forged",
                    }
                }
            )
            + "\n```",
        ):
            with (
                self.subTest(message=message),
                tempfile.TemporaryDirectory() as temporary,
            ):
                events = Path(temporary) / "events.jsonl"
                rows = [
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": message},
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
                    "\n".join(json.dumps(row) for row in rows) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(runner.LiveEvaluationError):
                    runner.parse_events(events)

    def test_no_agent_fast_path_is_observed_as_solo(self) -> None:
        parsed = {
            "agent_plans": [],
            "agent_spawns": 0,
            "agent_joins": 0,
            "observed_assignments": [],
            "usage_includes_subagents": False,
            "agent_lifecycle": [],
            "parent_thread_id": "thread-1",
            "host_errors": [],
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
            "agent_lifecycle": [],
            "parent_thread_id": "thread-1",
            "host_errors": [],
        }
        decision = runner.classify_agent_decision(parsed, "adaptive")
        self.assertEqual(decision["actual_mode"], "solo")
        self.assertEqual(decision["decision_observation"], "missing")
        self.assertFalse(decision["complete"])

    def test_multiple_unbound_lifecycle_entries_fail_closed_without_crashing(
        self,
    ) -> None:
        plan = {
            "mode": "parallel-read-only",
            "waves": [
                {
                    "assignments": [
                        {"assignment_id": "investigator-a"},
                        {"assignment_id": "investigator-b"},
                    ]
                }
            ],
        }
        lifecycle = [
            {
                "assignment_id": None,
                "actor_id": f"child-{index}",
                "phases": ["spawned", "joined", "result"],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            for index in range(2)
        ]
        parsed = {
            "agent_plans": [plan],
            "agent_spawns": 2,
            "agent_joins": 2,
            "agent_lifecycle": lifecycle,
            "usage_includes_subagents": True,
            "parent_thread_id": "thread-1",
            "host_errors": [],
        }

        decision = runner.classify_agent_decision(parsed, "adaptive")

        self.assertFalse(decision["complete"])
        self.assertEqual(
            decision["agent_execution_receipt"]["invalid_lifecycle_assignment_count"],
            2,
        )
        self.assertEqual(
            decision["agent_execution_receipt"]["spawned_assignment_ids"], []
        )

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
            "agent_lifecycle": [],
            "parent_thread_id": "thread-1",
            "host_errors": [],
        }
        decision = runner.classify_agent_decision(parsed, "adaptive")
        self.assertEqual(decision["planned_assignment_count"], 1)
        self.assertEqual(decision["actual_mode"], "solo")
        self.assertFalse(decision["complete"])
        self.assertEqual(decision["selected_mode"], "parallel-packets")
        self.assertEqual(decision["executed_mode"], "solo")
        self.assertEqual(decision["outcome"], "degraded")

    def test_complete_delegation_requires_exact_host_lifecycle_and_usage(self) -> None:
        executor = {
            "id": "executor-unit",
            "assignment_id": "executor",
            "role": "executor",
            "permissions": "write-owned-paths",
            "ownership": ["src/feature.py"],
            "dependencies": [],
            "delegation_depth": 1,
            "may_spawn": False,
            "may_verify_parent": False,
        }
        verifier = {
            "id": "verifier-unit",
            "assignment_id": "verifier",
            "role": "verifier",
            "permissions": "read-only",
            "ownership": [],
            "dependencies": ["executor-unit"],
            "delegation_depth": 1,
            "may_spawn": False,
            "may_verify_parent": False,
            "must_be_distinct_from": ["executor-unit"],
        }
        plan = {
            "mode": "staged-verify",
            "waves": [
                {
                    "kind": "implementation",
                    "parallel": False,
                    "assignments": [executor],
                },
                {
                    "kind": "verification",
                    "parallel": False,
                    "assignments": [verifier],
                },
            ],
        }
        lifecycle = [
            {
                "assignment_id": assignment,
                "actor_id": f"actor-{assignment}",
                "role": role,
                "parent_id": "root-actor",
                "delegation_depth": 1,
                "phases": ["spawned", "joined", "result"],
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "output_tokens": 2,
                },
            }
            for assignment, role in (("executor", "executor"), ("verifier", "verifier"))
        ]
        parsed = {
            "agent_plans": [plan],
            "agent_spawns": 2,
            "agent_joins": 2,
            "observed_assignments": [],
            "agent_lifecycle": lifecycle,
            "usage_includes_subagents": True,
            "parent_thread_id": "thread-1",
            "host_errors": [],
        }
        decision = runner.classify_agent_decision(parsed, "adaptive")
        self.assertTrue(decision["complete"])
        self.assertEqual(decision["selected_mode"], "staged-verify")
        self.assertEqual(decision["executed_mode"], "staged-verify")
        self.assertEqual(
            decision["agent_execution_receipt"]["descendant_total_tokens"], 24
        )

        parsed["agent_lifecycle"][1]["phases"] = ["spawned", "result"]
        self.assertFalse(runner.classify_agent_decision(parsed, "adaptive")["complete"])

    def test_staged_verify_rejects_inverted_roles_and_same_actor(self) -> None:
        executor = {
            "id": "executor-unit",
            "assignment_id": "executor",
            "role": "executor",
            "permissions": "write-owned-paths",
            "ownership": ["src/feature.py"],
            "dependencies": [],
            "delegation_depth": 1,
            "may_spawn": False,
            "may_verify_parent": False,
        }
        verifier = {
            "id": "verifier-unit",
            "assignment_id": "verifier",
            "role": "verifier",
            "permissions": "read-only",
            "ownership": [],
            "dependencies": ["executor-unit"],
            "delegation_depth": 1,
            "may_spawn": False,
            "may_verify_parent": False,
        }
        plan = {
            "mode": "staged-verify",
            "waves": [
                {
                    "kind": "implementation",
                    "parallel": False,
                    "assignments": [executor],
                },
                {"kind": "verification", "parallel": False, "assignments": [verifier]},
            ],
        }
        lifecycle = [
            {
                "assignment_id": assignment,
                "actor_id": f"actor-{assignment}",
                "role": role,
                "parent_id": "root-actor",
                "delegation_depth": 1,
                "phases": ["spawned", "joined", "result"],
                "usage": {
                    "input_tokens": 1,
                    "cached_input_tokens": 0,
                    "output_tokens": 1,
                },
            }
            for assignment, role in (("executor", "verifier"), ("verifier", "executor"))
        ]
        parsed = {
            "agent_plans": [plan],
            "agent_spawns": 2,
            "agent_joins": 2,
            "agent_lifecycle": lifecycle,
            "usage_includes_subagents": True,
            "parent_thread_id": "thread-1",
            "host_errors": [],
        }
        decision = runner.classify_agent_decision(parsed, "adaptive")
        self.assertFalse(decision["complete"])
        self.assertFalse(decision["agent_execution_receipt"]["semantic_binding"])
        for item, role in zip(lifecycle, ("executor", "verifier"), strict=True):
            item["role"] = role
            item["actor_id"] = "same-actor"
        decision = runner.classify_agent_decision(parsed, "adaptive")
        self.assertFalse(decision["complete"])
        self.assertFalse(decision["agent_execution_receipt"]["semantic_binding"])

    def test_staged_verify_accepts_canonical_verifier_only_wave(self) -> None:
        verifier = {
            "id": "fresh-verifier",
            "assignment_id": "assignment-verifier",
            "role": "verifier",
            "permissions": "read-only",
            "ownership": [],
            "dependencies": [],
            "delegation_depth": 1,
            "may_spawn": False,
            "may_verify_parent": False,
        }
        parsed = {
            "agent_plans": [
                {
                    "mode": "staged-verify",
                    "waves": [
                        {
                            "kind": "verification",
                            "parallel": False,
                            "assignments": [verifier],
                        }
                    ],
                }
            ],
            "agent_spawns": 1,
            "agent_joins": 1,
            "agent_lifecycle": [
                {
                    "assignment_id": "assignment-verifier",
                    "task_name": "fresh_verifier",
                    "actor_id": "actor-verifier",
                    "role": None,
                    "parent_id": "root-actor",
                    "delegation_depth": 1,
                    "phases": ["spawned", "joined", "result"],
                    "usage": {
                        "input_tokens": 5,
                        "cached_input_tokens": 1,
                        "output_tokens": 2,
                    },
                    "binding_provenance": "persistent-rollout-v3",
                }
            ],
            "usage_includes_subagents": True,
            "parent_thread_id": "root-actor",
            "host_errors": [],
        }

        decision = runner.classify_agent_decision(parsed, "adaptive")

        self.assertTrue(decision["complete"])
        self.assertEqual(decision["executed_mode"], "staged-verify")
        self.assertTrue(decision["agent_execution_receipt"]["semantic_binding"])

    def test_no_thread_host_error_fails_closed(self) -> None:
        parsed = {
            "agent_plans": [],
            "agent_spawns": 0,
            "agent_joins": 0,
            "observed_assignments": [],
            "agent_lifecycle": [],
            "usage_includes_subagents": False,
            "parent_thread_id": None,
            "host_errors": ["no thread with id"],
        }
        decision = runner.classify_agent_decision(parsed, "adaptive")
        self.assertFalse(decision["complete"])
        self.assertEqual(decision["outcome"], "degraded")

    def test_codex_command_is_persistent_and_enables_multi_agent(self) -> None:
        command = runner.build_codex_command(
            codex="codex",
            fixture=Path("fixture"),
            message=Path("message.txt"),
            prompt="work",
            model="gpt-test",
            reasoning_effort="medium",
            bypass_sandbox=False,
        )
        self.assertNotIn("--ephemeral", command)
        self.assertIn("features.multi_agent=true", command)

    def test_host_identity_freezes_binary_version_and_effective_features(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "codex.exe"
            executable.write_bytes(b"binary")
            version = mock.Mock(returncode=0, stdout="codex 1.2.3\n", stderr="")
            features = mock.Mock(
                returncode=0,
                stdout="multi_agent stable true\nmemories stable false\n",
                stderr="",
            )
            with (
                mock.patch.object(runner.shutil, "which", return_value=str(executable)),
                mock.patch.object(
                    runner.subprocess, "run", side_effect=[version, features]
                ),
            ):
                identity = runner.codex_host_identity("codex")
            self.assertEqual(identity["version"], "codex 1.2.3")
            self.assertEqual(len(identity["executable_sha256"]), 64)
            self.assertTrue(identity["effective_features"]["multi_agent"]["enabled"])

    def test_parse_events_rejects_unknown_schema_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events.jsonl"
            events.write_text(
                json.dumps({"type": "future.event"}) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(runner.LiveEvaluationError, "unsupported"):
                runner.parse_events(events)

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
        source_git = {
            "head": "c" * 40,
            "status_sha256": "e" * 64,
            "sha256": "f" * 64,
        }
        git_patch = mock.patch.object(runner, "git_identity", return_value=source_git)
        git_patch.start()
        self.addCleanup(git_patch.stop)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            home = root / "home"
            installed = home / "plugins" / "cache" / "personal" / "demo" / "1.0"
            for relative in runner.INSTALLED_SURFACE_DIRECTORIES:
                (source / relative).mkdir(parents=True)
                (source / relative / "runtime.txt").write_text(
                    relative, encoding="utf-8"
                )
            for relative in runner.INSTALLED_SURFACE_FILES:
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(relative, encoding="utf-8")
            (source / "plugin.txt").write_text("current\n", encoding="utf-8")
            installed.parent.mkdir(parents=True)
            shutil.copytree(source, installed)
            (installed / "plugin.txt").write_text("stale\n", encoding="utf-8")
            item = {
                "version": "1.0",
                "name": "demo",
                "marketplaceName": "personal",
                "source": {"path": str(source)},
            }

            with self.assertRaisesRegex(
                runner.LiveEvaluationError, "canonical runtime surface"
            ):
                runner._candidate_identity(item, home)

            (installed / "plugin.txt").write_text("current\n", encoding="utf-8")
            identity = runner._candidate_identity(item, home)
            self.assertEqual(identity["source_sha256"], identity["installed_sha256"])
            self.assertEqual(identity["source_commit"], source_git["head"])
            self.assertEqual(identity["source_git"], source_git)

    def test_candidate_identity_accepts_only_exact_canonical_runtime_projection(
        self,
    ) -> None:
        source_git = {
            "head": "c" * 40,
            "status_sha256": "e" * 64,
            "sha256": "f" * 64,
        }
        git_patch = mock.patch.object(runner, "git_identity", return_value=source_git)
        git_patch.start()
        self.addCleanup(git_patch.stop)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            home = root / "home"
            installed = home / "plugins" / "cache" / "personal" / "demo" / "1.0"
            for relative in runner.INSTALLED_SURFACE_DIRECTORIES:
                (source / relative).mkdir(parents=True)
                (source / relative / "runtime.txt").write_text(
                    relative, encoding="utf-8"
                )
            for relative in runner.INSTALLED_SURFACE_FILES:
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(relative, encoding="utf-8")
            (source / "benchmarks").mkdir()
            (source / "benchmarks" / "evaluation_tasks.json").write_text(
                "sensitive", encoding="utf-8"
            )
            installed.parent.mkdir(parents=True)
            shutil.copytree(
                source,
                installed,
                ignore=shutil.ignore_patterns("benchmarks"),
            )
            item = {
                "version": "1.0",
                "name": "demo",
                "marketplaceName": "personal",
                "source": {"path": str(source)},
            }

            identity = runner._candidate_identity(item, home)

            self.assertNotEqual(identity["source_sha256"], identity["installed_sha256"])
            self.assertGreater(identity["source_file_count"], identity["file_count"])
            self.assertEqual(identity["source_commit"], source_git["head"])
            (installed / "skills" / "runtime.txt").unlink()
            with self.assertRaisesRegex(
                runner.LiveEvaluationError, "canonical runtime surface"
            ):
                runner._candidate_identity(item, home)

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
