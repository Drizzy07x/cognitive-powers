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
# noise, and noise is what stops the channel from being read. Read from the
# case file the benchmark actually gates on: three hand-kept copies of this
# corpus had drifted apart, and the JSON one is the copy an author is least
# likely to remember to update.
CASES = json.loads(
    (PLUGIN_ROOT / "benchmarks" / "skill_routing_cases.json").read_text(
        encoding="utf-8"
    )
)
ORDINARY_PROMPTS = CASES["quiet"]
ORDINARY_SPANISH_PROMPTS = CASES["spanish_quiet"]


def load_module():
    spec = importlib.util.spec_from_file_location("test_skill_router_module", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


router = load_module()

# The module the hook must not diverge from. Loaded directly so the comparison
# is against the shared decision, not against a second copy of the thresholds.
_core_spec = importlib.util.spec_from_file_location(
    "test_skill_router_core", PLUGIN_ROOT / "scripts" / "skill_routing.py"
)
core = importlib.util.module_from_spec(_core_spec)
sys.modules[_core_spec.name] = core
_core_spec.loader.exec_module(core)


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
        # The Skill-tool wording is the Claude Code shape, so the host is named
        # rather than inherited: with neither variable set the hook falls back
        # to naming the workflow file, which is the form both hosts can follow.
        environment = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
        for prompt, expected in STRONG_MATCHES.items():
            with self.subTest(prompt=prompt):
                completed = run_hook(
                    {"hook_event_name": "UserPromptSubmit", "user_input": prompt},
                    env=environment,
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

    def test_ordinary_work_draws_no_named_suggestion(self) -> None:
        """What this protects is the naming, which is the part that can be wrong.

        It used to assert total silence, and that was the same assertion while
        the hook had one payload. It now has two, and only one of them makes a
        claim about the prompt: naming a workflow for ordinary work is the
        noise that teaches the agent to stop reading the channel, so it stays
        gated. The standing instruction claims nothing and is not gated,
        because the measured defect was a check that never happened at all.
        """
        for prompt in ORDINARY_PROMPTS:
            with self.subTest(prompt=prompt):
                completed = run_hook(
                    {"hook_event_name": "UserPromptSubmit", "user_input": prompt}
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                context = json.loads(completed.stdout)["hookSpecificOutput"][
                    "additionalContext"
                ]
                self.assertEqual(context, router.FORCED_EVAL)
                self.assertNotIn("matches the", context)

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

    def test_a_skill_name_in_ordinary_prose_is_not_a_request(self) -> None:
        """Two adjacent words are a noun phrase, not an invocation.

        Accepting the spaced spelling bare made "verify delivery of the
        shipment before friday" an explicit request scoring 2.0, and an
        explicit request answers to no threshold -- a new noise channel with
        no floor at all, on the one channel whose value is being right.
        """
        for prompt in (
            "verify delivery of the shipment before friday",
            "map project milestones onto the calendar",
            "audit capabilities of the vendor account",
            "engineer prompts for the marketing copy",
        ):
            with self.subTest(prompt=prompt):
                outcome = router.suggest({"user_input": prompt})

                self.assertNotEqual(outcome.get("reason"), "named skill")

    def test_naming_the_plugin_to_complain_is_not_asking_for_it(self) -> None:
        """Answering "turn this off" with another suggestion is the worst
        possible reply, and the alias branch skipped every gate to give it."""
        for prompt in (
            "turn off cognitive powers",
            "uninstall cognitive-powers",
            "cognitive powers is spamming me, turn the hook off",
            "desactiva cognitive powers",
        ):
            with self.subTest(prompt=prompt):
                outcome = router.suggest({"user_input": prompt})

                self.assertEqual(outcome["status"], "below-threshold")
                self.assertEqual(outcome["reason"], "named plugin as the subject")

    def test_naming_two_skills_is_not_naming_one(self) -> None:
        outcome = core.decide(
            "use alpha-skill and beta-skill",
            {"alpha-skill": "Handle work.", "beta-skill": "Handle work."},
        )

        self.assertEqual(outcome["status"], "below-threshold")
        self.assertEqual(outcome["reason"], "named two skills")

    def test_the_plugin_alias_never_falls_back_to_alphabetical_order(self) -> None:
        """With no domain vocabulary every score ties at zero and rank_skills
        breaks the tie by name, so the alias used to name whichever skill
        sorted first with no evidence behind it."""
        outcome = core.decide(
            "use cognitive powers to sort my email",
            {"alpha-skill": "Handle alpha.", "zeta-skill": "Handle zeta."},
        )

        self.assertEqual(outcome["status"], "below-threshold")
        self.assertEqual(outcome["reason"], "no default workflow installed")

    def test_the_plugin_alias_does_not_pick_between_tied_skills(self) -> None:
        """Naming the plugin defers the workflow choice to the ranking, and a
        ranking that cannot separate two skills has made no choice to defer to
        -- the alias branch used to return whichever won by rounding."""
        outcome = core.decide(
            "use cognitive powers to verify browser behavior",
            {
                "alpha-skill": "Verify browser behavior with evidence.",
                "beta-skill": "Verify browser behavior with evidence.",
            },
        )

        self.assertEqual(outcome["status"], "below-threshold")
        self.assertEqual(outcome["reason"], "near tie")

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

        with mock.patch.object(
            router, "load_parsable_skill_descriptions", return_value=(tied, [])
        ):
            outcome = router.suggest(
                {"user_input": "Verify browser behavior using Playwright evidence"}
            )

        self.assertEqual(outcome["status"], "below-threshold")

    def test_single_skill_install_stays_silent(self) -> None:
        with mock.patch.object(
            router,
            "load_parsable_skill_descriptions",
            return_value=({"only-skill": "Do work."}, []),
        ):
            outcome = router.suggest({"user_input": "Do work."})

        self.assertEqual(outcome["status"], "skipped")

    def test_unreadable_descriptions_stay_silent(self) -> None:
        with mock.patch.object(
            router,
            "load_parsable_skill_descriptions",
            side_effect=ValueError("bad frontmatter"),
        ):
            outcome = router.suggest({"user_input": next(iter(STRONG_MATCHES))})

        self.assertEqual(outcome["status"], "skipped")

    def test_an_unparsable_skill_is_named_instead_of_silently_dropped(self) -> None:
        """A dead router and a quiet one used to look identical.

        One malformed SKILL.md aborted the whole load, and the hook renders a
        failed load exactly like a prompt that matched nothing -- for every
        prompt, with no runtime signal that sixteen workflows had stopped
        being routable.
        """
        surviving = {
            "verify-web-behavior": "Verify browser behavior through Playwright."
        }
        with mock.patch.object(
            router,
            "load_parsable_skill_descriptions",
            return_value=(surviving, ["map-project", "solve-efficiently"]),
        ):
            outcome = router.suggest({"user_input": "unrelated ordinary question"})

        self.assertIn("map-project", outcome["warning"])
        self.assertIn("solve-efficiently", outcome["warning"])

    def test_the_standing_instruction_survives_an_abstaining_ranking(self) -> None:
        """The ranking abstains far more often than it is wrong.

        Against prompts written independently of the skill descriptions it
        named the right workflow three times in ten and said nothing five
        times. A silent hook leaves the agent with no signal that a catalogue
        exists at all, so the instruction is not conditional on a winner.
        """
        outcome = router.suggest({"user_input": ORDINARY_PROMPTS[0]})
        self.assertEqual(outcome["status"], "below-threshold")

        output = router._router_output(outcome, None)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(context, router.FORCED_EVAL)

    def test_a_named_match_carries_the_instruction_and_the_suggestion(self) -> None:
        prompt, expected = next(iter(STRONG_MATCHES.items()))
        outcome = router.suggest({"user_input": prompt})
        self.assertEqual(outcome["status"], "suggested")

        context = router._router_output(outcome, None)["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertTrue(context.startswith(router.FORCED_EVAL))
        self.assertIn(expected, context)

    def test_an_unreadable_catalogue_points_the_agent_at_nothing(self) -> None:
        # Injected on every prompt, so an instruction to consult an index that
        # never loaded would be a standing order to check a thing that is not
        # there. The warning is the honest channel for that state.
        with mock.patch.object(
            router,
            "load_parsable_skill_descriptions",
            return_value=({}, ["map-project", "solve-efficiently"]),
        ):
            outcome = router.suggest({"user_input": "anything at all"})

        self.assertEqual(outcome["status"], "skipped")
        output = router._router_output(outcome, outcome.get("warning"))
        self.assertNotIn("hookSpecificOutput", output)

    def test_the_injection_stays_inside_its_prompt_budget(self) -> None:
        # Paid on every prompt in the session, including the short ones, so
        # the ceiling is part of the contract rather than a preference.
        self.assertLessEqual(len(router.FORCED_EVAL), 400)
        prompt = next(iter(STRONG_MATCHES))
        outcome = router.suggest({"user_input": prompt})
        context = router._router_output(outcome, None)["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertLessEqual(len(context), 700)

    def test_an_empty_catalogue_is_not_treated_as_a_clean_load(self) -> None:
        with mock.patch.object(
            router, "load_parsable_skill_descriptions", return_value=({}, ["broken"])
        ):
            outcome = router.suggest({"user_input": next(iter(STRONG_MATCHES))})

        self.assertEqual(outcome["status"], "skipped")

    def test_the_suggestion_names_a_route_the_running_host_has(self) -> None:
        """Both hosts run this hook and reach a workflow differently.

        Claude Code installs all of skills/ and invokes one through the Skill
        tool. Codex installs the three routers in skills-core/ and reaches the
        rest by reading skills/<name>/SKILL.md, so a Skill-tool id named there
        instructed the agent to call something that host does not have -- for
        thirteen of the sixteen workflows.
        """
        prompt = "Diagnose an intermittent performance regression"
        environment = os.environ.copy()
        environment.pop("CLAUDE_PLUGIN_ROOT", None)
        environment.pop("PLUGIN_ROOT", None)

        claude = run_hook(
            {"user_input": prompt},
            env={**environment, "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)},
        )
        codex = run_hook(
            {"user_input": prompt},
            env={**environment, "PLUGIN_ROOT": str(PLUGIN_ROOT)},
        )

        claude_context = json.loads(claude.stdout)["hookSpecificOutput"][
            "additionalContext"
        ]
        codex_context = json.loads(codex.stdout)["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertIn("cognitive-powers:diagnose-systematically", claude_context)
        self.assertNotIn("cognitive-powers:", codex_context)
        self.assertIn("skills/diagnose-systematically/SKILL.md", codex_context)

    def test_workflows_codex_only_reads_are_not_offered_as_installed_skills(
        self,
    ) -> None:
        """Only skills-core is installed there; the other thirteen are files."""
        core = {
            path.parent.name
            for path in (PLUGIN_ROOT / "skills-core").glob("*/SKILL.md")
        }
        internal = {
            path.parent.name for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")
        }
        self.assertTrue(core < internal)

        environment = os.environ.copy()
        environment.pop("CLAUDE_PLUGIN_ROOT", None)
        environment["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        for name in sorted(internal - core):
            completed = run_hook({"user_input": f"use {name}"}, env=environment)
            with self.subTest(skill=name):
                context = json.loads(completed.stdout)["hookSpecificOutput"][
                    "additionalContext"
                ]
                self.assertIn(f"skills/{name}/SKILL.md", context)
                self.assertNotIn("Skill tool", context)

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
        for prompt in ORDINARY_SPANISH_PROMPTS:
            with self.subTest(prompt=prompt):
                self.assertNotEqual(
                    router.suggest({"user_input": prompt})["status"], "suggested"
                )

    def test_the_hook_and_the_benchmark_decide_alike(self) -> None:
        """The invariant skill_routing exists to hold.

        Compared prompt by prompt, not as a rate. The earlier version of this
        test recomputed the aggregate suggestion rate and checked it against
        the same threshold the benchmark already enforces, so a hook that
        named the wrong owner on five prompts and the right one on five it
        currently misses kept the ratio and passed -- a weaker form of exactly
        the divergence being guarded. Wrapping the hook to drop every Spanish
        prompt also passed it.
        """
        cases = json.loads(
            (PLUGIN_ROOT / "benchmarks" / "skill_routing_cases.json").read_text(
                encoding="utf-8"
            )
        )
        descriptions = core.load_skill_descriptions(PLUGIN_ROOT)
        prompts = [
            *(
                case["prompt"]
                for entry in cases["skills"]
                for kind in ("positives", "negatives", "adversarial")
                for case in entry[kind]
            ),
            *cases["quiet"],
            *cases["spanish_quiet"],
            *(case["prompt"] for case in cases["spanish"]),
        ]

        divergent = []
        for prompt in prompts:
            hook = router.suggest({"user_input": prompt})
            reference = core.decide(prompt, descriptions)
            observed = (hook["status"], hook.get("skill"), hook.get("reason"))
            expected = (
                reference["status"],
                reference.get("skill"),
                reference.get("reason"),
            )
            if observed != expected:
                divergent.append((prompt, observed, expected))

        self.assertEqual(divergent, [], "the hook decided differently from decide()")
        self.assertGreater(len(prompts), 180)

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
