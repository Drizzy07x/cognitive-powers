from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN_ROOT / "hooks" / "skill_activation.py"


def load_module():
    if str(PLUGIN_ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "test_skill_activation_module", SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


activation = load_module()

# Loaded directly rather than restated, so the index is measured against the
# catalogue the host actually lists instead of a second copy of it.
_core_spec = importlib.util.spec_from_file_location(
    "test_skill_activation_core", PLUGIN_ROOT / "scripts" / "skill_routing.py"
)
core = importlib.util.module_from_spec(_core_spec)
sys.modules[_core_spec.name] = core
_core_spec.loader.exec_module(core)

INSTALLED = core.load_skill_triggers(PLUGIN_ROOT)


def run_hook(payload: object, env=None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "session-start"],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


class SkillActivationRenderTests(unittest.TestCase):
    def test_index_lists_every_installed_workflow(self) -> None:
        message = activation.index_message(INSTALLED, True)
        self.assertIsNotNone(message)
        listed = {
            line[2:].split(":", 1)[0]
            for line in message.splitlines()
            if line.startswith("- ")
        }
        self.assertEqual(listed, set(INSTALLED))

    def test_invocation_names_what_the_running_host_can_reach(self) -> None:
        """Both hosts run this hook and reach a workflow differently.

        Claude Code invokes through the Skill tool; Codex installs three
        routers and reads the rest from the tree. Naming a Skill-tool id on
        Codex instructs the agent to call something that is not there, which is
        the defect the router's per-host wording already exists to avoid.
        """
        claude = activation.index_message(INSTALLED, True)
        codex = activation.index_message(INSTALLED, False)
        self.assertIn("cognitive-powers:<name>", claude)
        self.assertNotIn("SKILL.md", claude)
        self.assertIn("skills/<name>/SKILL.md", codex)
        self.assertNotIn("cognitive-powers:<name>", codex)

    def test_payload_stays_inside_the_budget_it_declares(self) -> None:
        # Paid once per session, but it competes with the user's own context
        # for the same window, so the ceiling is part of the contract rather
        # than a guideline.
        for claude_code in (True, False):
            with self.subTest(claude_code=claude_code):
                message = activation.index_message(INSTALLED, claude_code)
                self.assertLessEqual(len(message), activation.MAX_PAYLOAD_CHARS)

    def test_one_verbose_skill_cannot_crowd_out_the_others(self) -> None:
        triggers = {"verbose": "word " * 400, "terse": "Use when the build fails"}
        message = activation.index_message(triggers, True)
        self.assertIn("- terse: Use when the build fails", message)
        for line in message.splitlines():
            if line.startswith("- verbose:"):
                self.assertLessEqual(
                    len(line), activation.MAX_TRIGGER_CHARS + len("- verbose: ")
                )

    def test_overflow_is_counted_rather_than_dropped_in_silence(self) -> None:
        """An index listing part of the catalogue must not read like a full one."""
        triggers = {
            f"skill-{index:03d}": "Use when something happens" for index in range(200)
        }
        message = activation.index_message(triggers, True)
        self.assertLessEqual(len(message), activation.MAX_PAYLOAD_CHARS)
        self.assertRegex(message, r"- \(\d+ further workflows omitted for length\)")

    def test_empty_catalogue_renders_nothing_at_all(self) -> None:
        # A standing instruction to consult a list that is not there would
        # spend the session's attention on a defect it cannot act on.
        self.assertIsNone(activation.index_message({}, True))


class SkillActivationHookTests(unittest.TestCase):
    def test_startup_emits_session_start_context(self) -> None:
        # The host is named rather than inherited: with neither variable set
        # the hook falls back to the wording both hosts can follow, so a test
        # that asserts Claude Code phrasing without declaring the host is
        # asserting against whatever the environment happened to hold.
        environment = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
        result = run_hook({"source": "startup"}, env=environment)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        specific = payload["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "SessionStart")
        self.assertTrue(payload["suppressOutput"])
        self.assertIn("cognitive-powers:<name>", specific["additionalContext"])

    def test_the_shipped_injection_is_the_instruction_without_the_catalogue(
        self,
    ) -> None:
        """What the baseline decided, held by an assertion rather than a note.

        ``docs/analysis/activation-baseline-v1.md`` found the two renderings
        equivalent over 61 paired trials at a 0.10 margin, so the 743-token
        catalogue stopped shipping. Nothing before this checked which rendering
        the hook actually chooses, which is how it came to ship for two releases
        on the strength of nothing having argued against it.
        """
        outcome = activation.build({"source": "startup"})
        self.assertEqual(outcome["status"], "injected")
        self.assertFalse(outcome["index"])
        self.assertNotIn(activation.HEADER, outcome["message"])
        self.assertIn(activation.INSTRUCTION_ONLY_HEADER, outcome["message"])

    def test_the_catalogue_is_still_reachable_for_the_arm_that_lost(self) -> None:
        # The eval harness's `full` arm is this branch. Deleting it would delete
        # the only configuration that can re-decide the baseline against a
        # different model, so it stays, off by default.
        with mock.patch.dict(
            os.environ, {"COGNITIVE_POWERS_ENABLE_ACTIVATION_INDEX": "1"}
        ):
            outcome = activation.build({"source": "startup"})
        self.assertTrue(outcome["index"])
        self.assertIn(activation.HEADER, outcome["message"])

    def test_resumed_session_is_not_injected_twice(self) -> None:
        # The text is already in a resumed session's history; paying for it
        # again buys nothing and doubles the cost of the longest sessions.
        outcome = activation.build({"source": "resume"})
        self.assertEqual(outcome["status"], "skipped")
        self.assertEqual(run_hook({"source": "resume"}).stdout, "")

    def test_clear_and_compact_are_injected(self) -> None:
        for source in ("clear", "compact"):
            with self.subTest(source=source):
                self.assertEqual(
                    activation.build({"source": source})["status"], "injected"
                )

    def test_missing_skills_directory_emits_nothing_and_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            with mock.patch.object(
                activation, "resolve_host", return_value=(Path(empty), True)
            ):
                outcome = activation.build({"source": "startup"})
        self.assertEqual(outcome["status"], "skipped")
        self.assertNotIn("message", outcome)

    def test_disabled_by_environment_emits_nothing(self) -> None:
        environment = {**os.environ, "COGNITIVE_POWERS_DISABLE_ACTIVATION": "1"}
        result = run_hook({"source": "startup"}, env=environment)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_unusable_input_never_fails_the_session(self) -> None:
        for payload in ("", "not json", "[]", '{"source": 5}'):
            with self.subTest(payload=payload):
                result = run_hook(payload)
                self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
