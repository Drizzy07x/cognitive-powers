from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "run_skill_routing_benchmarks.py"
ROUTING_PATH = PLUGIN_ROOT / "scripts" / "skill_routing.py"
CASES_PATH = PLUGIN_ROOT / "benchmarks" / "skill_routing_cases.json"


def load_module(name: str = "test_skill_routing_module", path: Path = SCRIPT_PATH):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


routing = load_module()
# The runner re-exports the ranking helpers but not the thresholds, and the
# thresholds are half the routing decision.
core = load_module("test_skill_routing_core", ROUTING_PATH)


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

    def _description_vocabulary(self) -> set[str]:
        descriptions = core.load_skill_descriptions(PLUGIN_ROOT)
        vocabulary: set[str] = set()
        for name, description in descriptions.items():
            vocabulary |= set(core._document(name, description))
        return vocabulary

    def test_every_translation_lands_in_the_description_vocabulary(self) -> None:
        """A mapping onto a word no skill declares cannot help and does hurt.

        It contributes nothing to any numerator while still counting toward
        the query norm, so it lowers the score of the real matches beside it.
        """
        vocabulary = self._description_vocabulary()
        stranded = {
            spanish: english
            for spanish, english in core.SPANISH_TERMS.items()
            if core._stem(english) not in vocabulary
        }

        self.assertEqual(stranded, {})

    def test_spanish_stopwords_silence_no_description_word(self) -> None:
        """tokenize applies one list to prompts and to the English listings.

        So a Spanish function word that happens to be spelled like an English
        content word would delete that word from the skill that depends on it.
        """
        self.assertEqual(core.SPANISH_STOPWORDS & self._description_vocabulary(), set())

    def test_no_translation_rewrites_an_english_word_into_a_different_stem(
        self,
    ) -> None:
        """Nothing here knows which language it was handed.

        A key spelled the same in both languages rewrites English prompts too:
        "reduce" mapped to "reduced" moved an English case off its owner by
        0.009. Identity mappings are safe; a shared spelling that lands
        somewhere else is not, so it must be spelled unambiguously in Spanish.
        """
        english = set()
        for name, description in core.load_skill_descriptions(PLUGIN_ROOT).items():
            english |= set(
                core.TOKEN_PATTERN.findall(
                    core._fold(f"{name.replace('-', ' ')} {description}".casefold())
                )
            )
        rewritten = {
            spanish: english_term
            for spanish, english_term in core.SPANISH_TERMS.items()
            if spanish in english and core._stem(spanish) != core._stem(english_term)
        }

        self.assertEqual(rewritten, {})

    def test_accented_spanish_survives_tokenization(self) -> None:
        """TOKEN_PATTERN is ASCII, so without folding these split into pieces.

        "implementacion" arrived as ['implementaci', 'n'] and "codigo" as
        ['c', 'digo'] -- not weak matches but garbage that also inflated the
        query norm, which no amount of translation above it could repair.
        """
        for text, expected in (
            ("código", "code"),
            ("diseño", "design"),
            ("página", "pagina"),
            ("análisis", "analysi"),
            ("implementación", "implement"),
        ):
            with self.subTest(text=text):
                self.assertEqual(core.tokenize(text), [expected])

    def test_spanish_cases_cover_every_skill_and_its_own_quiet_corpus(self) -> None:
        data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        descriptions = core.load_skill_descriptions(PLUGIN_ROOT)

        owners = {case["owner"] for case in data["spanish"]}
        self.assertEqual(owners, set(descriptions))
        self.assertGreaterEqual(len(data["spanish_quiet"]), 15)

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

        substring = dict(routing.rank_skills("Use alpha-skillful", descriptions))
        exact = dict(routing.rank_skills("Use alpha-skill", descriptions))

        # The boost is what the exactness rule guards, so assert on the boost
        # rather than on the winner: with two descriptions this similar, which
        # one leads on wording alone is not what this test is about.
        self.assertLess(substring["alpha-skill"], core.EXPLICIT_REQUEST_SCORE)
        self.assertGreaterEqual(exact["alpha-skill"], core.EXPLICIT_REQUEST_SCORE)

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
