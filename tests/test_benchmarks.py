from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_SCRIPT = PLUGIN_ROOT / "scripts" / "run_benchmarks.py"
SEMANTIC_BENCHMARK_SCRIPT = PLUGIN_ROOT / "scripts" / "run_semantic_benchmarks.py"
BROWSER_BENCHMARK_SCRIPT = PLUGIN_ROOT / "scripts" / "run_browser_benchmarks.py"
SKYVERN_BENCHMARK_SCRIPT = PLUGIN_ROOT / "scripts" / "run_skyvern_benchmarks.py"
COMMUNICATION_BENCHMARK_SCRIPT = (
    PLUGIN_ROOT / "scripts" / "run_communication_benchmarks.py"
)
DESIGN_BENCHMARK_SCRIPT = PLUGIN_ROOT / "scripts" / "run_design_benchmarks.py"
CAPABILITY_BENCHMARK_SCRIPT = PLUGIN_ROOT / "scripts" / "run_capability_benchmarks.py"
COORDINATION_BENCHMARK_SCRIPT = (
    PLUGIN_ROOT / "scripts" / "run_coordination_benchmarks.py"
)
QCU_BENCHMARK_SCRIPT = PLUGIN_ROOT / "scripts" / "run_qcu_benchmarks.py"


class BenchmarkIntegrationTests(unittest.TestCase):
    def test_context_selection_suite_passes(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(BENCHMARK_SCRIPT)],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("Version-aware external context benchmark", completed.stdout)
        self.assertIn("PASS suite", completed.stdout)

    def test_semantic_suite_cannot_pass_without_real_codegraph_results(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(SEMANTIC_BENCHMARK_SCRIPT),
                "--codegraph",
                "missing-codegraph-cognitive-powers-test",
            ],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn("FAIL payment-retry-impact", completed.stdout)
        self.assertIn("version=None", completed.stdout)

    def test_browser_suite_cannot_pass_without_playwright(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(BROWSER_BENCHMARK_SCRIPT),
                "--playwright",
                "missing-cognitive-powers-playwright",
                "--json",
            ],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertFalse(report["passed"])
        self.assertIn("executable not found", report["cases"][0]["error"])

    def test_skyvern_contract_passes_without_claiming_live_validation(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SKYVERN_BENCHMARK_SCRIPT), "--json"],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report["contractPassed"])
        self.assertFalse(report["liveProviderValidated"])
        self.assertIsNone(report["live"])

    def test_communication_contract_does_not_claim_end_to_end_improvement(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(COMMUNICATION_BENCHMARK_SCRIPT), "--json"],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report["contractPassed"])
        self.assertFalse(report["endToEndImprovementValidated"])

    def test_design_contract_does_not_claim_render_or_visual_quality(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(DESIGN_BENCHMARK_SCRIPT), "--json"],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report["contractPassed"])
        self.assertFalse(report["liveRenderValidated"])
        self.assertFalse(report["visualQualityValidated"])

    def test_capability_contract_does_not_claim_quality_improvement(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CAPABILITY_BENCHMARK_SCRIPT), "--json"],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report["passed"])
        self.assertFalse(report["quality_improvement_proven"])
        self.assertEqual(len(report["cases"]), 4)

    def test_coordination_contract_does_not_claim_end_to_end_improvement(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(COORDINATION_BENCHMARK_SCRIPT), "--json"],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report["passed"])
        self.assertFalse(report["end_to_end_improvement_proven"])
        self.assertEqual(len(report["cases"]), 6)

    def test_qcu_contract_passes_without_claiming_live_desktop_validation(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(QCU_BENCHMARK_SCRIPT), "--json"],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report["passed"])
        self.assertFalse(report["liveDesktopValidated"])
        self.assertEqual(len(report["cases"]), 3)


if __name__ == "__main__":
    unittest.main()
