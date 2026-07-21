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


class OrchestrationPolicyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
