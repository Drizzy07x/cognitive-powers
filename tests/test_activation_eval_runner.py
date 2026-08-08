"""Detection, arm verification, scoring, and the process path that drives them."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT / "evals") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "evals"))

from activation_core import fixtures, runner, transcript  # noqa: E402
from tests.activation_eval_support import TempTreeTestCase  # noqa: E402
from activation_core.arms import ARMS  # noqa: E402
from activation_core.cases import Case  # noqa: E402
from activation_core.report import as_markdown  # noqa: E402
from activation_core.scoring import (  # noqa: E402
    bottom_skills,
    case_stability,
    compare,
    observe,
    score_arm,
    summarize,
)
from activation_core import session as session_module  # noqa: E402
from activation_core.session import (  # noqa: E402
    DEFAULT_TOOLS,
    PLUGIN_CONFIG_KEY,
    Run,
    _environment,
    build_argv,
    looks_rate_limited,
    run_case,
)

INSTALLED = frozenset(
    {"diagnose-systematically", "refactor-cleanly", "verify-delivery", "map-project"}
)

INDEX_TEXT = (
    '{"suppressOutput": true, "hookSpecificOutput": {"hookEventName": '
    '"SessionStart", "additionalContext": "Cognitive Powers workflows are '
    "installed here. Before starting any non-trivial request, check whether one "
    'of these trigger conditions matches what was actually asked:"}}'
)
STANDING_TEXT = (
    '{"hookSpecificOutput": {"additionalContext": "Cognitive Powers: before '
    "acting, check this session's workflow index for a trigger condition "
    'matching this request."}}'
)
SESSION_STANDING_TEXT = (
    '{"hookSpecificOutput": {"additionalContext": "Cognitive Powers workflows '
    "are installed here, and this session already lists each one's name and "
    'trigger conditions."}}'
)


def event(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def hook_response(text: str, *, name: str = "SessionStart", error: bool = False) -> str:
    return event(
        {
            "type": "system",
            "subtype": "hook_response",
            "hook_name": name,
            "output": text,
            "exit_code": 1 if error else 0,
            "outcome": "error" if error else "success",
        }
    )


def tool_use(name: str, payload: dict) -> str:
    return event(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": name, "input": payload}]
            },
        }
    )


def skill_call(workflow: str) -> str:
    return tool_use("Skill", {"skill": f"cognitive-powers:{workflow}", "args": "x"})


def result(is_error: bool = False, cost: float = 0.02) -> str:
    return event(
        {
            "type": "result",
            "subtype": "success",
            "is_error": is_error,
            "total_cost_usd": cost,
        }
    )


def stream(*lines: str) -> str:
    return "\n".join(lines)


class DetectionTests(TempTreeTestCase):
    def test_a_skill_tool_use_is_an_activation(self) -> None:
        reading = transcript.read(
            stream(skill_call("diagnose-systematically"), result()), INSTALLED
        )
        self.assertTrue(reading.complete)
        self.assertEqual(reading.fired, ("diagnose-systematically",))
        self.assertEqual(reading.first_fired, "diagnose-systematically")

    def test_the_plugins_own_injection_is_not_an_activation(self) -> None:
        # The router's message contains the literal text
        # "cognitive-powers:<name>". A harness that scanned the stream for it
        # would score its own instrumentation as the result, which is the
        # failure mode the reference harnesses record and this test pins.
        suggestion = hook_response(
            '{"hookSpecificOutput": {"additionalContext": "Cognitive Powers: '
            "this request matches the 'refactor-cleanly' skill. Invoke it with "
            'the Skill tool as cognitive-powers:refactor-cleanly."}}',
            name="UserPromptSubmit",
        )
        reading = transcript.read(
            stream(INDEX_TEXT and hook_response(INDEX_TEXT), suggestion, result()),
            INSTALLED,
        )
        self.assertEqual(reading.fired, ())
        self.assertTrue(reading.complete)

    def test_prose_claiming_a_workflow_is_not_an_activation(self) -> None:
        prose = event(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "I consulted cognitive-powers:verify-delivery.",
                        }
                    ]
                },
            }
        )
        reading = transcript.read(stream(prose, result()), INSTALLED)
        self.assertEqual(reading.fired, ())

    def test_a_workflow_that_is_not_installed_is_not_counted(self) -> None:
        reading = transcript.read(
            stream(skill_call("some-other-plugin-skill"), result()), INSTALLED
        )
        self.assertEqual(reading.fired, ())

    def test_a_skill_from_another_plugin_is_not_counted(self) -> None:
        reading = transcript.read(
            stream(tool_use("Skill", {"skill": "other:verify-delivery"}), result()),
            INSTALLED,
        )
        self.assertEqual(reading.fired, ())

    def test_repeated_invocations_collapse_but_keep_order(self) -> None:
        reading = transcript.read(
            stream(
                skill_call("verify-delivery"),
                skill_call("map-project"),
                skill_call("verify-delivery"),
                result(),
            ),
            INSTALLED,
        )
        self.assertEqual(reading.fired, ("verify-delivery", "map-project"))

    def test_a_stream_without_a_terminal_result_is_incomplete(self) -> None:
        reading = transcript.read(stream(skill_call("map-project")), INSTALLED)
        self.assertFalse(reading.complete)
        self.assertIn("terminal result", reading.incomplete_reason or "")

    def test_a_malformed_stream_is_incomplete_rather_than_silent(self) -> None:
        reading = transcript.read(stream("{not json", result()), INSTALLED)
        self.assertFalse(reading.complete)
        self.assertIn("not JSON", reading.incomplete_reason or "")

    def test_an_empty_stream_is_incomplete(self) -> None:
        reading = transcript.read("", INSTALLED)
        self.assertFalse(reading.complete)
        self.assertIn("no events", reading.incomplete_reason or "")

    def test_failed_hooks_are_counted_without_spoiling_the_run(self) -> None:
        reading = transcript.read(
            stream(hook_response("Python was not found", error=True), result()),
            INSTALLED,
        )
        self.assertEqual(reading.injections.failed_hooks, 1)
        self.assertTrue(reading.complete)

    def test_work_after_firing_is_recorded_as_the_proxy_it_is(self) -> None:
        named_only = transcript.read(
            stream(skill_call("map-project"), result()), INSTALLED
        )
        self.assertFalse(named_only.worked_after_firing)
        worked = transcript.read(
            stream(skill_call("map-project"), tool_use("Read", {}), result()),
            INSTALLED,
        )
        self.assertTrue(worked.worked_after_firing)

    def test_work_before_firing_does_not_count_as_work_after_it(self) -> None:
        # The case the field exists to catch, and the one the first version
        # reported as its opposite: a model that explored and then named a
        # workflow it did nothing with scored as having worked afterwards,
        # because position was never compared.
        explored_then_named = transcript.read(
            stream(
                tool_use("Read", {}),
                tool_use("Glob", {}),
                skill_call("map-project"),
                result(),
            ),
            INSTALLED,
        )
        self.assertEqual(explored_then_named.fired, ("map-project",))
        self.assertEqual(len(explored_then_named.other_tools), 2)
        self.assertEqual(explored_then_named.tools_after_firing, ())
        self.assertFalse(explored_then_named.worked_after_firing)

    def test_only_tools_after_the_first_invocation_are_kept(self) -> None:
        reading = transcript.read(
            stream(
                tool_use("Read", {}),
                skill_call("map-project"),
                tool_use("Grep", {}),
                result(),
            ),
            INSTALLED,
        )
        self.assertEqual(reading.other_tools, ("Read", "Grep"))
        self.assertEqual(reading.tools_after_firing, ("Grep",))
        self.assertTrue(reading.worked_after_firing)


class ArmVerificationTests(TempTreeTestCase):
    def read(self, *lines: str) -> transcript.Reading:
        return transcript.read(stream(*lines, result()), INSTALLED)

    def test_full_arm_needs_both_injections(self) -> None:
        arm = ARMS["full"]
        good = self.read(hook_response(INDEX_TEXT), hook_response(STANDING_TEXT))
        self.assertIsNone(
            transcript.arm_mismatch(good, arm.expects_index, arm.expects_instruction)
        )
        missing = self.read(hook_response(STANDING_TEXT))
        self.assertIn(
            "expected the workflow index",
            transcript.arm_mismatch(missing, arm.expects_index, arm.expects_instruction)
            or "",
        )

    def test_none_arm_fails_when_anything_was_injected(self) -> None:
        arm = ARMS["none"]
        leaked = self.read(hook_response(STANDING_TEXT))
        self.assertIn(
            "suppressed the standing instruction",
            transcript.arm_mismatch(leaked, arm.expects_index, arm.expects_instruction)
            or "",
        )

    def test_instruction_arm_rejects_a_leaking_index(self) -> None:
        arm = ARMS["instruction"]
        clean = self.read(hook_response(SESSION_STANDING_TEXT))
        self.assertIsNone(
            transcript.arm_mismatch(clean, arm.expects_index, arm.expects_instruction)
        )
        leaked = self.read(
            hook_response(INDEX_TEXT), hook_response(SESSION_STANDING_TEXT)
        )
        self.assertIn(
            "suppressed the workflow index",
            transcript.arm_mismatch(leaked, arm.expects_index, arm.expects_instruction)
            or "",
        )

    def test_a_double_injection_is_not_the_arm(self) -> None:
        # Both hook manifests loading at once injects everything twice. An arm
        # delivered twice is a different amount of context than the one
        # declared, so it must not be scored as that arm.
        arm = ARMS["full"]
        doubled = self.read(
            hook_response(INDEX_TEXT),
            hook_response(INDEX_TEXT),
            hook_response(STANDING_TEXT),
        )
        self.assertIn(
            "injected 2 times",
            transcript.arm_mismatch(doubled, arm.expects_index, arm.expects_instruction)
            or "",
        )

    def test_a_doubled_prompt_instruction_is_not_the_arm_either(self) -> None:
        # The prompt-side twin of the case above: a double-registered
        # UserPromptSubmit delivers the standing instruction twice, and only
        # the index and session renderings used to be checked, so this run
        # was still scored as the arm.
        arm = ARMS["full"]
        doubled = self.read(
            hook_response(INDEX_TEXT),
            hook_response(STANDING_TEXT),
            hook_response(STANDING_TEXT),
        )
        self.assertIn(
            "prompt standing instruction was injected 2 times",
            transcript.arm_mismatch(doubled, arm.expects_index, arm.expects_instruction)
            or "",
        )


def make_case(**overrides) -> Case:
    values = {
        "case_id": "c",
        "prompt": "the login button does nothing",
        "lang": "en",
        "expect": ("diagnose-systematically",),
        "forbid": (),
        "fixture": "webshop",
        "quick": True,
        "mode": "all",
        "source": "test",
    }
    values.update(overrides)
    return Case(**values)


def make_observation(case: Case, reading: transcript.Reading, arm: str = "full"):
    configured = ARMS[arm]
    return observe(
        case,
        arm,
        1,
        reading,
        expects_index=configured.expects_index,
        expects_instruction=configured.expects_instruction,
        duration_seconds=1.0,
        stopped_early=False,
    )


class ScoringTests(TempTreeTestCase):
    def full(self, *lines: str) -> transcript.Reading:
        return transcript.read(
            stream(hook_response(INDEX_TEXT), hook_response(STANDING_TEXT), *lines),
            INSTALLED,
        )

    def test_a_cohort_with_no_complete_run_has_no_rate(self) -> None:
        case = make_case()
        broken = make_observation(case, transcript.read("{bad", INSTALLED))
        summary = summarize([broken])
        self.assertEqual(summary["status"], "empty")
        self.assertNotIn("passRate", summary)
        self.assertTrue(summary["reasons"])

    def test_an_arm_that_never_took_effect_does_not_score(self) -> None:
        case = make_case()
        reading = transcript.read(
            stream(skill_call("diagnose-systematically"), result()), INSTALLED
        )
        observation = make_observation(case, reading, arm="full")
        self.assertFalse(observation.complete)
        self.assertIsNone(observation.passed)

    def test_a_negative_case_stopped_early_cannot_count(self) -> None:
        case = make_case(case_id="quiet", expect=())
        configured = ARMS["full"]
        observation = observe(
            case,
            "full",
            1,
            self.full(result()),
            expects_index=configured.expects_index,
            expects_instruction=configured.expects_instruction,
            duration_seconds=1.0,
            stopped_early=True,
        )
        self.assertFalse(observation.complete)
        self.assertIn("stopped early", observation.incomplete_reason or "")

    def test_misroute_is_distinguished_from_silence(self) -> None:
        case = make_case()
        wrong = make_observation(case, self.full(skill_call("map-project"), result()))
        silent = make_observation(case, self.full(result()))
        self.assertTrue(wrong.misrouted)
        self.assertFalse(silent.misrouted)
        summary = summarize([wrong, silent])
        self.assertEqual(summary["misroutedRuns"], 1)
        self.assertEqual(summary["passRate"], 0.0)

    def test_case_stability_marks_a_flip(self) -> None:
        case = make_case()
        passed = make_observation(
            case, self.full(skill_call("diagnose-systematically"), result())
        )
        failed = make_observation(case, self.full(result()))
        entry = case_stability([passed, failed])
        self.assertTrue(entry["flipped"])
        self.assertEqual(entry["passRate"], 0.5)
        self.assertEqual(entry["variance"], 0.25)
        steady = case_stability([passed, passed])
        self.assertFalse(steady["flipped"])
        self.assertEqual(steady["variance"], 0.0)

    def test_false_positive_rate_comes_only_from_negative_cases(self) -> None:
        positive = make_case()
        negative = make_case(case_id="quiet", expect=(), fixture="pylib")
        observations = [
            make_observation(
                positive, self.full(skill_call("diagnose-systematically"), result())
            ),
            make_observation(negative, self.full(skill_call("map-project"), result())),
            make_observation(negative, self.full(result())),
        ]
        scored = score_arm("full", observations, [positive, negative])
        self.assertEqual(scored["shouldFire"]["passRate"], 1.0)
        self.assertEqual(scored["falsePositiveRate"], 0.5)

    def test_bottom_skills_omits_workflows_that_were_never_measured(self) -> None:
        # An unmeasured workflow ranked last would read as the opposite of what
        # the data says.
        measured = make_case(case_id="m")
        unmeasured = make_case(case_id="u", expect=("refactor-cleanly",))
        observations = [
            make_observation(measured, self.full(result())),
            make_observation(unmeasured, transcript.read("{bad", INSTALLED)),
        ]
        scored = score_arm("full", observations, [measured, unmeasured])
        ranked = bottom_skills(scored)
        self.assertEqual(
            [entry["skill"] for entry in ranked], ["diagnose-systematically"]
        )

    def test_compare_reports_deltas_against_the_no_injection_arm(self) -> None:
        rows = compare(
            [
                {
                    "arm": "none",
                    "shouldFire": {"passRate": 0.4, "complete": 5},
                    "falsePositiveRate": 0.0,
                },
                {
                    "arm": "full",
                    "shouldFire": {"passRate": 0.9, "complete": 5},
                    "falsePositiveRate": 0.1,
                },
            ]
        )
        self.assertEqual(rows["arms"][1]["activationDelta"], 0.5)


class ReportTests(TempTreeTestCase):
    def test_markdown_shows_a_missing_rate_rather_than_a_zero(self) -> None:
        payload = {
            "run": {"model": "sonnet", "repetitions": 3, "cases": 1, "invocations": 3},
            "arms": [
                {
                    "arm": "full",
                    "overall": {
                        "total": 3,
                        "complete": 0,
                        "incomplete": 3,
                        "reasons": {"claude exited 1": 3},
                    },
                    "shouldFire": {"total": 3, "complete": 0, "incomplete": 3},
                    "shouldNotFire": {"total": 0, "complete": 0, "incomplete": 0},
                    "multiSkill": {"total": 0},
                    "spanish": {"total": 0},
                    "falsePositiveRate": None,
                    "perSkill": {},
                    "flippedCases": [],
                }
            ],
        }
        rendered = as_markdown(payload)
        self.assertIn("--", rendered)
        self.assertNotIn("0%", rendered)
        self.assertIn("3 x claude exited 1", rendered)


class SessionShapeTests(TempTreeTestCase):
    def test_argv_carries_the_flags_the_measurement_depends_on(self) -> None:
        argv = build_argv(
            make_case(),
            plugin_root=PLUGIN_ROOT,
            settings_path=Path("settings.json"),
            model="sonnet",
            max_cost_usd=0.5,
        )
        self.assertEqual(argv[:3], ["claude", "-p", "the login button does nothing"])
        for flag in (
            "--plugin-dir",
            "--include-hook-events",
            "--output-format",
            "--no-session-persistence",
            "--setting-sources",
        ):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--tools") + 1], ",".join(DEFAULT_TOOLS))
        self.assertNotIn("Bash", argv[argv.index("--tools") + 1])

    def test_the_environment_is_the_arm_and_nothing_inherited(self) -> None:
        os.environ["COGNITIVE_POWERS_DISABLE_ROUTER"] = "1"
        os.environ["CLAUDECODE"] = "1"
        try:
            env = _environment(ARMS["full"], Path("data"))
        finally:
            os.environ.pop("COGNITIVE_POWERS_DISABLE_ROUTER", None)
            os.environ.pop("CLAUDECODE", None)
        # The operator's own exported toggle would otherwise silently become
        # part of the arm under measurement.
        self.assertNotIn("COGNITIVE_POWERS_DISABLE_ROUTER", env)
        self.assertNotIn("CLAUDECODE", env)
        self.assertEqual(env["COGNITIVE_POWERS_DATA"], "data")

        none_env = _environment(ARMS["none"], Path("data"))
        self.assertEqual(none_env["COGNITIVE_POWERS_DISABLE_ACTIVATION"], "1")
        self.assertEqual(none_env["COGNITIVE_POWERS_DISABLE_ROUTER"], "1")

    def test_settings_name_the_key_an_inline_plugin_actually_reads(self) -> None:
        self.assertEqual(PLUGIN_CONFIG_KEY, "cognitive-powers@inline")


def run_shaped(**overrides) -> Run:
    values = {
        "case_id": "c",
        "arm": "full",
        "repetition": 1,
        "stream": "",
        "stderr": "",
        "exit_code": 0,
        "duration_seconds": 1.0,
        "stopped_early": False,
        "timed_out": False,
    }
    values.update(overrides)
    return Run(**values)


class RateLimitTests(TempTreeTestCase):
    def test_a_healthy_run_is_never_retried_however_it_reads(self) -> None:
        self.assertFalse(
            looks_rate_limited(
                run_shaped(stream='{"type":"rate_limit_event"}', exit_code=0)
            )
        )

    def test_a_deliberate_stop_is_never_read_as_throttling(self) -> None:
        # The regression that cost a whole matrix. A run stopped once its
        # expectation was met exits non-zero because the harness killed it, and
        # every session emits `rate_limit_event` as ordinary telemetry. Together
        # those made every successful early stop retry five times with backoff:
        # the cheapest runs in the corpus became the most expensive.
        settled = run_shaped(
            stream="\n".join(
                [
                    '{"type":"system","subtype":"rate_limit_event"}',
                    skill_call("diagnose-systematically"),
                ]
            ),
            exit_code=1,
            stopped_early=True,
        )
        self.assertFalse(looks_rate_limited(settled))

    def test_a_timed_out_run_is_not_read_as_throttling_either(self) -> None:
        self.assertFalse(looks_rate_limited(run_shaped(exit_code=1, timed_out=True)))

    def test_telemetry_in_a_failed_stream_is_not_evidence_of_throttling(self) -> None:
        # Only the terminal result's own failure text counts. Scanning the whole
        # stream is what made ordinary telemetry look like a throttled provider.
        noisy = run_shaped(
            stream="\n".join(
                [
                    '{"type":"system","subtype":"rate_limit_event"}',
                    '{"type":"result","subtype":"success","result":"all done"}',
                ]
            ),
            exit_code=1,
        )
        self.assertFalse(looks_rate_limited(noisy))

    def test_a_failed_run_that_says_rate_limit_is_retryable(self) -> None:
        for text in ("429 Too Many Requests", "rate limit exceeded", "Overloaded"):
            with self.subTest(text=text):
                self.assertTrue(
                    looks_rate_limited(run_shaped(stderr=text, exit_code=1))
                )

    def test_a_terminal_result_reporting_a_rate_limit_is_retryable(self) -> None:
        throttled = run_shaped(
            stream=json.dumps(
                {
                    "type": "result",
                    "subtype": "error",
                    "result": "Rate limit reached, retry later",
                }
            ),
            exit_code=1,
        )
        self.assertTrue(looks_rate_limited(throttled))

    def test_a_failure_for_any_other_reason_is_not_retried(self) -> None:
        self.assertFalse(
            looks_rate_limited(
                run_shaped(stderr="Not logged in - please run /login", exit_code=1)
            )
        )

    def test_a_throttled_run_is_retried_with_growing_waits(self) -> None:
        waits: list[float] = []
        attempts = {"n": 0}

        def flaky(*argv, **kwargs):
            attempts["n"] += 1
            throttled = attempts["n"] < 3
            return Run(
                case_id="c",
                arm="full",
                repetition=1,
                stream="",
                stderr="429 rate limit" if throttled else "",
                exit_code=1 if throttled else 0,
                duration_seconds=0.1,
                stopped_early=False,
                timed_out=False,
            )

        with mock.patch.object(session_module, "_attempt", flaky):
            outcome = run_case(
                make_case(),
                ARMS["full"],
                1,
                plugin_root=PLUGIN_ROOT,
                workspace_root=self.temp_dir("cp-retry-"),
                python_executable=sys.executable,
                installed=INSTALLED,
                backoff_seconds=1.0,
                sleep=waits.append,
            )
        self.assertEqual(outcome.attempts, 3)
        self.assertFalse(outcome.rate_limited)
        self.assertEqual(waits, [1.0, 2.0])

    def test_exhausted_retries_come_back_marked_rather_than_scored(self) -> None:
        def always(*argv, **kwargs):
            return Run(
                case_id="c",
                arm="full",
                repetition=1,
                stream="",
                stderr="429 rate limit",
                exit_code=1,
                duration_seconds=0.1,
                stopped_early=False,
                timed_out=False,
            )

        with mock.patch.object(session_module, "_attempt", always):
            outcome = run_case(
                make_case(),
                ARMS["full"],
                1,
                plugin_root=PLUGIN_ROOT,
                workspace_root=self.temp_dir("cp-retry2-"),
                python_executable=sys.executable,
                installed=INSTALLED,
                max_attempts=2,
                backoff_seconds=0.0,
                sleep=lambda _: None,
            )
        self.assertTrue(outcome.rate_limited)
        self.assertEqual(outcome.attempts, 2)
        # Incomplete, so it is absent from the denominator rather than counted
        # as a workflow that failed to activate.
        observation = observe(
            make_case(),
            "full",
            1,
            transcript.read(outcome.stream, INSTALLED),
            expects_index=True,
            expects_instruction=True,
            duration_seconds=0.1,
            stopped_early=False,
            stream_error="claude exited 1",
        )
        self.assertFalse(observation.complete)
        self.assertIsNone(observation.passed)


class FixtureTests(TempTreeTestCase):
    def test_every_named_fixture_materializes(self) -> None:
        base = self.temp_dir("cp-fixture-")
        for name in fixtures.fixture_names():
            root = fixtures.materialize(name, base / name)
            self.assertTrue((root / "README.md").is_file())

    def test_an_unknown_fixture_is_refused_by_name(self) -> None:
        with self.assertRaisesRegex(KeyError, "unknown fixture"):
            fixtures.materialize("nope", self.temp_dir())


STUB = """
import json
import sys

lines = [
    {"type": "system", "subtype": "hook_response", "hook_name": "SessionStart",
     "output": "Cognitive Powers workflows are installed here. Before starting "
               "any non-trivial request, check whether one of these trigger "
               "conditions matches what was actually asked:",
     "exit_code": 0, "outcome": "success"},
    {"type": "system", "subtype": "hook_response", "hook_name": "UserPromptSubmit",
     "output": "Cognitive Powers: before acting, check this session's workflow "
               "index for a trigger condition matching this request.",
     "exit_code": 0, "outcome": "success"},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Skill",
         "input": {"skill": "cognitive-powers:diagnose-systematically"}}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "x"}}]}},
    {"type": "result", "subtype": "success", "is_error": False,
     "total_cost_usd": 0.01},
]
for line in lines:
    print(json.dumps(line), flush=True)
sys.exit(0)
"""


class SubprocessPathTests(TempTreeTestCase):
    """Exercise the real process path against a stand-in for the host.

    Not a mock of the code under test: ``run_case`` builds the argv, spawns the
    process, streams stdout, applies the early stop and tears down for real. The
    only substitution is the executable at the end of it, because the point is
    to test the plumbing without paying a provider for every assertion.
    """

    def _stub(self, directory: Path) -> str:
        script = directory / "stub_claude.py"
        script.write_text(STUB, encoding="utf-8")
        if os.name == "nt":
            launcher = directory / "stub_claude.cmd"
            launcher.write_text(
                f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
            )
            return str(launcher)
        launcher = directory / "stub_claude.sh"
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)
        return str(launcher)

    def test_a_deliberate_stop_still_counts_as_a_complete_observation(self) -> None:
        # A stopped run has no terminal result event by construction. Scoring
        # that as a truncated stream discarded every run in which the workflow
        # actually fired, which is the positive half of the measurement.
        case = make_case()
        reading = transcript.read(
            stream(
                hook_response(INDEX_TEXT),
                hook_response(STANDING_TEXT),
                skill_call("diagnose-systematically"),
            ),
            INSTALLED,
        )
        self.assertFalse(reading.complete)
        observation = observe(
            case,
            "full",
            1,
            reading,
            expects_index=True,
            expects_instruction=True,
            duration_seconds=2.0,
            stopped_early=True,
        )
        self.assertTrue(observation.complete)
        self.assertTrue(observation.passed)

    def test_a_truncated_stream_without_a_stop_is_still_incomplete(self) -> None:
        case = make_case()
        reading = transcript.read(
            stream(hook_response(INDEX_TEXT), hook_response(STANDING_TEXT)),
            INSTALLED,
        )
        observation = observe(
            case,
            "full",
            1,
            reading,
            expects_index=True,
            expects_instruction=True,
            duration_seconds=2.0,
            stopped_early=False,
        )
        self.assertFalse(observation.complete)

    def test_a_run_streams_and_stops_once_the_verdict_is_settled(self) -> None:
        base = self.temp_dir("cp-run-")
        outcome = run_case(
            make_case(),
            ARMS["full"],
            1,
            plugin_root=PLUGIN_ROOT,
            workspace_root=base,
            python_executable=sys.executable,
            installed=INSTALLED,
            model="sonnet",
            max_cost_usd=0.1,
            timeout_seconds=60.0,
            claude_executable=self._stub(base),
        )
        self.assertTrue(outcome.stopped_early)
        self.assertFalse(outcome.timed_out)
        reading = transcript.read(outcome.stream, INSTALLED)
        self.assertEqual(reading.fired, ("diagnose-systematically",))
        # The workspace really was created and the settings really were written.
        self.assertTrue((base / "c-full-1" / "ws" / "src" / "login.js").is_file())
        settings = json.loads((base / "c-full-1" / "settings.json").read_text())
        self.assertIn(PLUGIN_CONFIG_KEY, settings["pluginConfigs"])

    def test_a_silent_stall_is_killed_by_the_watchdog(self) -> None:
        # The clock cannot be checked inside the read loop alone: a session
        # that stalls with its pipe open produces no line to check it on, and
        # would hold the whole matrix.
        base = self.temp_dir("cp-run-hang-")
        script = base / "stall.py"
        script.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
        if os.name == "nt":
            launcher = base / "stall.cmd"
            launcher.write_text(
                f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
            )
        else:
            launcher = base / "stall.sh"
            launcher.write_text(
                f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
                encoding="utf-8",
            )
            launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)

        outcome = run_case(
            make_case(),
            ARMS["full"],
            1,
            plugin_root=PLUGIN_ROOT,
            workspace_root=base,
            python_executable=sys.executable,
            installed=INSTALLED,
            model="sonnet",
            max_cost_usd=0.1,
            timeout_seconds=2.0,
            claude_executable=str(launcher),
        )
        self.assertTrue(outcome.timed_out)
        self.assertLess(outcome.duration_seconds, 60.0)

    def test_a_negative_case_is_never_cut_short(self) -> None:
        base = self.temp_dir("cp-run-neg-")
        outcome = run_case(
            make_case(case_id="quiet", expect=()),
            ARMS["full"],
            1,
            plugin_root=PLUGIN_ROOT,
            workspace_root=base,
            python_executable=sys.executable,
            installed=INSTALLED,
            model="sonnet",
            max_cost_usd=0.1,
            timeout_seconds=60.0,
            claude_executable=self._stub(base),
        )
        self.assertFalse(outcome.stopped_early)
        self.assertEqual(outcome.exit_code, 0)


class OrchestrationTests(TempTreeTestCase):
    def test_the_plan_enumerates_every_intended_invocation(self) -> None:
        cases = [make_case(), make_case(case_id="quiet", expect=())]
        plan = runner.plan([ARMS["none"], ARMS["full"]], cases, 3)
        self.assertEqual(len(plan), 12)
        self.assertEqual({entry["arm"] for entry in plan}, {"none", "full"})

    def test_execute_scores_every_arm_it_was_given(self) -> None:
        cases = [make_case()]

        def fake(case, arm, repetition, **kwargs):
            fired = skill_call("diagnose-systematically") if arm.name == "full" else ""
            hooks = []
            if arm.expects_index:
                hooks.append(hook_response(INDEX_TEXT))
            if arm.expects_instruction:
                hooks.append(hook_response(STANDING_TEXT))
            body = [line for line in (*hooks, fired, result()) if line]
            return Run(
                case_id=case.case_id,
                arm=arm.name,
                repetition=repetition,
                stream=stream(*body),
                stderr="",
                exit_code=0,
                duration_seconds=0.1,
                stopped_early=False,
                timed_out=False,
            )

        payload = runner.execute(
            cases=cases,
            arms=[ARMS["none"], ARMS["full"]],
            repetitions=2,
            plugin_root=PLUGIN_ROOT,
            installed=INSTALLED,
            python_executable=sys.executable,
            model="sonnet",
            max_cost_usd=0.1,
            timeout_seconds=10.0,
            claude_executable="unused",
            artifacts=None,
            workspace_root=self.temp_dir("cp-exec-"),
            runner=fake,
        )
        self.assertEqual(payload["run"]["invocations"], 4)
        # The report renders this, and nothing used to write it.
        self.assertEqual(payload["run"]["suite"], "unlabelled")
        self.assertNotIn("Suite: None", as_markdown(payload))
        by_arm = {entry["arm"]: entry for entry in payload["arms"]}
        self.assertEqual(by_arm["none"]["shouldFire"]["passRate"], 0.0)
        self.assertEqual(by_arm["full"]["shouldFire"]["passRate"], 1.0)
        # The delta is attached to the arm, so the rendered table has it.
        self.assertEqual(by_arm["full"]["activationDelta"], 1.0)
        self.assertEqual(by_arm["none"]["activationDelta"], 0.0)
        self.assertIn("+100 pts", as_markdown(payload))

    def test_the_gate_fails_an_arm_it_could_not_measure(self) -> None:
        payload = {
            "arms": [
                {
                    "arm": "full",
                    "shouldFire": {"passRate": None},
                    "falsePositiveRate": None,
                }
            ]
        }
        failures = runner.gate(payload, floor=0.5, max_false_positive=0.1)
        self.assertEqual(len(failures), 1)
        self.assertIn("no complete should-fire run", failures[0])

    def test_the_gate_fails_a_ceiling_nothing_was_measured_against(self) -> None:
        """A missing false-positive rate used to pass, which is the whole hole.

        The arm has a healthy activation rate and twenty planned should-not-fire
        cases none of which produced a rate. Read as a pass, that is a run that
        never measured over-triggering reported exactly like one that measured
        it and found none.
        """
        payload = {
            "run": {"shouldNotFireCases": 20},
            "arms": [
                {
                    "arm": "full",
                    "shouldFire": {"passRate": 0.95},
                    "falsePositiveRate": None,
                }
            ],
        }
        failures = runner.gate(payload, floor=0.7, max_false_positive=0.15)
        self.assertEqual(len(failures), 1)
        self.assertIn("no false-positive rate", failures[0])

    def test_the_gate_fails_a_floor_and_a_false_positive_ceiling(self) -> None:
        payload = {
            "arms": [
                {
                    "arm": "full",
                    "shouldFire": {"passRate": 0.4},
                    "falsePositiveRate": 0.3,
                }
            ]
        }
        failures = runner.gate(payload, floor=0.7, max_false_positive=0.1)
        self.assertEqual(len(failures), 2)

    def test_results_keep_corpus_order_whatever_the_pool_does(self) -> None:
        # Row order that depended on scheduling would make two matrices
        # undiffable even when every observation agreed.
        cases = [make_case(case_id=f"c{index}") for index in range(6)]
        delays = {"c0": 0.05, "c3": 0.03}

        def fake(case, arm, repetition, **kwargs):
            time.sleep(delays.get(case.case_id, 0.0))
            return Run(
                case_id=case.case_id,
                arm=arm.name,
                repetition=repetition,
                stream=stream(
                    hook_response(INDEX_TEXT),
                    hook_response(STANDING_TEXT),
                    skill_call("diagnose-systematically"),
                    result(),
                ),
                stderr="",
                exit_code=0,
                duration_seconds=0.1,
                stopped_early=False,
                timed_out=False,
            )

        payload = runner.execute(
            cases=cases,
            arms=[ARMS["full"]],
            repetitions=1,
            plugin_root=PLUGIN_ROOT,
            installed=INSTALLED,
            python_executable=sys.executable,
            model="sonnet",
            max_cost_usd=0.1,
            timeout_seconds=10.0,
            claude_executable="unused",
            artifacts=None,
            workspace_root=self.temp_dir("cp-order-"),
            runner=fake,
            workers=4,
        )
        observed = [item["case_id"] for item in payload["observations"]]
        self.assertEqual(observed, [case.case_id for case in cases])
        self.assertEqual(payload["run"]["workers"], 4)

    def test_one_case_that_explodes_does_not_end_the_matrix(self) -> None:
        cases = [make_case(case_id=f"c{index}") for index in range(4)]

        def flaky(case, arm, repetition, **kwargs):
            if case.case_id == "c2":
                raise OSError("workspace is on fire")
            return Run(
                case_id=case.case_id,
                arm=arm.name,
                repetition=repetition,
                stream=stream(
                    hook_response(INDEX_TEXT),
                    hook_response(STANDING_TEXT),
                    skill_call("diagnose-systematically"),
                    result(),
                ),
                stderr="",
                exit_code=0,
                duration_seconds=0.1,
                stopped_early=False,
                timed_out=False,
            )

        payload = runner.execute(
            cases=cases,
            arms=[ARMS["full"]],
            repetitions=1,
            plugin_root=PLUGIN_ROOT,
            installed=INSTALLED,
            python_executable=sys.executable,
            model="sonnet",
            max_cost_usd=0.1,
            timeout_seconds=10.0,
            claude_executable="unused",
            artifacts=None,
            workspace_root=self.temp_dir("cp-boom-"),
            runner=flaky,
            workers=2,
        )
        self.assertEqual(len(payload["observations"]), 4)
        arm = payload["arms"][0]
        # Three scored, one absent from the denominator with its reason stated.
        self.assertEqual(arm["shouldFire"]["complete"], 3)
        self.assertEqual(arm["shouldFire"]["passRate"], 1.0)
        self.assertIn("workspace is on fire", json.dumps(arm["shouldFire"]["reasons"]))

    def _arm_run(self, case, arm, repetition, passes: bool):
        body = []
        if arm.expects_index:
            body.append(hook_response(INDEX_TEXT))
        if arm.expects_instruction:
            body.append(hook_response(STANDING_TEXT))
        if passes:
            body.append(skill_call("diagnose-systematically"))
        body.append(result())
        return Run(
            case_id=case.case_id,
            arm=arm.name,
            repetition=repetition,
            stream=stream(*body),
            stderr="",
            exit_code=0,
            duration_seconds=0.1,
            stopped_early=False,
            timed_out=False,
        )

    def test_a_decided_comparison_stops_paying_for_more_runs(self) -> None:
        """The saving that makes the comparison runnable at all.

        The full matrix is hours, and most of them are spent after the answer
        has stopped changing. With one arm winning every trial, the verdict is
        settled long before the corpus is exhausted.
        """
        cases = [make_case(case_id=f"c{index:03d}") for index in range(60)]
        spawned = []

        def fake(case, arm, repetition, **kwargs):
            spawned.append((case.case_id, arm.name))
            return self._arm_run(case, arm, repetition, passes=arm.name == "full")

        payload = runner.execute(
            cases=cases,
            arms=[ARMS["instruction"], ARMS["full"]],
            repetitions=1,
            plugin_root=PLUGIN_ROOT,
            installed=INSTALLED,
            python_executable=sys.executable,
            model="sonnet",
            max_cost_usd=0.1,
            timeout_seconds=10.0,
            claude_executable="unused",
            artifacts=None,
            workspace_root=self.temp_dir("cp-seq-"),
            runner=fake,
            workers=1,
            stop_when_decided=True,
        )
        self.assertEqual(payload["run"]["plannedInvocations"], 120)
        self.assertLess(len(spawned), 120)
        self.assertIsNotNone(payload["run"]["stoppedAt"])
        verdicts = [row["verdict"] for row in payload["comparisons"]]
        self.assertEqual(verdicts, ["superior"])

    def test_the_stop_never_skips_the_pool_the_ceiling_is_read_from(self) -> None:
        """The stop decides which arm fires more, and nothing else.

        Measured for real on 2026-08-03: the comparison settled after 132 of
        206 invocations and every one of the twenty should-not-fire cases sat
        after the stop, so both arms reported an activation rate with
        ``falsePositiveRate: null`` beside it and `gate` passed them. An
        activation rate with no false-positive rate is the one pairing this
        corpus exists to keep together.
        """
        cases = [make_case(case_id=f"c{index:03d}") for index in range(60)]
        cases += [
            make_case(case_id=f"n{index:03d}", expect=(), quick=True)
            for index in range(4)
        ]
        spawned = []

        def fake(case, arm, repetition, **kwargs):
            spawned.append((case.case_id, arm.name))
            return self._arm_run(case, arm, repetition, passes=arm.name == "full")

        payload = runner.execute(
            cases=cases,
            arms=[ARMS["instruction"], ARMS["full"]],
            repetitions=1,
            plugin_root=PLUGIN_ROOT,
            installed=INSTALLED,
            python_executable=sys.executable,
            model="sonnet",
            max_cost_usd=0.1,
            timeout_seconds=10.0,
            claude_executable="unused",
            artifacts=None,
            workspace_root=self.temp_dir("cp-neg-"),
            runner=fake,
            workers=1,
            stop_when_decided=True,
        )
        self.assertIsNotNone(payload["run"]["stoppedAt"])
        self.assertLess(len(spawned), 128)
        negatives = {name for name, _ in spawned if name.startswith("n")}
        self.assertEqual(len(negatives), 4, "the stop skipped should-not-fire cases")
        for scored in payload["arms"]:
            self.assertEqual(scored["shouldNotFire"]["complete"], 4)
            self.assertIsNotNone(scored["falsePositiveRate"])

    def test_an_undecided_comparison_is_paid_in_full(self) -> None:
        """One undecided pair keeps the whole run going.

        Arms that agree on everything are not equivalent over a handful of
        trials, so the stop must not fire on the agreement alone.
        """
        cases = [make_case(case_id=f"c{index:03d}") for index in range(8)]
        spawned = []

        def fake(case, arm, repetition, **kwargs):
            spawned.append(arm.name)
            return self._arm_run(case, arm, repetition, passes=True)

        payload = runner.execute(
            cases=cases,
            arms=[ARMS["instruction"], ARMS["full"]],
            repetitions=1,
            plugin_root=PLUGIN_ROOT,
            installed=INSTALLED,
            python_executable=sys.executable,
            model="sonnet",
            max_cost_usd=0.1,
            timeout_seconds=10.0,
            claude_executable="unused",
            artifacts=None,
            workspace_root=self.temp_dir("cp-seq2-"),
            runner=fake,
            workers=1,
            stop_when_decided=True,
        )
        self.assertEqual(len(spawned), 16)
        self.assertIsNone(payload["run"]["stoppedAt"])
        self.assertEqual(payload["comparisons"][0]["verdict"], "not-proven")

    def test_arms_are_interleaved_so_position_is_not_the_arm(self) -> None:
        """Arm-major order made wall-clock position a property of the arm.

        Over a run of hours, provider load and throttling would land on
        whichever arm happened to be executing, and the report would attribute
        them to the injected context.
        """
        cases = [make_case(case_id=f"c{index}") for index in range(3)]
        order = []

        def fake(case, arm, repetition, **kwargs):
            order.append(arm.name)
            return self._arm_run(case, arm, repetition, passes=True)

        runner.execute(
            cases=cases,
            arms=[ARMS["none"], ARMS["instruction"], ARMS["full"]],
            repetitions=1,
            plugin_root=PLUGIN_ROOT,
            installed=INSTALLED,
            python_executable=sys.executable,
            model="sonnet",
            max_cost_usd=0.1,
            timeout_seconds=10.0,
            claude_executable="unused",
            artifacts=None,
            workspace_root=self.temp_dir("cp-order2-"),
            runner=fake,
            workers=1,
            stop_when_decided=False,
        )
        self.assertEqual(
            order,
            ["none", "instruction", "full"] * 3,
            "each trial must run all arms before the next trial starts",
        )

    def test_workers_outside_the_ceiling_are_refused(self) -> None:
        for workers in (0, runner.MAX_WORKERS + 1):
            with self.subTest(workers=workers), self.assertRaises(runner.RunnerError):
                runner.execute(
                    cases=[make_case()],
                    arms=[ARMS["full"]],
                    repetitions=1,
                    plugin_root=PLUGIN_ROOT,
                    installed=INSTALLED,
                    python_executable=sys.executable,
                    model="sonnet",
                    max_cost_usd=0.1,
                    timeout_seconds=10.0,
                    claude_executable="unused",
                    artifacts=None,
                    workers=workers,
                )

    def test_the_default_worker_count_stays_where_it_was_set(self) -> None:
        self.assertEqual(runner.DEFAULT_WORKERS, 3)
        self.assertEqual(runner.MAX_WORKERS, 4)

    def test_execute_refuses_an_empty_selection(self) -> None:
        with self.assertRaises(runner.RunnerError):
            runner.execute(
                cases=[],
                arms=[ARMS["full"]],
                repetitions=1,
                plugin_root=PLUGIN_ROOT,
                installed=INSTALLED,
                python_executable=sys.executable,
                model="sonnet",
                max_cost_usd=0.1,
                timeout_seconds=10.0,
                claude_executable="unused",
                artifacts=None,
            )


class CommandLineTests(TempTreeTestCase):
    def test_unknown_skills_are_rejected_before_anything_is_spawned(self) -> None:
        # The skill filter keeps the negative pool unconditionally, so a typo
        # in --skills used to run only the should-not-fire cases and spend
        # every paid invocation measuring nothing. The names are checked
        # against the installed tree instead, before a single host is spawned.
        import run_activation_eval

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = run_activation_eval.main(
                ["--skills", "diagnose-thoroughly,map-project", "--validate-only"]
            )
        self.assertEqual(code, 2)
        self.assertIn(
            "unknown workflows in --skills: diagnose-thoroughly", stderr.getvalue()
        )
        # The installed sibling in the same flag is not what gets reported.
        self.assertNotIn("map-project", stderr.getvalue())

    def test_installed_skills_still_pass_the_name_check(self) -> None:
        import run_activation_eval

        known = sorted(run_activation_eval._installed_workflows())[0]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                run_activation_eval.main(["--skills", known, "--validate-only"]), 0
            )


if __name__ == "__main__":
    unittest.main()
