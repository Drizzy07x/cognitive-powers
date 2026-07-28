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
