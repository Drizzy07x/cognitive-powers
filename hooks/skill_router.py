#!/usr/bin/env python3
"""Name the skill that matches a prompt before the agent starts working.

Skills are selected from their descriptions, so a workflow only runs when the
agent happens to recognise the match while reading the request. This hook makes
that check deterministic: it scores the submitted prompt against the installed
skill descriptions and, when one skill clearly wins, says so before the turn
begins.

Advisory in full, like ``semantic_index.py`` and unlike the Stop gate in
``selective_hooks.py``: it never blocks a prompt and stays silent on every
error. A suggestion that fires on ordinary work would train the agent to ignore
the channel, so the named suggestion still errs toward silence -- but silence
is a failure too, and this file used to reach it for a third of the prompts the
plugin is for.

That is why the hook now carries two payloads rather than one. The named
suggestion is a claim about this prompt and stays rare. The standing
instruction is not a claim about anything: it fires whenever the catalogue was
readable, because the measured defect is a check that never happens. Against
prompts written independently of the skill descriptions, the ranking named the
right workflow three times in ten and said nothing five times, and a ranking
that abstains cannot be corrected by the agent that never knew it ran.

Reading the payload, rendering the message, and honouring the disable switch
are all this hook does. Which skill to name, and whether to name one at all,
belong to ``skill_routing.decide`` so the benchmark measures the same decision
the host gets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

try:
    from plugin_host import resolve_host
    from skill_routing import decide, load_parsable_skill_descriptions

    ROUTING_AVAILABLE = True
except ImportError:  # A broken install must not fail the turn.
    ROUTING_AVAILABLE = False

MAX_STDIN_BYTES = 2 * 1024 * 1024

# Named suggestion and standing instruction are two different jobs on one
# event. The suggestion fires only for a clear winner, which is right for a
# claim about this prompt; the instruction fires whenever the catalogue is
# readable, because the failure this hook was losing to is not a wrong
# suggestion but no check at all -- three right workflows in ten natural
# requests, five of them silent. Kept to roughly a hundred tokens: it is paid
# on every prompt, and an instruction that costs more than the check it asks
# for would be the wrong trade on the short requests that dominate a session.
FORCED_EVAL = (
    "Cognitive Powers: before acting, check this session's workflow index for "
    "a trigger condition matching this request. Name the workflows that apply "
    "and follow them first. If none apply, say so in one clause and continue."
)
# The two statuses that mean the descriptions were read. Everything else --
# a disabled router, an unusable prompt, an unreadable catalogue -- has nothing
# to point the agent at.
CATALOGUE_READ = frozenset({"suggested", "below-threshold"})


def _read_payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    except OSError:
        return {}
    if not raw or len(raw) > MAX_STDIN_BYTES:
        return {}
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _first(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = payload.get(name)
        if value is not None:
            return value
    return None


def _prompt(payload: dict[str, Any]) -> str | None:
    # Claude Code sends the text as user_input; prompt is accepted so a host
    # that renames the field degrades to silence rather than to a wrong match.
    value = _first(payload, "user_input", "prompt")
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _message(name: str, claude_code: bool) -> str:
    """Name the workflow the way the running host can actually reach it.

    Both hosts run this hook, and they reach a workflow differently. Claude
    Code installs all of skills/ and invokes one through the Skill tool. Codex
    installs the three routers in skills-core/ and reaches the rest by reading
    skills/<name>/SKILL.md, so naming a Skill-tool id there instructed the
    agent to call something that does not exist on that host -- for thirteen of
    the sixteen workflows, on a channel whose whole value is that it is not
    usually wrong.
    """
    caveat = (
        " This is a description-similarity match computed from the prompt "
        "alone, not a judgment about the work, so proceed without it when it "
        "does not apply."
    )
    if claude_code:
        return (
            f"Cognitive Powers: this request matches the {name!r} skill. Invoke "
            f"it with the Skill tool as cognitive-powers:{name} before starting "
            f"if it fits the actual task.{caveat}"
        )
    return (
        f"Cognitive Powers: this request matches the {name!r} workflow. Read "
        f"skills/{name}/SKILL.md under the plugin root and follow it before "
        f"starting if it fits the actual task.{caveat}"
    )


def _unavailable() -> dict[str, Any] | None:
    """Report the two conditions under which routing does not run at all."""
    if os.environ.get("COGNITIVE_POWERS_DISABLE_ROUTER"):
        return {"status": "disabled", "reason": "disabled by environment"}
    if not ROUTING_AVAILABLE:
        return {"status": "skipped", "reason": "skill_routing is unavailable"}
    return None


def _unparsable_warning(unparsable: set[str]) -> str | None:
    """Name the skills that did not parse, or return None when every one did.

    Silence is this hook's ordinary output, so a catalogue that failed to load
    looks exactly like a prompt that matched nothing. Naming the skills that
    did not parse is the only way a broken install stops being invisible.
    """
    if not unparsable:
        return None
    several = len(unparsable) > 1
    return (
        "Cognitive Powers: "
        + ", ".join(sorted(unparsable))
        + " could not be read and "
        + ("were" if several else "was")
        + " left out of skill routing. Check the frontmatter of "
        + ("those skills" if several else "that skill")
        + "; a single-line description is required."
    )


def _decided(
    outcome: dict[str, Any], warning: str | None, claude_code: bool
) -> dict[str, Any]:
    """Attach the host-shaped message and any warning to a routing outcome."""
    if outcome["status"] != "suggested":
        return {**outcome, "warning": warning} if warning else outcome
    message = _message(str(outcome["skill"]), claude_code)
    return {**outcome, "message": message, "warning": warning}


def _resolved_catalogue() -> tuple[dict[str, str], set[str], bool] | None:
    """Resolve the host and read its catalogue, or None when nothing parses.

    The host lookup stays inside the same try as the load: a failure to resolve
    the root and a failure to read what is under it are the same silence to
    this hook, and separating them would let one of the two escape.
    """
    try:
        root, claude_code = resolve_host(PLUGIN_ROOT)
        descriptions, unparsable = load_parsable_skill_descriptions(root)
    except (OSError, ValueError):
        return None
    if not descriptions:
        return None
    return descriptions, unparsable, claude_code


def suggest(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a structured outcome; never raise."""
    unavailable = _unavailable()
    if unavailable is not None:
        return unavailable

    prompt = _prompt(payload)
    if prompt is None:
        return {"status": "skipped", "reason": "no usable prompt"}

    catalogue = _resolved_catalogue()
    if catalogue is None:
        return {"status": "skipped", "reason": "skill descriptions are unreadable"}

    descriptions, unparsable, claude_code = catalogue
    warning = _unparsable_warning(unparsable)
    outcome = decide(prompt, descriptions)
    return _decided(outcome, warning, claude_code)


def _injected(outcome: dict[str, Any]) -> str | None:
    """Return the context to inject, or None when the catalogue never loaded.

    A readable catalogue is the precondition for both parts. Telling the agent
    to consult an index that failed to load would spend a turn's attention on
    a defect it cannot act on, and it would do so on every prompt.
    """
    if outcome["status"] not in CATALOGUE_READ:
        return None
    if outcome["status"] == "suggested":
        return f"{FORCED_EVAL} {outcome['message']}"
    return FORCED_EVAL


def _router_output(outcome: dict[str, Any], warning: str | None) -> dict[str, Any]:
    """Shape the host payload: injected context, a warning, or both."""
    output: dict[str, Any] = {"suppressOutput": True}
    context = _injected(outcome)
    if context is not None:
        output["hookSpecificOutput"] = {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    if warning:
        output["systemMessage"] = warning
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["user-prompt-submit"])
    parser.parse_args(argv)

    try:
        outcome = suggest(_read_payload())
    except Exception:  # noqa: BLE001 - advisory hook must not fail closed
        return 0

    # Silence is no longer the ordinary path, and that is the change: a
    # readable catalogue always earns the standing instruction, because the
    # measured failure was the check never happening rather than happening
    # wrongly. Silence is now reserved for the states with nothing to say --
    # disabled, no usable prompt, or a catalogue that did not load, the last of
    # which speaks through the warning instead.
    warning = outcome.get("warning")
    if outcome["status"] not in CATALOGUE_READ and not warning:
        return 0

    print(json.dumps(_router_output(outcome, warning), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
