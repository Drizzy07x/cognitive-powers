from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "run_skill_routing_benchmarks.py"
CASES_PATH = PLUGIN_ROOT / "benchmarks" / "skill_routing_cases.json"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_skill_routing_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


routing = load_module()


class SkillRoutingTests(unittest.TestCase):
    def test_catalog_has_complete_positive_negative_and_adversarial_cases(self) -> None:
        descriptions = routing.load_skill_descriptions(PLUGIN_ROOT)
        data = json.loads(CASES_PATH.read_text(encoding="utf-8"))

        self.assertEqual({entry["name"] for entry in data["skills"]}, set(descriptions))
        self.assertGreaterEqual(len(descriptions), 13)
        for entry in data["skills"]:
            self.assertGreaterEqual(len(entry["positives"]), 3, entry["name"])
            self.assertTrue(entry["negatives"], entry["name"])
            self.assertTrue(entry["adversarial"], entry["name"])
            self.assertTrue(all(case.get("owner") for case in entry["negatives"]))

    def test_checked_in_routing_contract_passes_without_quality_claim(self) -> None:
        report = routing.evaluate(PLUGIN_ROOT, CASES_PATH)

        self.assertTrue(report["passed"])
        self.assertEqual(report["metrics"]["top_k_rate"], 1.0)
        self.assertEqual(report["metrics"]["negative_owner_rate"], 1.0)
        self.assertEqual(report["metrics"]["adversarial_owner_rate"], 1.0)
        self.assertFalse(report["end_to_end_improvement_proven"])

    def test_orchestration_collision_cases_keep_their_specialized_owner(self) -> None:
        report = routing.evaluate(PLUGIN_ROOT, CASES_PATH)
        collision_skills = {
            "solve-efficiently",
            "diagnose-systematically",
            "research-systematically",
            "verify-delivery",
        }
        cases = [
            case
            for case in report["cases"]
            if case["kind"] == "negative" and case["skill"] in collision_skills
        ]

        self.assertGreaterEqual(len(cases), 8)
        self.assertTrue(all(case["passed"] for case in cases))
        self.assertTrue(
            collision_skills.issubset(
                {case["owner"] for case in cases} | {case["skill"] for case in cases}
            )
        )

    def test_ranker_prefers_explicit_skill_request(self) -> None:
        descriptions = {
            "alpha-skill": "Handle alpha project work.",
            "beta-skill": "Handle beta project work.",
        }

        ranking = routing.rank_skills("Use $beta-skill for this task", descriptions)

        self.assertEqual(ranking[0][0], "beta-skill")

    def test_explicit_skill_boost_requires_an_exact_token(self) -> None:
        descriptions = {
            "alpha-skill": "Unrelated alpha project work.",
            "beta-skill": "Handle a skillful request only.",
        }

        substring = routing.rank_skills("Use alpha-skillful only", descriptions)
        exact = routing.rank_skills("Use alpha-skill only", descriptions)

        self.assertEqual(substring[0][0], "beta-skill")
        self.assertEqual(exact[0][0], "alpha-skill")

    def test_collision_detector_reports_near_identical_descriptions(self) -> None:
        descriptions = {
            "one": "Verify browser behavior using Playwright tests and evidence.",
            "two": "Verify browser behavior using Playwright tests and evidence.",
            "three": "Map a repository into compact guidance.",
        }

        collisions = routing.description_collisions(descriptions, 0.9)

        self.assertEqual(len(collisions), 1)
        self.assertEqual(
            {collisions[0]["left"], collisions[0]["right"]}, {"one", "two"}
        )

    def test_cli_emits_machine_readable_report(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--json"],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report["passed"])
        self.assertFalse(report["end_to_end_improvement_proven"])


if __name__ == "__main__":
    unittest.main()
