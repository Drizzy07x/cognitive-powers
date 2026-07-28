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
the channel, so the thresholds below buy precision with recall.
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
    from skill_routing import load_skill_descriptions, rank_skills

    ROUTING_AVAILABLE = True
except ImportError:  # A broken install must not fail the turn.
    ROUTING_AVAILABLE = False

MAX_STDIN_BYTES = 2 * 1024 * 1024

# Calibrated against benchmarks/skill_routing_cases.json plus off-domain
# prompts (renames, commits, typo fixes, small talk). At this pair no
# off-domain prompt fires and every firing positive names its declared owner,
# for roughly three fifths of the positives. Suggesting a workflow for "fix the
# typo in the README" costs more than staying quiet, because the agent stops
# reading a channel that is usually wrong.
MIN_SCORE = 0.27
# A near-tie means the wording matched a family of skills rather than one of
# them, and naming either is misleading.
MIN_MARGIN = 0.02
# rank_skills adds this when the prompt names a skill outright; such a request
# is explicit rather than inferred and always clears the bar.
EXPLICIT_REQUEST_SCORE = 2.0


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


def _plugin_root() -> Path:
    value = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if value:
        try:
            root = Path(value).expanduser().resolve()
        except OSError:
            return PLUGIN_ROOT
        if (root / "skills").is_dir():
            return root
    return PLUGIN_ROOT


def _message(name: str) -> str:
    return (
        f"Cognitive Powers: this request matches the {name!r} skill. Invoke it "
        f"with the Skill tool as cognitive-powers:{name} before starting if it "
        "fits the actual task. This is a description-similarity match computed "
        "from the prompt alone, not a judgment about the work, so proceed "
        "without it when it does not apply."
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
        descriptions = load_skill_descriptions(_plugin_root())
    except (OSError, ValueError):
        return {"status": "skipped", "reason": "skill descriptions are unreadable"}
    if len(descriptions) < 2:
        # Margin is meaningless without a runner-up to compare against.
        return {"status": "skipped", "reason": "not enough skills to rank"}

    ranking = rank_skills(prompt, descriptions)
    name, score = ranking[0]
    margin = score - ranking[1][1]
    if score < EXPLICIT_REQUEST_SCORE and (score < MIN_SCORE or margin < MIN_MARGIN):
        return {
            "status": "below-threshold",
            "skill": name,
            "score": score,
            "margin": round(margin, 8),
        }
    return {
        "status": "suggested",
        "skill": name,
        "score": score,
        "margin": round(margin, 8),
        "message": _message(name),
    }


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
