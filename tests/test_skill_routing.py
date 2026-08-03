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

# Keys spelled alike in both languages whose English reading wants the same
# target anyway, so the rewrite is correct rather than a false friend. Every
# other shared spelling is a defect: keep this list short and justified.
SAME_TARGET_IN_BOTH_LANGUAGES = frozenset({"error", "bug"})


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
        """Stemmed words the listings keep, for checking translation targets."""
        descriptions = core.load_skill_descriptions(PLUGIN_ROOT)
        vocabulary: set[str] = set()
        for name, description in descriptions.items():
            vocabulary |= set(core._document(name, description))
        return vocabulary

    def _raw_description_words(self) -> set[str]:
        """Words the listings contain *before* tokenize filters anything.

        Anything that goes looking for stopwords has to use this. The stemmed
        vocabulary above is produced by tokenize, which has already deleted
        every stopword, so intersecting it with a stopword list is empty by
        construction and proves nothing.
        """
        words: set[str] = set()
        for name, description in core.load_skill_descriptions(PLUGIN_ROOT).items():
            words |= set(
                core.TOKEN_PATTERN.findall(
                    core._fold(f"{name.replace('-', ' ')} {description}".casefold())
                )
            )
        return words

    def _repository_english(self) -> set[str]:
        """English words this repository's own prose uses.

        Wider than the descriptions on purpose: a lexicon key collides with
        English in the *prompt*, and a prompt is not drawn from the sixteen
        listings. Checking only those is what let "actual", "extension",
        "simple", "opera", "multiple" and "legible" through -- none of them
        appears in a description, so the narrow check saw nothing.
        """
        words: set[str] = set()
        for document in PLUGIN_ROOT.glob("**/*.md"):
            # CHANGELOG quotes Spanish prompts verbatim to explain the lexicon,
            # so it is not an English corpus; including it makes "archivo" and
            # "prueba" look like English and the check reports nothing.
            if ".git" in document.parts or document.name == "CHANGELOG.md":
                continue
            words |= set(
                core.TOKEN_PATTERN.findall(
                    core._fold(document.read_text(encoding="utf-8").casefold())
                )
            )
        return words

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
        Checked against the raw listing words: the earlier version of this test
        intersected the *tokenized* vocabulary, which tokenize has already
        stripped of every stopword, so it passed for any list at all --
        injecting "browser" or "playwright" left it green while those words
        vanished from the skills that depend on them.
        """
        self.assertEqual(core.SPANISH_STOPWORDS & self._raw_description_words(), set())

    def test_the_stopword_guard_can_actually_fail(self) -> None:
        """The invariant above is load-bearing, so prove the check is live."""
        victim = next(iter(core.SPANISH_TERMS.values()))
        self.assertIn(victim, self._raw_description_words())
        self.assertTrue({victim} & self._raw_description_words())

    def test_no_translation_rewrites_an_english_word_into_a_different_stem(
        self,
    ) -> None:
        """Nothing here knows which language it was handed.

        A key spelled the same in both languages rewrites English prompts too:
        "reduce" mapped to "reduced" moved an English case off its owner by
        0.009. Identity mappings are safe; a shared spelling that lands
        somewhere else is not, so it must be spelled unambiguously in Spanish.

        Scanned against this repository's whole English prose rather than the
        sixteen listings. A prompt is not drawn from the listings, so checking
        only those missed six live rewrites at once.

        The prose is a proxy for English, not a dictionary: it catches
        "actual", "simple", "extension", "multiple" and "reduce", but an
        English word this repository never writes -- "opera", "legible" --
        stays invisible here and has to be caught in review. Widening the
        corpus is the way to strengthen this, not trusting it further.
        """
        english = self._repository_english()
        rewritten = {
            spanish: english_term
            for spanish, english_term in core.SPANISH_TERMS.items()
            if spanish in english
            and core._stem(spanish) != core._stem(english_term)
            and spanish not in SAME_TARGET_IN_BOTH_LANGUAGES
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
            # "imple", not "implement": the stem is the fixpoint, and the
            # English "implementation" these prompts have to meet reaches the
            # same token only because both are stemmed all the way down.
            ("implementación", "imple"),
        ):
            with self.subTest(text=text):
                self.assertEqual(core.tokenize(text), [expected])

    def test_stemming_a_stem_changes_nothing(self) -> None:
        """A stem that is itself stemmable splits one concept into two tokens.

        Stripping a single suffix and returning did exactly that: "implement"
        became "imple" while "implementation" stopped at "implement", so the
        only skill describing itself as spanning "implementation" shared no
        token with a user asking to implement something -- and
        diagnose-systematically, which writes "implement" in a clause about
        not implementing, was the one that matched instead. Idempotence is the
        property that was missing, so assert it directly rather than pinning
        the handful of pairs noticed so far.
        """
        for word in self._repository_english():
            with self.subTest(word=word):
                self.assertEqual(core._stem(core._stem(word)), core._stem(word))

    def test_a_word_and_its_longer_form_reach_one_token(self) -> None:
        """The pairs the split was found through, kept as regression cases."""
        for shorter, longer in (
            ("implement", "implementation"),
            ("document", "documentation"),
            ("measure", "measurements"),
        ):
            with self.subTest(pair=(shorter, longer)):
                self.assertEqual(core._stem(shorter), core._stem(longer))

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
