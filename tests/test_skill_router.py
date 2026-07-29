from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN_ROOT / "hooks" / "skill_router.py"

# Confirmed to clear the threshold against the installed descriptions. Each one
# uses vocabulary that only its owner declares.
STRONG_MATCHES = {
    "Verify this browser-visible regression with Playwright": "verify-web-behavior",
    "Operate this native Windows WPF app through QCU": "operate-desktop-adaptively",
    "Diagnose an intermittent performance regression with falsifiable hypotheses": (
        "diagnose-systematically"
    ),
}

# Ordinary work and chatter that owns no workflow. A suggestion here would be
# noise, and noise is what stops the channel from being read.
ORDINARY_PROMPTS = [
    "fix the typo in the README",
    "rename this variable to userId",
    "bump the version to 2.0.1",
    "commit these changes",
    "add a comment above line 40",
    "ok gracias",
    "hola, como estas?",
]


def load_module():
    spec = importlib.util.spec_from_file_location("test_skill_router_module", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


router = load_module()


def run_hook(payload: object, env=None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "user-prompt-submit"],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


class SkillRouterHookTests(unittest.TestCase):
    def test_strong_match_emits_user_prompt_submit_context(self) -> None:
        for prompt, expected in STRONG_MATCHES.items():
            with self.subTest(prompt=prompt):
                completed = run_hook(
                    {"hook_event_name": "UserPromptSubmit", "user_input": prompt}
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                output = json.loads(completed.stdout)
                specific = output["hookSpecificOutput"]
                self.assertEqual(specific["hookEventName"], "UserPromptSubmit")
                self.assertIn(
                    f"cognitive-powers:{expected}", specific["additionalContext"]
                )

    def test_suggestion_names_an_installed_skill(self) -> None:
        for prompt in STRONG_MATCHES:
            with self.subTest(prompt=prompt):
                outcome = router.suggest({"user_input": prompt})

                self.assertEqual(outcome["status"], "suggested")
                skill = PLUGIN_ROOT / "skills" / str(outcome["skill"]) / "SKILL.md"
                self.assertTrue(skill.is_file(), skill)

    def test_ordinary_work_stays_silent(self) -> None:
        for prompt in ORDINARY_PROMPTS:
            with self.subTest(prompt=prompt):
                completed = run_hook(
                    {"hook_event_name": "UserPromptSubmit", "user_input": prompt}
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, "")

    def test_explicit_skill_request_fires_regardless_of_wording(self) -> None:
        outcome = router.suggest({"user_input": "use solve-efficiently here"})

        self.assertEqual(outcome["status"], "suggested")
        self.assertEqual(outcome["skill"], "solve-efficiently")

    def test_a_skill_named_as_prose_is_still_named(self) -> None:
        """Nobody types the hyphen when asking for a workflow out loud."""
        for prompt in (
            "use solve efficiently here",
            "use solve_efficiently here",
            "run Solve-Efficiently",
        ):
            with self.subTest(prompt=prompt):
                outcome = router.suggest({"user_input": prompt})

                self.assertEqual(outcome["status"], "suggested")
                self.assertEqual(outcome["skill"], "solve-efficiently")
                self.assertEqual(outcome["reason"], "named skill")

    def test_the_plugin_name_routes_to_a_workflow(self) -> None:
        """The phrase a user reaches for when nothing seems to be happening.

        solve-efficiently declares it runs "when Cognitive Powers is requested
        by name", but only individual skill names were ever recognised, so the
        plugin's own name matched nothing. It is also the only trigger that
        survives a prompt written in a language the English descriptions cannot
        score, which is how this was found.
        """
        for prompt in (
            "use cognitive powers",
            "Cognitive Powers",
            "cognitive-powers please",
            "usa cognitive powers para esto",
        ):
            with self.subTest(prompt=prompt):
                outcome = router.suggest({"user_input": prompt})

                self.assertEqual(outcome["status"], "suggested", prompt)
                self.assertEqual(outcome["reason"], "named plugin")
                skill = PLUGIN_ROOT / "skills" / str(outcome["skill"]) / "SKILL.md"
                self.assertTrue(skill.is_file(), skill)

    def test_the_plugin_name_still_picks_the_fitting_workflow(self) -> None:
        outcome = router.suggest(
            {"user_input": "use cognitive powers to audit whether this release is done"}
        )

        self.assertEqual(outcome["skill"], "verify-delivery")

    def test_a_single_shared_word_never_names_a_workflow(self) -> None:
        """The rule that keeps ordinary editing out of the channel.

        "reformat this file" lands on solve-efficiently through the one word
        "file", and scores as high doing it as a genuine multi-file request
        does on four words. Score alone cannot separate them; overlap can.
        """
        outcome = router.suggest({"user_input": "reformat this file"})

        self.assertEqual(outcome["status"], "below-threshold")
        self.assertEqual(outcome["reason"], "too few shared words")
        self.assertLess(outcome["shared_tokens"], 2)

    def test_a_clear_winner_is_not_discarded_for_a_modest_score(self) -> None:
        """The shipped gate required a high score *and* a margin, so a prompt
        that beat every other skill outright was still dropped for scoring
        below an absolute floor."""
        outcome = router.suggest(
            {"user_input": "Solve this non-trivial multi-file coding task efficiently"}
        )

        self.assertEqual(outcome["status"], "suggested")
        self.assertEqual(outcome["skill"], "solve-efficiently")

    def test_malformed_input_never_fails_the_turn(self) -> None:
        for payload in ("", "not json at all", "[]", '{"user_input": 42}', "{}"):
            with self.subTest(payload=payload):
                completed = run_hook(payload)

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, "")

    def test_environment_switch_silences_a_strong_match(self) -> None:
        prompt = next(iter(STRONG_MATCHES))
        environment = os.environ.copy()
        environment["COGNITIVE_POWERS_DISABLE_ROUTER"] = "1"

        completed = run_hook({"user_input": prompt}, env=environment)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_near_tie_stays_silent(self) -> None:
        """Wording that matches a family of skills must not name one of them."""
        tied = {
            "alpha-skill": "Verify browser behavior using Playwright evidence.",
            "beta-skill": "Verify browser behavior using Playwright evidence.",
        }

        with mock.patch.object(router, "load_skill_descriptions", return_value=tied):
            outcome = router.suggest(
                {"user_input": "Verify browser behavior using Playwright evidence"}
            )

        self.assertEqual(outcome["status"], "below-threshold")

    def test_single_skill_install_stays_silent(self) -> None:
        with mock.patch.object(
            router, "load_skill_descriptions", return_value={"only-skill": "Do work."}
        ):
            outcome = router.suggest({"user_input": "Do work."})

        self.assertEqual(outcome["status"], "skipped")

    def test_unreadable_descriptions_stay_silent(self) -> None:
        with mock.patch.object(
            router, "load_skill_descriptions", side_effect=ValueError("bad frontmatter")
        ):
            outcome = router.suggest({"user_input": next(iter(STRONG_MATCHES))})

        self.assertEqual(outcome["status"], "skipped")

    def test_a_spanish_request_reaches_its_workflow(self) -> None:
        """What the report was actually about.

        The listings are English and the scorer is lexical, so before the
        translation layer these scored near zero and the hook was silent on
        every one -- the user saw a plugin that never did anything.
        """
        for prompt, expected in (
            (
                "Arregla el defecto usando la reproducción suministrada",
                "solve-efficiently",
            ),
            (
                "Diagnostica una regresión de rendimiento intermitente",
                "diagnose-systematically",
            ),
            (
                "Verifica esta regresión en el navegador con Playwright",
                "verify-web-behavior",
            ),
            ("Audita si el lanzamiento está realmente completo", "verify-delivery"),
            ("Explícame este artículo en lenguaje llano", "eli5"),
        ):
            with self.subTest(prompt=prompt):
                outcome = router.suggest({"user_input": prompt})

                self.assertEqual(outcome["status"], "suggested", prompt)
                self.assertEqual(outcome["skill"], expected)

    def test_ordinary_spanish_work_stays_silent(self) -> None:
        """The lexicon buys recall with the same currency English spends.

        A mapping wide enough to match anything would read as coverage and
        arrive as noise, so ordinary Spanish editing is held to the same bar.
        """
        for prompt in (
            "arregla la errata del README",
            "renombra esta variable a userId",
            "reformatea este archivo",
            "haz commit de estos cambios",
            "¿qué hora es?",
            "añade una línea de log aquí",
        ):
            with self.subTest(prompt=prompt):
                self.assertNotEqual(
                    router.suggest({"user_input": prompt})["status"], "suggested"
                )

    def test_the_hook_and_the_benchmark_decide_alike(self) -> None:
        """The invariant skill_routing exists to hold.

        The benchmark can only vouch for what the host gets if both read the
        same thresholds. A hook with its own copy passed every checked-in case
        while staying silent on a third of them at runtime.
        """
        cases = json.loads(
            (PLUGIN_ROOT / "benchmarks" / "skill_routing_cases.json").read_text(
                encoding="utf-8"
            )
        )

        for prompt in cases["quiet"]:
            with self.subTest(prompt=prompt):
                self.assertNotEqual(
                    router.suggest({"user_input": prompt})["status"], "suggested"
                )

        named = 0
        prompts = [
            (case["prompt"], entry["name"])
            for entry in cases["skills"]
            for case in entry["positives"]
        ]
        for prompt, owner in prompts:
            outcome = router.suggest({"user_input": prompt})
            named += int(outcome["status"] == "suggested" and outcome["skill"] == owner)

        self.assertGreaterEqual(
            named / len(prompts),
            float(cases["thresholds"]["min_suggestion_rate"]),
            "the hook names fewer owners than the benchmark contract allows",
        )

    def test_rejects_an_unknown_mode(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "session-start"],
            input="{}",
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
