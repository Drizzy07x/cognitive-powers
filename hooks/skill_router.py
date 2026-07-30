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
the channel, so the decision errs toward silence -- but silence is a failure
too, and this file used to reach it for a third of the prompts the plugin is
for.

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
    from skill_routing import decide, load_skill_descriptions

    ROUTING_AVAILABLE = True
except ImportError:  # A broken install must not fail the turn.
    ROUTING_AVAILABLE = False

MAX_STDIN_BYTES = 2 * 1024 * 1024


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


def _resolve_host() -> tuple[Path, bool]:
    """Return the plugin root and whether the host is Claude Code.

    One question, answered once. These used to be two independent lookups --
    the root by validated precedence here, the host by a bare
    ``CLAUDE_PLUGIN_ROOT`` test inside ``_message`` -- so a stale or partial
    ``CLAUDE_PLUGIN_ROOT`` made the root resolve from ``PLUGIN_ROOT`` while
    the message still named a Skill-tool id, reinstating on Codex the exact
    defect the per-host wording exists to remove.

    ``PLUGIN_ROOT`` is tried first because ``selective_hooks._roots`` does,
    and two hooks of one plugin resolving different installs in one session is
    the condition its own docstring calls out as fatal to the Stop gate.
    """
    for variable in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        value = os.environ.get(variable)
        if not value:
            continue
        try:
            root = Path(value).expanduser().resolve()
        except OSError:
            continue
        if (root / "skills").is_dir():
            return root, variable == "CLAUDE_PLUGIN_ROOT"
    return PLUGIN_ROOT, False


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


def suggest(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a structured outcome; never raise."""
    if os.environ.get("COGNITIVE_POWERS_DISABLE_ROUTER"):
        return {"status": "disabled", "reason": "disabled by environment"}
    if not ROUTING_AVAILABLE:
        return {"status": "skipped", "reason": "skill_routing is unavailable"}

    prompt = _prompt(payload)
    if prompt is None:
        return {"status": "skipped", "reason": "no usable prompt"}

    try:
        root, claude_code = _resolve_host()
        descriptions = load_skill_descriptions(root)
    except (OSError, ValueError):
        return {"status": "skipped", "reason": "skill descriptions are unreadable"}

    outcome = decide(prompt, descriptions)
    if outcome["status"] != "suggested":
        return outcome
    return {**outcome, "message": _message(str(outcome["skill"]), claude_code)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["user-prompt-submit"])
    parser.parse_args(argv)

    try:
        outcome = suggest(_read_payload())
    except Exception:  # noqa: BLE001 - advisory hook must not fail closed
        return 0

    # Silence is the ordinary path. Only a clear winner is worth spending the
    # agent's attention on.
    if outcome["status"] != "suggested":
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": outcome["message"],
                },
                "suppressOutput": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
