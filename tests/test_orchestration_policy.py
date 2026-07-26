from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "solve-efficiently" / "scripts" / "orchestration_policy.py"
)
CASES_PATH = PLUGIN_ROOT / "benchmarks" / "orchestration_cases.json"
AGENT_CASES_PATH = PLUGIN_ROOT / "benchmarks" / "agent_plan_cases.json"


def load_policy():
    spec = importlib.util.spec_from_file_location(
        "test_orchestration_policy_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


policy = load_policy()


def signals(**overrides):
    result = {
        "schema_version": 1,
        "request_mode": "change",
        "estimated_steps": 1,
        "affected_files": 1,
        "unclear_context": False,
        "cross_cutting": False,
        "multi_turn_expected": False,
        "compaction_risk": False,
        "resumable_required": False,
        "durable_evidence_required": False,
    }
    result.update(overrides)
    return result


def unit(unit_id: str, **overrides):
    result = {
        "id": unit_id,
        "role": "investigator",
        "objective": f"Investigate {unit_id}",
        "context": ["task contract"],
        "owned_paths": [],
        "dependencies": [],
        "read_only": True,
        "ready": True,
        "distinct_output": True,
        "expected_output": "Evidence-backed finding",
        "check": ["python", "-m", "unittest"],
        "stop_conditions": ["Stop if the assigned boundary is invalid"],
        "red_test_possible": False,
        "depth": 1,
    }
    result.update(overrides)
    return result


def agent_signals(**overrides):
    result = {
        "schema_version": 1,
        "request_mode": "diagnose",
        "phase": "diagnose",
        "authorization": "read-only",
        "boundaries_clear": True,
        "cheap_local_step_available": False,
        "symptom_reproduced": True,
        "durable_or_release_critical": False,
        "quality_claim": False,
        "delegated_change": False,
        "packet_plan_valid": False,
        "previous_worker_failed": False,
        "failure_classified": False,
        "available_agent_slots": 4,
        "current_depth": 0,
        "retry_attempts": 0,
        "completed_unit_ids": [],
        "units": [unit("lane-a"), unit("lane-b")],
    }
    result.update(overrides)
    return result


def agent_signals_v2(**overrides):
    result = agent_signals()
    result["schema_version"] = 2
    result.pop("previous_worker_failed")
    result.pop("failure_classified")
    result.pop("retry_attempts")
    result["retry_record"] = None
    result["verification_check"] = ["python", "-m", "unittest"]
    result.update(overrides)
    return result


def red_observation():
    return {
        "observed": True,
        "command": ["python", "-m", "unittest", "tests.test_feature"],
        "exit_code": 1,
        "evidence": "test_feature failed before implementation",
        "state_sha256": "a" * 64,
    }


class OrchestrationPolicyTests(unittest.TestCase):
    def test_explain_is_deterministic_complete_and_side_effect_free(self) -> None:
        payload = agent_signals_v2(
            completed_unit_ids=["done"],
            units=[
                unit("done"),
                unit("eligible", dependencies=["done"]),
                unit("excluded", ready=False),
            ],
        )
        first = policy.explain_agent_plan(payload)
        second = policy.explain_agent_plan(payload)
        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )
        self.assertEqual(first["selected_mode"], first["plan"]["mode"])
        self.assertEqual(first["units"]["completed"], ["done"])
        self.assertEqual(first["units"]["eligible"], ["eligible"])
        self.assertEqual(
            first["units"]["excluded"], [{"id": "excluded", "reasons": ["not-ready"]}]
        )
        self.assertEqual(
            first["dependencies"], {"done": [], "eligible": ["done"], "excluded": []}
        )
        self.assertIn("waves", first)
        self.assertIn("capacity", first)
        self.assertIn("signals_consumed", first)
        self.assertIn("abstentions", first)
        self.assertIn("pending_gates", first)

    def test_explain_uses_the_real_phase_and_authorization_eligibility(self) -> None:
        payload = agent_signals_v2(
            phase="diagnose",
            request_mode="change",
            authorization="change",
            units=[
                unit(
                    "writer",
                    role="executor",
                    read_only=False,
                    owned_paths=["src/"],
                    red_test_possible=True,
                    red_observation=red_observation(),
                ),
                unit("reader-a"),
                unit("reader-b"),
            ],
        )
        explanation = policy.explain_agent_plan(payload)
        self.assertNotIn("writer", explanation["units"]["eligible"])
        self.assertIn(
            {"id": "writer", "reasons": ["phase-role-filter"]},
            explanation["units"]["excluded"],
        )

    def test_agent_plan_template_is_versioned_compact_and_executable(self) -> None:
        def discover(version: int) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--agent-plan-template",
                    str(version),
                    "--json",
                ],
                cwd=PLUGIN_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        completed_v1 = discover(1)
        completed_v2 = discover(2)
        v1 = json.loads(completed_v1.stdout)
        v2 = json.loads(completed_v2.stdout)
        planned = policy.select_agent_plan(v2["template"])

        self.assertEqual(v1["schema_version"], 1)
        self.assertEqual(v1["planner_input_schema_version"], 1)
        self.assertEqual(v2["planner_input_schema_version"], 2)
        self.assertEqual(v2["supported_planner_input_schema_versions"], [1, 2])
        self.assertEqual(
            v2["allowed_values"],
            {
                "request_mode": ["answer", "change", "diagnose", "monitor"],
                "phase": ["diagnose", "discover", "implement", "verify"],
                "authorization": ["change", "read-only"],
                "unit_role": [
                    "executor",
                    "investigator",
                    "researcher",
                    "reviewer",
                    "test-writer",
                ],
            },
        )
        self.assertTrue(v2["field_rules"]["enum_values_are_exact"])
        self.assertIn(
            "do not put role=verifier",
            v2["field_rules"]["verifier_units_are_synthetic"],
        )
        self.assertIn("retry_record", v2["template"])
        self.assertIn("previous_worker_failed", v1["template"])
        self.assertTrue(planned["valid_input"])
        self.assertEqual(
            completed_v1.returncode, 0, completed_v1.stdout + completed_v1.stderr
        )
        self.assertEqual(
            completed_v2.returncode, 0, completed_v2.stdout + completed_v2.stderr
        )

    def test_new_plan_distinguishes_selection_from_host_execution(self) -> None:
        delegated = policy.select_agent_plan(agent_signals_v2())
        solo = policy.select_agent_plan(agent_signals_v2(boundaries_clear=False))

        for plan in (delegated, solo):
            self.assertEqual(plan["selected_mode"], plan["mode"])
            self.assertIsNone(plan["executed_mode"])
            self.assertEqual(plan["outcome"], "planned")
            self.assertIsNone(plan["degradation"])

    def test_simple_work_abstains_from_heavy_process(self) -> None:
        result = policy.select_intensity(signals())

        self.assertEqual(result["intensity"], "focused")
        self.assertFalse(result["process"]["external_state"])
        self.assertFalse(result["process"]["evidence_receipts"])
        self.assertFalse(result["process"]["memory_retrieval"])
        self.assertFalse(result["process"]["delegation"])
        self.assertIn("short, local", result["reasons"][0])

    def test_standard_boundary_is_two_files_or_three_steps(self) -> None:
        two_files = policy.select_intensity(signals(affected_files=2))
        three_steps = policy.select_intensity(signals(estimated_steps=3))

        self.assertEqual(two_files["intensity"], "standard")
        self.assertEqual(three_steps["intensity"], "standard")
        self.assertTrue(two_files["process"]["progressive_context"])
        self.assertFalse(two_files["process"]["external_state"])

    def test_any_explicit_durable_signal_takes_precedence(self) -> None:
        for field in (
            "multi_turn_expected",
            "compaction_risk",
            "resumable_required",
            "durable_evidence_required",
        ):
            with self.subTest(field=field):
                result = policy.select_intensity(signals(**{field: True}))
                self.assertEqual(result["intensity"], "durable")
                self.assertTrue(result["process"]["external_state"])
                self.assertTrue(result["process"]["evidence_receipts"])

    def test_diagnosis_never_implies_implementation(self) -> None:
        focused = policy.select_intensity(signals(request_mode="diagnose"))
        durable = policy.select_intensity(
            signals(request_mode="diagnose", compaction_risk=True)
        )

        self.assertFalse(focused["implementation_authorized"])
        self.assertFalse(durable["implementation_authorized"])
        self.assertIn("investigation-only", durable["reasons"][-1])

    def test_boolean_is_not_accepted_as_an_integer(self) -> None:
        with self.assertRaisesRegex(policy.OrchestrationError, "estimated_steps"):
            policy.select_intensity(signals(estimated_steps=True))

    def test_fixture_covers_all_intensities_and_passes(self) -> None:
        data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        report = policy.evaluate_cases(CASES_PATH)

        self.assertEqual(
            {case["expected_intensity"] for case in data["cases"]},
            {"focused", "standard", "durable"},
        )
        self.assertTrue(report["passed"])
        self.assertFalse(report["end_to_end_improvement_proven"])

    def test_cli_emits_machine_readable_case_report(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--cases", str(CASES_PATH), "--json"],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["passed"])

    def test_agent_plan_fails_closed_without_complete_signals(self) -> None:
        result = policy.select_agent_plan({"schema_version": 1})

        self.assertEqual(result["mode"], "solo")
        self.assertFalse(result["valid_input"])
        self.assertEqual(result["spawn_count"], 0)

    def test_small_unclear_or_cheaper_work_stays_solo(self) -> None:
        one_unit = policy.select_agent_plan(agent_signals(units=[unit("only")]))
        unclear = policy.select_agent_plan(agent_signals(boundaries_clear=False))
        cheaper = policy.select_agent_plan(
            agent_signals(cheap_local_step_available=True)
        )

        self.assertEqual(one_unit["mode"], "solo")
        self.assertIn("boundaries", unclear["reasons"][0])
        self.assertIn("cheaper local", cheaper["reasons"][0])

    def test_v2_completed_units_are_not_rescheduled(self) -> None:
        plan = policy.select_agent_plan(
            agent_signals_v2(
                quality_claim=True,
                available_agent_slots=2,
                completed_unit_ids=["done"],
                units=[
                    unit("done"),
                    unit("next", dependencies=["done"]),
                ],
            )
        )
        worker_ids = [
            assignment["id"]
            for wave in plan["waves"]
            if wave["kind"] != "verification"
            for assignment in wave["assignments"]
        ]

        self.assertEqual(worker_ids, ["next"])
        self.assertEqual(plan["spawn_count"], 1)

    def test_diagnosis_reproduces_before_parallel_investigation(self) -> None:
        result = policy.select_agent_plan(agent_signals(symptom_reproduced=False))

        self.assertEqual(result["mode"], "solo")
        self.assertIn("reproduce", result["reasons"][0])

    def test_read_only_plan_obeys_slot_cap_and_is_deterministic(self) -> None:
        units = [unit(f"lane-{index}") for index in range(5)]
        one_worker = policy.select_agent_plan(
            agent_signals(units=units, available_agent_slots=2)
        )
        capped = policy.select_agent_plan(agent_signals(units=units))
        reversed_result = policy.select_agent_plan(
            agent_signals(units=list(reversed(units)))
        )

        self.assertEqual(one_worker["mode"], "parallel-read-only")
        self.assertEqual(one_worker["spawn_count"], 5)
        self.assertEqual(one_worker["max_concurrent_workers"], 1)
        self.assertEqual(len(one_worker["waves"]), 5)
        self.assertEqual(capped["mode"], "parallel-read-only")
        self.assertEqual(capped["spawn_count"], 5)
        self.assertEqual(capped["max_concurrent_workers"], 3)
        self.assertEqual(
            [len(wave["assignments"]) for wave in capped["waves"]],
            [3, 2],
        )
        self.assertEqual(capped["max_depth"], 1)
        self.assertEqual(capped, reversed_result)
        assignment = capped["waves"][0]["assignments"][0]
        self.assertEqual(assignment["permissions"], "read-only")
        self.assertFalse(assignment["may_spawn"])
        self.assertFalse(assignment["may_verify_parent"])

    def test_diagnosis_never_delegates_write_units(self) -> None:
        write_units = [
            unit(
                "writer-a",
                role="executor",
                read_only=False,
                owned_paths=["src/a.py"],
            ),
            unit(
                "writer-b",
                role="executor",
                read_only=False,
                owned_paths=["src/b.py"],
            ),
        ]
        result = policy.select_agent_plan(agent_signals(units=write_units))

        self.assertEqual(result["mode"], "solo")
        self.assertEqual(result["spawn_count"], 0)

    def test_disjoint_write_packets_parallelize_then_verify(self) -> None:
        write_units = [
            unit(
                "writer-a",
                role="executor",
                read_only=False,
                owned_paths=["src/a.py"],
            ),
            unit(
                "writer-b",
                role="executor",
                read_only=False,
                owned_paths=["src/b.py"],
            ),
        ]
        result = policy.select_agent_plan(
            agent_signals(
                request_mode="change",
                phase="implement",
                authorization="change",
                packet_plan_valid=True,
                units=write_units,
            )
        )

        self.assertEqual(result["mode"], "parallel-packets")
        self.assertTrue(result["reserve_verifier_slot"])
        self.assertEqual(
            [wave["kind"] for wave in result["waves"]],
            ["implementation", "verification"],
        )
        verifier = result["waves"][-1]["assignments"][0]
        self.assertEqual(verifier["permissions"], "read-only")
        self.assertEqual(
            set(verifier["must_be_distinct_from"]), {"writer-a", "writer-b"}
        )

    def test_overlapping_or_invalid_packet_plan_fails_closed(self) -> None:
        overlapping = [
            unit(
                "writer-a",
                role="executor",
                read_only=False,
                owned_paths=["src"],
            ),
            unit(
                "writer-b",
                role="executor",
                read_only=False,
                owned_paths=["src/b.py"],
            ),
        ]
        base = agent_signals(
            request_mode="change",
            phase="implement",
            authorization="change",
            packet_plan_valid=True,
            units=overlapping,
        )
        overlap_result = policy.select_agent_plan(base)
        invalid_plan = policy.select_agent_plan({**base, "packet_plan_valid": False})

        self.assertIn("overlaps", overlap_result["reasons"][0])
        self.assertIn("validated packet", invalid_plan["reasons"][0])

    def test_test_writer_requires_real_red_and_runs_before_executor(self) -> None:
        test_writer = unit(
            "tests",
            role="test-writer",
            read_only=False,
            owned_paths=["tests/test_feature.py"],
            red_test_possible=True,
        )
        executor = unit(
            "source",
            role="executor",
            read_only=False,
            owned_paths=["src/feature.py"],
            dependencies=["tests"],
        )
        base = agent_signals(
            request_mode="change",
            phase="implement",
            authorization="change",
            packet_plan_valid=True,
            units=[test_writer, executor],
        )
        result = policy.select_agent_plan(base)
        no_red = policy.select_agent_plan(
            {**base, "units": [{**test_writer, "red_test_possible": False}, executor]}
        )

        self.assertEqual(result["mode"], "staged-verify")
        self.assertEqual(
            [wave["kind"] for wave in result["waves"]],
            ["test-first", "implementation", "verification"],
        )
        self.assertIn("structured RED", no_red["reasons"][0])

    def test_verify_phase_uses_one_fresh_read_only_agent(self) -> None:
        result = policy.select_agent_plan(
            agent_signals(phase="verify", units=[], delegated_change=True)
        )

        self.assertEqual(result["mode"], "staged-verify")
        self.assertEqual(result["spawn_count"], 0)
        self.assertEqual(result["total_planned_agents"], 1)
        self.assertEqual(result["waves"][0]["kind"], "verification")

    def test_single_executor_can_stage_a_distinct_verifier_without_parallel_packet_plan(
        self,
    ) -> None:
        result = policy.select_agent_plan(
            agent_signals_v2(
                request_mode="change",
                phase="implement",
                authorization="change",
                durable_or_release_critical=True,
                quality_claim=True,
                packet_plan_valid=False,
                units=[
                    unit(
                        "executor",
                        role="executor",
                        read_only=False,
                        owned_paths=["src/target.txt"],
                    )
                ],
                verification_check=["python", "verification/verify.py"],
            )
        )

        self.assertEqual(result["mode"], "staged-verify")
        self.assertEqual(
            [wave["kind"] for wave in result["waves"]],
            ["implementation", "verification"],
        )
        executor = result["waves"][0]["assignments"][0]
        verifier = result["waves"][1]["assignments"][0]
        self.assertEqual(verifier["dependencies"], ["executor"])
        self.assertEqual(verifier["permissions"], "read-only")
        self.assertIn(executor["id"], verifier["must_be_distinct_from"])

    def test_workers_cannot_delegate_and_depth_two_is_read_only(self) -> None:
        child_call = policy.select_agent_plan(agent_signals(current_depth=1))
        depth_two = policy.select_agent_plan(
            agent_signals(units=[unit("child-a", depth=2), unit("child-b", depth=2)])
        )
        invalid_child = policy.select_agent_plan(
            agent_signals(
                request_mode="change",
                phase="implement",
                authorization="change",
                packet_plan_valid=True,
                units=[
                    unit(
                        "child-a",
                        role="executor",
                        read_only=False,
                        owned_paths=["a.py"],
                        depth=2,
                    ),
                    unit(
                        "child-b",
                        role="executor",
                        read_only=False,
                        owned_paths=["b.py"],
                        depth=2,
                    ),
                ],
            )
        )

        self.assertEqual(child_call["mode"], "solo")
        self.assertEqual(depth_two["mode"], "parallel-read-only")
        self.assertEqual(depth_two["max_depth"], 2)
        self.assertFalse(invalid_child["valid_input"])

    def test_invalid_dependencies_and_missing_slots_fail_closed(self) -> None:
        cyclic = policy.select_agent_plan(
            agent_signals(
                units=[
                    unit("lane-a", dependencies=["lane-b"]),
                    unit("lane-b", dependencies=["lane-a"]),
                ]
            )
        )
        no_slots = policy.select_agent_plan(agent_signals(available_agent_slots=1))

        self.assertFalse(cyclic["valid_input"])
        self.assertIn("acyclic", cyclic["reasons"][0])
        self.assertEqual(no_slots["mode"], "solo")
        self.assertIn("no worker slot", no_slots["reasons"][0])

    def test_only_one_retry_is_allowed(self) -> None:
        retry = policy.select_agent_plan(
            agent_signals(
                previous_worker_failed=True,
                failure_classified=True,
                retry_attempts=0,
            )
        )
        exhausted = policy.select_agent_plan(
            agent_signals(
                previous_worker_failed=True,
                failure_classified=True,
                retry_attempts=1,
            )
        )
        unclassified = policy.select_agent_plan(
            agent_signals(previous_worker_failed=True, retry_attempts=0)
        )

        self.assertTrue(retry["retry_policy"]["retry_allowed"])
        self.assertEqual(exhausted["mode"], "solo")
        self.assertIn("retry is exhausted", exhausted["reasons"][0])
        self.assertEqual(unclassified["mode"], "solo")
        self.assertIn("classify", unclassified["reasons"][0])

    def test_decision_receipt_is_external_only_for_durable_work(self) -> None:
        normal = policy.select_agent_plan(agent_signals())
        durable = policy.select_agent_plan(
            agent_signals(durable_or_release_critical=True)
        )

        self.assertFalse(normal["receipt_policy"]["emit_json"])
        self.assertTrue(durable["receipt_policy"]["emit_json"])
        self.assertFalse(durable["receipt_policy"]["end_to_end_improvement_proven"])

    def test_worker_result_contract_requires_real_command_exits(self) -> None:
        valid = policy.validate_worker_result(
            {
                "schema_version": 1,
                "status": "completed",
                "changed_paths": ["src/a.py"],
                "commands": [{"argv": ["python", "-m", "unittest"], "exit_code": 0}],
                "blockers": [],
                "risks": ["integration not yet run"],
            }
        )
        invalid = policy.validate_worker_result(
            {
                "schema_version": 1,
                "status": "completed",
                "changed_paths": [],
                "commands": [{"argv": ["python"], "exit_code": "0"}],
                "blockers": [],
                "risks": [],
            }
        )

        self.assertTrue(valid["valid"])
        self.assertFalse(valid["durable_claim_eligible"])
        self.assertFalse(invalid["valid"])

    def test_v2_payloads_fail_closed_and_content_ids_are_deterministic(self) -> None:
        invalid = policy.select_agent_plan(["not", "an", "object"])
        first = policy.select_agent_plan(agent_signals_v2())
        second = policy.select_agent_plan(agent_signals_v2())

        self.assertEqual(invalid["mode"], "solo")
        self.assertFalse(invalid["valid_input"])
        self.assertEqual(first["schema_version"], 2)
        self.assertEqual(first["plan_id"], second["plan_id"])
        assignment_ids = [
            assignment["assignment_id"]
            for wave in first["waves"]
            for assignment in wave["assignments"]
        ]
        self.assertEqual(len(assignment_ids), len(set(assignment_ids)))

    def test_v2_topological_test_writer_requires_observed_red(self) -> None:
        writer = unit(
            "tests",
            role="test-writer",
            read_only=False,
            owned_paths=["tests/test_feature.py"],
            check=["python", "-m", "unittest", "tests.test_feature"],
            red_observation=red_observation(),
        )
        executor = unit(
            "source",
            role="executor",
            read_only=False,
            owned_paths=["src/feature.py"],
            dependencies=["tests"],
        )
        base = agent_signals_v2(
            request_mode="change",
            phase="implement",
            authorization="change",
            packet_plan_valid=True,
            units=[executor, writer],
        )
        result = policy.select_agent_plan(base)
        missing = policy.select_agent_plan(
            {**base, "units": [{**writer, "red_observation": None}, executor]}
        )
        mismatched_red = policy.select_agent_plan(
            {
                **base,
                "units": [
                    {
                        **writer,
                        "red_observation": {
                            **red_observation(),
                            "command": ["python", "unrelated_check.py"],
                        },
                    },
                    executor,
                ],
            }
        )
        overlapping = policy.select_agent_plan(
            {
                **base,
                "units": [
                    {**writer, "owned_paths": ["src"]},
                    executor,
                ],
            }
        )

        self.assertEqual(result["mode"], "staged-verify")
        self.assertEqual(
            [wave["kind"] for wave in result["waves"]],
            ["test-first", "implementation", "verification"],
        )
        self.assertEqual(
            result["waves"][1]["assignments"][0]["dependencies"], ["tests"]
        )
        self.assertEqual(missing["mode"], "solo")
        self.assertIn("observed structured RED", missing["reasons"][0])
        self.assertFalse(mismatched_red["valid_input"])
        self.assertIn("match the assigned check", mismatched_red["reasons"][0])
        self.assertIn("separate ownership", overlapping["reasons"][0])

    def test_v2_verification_check_is_executable_and_verifier_id_is_unique(
        self,
    ) -> None:
        units = [unit("fresh-verifier"), unit("lane-b")]
        valid = policy.select_agent_plan(
            agent_signals_v2(units=units, quality_claim=True)
        )
        marker = policy.select_agent_plan(
            agent_signals_v2(
                units=units,
                quality_claim=True,
                verification_check=["use", "declared", "verification", "target"],
            )
        )
        verifier = valid["waves"][-1]["assignments"][0]

        self.assertEqual(verifier["id"], "fresh-verifier-2")
        self.assertEqual(verifier["check"], ["python", "-m", "unittest"])
        self.assertFalse(marker["valid_input"])
        self.assertIn("marker", marker["reasons"][0])

    def test_windows_paths_fail_closed_and_overlap_case_insensitively(self) -> None:
        for unsafe in (r"C:\src\a.py", r"\\server\share\a.py", "/src/a.py", "../a.py"):
            with self.subTest(path=unsafe):
                result = policy.select_agent_plan(
                    agent_signals_v2(
                        request_mode="change",
                        phase="implement",
                        authorization="change",
                        packet_plan_valid=True,
                        units=[
                            unit(
                                "a",
                                role="executor",
                                read_only=False,
                                owned_paths=[unsafe],
                            ),
                            unit(
                                "b",
                                role="executor",
                                read_only=False,
                                owned_paths=["src/b.py"],
                            ),
                        ],
                    )
                )
                self.assertFalse(result["valid_input"])
        overlap = policy.select_agent_plan(
            agent_signals_v2(
                request_mode="change",
                phase="implement",
                authorization="change",
                packet_plan_valid=True,
                units=[
                    unit(
                        "a",
                        role="executor",
                        read_only=False,
                        owned_paths=["Src"],
                    ),
                    unit(
                        "b",
                        role="executor",
                        read_only=False,
                        owned_paths=["src/b.py"],
                    ),
                ],
            )
        )
        self.assertEqual(overlap["mode"], "solo")
        self.assertIn("overlaps", overlap["reasons"][0])

    def test_v2_retry_is_bound_to_exactly_one_assignment(self) -> None:
        original = policy.select_agent_plan(agent_signals_v2())
        target = original["waves"][0]["assignments"][0]["assignment_id"]
        retry = policy.select_agent_plan(
            agent_signals_v2(
                retry_record={
                    "failed_assignment_id": target,
                    "failure_class": "tool",
                    "evidence": "worker command timed out",
                    "attempts": 0,
                }
            )
        )
        invalid = policy.select_agent_plan(
            agent_signals_v2(
                retry_record={
                    "failed_assignment_id": "assignment-" + "0" * 64,
                    "failure_class": "tool",
                    "evidence": "worker command timed out",
                    "attempts": 0,
                }
            )
        )

        worker_assignments = [
            assignment
            for wave in retry["waves"]
            if wave["kind"] != "verification"
            for assignment in wave["assignments"]
        ]
        self.assertEqual(retry["mode"], "staged-verify")
        self.assertEqual(len(worker_assignments), 1)
        self.assertEqual(worker_assignments[0]["assignment_id"], target)
        self.assertEqual(retry["retry_policy"]["target_assignment_id"], target)
        self.assertFalse(invalid["valid_input"])

    def test_v2_worker_capacity_is_per_wave_and_does_not_reserve_future_verifier(
        self,
    ) -> None:
        for slots, worker_count in ((2, 1), (3, 2), (4, 3)):
            with self.subTest(slots=slots, worker_count=worker_count):
                units = [
                    unit(
                        f"worker-{index}",
                        role="executor",
                        read_only=False,
                        owned_paths=[f"src/worker_{index}.py"],
                    )
                    for index in range(worker_count)
                ]
                plan = policy.select_agent_plan(
                    agent_signals_v2(
                        request_mode="change",
                        phase="implement",
                        authorization="change",
                        delegated_change=True,
                        packet_plan_valid=True,
                        available_agent_slots=slots,
                        units=units,
                    )
                )
                worker_waves = [
                    wave for wave in plan["waves"] if wave["kind"] != "verification"
                ]
                planned_workers = [
                    assignment
                    for wave in worker_waves
                    for assignment in wave["assignments"]
                ]

                self.assertEqual(
                    plan["mode"],
                    "staged-verify" if worker_count == 1 else "parallel-packets",
                )
                self.assertEqual(plan["spawn_count"], worker_count)
                self.assertEqual(plan["total_planned_agents"], worker_count + 1)
                self.assertEqual(
                    plan["max_concurrent_workers"],
                    min(worker_count, slots - 1),
                )
                self.assertTrue(worker_waves)
                self.assertTrue(
                    all(len(wave["assignments"]) <= slots - 1 for wave in worker_waves)
                )
                self.assertEqual(
                    {assignment["id"] for assignment in planned_workers},
                    {item["id"] for item in units},
                )
                self.assertEqual(plan["waves"][-1]["kind"], "verification")
                self.assertEqual(len(plan["waves"][-1]["assignments"]), 1)

    def test_v2_complete_plan_uses_later_waves_instead_of_partial_selection(
        self,
    ) -> None:
        for slots, unit_count in ((2, 2), (3, 2), (3, 3), (4, 3)):
            with self.subTest(slots=slots, unit_count=unit_count):
                units = [unit(f"lane-{index}") for index in range(unit_count)]
                plan = policy.select_agent_plan(
                    agent_signals_v2(
                        available_agent_slots=slots,
                        units=units,
                    )
                )
                assignments = [
                    assignment
                    for wave in plan["waves"]
                    for assignment in wave["assignments"]
                ]

                self.assertEqual(plan["mode"], "parallel-read-only")
                self.assertEqual(plan["spawn_count"], unit_count)
                self.assertEqual(
                    {assignment["id"] for assignment in assignments},
                    {item["id"] for item in units},
                )
                self.assertTrue(
                    all(len(wave["assignments"]) <= slots - 1 for wave in plan["waves"])
                )

    def test_v2_complete_plan_preserves_topology_and_excludes_ineligible_units(
        self,
    ) -> None:
        topological = policy.select_agent_plan(
            agent_signals_v2(
                available_agent_slots=3,
                units=[
                    unit("a"),
                    unit("b", dependencies=["a"]),
                    unit("c", dependencies=["b"]),
                ],
            )
        )
        layers = [
            [assignment["id"] for assignment in wave["assignments"]]
            for wave in topological["waves"]
        ]
        self.assertEqual(layers, [["a"], ["b"], ["c"]])

        filtered = policy.select_agent_plan(
            agent_signals_v2(
                available_agent_slots=3,
                units=[
                    unit("ready-a"),
                    unit("ready-b"),
                    unit("not-ready", ready=False),
                    unit("not-distinct", distinct_output=False),
                ],
            )
        )
        filtered_ids = {
            assignment["id"]
            for wave in filtered["waves"]
            for assignment in wave["assignments"]
        }
        self.assertEqual(filtered_ids, {"ready-a", "ready-b"})
        self.assertEqual(filtered["spawn_count"], 2)

        blocked = policy.select_agent_plan(
            agent_signals_v2(
                available_agent_slots=3,
                units=[
                    unit("blocked-dependency", ready=False),
                    unit("otherwise-ready", dependencies=["blocked-dependency"]),
                    unit("independent"),
                ],
            )
        )
        self.assertEqual(blocked["mode"], "solo")
        self.assertEqual(blocked["spawn_count"], 0)
        self.assertTrue(
            any(
                term in " ".join(blocked["reasons"]).lower()
                for term in ("unscheduled", "blocked", "dependency", "unsatisfied")
            )
        )

    def test_v2_retry_selects_target_even_when_it_is_beyond_first_wave_capacity(
        self,
    ) -> None:
        units = [unit("a"), unit("b"), unit("c")]
        original = policy.select_agent_plan(
            agent_signals_v2(available_agent_slots=4, units=units)
        )
        target = next(
            assignment["assignment_id"]
            for wave in original["waves"]
            for assignment in wave["assignments"]
            if assignment["id"] == "c"
        )
        retry = policy.select_agent_plan(
            agent_signals_v2(
                available_agent_slots=2,
                units=units,
                retry_record={
                    "failed_assignment_id": target,
                    "failure_class": "tool",
                    "evidence": "worker command timed out",
                    "attempts": 0,
                },
            )
        )
        workers = [
            assignment
            for wave in retry["waves"]
            if wave["kind"] != "verification"
            for assignment in wave["assignments"]
        ]
        self.assertEqual(
            [assignment["assignment_id"] for assignment in workers], [target]
        )
        self.assertEqual(retry["spawn_count"], 1)
        self.assertEqual(retry["max_concurrent_workers"], 1)

    def test_v2_result_validation_binds_plan_ownership_and_execution(self) -> None:
        plan = policy.select_agent_plan(
            agent_signals_v2(
                request_mode="change",
                phase="implement",
                authorization="change",
                packet_plan_valid=True,
                units=[
                    unit(
                        "a",
                        role="executor",
                        read_only=False,
                        owned_paths=["src/a.py"],
                    ),
                    unit(
                        "b",
                        role="executor",
                        read_only=False,
                        owned_paths=["src/b.py"],
                    ),
                ],
            )
        )
        assignment = plan["waves"][0]["assignments"][0]
        result = {
            "plan_id": plan["plan_id"],
            "assignment_id": assignment["assignment_id"],
            "actor_id": "worker-a",
            "role": assignment["role"],
            "permissions": assignment["permissions"],
            "ownership": assignment["ownership"],
            "delegation_depth": assignment["delegation_depth"],
            "dependencies": assignment["dependencies"],
            "status": "completed",
            "changed_paths": ["src/a.py"],
            "commands": [{"argv": assignment["check"], "exit_code": 0}],
            "blockers": [],
            "risks": [],
        }
        valid = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": assignment["assignment_id"],
                "result": result,
            }
        )
        execution_context = {
            "schema_version": 1,
            "source_sha256": "c" * 64,
            "actor_bindings": {
                assignment["assignment_id"]: result["actor_id"],
            },
            "worker_results": [],
        }
        eligible = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": assignment["assignment_id"],
                "result": result,
                "execution_context": execution_context,
            }
        )
        missing_source = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": assignment["assignment_id"],
                "result": result,
                "execution_context": {
                    **execution_context,
                    "source_sha256": None,
                },
            }
        )
        missing_actor_binding = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": assignment["assignment_id"],
                "result": result,
                "execution_context": {
                    **execution_context,
                    "actor_bindings": {},
                },
            }
        )
        outside = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": assignment["assignment_id"],
                "result": {**result, "changed_paths": ["src/b.py"]},
            }
        )
        failed_completion = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": assignment["assignment_id"],
                "result": {
                    **result,
                    "commands": [{"argv": assignment["check"], "exit_code": 1}],
                },
            }
        )

        self.assertTrue(valid["valid"])
        self.assertFalse(valid["durable_claim_eligible"])
        self.assertTrue(eligible["valid"])
        self.assertTrue(eligible["durable_claim_eligible"])
        self.assertTrue(missing_source["valid"])
        self.assertFalse(missing_source["durable_claim_eligible"])
        self.assertTrue(missing_actor_binding["valid"])
        self.assertFalse(missing_actor_binding["durable_claim_eligible"])
        self.assertFalse(outside["valid"])
        self.assertFalse(failed_completion["valid"])

    def test_v2_result_requires_declared_check_exact_assignment_and_explicit_noop(
        self,
    ) -> None:
        plan = policy.select_agent_plan(
            agent_signals_v2(
                request_mode="change",
                phase="implement",
                authorization="change",
                packet_plan_valid=True,
                units=[
                    unit(
                        "a",
                        role="executor",
                        read_only=False,
                        owned_paths=["src/a.py"],
                    ),
                    unit(
                        "b",
                        role="executor",
                        read_only=False,
                        owned_paths=["src/b.py"],
                    ),
                ],
            )
        )
        assignment = plan["waves"][0]["assignments"][0]
        result = {
            "plan_id": plan["plan_id"],
            "assignment_id": assignment["assignment_id"],
            "actor_id": "worker-a",
            "role": assignment["role"],
            "permissions": assignment["permissions"],
            "ownership": assignment["ownership"],
            "delegation_depth": assignment["delegation_depth"],
            "dependencies": assignment["dependencies"],
            "status": "completed",
            "changed_paths": ["src/a.py"],
            "commands": [{"argv": assignment["check"], "exit_code": 0}],
            "blockers": [],
            "risks": [],
        }
        context = {
            "schema_version": 1,
            "source_sha256": "d" * 64,
            "actor_bindings": {
                assignment["assignment_id"]: result["actor_id"],
            },
            "worker_results": [],
        }

        wrong_check = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": assignment["assignment_id"],
                "result": {
                    **result,
                    "commands": [
                        {
                            "argv": ["python", "-c", "print('not the check')"],
                            "exit_code": 0,
                        }
                    ],
                },
                "execution_context": context,
            }
        )
        wrong_ownership = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": assignment["assignment_id"],
                "result": {**result, "ownership": ["src/b.py"]},
                "execution_context": context,
            }
        )
        ambiguous_empty_write = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": assignment["assignment_id"],
                "result": {**result, "changed_paths": []},
                "execution_context": context,
            }
        )
        explicit_noop = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": assignment["assignment_id"],
                "result": {
                    **result,
                    "status": "no-op",
                    "changed_paths": [],
                    "no_op_reason": "Required behavior was already present and the check confirms it.",
                },
                "execution_context": context,
            }
        )

        self.assertFalse(wrong_check["valid"])
        self.assertFalse(wrong_ownership["valid"])
        self.assertFalse(ambiguous_empty_write["valid"])
        self.assertTrue(explicit_noop["valid"])
        self.assertTrue(explicit_noop["durable_claim_eligible"])

    def test_v2_verifier_statuses_are_separate_and_self_verification_fails(
        self,
    ) -> None:
        plan = policy.select_agent_plan(agent_signals_v2(quality_claim=True))
        verifier = plan["waves"][-1]["assignments"][0]
        worker_ids = [
            assignment["assignment_id"]
            for wave in plan["waves"][:-1]
            for assignment in wave["assignments"]
        ]
        result = {
            "plan_id": plan["plan_id"],
            "assignment_id": verifier["assignment_id"],
            "actor_id": "independent-verifier",
            "role": "verifier",
            "permissions": "read-only",
            "ownership": verifier["ownership"],
            "delegation_depth": 1,
            "dependencies": verifier["dependencies"],
            "status": "confirmed",
            "changed_paths": [],
            "commands": [{"argv": verifier["check"], "exit_code": 0}],
            "blockers": [],
            "risks": [],
            "verified_assignment_ids": worker_ids,
        }
        worker_assignments = [
            assignment
            for wave in plan["waves"][:-1]
            for assignment in wave["assignments"]
        ]
        worker_results = [
            {
                "plan_id": plan["plan_id"],
                "assignment_id": assignment["assignment_id"],
                "actor_id": f"actor-worker-{index}",
                "role": assignment["role"],
                "permissions": assignment["permissions"],
                "ownership": assignment["ownership"],
                "delegation_depth": assignment["delegation_depth"],
                "dependencies": assignment["dependencies"],
                "status": "completed",
                "changed_paths": [],
                "commands": [{"argv": assignment["check"], "exit_code": 0}],
                "blockers": [],
                "risks": [],
            }
            for index, assignment in enumerate(worker_assignments)
        ]
        actor_bindings = {
            item["assignment_id"]: item["actor_id"] for item in worker_results
        }
        actor_bindings[verifier["assignment_id"]] = result["actor_id"]
        execution_context = {
            "schema_version": 1,
            "source_sha256": "e" * 64,
            "actor_bindings": actor_bindings,
            "worker_results": worker_results,
        }
        legacy_compatible = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": verifier["assignment_id"],
                "result": result,
            }
        )
        valid = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": verifier["assignment_id"],
                "result": result,
                "execution_context": execution_context,
            }
        )
        self_verify = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": verifier["assignment_id"],
                "result": {**result, "actor_id": worker_results[0]["actor_id"]},
                "execution_context": {
                    **execution_context,
                    "actor_bindings": {
                        **actor_bindings,
                        verifier["assignment_id"]: worker_results[0]["actor_id"],
                    },
                },
            }
        )
        partial_coverage = policy.validate_worker_result(
            {
                "schema_version": 2,
                "plan": plan,
                "assignment_id": verifier["assignment_id"],
                "result": {
                    **result,
                    "verified_assignment_ids": worker_ids[:1],
                },
                "execution_context": execution_context,
            }
        )

        self.assertTrue(legacy_compatible["valid"])
        self.assertFalse(legacy_compatible["durable_claim_eligible"])
        self.assertTrue(valid["valid"])
        self.assertEqual(valid["status"], "confirmed")
        self.assertTrue(valid["durable_claim_eligible"])
        self.assertFalse(self_verify["valid"])
        self.assertFalse(partial_coverage["valid"])

    def test_agent_fixture_covers_all_modes_and_cli(self) -> None:
        report = policy.evaluate_agent_cases(AGENT_CASES_PATH)
        modes = {
            case["expected_mode"]
            for case in json.loads(AGENT_CASES_PATH.read_text(encoding="utf-8"))[
                "cases"
            ]
        }
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--agent-cases",
                str(AGENT_CASES_PATH),
                "--json",
            ],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            modes, {"solo", "parallel-read-only", "parallel-packets", "staged-verify"}
        )
        self.assertTrue(report["passed"])
        self.assertFalse(report["end_to_end_improvement_proven"])
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


class HostileInputTests(unittest.TestCase):
    """The planner promises solo on bad input, so it must not raise instead.

    ``value in some_set`` raises ``TypeError`` for an unhashable value, and the
    fail-closed handler catches only ``OrchestrationError``. A caller that
    received a traceback would have no plan at all, which is worse than the
    conservative plan the contract promises.
    """

    UNHASHABLE = ([], {}, [[]], {"a": 1}, [None])
    ENUM_FIELDS = ("schema_version", "request_mode", "phase", "authorization")

    def test_an_unhashable_enum_field_degrades_to_solo(self) -> None:
        for field in self.ENUM_FIELDS:
            for value in self.UNHASHABLE:
                with self.subTest(field=field, value=value):
                    plan = policy.select_agent_plan(agent_signals_v2(**{field: value}))
                    self.assertEqual(plan["mode"], "solo")
                    self.assertFalse(plan["valid_input"])
                    self.assertEqual(plan["spawn_count"], 0)

    def test_an_unhashable_unit_role_degrades_to_solo(self) -> None:
        plan = policy.select_agent_plan(
            agent_signals_v2(units=[unit("lane-a", role=[]), unit("lane-b")])
        )
        self.assertEqual(plan["mode"], "solo")
        self.assertFalse(plan["valid_input"])

    def test_a_boolean_is_never_accepted_as_a_schema_version(self) -> None:
        """Python evaluates ``True in {1, 2}`` as true; the enum must not."""
        for value in (True, False):
            with self.subTest(value=value):
                plan = policy.select_agent_plan(agent_signals_v2(schema_version=value))
                self.assertEqual(plan["mode"], "solo")
                self.assertFalse(plan["valid_input"])

    def test_intensity_selection_rejects_an_unhashable_request_mode(self) -> None:
        with self.assertRaises(policy.OrchestrationError):
            policy.select_intensity(signals(request_mode=[]))

    def test_the_cli_reports_bad_input_instead_of_a_traceback(self) -> None:
        payload = json.dumps(agent_signals_v2(authorization=[]))
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--agent-plan", "-", "--json"],
            input=payload,
            capture_output=True,
            text=True,
        )
        self.assertNotIn("Traceback", completed.stderr)
        plan = json.loads(completed.stdout)
        self.assertEqual(plan["mode"], "solo")


if __name__ == "__main__":
    unittest.main()
