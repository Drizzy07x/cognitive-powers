#!/usr/bin/env python3
"""Read what a session actually did out of its event stream.

Two rules govern everything here.

**Structure, never substring.** A workflow counts as invoked only when an
assistant message carries a ``tool_use`` block named ``Skill`` whose
``input.skill`` names an installed workflow. Nothing else counts -- not the
model saying it consulted one, not a file read under the plugin tree, and
emphatically not a text match on the stream. The last of those is not
hypothetical: with ``--include-hook-events`` the stream carries this plugin's
own router output, and that text contains the literal string
``cognitive-powers:<name>``. A harness that scanned for it would score its own
instrumentation as the result it was measuring.

**An unfinished run is not a run that fired nothing.** A stream that failed to
parse, carried no events, or never reached its terminal ``result`` is reported
as incomplete. Folding it into the denominator as a miss is how a harness turns
its own flakiness into a regression in the thing under test.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, NamedTuple

from .arms import (
    INDEX_MARKER,
    PROMPT_INSTRUCTION_MARKER,
    SESSION_INSTRUCTION_MARKER,
)

SKILL_TOOL = "Skill"
PLUGIN_PREFIX = "cognitive-powers:"

# Named so the one caller allowed to forgive it can say which reason it means.
# A run the harness stopped on purpose has no terminal event by construction,
# and treating that as a truncated stream would discard exactly the runs where
# the workflow fired fastest.
MISSING_TERMINAL = "stream never reached a terminal result event"


class Injections(NamedTuple):
    """How many times each payload reached the session."""

    index: int
    session_instruction: int
    prompt_instruction: int
    failed_hooks: int

    @property
    def instruction(self) -> int:
        return self.session_instruction + self.prompt_instruction


class Reading(NamedTuple):
    """Everything the harness is willing to claim about one run."""

    complete: bool
    incomplete_reason: str | None
    fired: tuple[str, ...]
    first_fired: str | None
    other_tools: tuple[str, ...]
    injections: Injections
    turns: int
    result_is_error: bool | None
    stop_reason: str | None
    cost_usd: float | None

    @property
    def worked_after_firing(self) -> bool:
        """Whether any tool ran after the first workflow was invoked.

        A proxy, and named as one. Invoking a workflow is not the same as the
        workflow shaping the answer, and this is the cheapest observable that
        separates a workflow which then did something from one that was named
        and abandoned. It is evidence, not proof, and no rate is computed from
        it.
        """
        return self.first_fired is not None and bool(self.other_tools)


def _content(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _skill_name(block: dict[str, Any], installed: frozenset[str]) -> str | None:
    """Return the installed workflow this block invokes, or None.

    The name is checked against what is actually installed rather than merely
    stripped of its prefix. An unknown name means the model invoked something
    this measurement does not cover, and scoring it as one of ours would credit
    the plugin for a different plugin's skill.
    """
    if block.get("name") != SKILL_TOOL:
        return None
    payload = block.get("input")
    if not isinstance(payload, dict):
        return None
    raw = payload.get("skill")
    if not isinstance(raw, str) or not raw.startswith(PLUGIN_PREFIX):
        return None
    name = raw[len(PLUGIN_PREFIX) :].strip()
    return name if name in installed else None


def _hook_text(event: dict[str, Any]) -> str:
    parts = [event.get("output"), event.get("stdout")]
    return " ".join(str(part) for part in parts if isinstance(part, str))


def parse_events(stream: str) -> tuple[list[dict[str, Any]], str | None]:
    """Split the NDJSON stream, or name why it cannot be trusted."""
    events: list[dict[str, Any]] = []
    for line in stream.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return events, "stream contains a line that is not JSON"
        if isinstance(value, dict):
            events.append(value)
    if not events:
        return events, "stream carried no events"
    return events, None


def read(stream: str, installed: Iterable[str]) -> Reading:
    """Turn one run's stream into the claims the harness will stand behind."""
    known = frozenset(installed)
    events, protocol_error = parse_events(stream)

    fired: list[str] = []
    other_tools: list[str] = []
    index = session_instruction = prompt_instruction = failed_hooks = 0
    turns = 0
    terminal: dict[str, Any] | None = None

    for event in events:
        kind = event.get("type")
        if kind == "assistant":
            turns += 1
            for block in _content(event):
                if block.get("type") != "tool_use":
                    continue
                name = _skill_name(block, known)
                if name is not None:
                    fired.append(name)
                    continue
                tool = block.get("name")
                if isinstance(tool, str):
                    other_tools.append(tool)
        elif event.get("subtype") == "hook_response":
            text = _hook_text(event)
            if event.get("outcome") == "error" or event.get("exit_code"):
                failed_hooks += 1
            if INDEX_MARKER in text:
                index += 1
            if SESSION_INSTRUCTION_MARKER in text:
                session_instruction += 1
            if PROMPT_INSTRUCTION_MARKER in text:
                prompt_instruction += 1
        elif kind == "result":
            terminal = event

    injections = Injections(
        index=index,
        session_instruction=session_instruction,
        prompt_instruction=prompt_instruction,
        failed_hooks=failed_hooks,
    )
    # Deduplicated, because one workflow invoked twice is still one workflow
    # invoked; order of first appearance is kept so first_fired stays meaningful.
    unique: list[str] = []
    for name in fired:
        if name not in unique:
            unique.append(name)

    reason = protocol_error
    if reason is None and terminal is None:
        reason = MISSING_TERMINAL

    return Reading(
        complete=reason is None,
        incomplete_reason=reason,
        fired=tuple(unique),
        first_fired=unique[0] if unique else None,
        other_tools=tuple(other_tools),
        injections=injections,
        turns=turns,
        result_is_error=(
            bool(terminal.get("is_error")) if isinstance(terminal, dict) else None
        ),
        stop_reason=(
            str(terminal.get("subtype")) if isinstance(terminal, dict) else None
        ),
        cost_usd=_cost(terminal),
    )


def _cost(terminal: dict[str, Any] | None) -> float | None:
    if not isinstance(terminal, dict):
        return None
    value = terminal.get("total_cost_usd")
    return float(value) if isinstance(value, (int, float)) else None


def arm_mismatch(
    reading: Reading, expects_index: bool, expects_instruction: bool
) -> str | None:
    """Name the way the session differed from the arm that was requested.

    The arm is the independent variable, and these hooks degrade silently by
    contract, so an arm that never took effect is indistinguishable from one
    that took effect and changed nothing. Every disagreement demotes the run to
    incomplete rather than being recorded as a result, including the duplicate
    injection that arises when both hook manifests load at once -- an arm
    delivered twice is not the arm.
    """
    injections = reading.injections
    if expects_index and injections.index == 0:
        return "arm expected the workflow index and none was injected"
    if not expects_index and injections.index:
        return "arm suppressed the workflow index and it was injected anyway"
    if expects_instruction and injections.instruction == 0:
        return "arm expected the standing instruction and none was injected"
    if not expects_instruction and injections.instruction:
        return "arm suppressed the standing instruction and it was injected anyway"
    if injections.index > 1:
        return f"workflow index was injected {injections.index} times"
    if injections.session_instruction > 1:
        return (
            "session standing instruction was injected "
            f"{injections.session_instruction} times"
        )
    return None
