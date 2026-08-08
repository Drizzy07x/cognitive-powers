#!/usr/bin/env python3
"""Put the standing instruction in front of the agent at session start.

Selection happens from descriptions the host lists, and nothing obliges the
agent to consult them. Measured against prompts written independently of those
descriptions, the deterministic router named the right workflow for three
requests in ten and stayed silent for five, so a workflow that fits can lose
simply because nothing prompted the check. This hook removes the silence at the
one moment every session passes through.

What it injects is the instruction alone. Through 1.9.0 it also rendered the
whole catalogue underneath -- nineteen entries, about 743 tokens -- on the
reasoning that nothing had shown the catalogue should not ship, which is not
evidence that it should. ``docs/analysis/activation-baseline-v1.md`` is the
measurement that closes that: over 61 paired trials the two renderings are
**equivalent** within the declared 0.10 margin, and identical on the 59
single-workflow cases neither run truncated. So the catalogue stopped shipping
and the instruction, which the host's own listing already backs, is what a
session carries.

``index_message`` stays, off by default and reachable through
``COGNITIVE_POWERS_ENABLE_ACTIVATION_INDEX``. Deleting it would delete the only
thing that can falsify the paragraph above: the eval harness's ``full`` arm is
this function, and a decision whose disconfirming arm no longer exists cannot
be re-run against the next model.

Advisory in full, like ``semantic_index.py`` and ``skill_router.py``, and
unlike the Stop gate in ``selective_hooks.py``: it never blocks a session and
stays silent on every error. An index that could not be built is worth no
message at all, because a session start that reports its own plumbing spends
the attention this hook exists to direct somewhere else.

Rendering is separated from I/O so the payload budget is measurable without a
filesystem or a host: ``index_message`` is a pure function of the catalogue.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

try:
    from plugin_host import resolve_host
    from skill_routing import load_skill_triggers

    CATALOGUE_AVAILABLE = True
except ImportError:  # A broken install must not fail the session.
    CATALOGUE_AVAILABLE = False

# A resumed session still holds this text in its history; re-injecting it there
# buys nothing and costs the budget twice. The other three sources each start
# from a context that no longer contains it.
INJECT_SOURCES = {"startup", "clear", "compact"}
MAX_STDIN_BYTES = 2 * 1024 * 1024

# Nineteen workflows at roughly 135 characters each, plus the standing
# instruction. The per-entry clip is what keeps one verbose skill from
# crowding out the other eighteen, and the total is the ceiling a test holds
# this hook to, because an index that grows without one stops being an index.
MAX_TRIGGER_CHARS = 110
MAX_PAYLOAD_CHARS = 3200

HEADER = (
    "Cognitive Powers workflows are installed here. Before starting any "
    "non-trivial request, check whether one of these trigger conditions "
    "matches what was actually asked:"
)
# The same standing order with no catalogue under it. The host already lists
# every workflow's name and trigger conditions, so this wording claims only the
# part the index does not duplicate, and it has to read correctly on its own,
# because the harness found the index earned nothing and this is what ships.
INSTRUCTION_ONLY_HEADER = (
    "Cognitive Powers workflows are installed here, and this session already "
    "lists each one's name and trigger conditions. Before starting any "
    "non-trivial request, check that list against what was actually asked."
)
CLAUDE_FOOTER = (
    "Invoke a match with the Skill tool as cognitive-powers:<name> before "
    "acting on the request."
)
CODEX_FOOTER = (
    "Follow a match by reading skills/<name>/SKILL.md under the plugin root "
    "before acting on the request."
)
STANDING_RULE = (
    "Act on a plausible match, not only a certain one: these workflows carry "
    "evidence and verification steps that are easy to skip by accident, and "
    "skipping them is not visible in the answer. When nothing here fits, say "
    "so in one clause and proceed without one."
)


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


def _clip(text: str, limit: int) -> str:
    """Collapse whitespace and cut on a word boundary.

    A mid-word cut reads as corruption rather than as brevity, and the entry
    still has to carry the vocabulary the agent matches against.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    head = collapsed[: max(limit - 3, 1)]
    if " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(" ,;:.") + "..."


def _used(lines: list[str]) -> int:
    """Characters the assembled entries occupy, newline separators included."""
    return sum(len(line) + 1 for line in lines)


def index_message(triggers: Mapping[str, str], claude_code: bool) -> str | None:
    """Render the standing instruction and index, or None when there is none.

    Returns None for an empty catalogue rather than a header with nothing
    under it: an instruction to consult a list that is not there would spend
    the session's attention on a defect it cannot act on.
    """
    if not triggers:
        return None
    footer = CLAUDE_FOOTER if claude_code else CODEX_FOOTER
    fixed = len(HEADER) + len(footer) + len(STANDING_RULE) + 6
    lines: list[str] = []
    omitted = 0
    for name in sorted(triggers):
        entry = f"- {name}: {_clip(triggers[name], MAX_TRIGGER_CHARS)}"
        # Budget checked before appending, and the overflow is counted rather
        # than dropped in silence: an index that quietly lists some of the
        # catalogue reads exactly like one that lists all of it.
        if fixed + _used(lines) + len(entry) > MAX_PAYLOAD_CHARS:
            omitted += 1
            continue
        lines.append(entry)
    if not lines:
        return None
    if omitted:
        # The note is payload too. Appending it after the budget was already
        # spent pushed the message past the ceiling this function exists to
        # hold, so entries give way to it rather than the other way round.
        note = f"- ({omitted} further workflows omitted for length)"
        while lines and fixed + _used(lines) + len(note) > MAX_PAYLOAD_CHARS:
            lines.pop()
            omitted += 1
            note = f"- ({omitted} further workflows omitted for length)"
        lines.append(note)
    return "\n".join([HEADER, *lines, footer, STANDING_RULE])


def standing_message(triggers: Mapping[str, str], claude_code: bool) -> str | None:
    """Render the standing instruction alone, or None for an empty catalogue.

    Same precondition as ``index_message``: an instruction to consult a
    catalogue is worth nothing when there is no catalogue to consult, and the
    emptiness is the only thing the two renderings must agree about.
    """
    if not triggers:
        return None
    footer = CLAUDE_FOOTER if claude_code else CODEX_FOOTER
    return "\n".join([INSTRUCTION_ONLY_HEADER, footer, STANDING_RULE])


def _source(payload: Mapping[str, Any]) -> Any:
    for name in ("source", "trigger"):
        value = payload.get(name)
        if value is not None:
            return value
    return None


def build(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a structured outcome; never raise."""
    if os.environ.get("COGNITIVE_POWERS_DISABLE_ACTIVATION"):
        return {"status": "disabled", "reason": "disabled by environment"}
    if not CATALOGUE_AVAILABLE:
        return {"status": "skipped", "reason": "skill_routing is unavailable"}

    source = _source(payload)
    if isinstance(source, str) and source and source not in INJECT_SOURCES:
        return {"status": "skipped", "reason": f"session source is {source}"}

    try:
        root, claude_code = resolve_host(PLUGIN_ROOT)
        triggers = load_skill_triggers(root)
    except (OSError, ValueError):
        return {"status": "skipped", "reason": "skill catalogue is unreadable"}

    # Two renderings, one event, and the default is the cheaper one because the
    # measurement said the two are equivalent. Selecting between them here
    # rather than in the harness is what makes the arm under measurement the
    # shipped code path -- which is also why the losing rendering stays: an arm
    # that no longer exists cannot re-decide this against the next model.
    index = bool(os.environ.get("COGNITIVE_POWERS_ENABLE_ACTIVATION_INDEX"))
    message = (
        index_message(triggers, claude_code)
        if index
        else standing_message(triggers, claude_code)
    )
    if message is None:
        return {"status": "skipped", "reason": "no readable skill catalogue"}
    return {
        "status": "injected",
        "message": message,
        "skills": len(triggers),
        "index": index,
    }


def main(argv: list[str] | None = None) -> int:
    # Hook output is UTF-8 JSON for the host, not console text: on a legacy
    # Windows codepage an ensure_ascii=False payload crashed the print itself.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["session-start"])
    parser.parse_args(argv)

    try:
        outcome = build(_read_payload())
    except Exception:  # noqa: BLE001 - advisory hook must not fail closed
        return 0

    if outcome["status"] != "injected":
        return 0

    print(
        json.dumps(
            {
                "suppressOutput": True,
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": outcome["message"],
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
