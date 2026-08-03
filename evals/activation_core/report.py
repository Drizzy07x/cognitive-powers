#!/usr/bin/env python3
"""Render a scored run as JSON and as Markdown.

The JSON is the record; the Markdown is for a person deciding something. Both
print the incomplete counts beside every rate, because a rate over three
complete runs and a rate over thirty look identical once the denominator is
dropped, and only one of them is worth acting on.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

DASH = "--"


def _pct(value: Any) -> str:
    if value is None:
        return DASH
    return f"{float(value) * 100:.0f}%"


def _signed(value: Any) -> str:
    if value is None:
        return DASH
    points = float(value) * 100
    return f"{points:+.0f} pts"


def as_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)


def _arm_table(arms: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "| Arm | Activation | vs none | False positives | Complete | Incomplete |",
        "|---|---|---|---|---|---|",
    ]
    for scored in arms:
        should_fire = scored["shouldFire"]
        lines.append(
            f"| `{scored['arm']}` | {_pct(should_fire.get('passRate'))} "
            f"| {_signed(scored.get('activationDelta'))} "
            f"| {_pct(scored.get('falsePositiveRate'))} "
            f"| {should_fire.get('complete', 0)} "
            f"| {should_fire.get('incomplete', 0)} |"
        )
    return lines


def _skill_table(scored: Mapping[str, Any]) -> list[str]:
    lines = [
        "| Workflow | Activation | Passed / complete | Misrouted | Incomplete |",
        "|---|---|---|---|---|",
    ]
    for name, entry in sorted(
        scored.get("perSkill", {}).items(),
        key=lambda item: (
            item[1].get("passRate") if item[1].get("passRate") is not None else 2,
            item[0],
        ),
    ):
        lines.append(
            f"| `{name}` | {_pct(entry.get('passRate'))} "
            f"| {entry.get('passed', 0)} / {entry.get('complete', 0)} "
            f"| {entry.get('misroutedRuns', 0)} "
            f"| {entry.get('incomplete', 0)} |"
        )
    return lines


def as_markdown(payload: Mapping[str, Any]) -> str:
    meta = payload.get("run", {})
    arms = payload.get("arms", [])
    lines: list[str] = [
        "# Skill activation report",
        "",
        f"- Model: `{meta.get('model')}`",
        f"- Repetitions per case: {meta.get('repetitions')}",
        f"- Cases: {meta.get('cases')} "
        f"({meta.get('shouldFireCases')} should-fire, "
        f"{meta.get('shouldNotFireCases')} should-not-fire)",
        f"- Host invocations: {meta.get('invocations')}",
        f"- Suite: {meta.get('suite')}",
        "",
        "## Arms",
        "",
    ]
    lines.extend(_arm_table(arms))
    lines.append("")

    for scored in arms:
        lines.extend(
            [
                f"## Arm `{scored['arm']}`",
                "",
                f"- Should-fire: {_pct(scored['shouldFire'].get('passRate'))} "
                f"over {scored['shouldFire'].get('complete', 0)} complete runs",
                f"- Should-not-fire false positives: "
                f"{_pct(scored.get('falsePositiveRate'))}",
                f"- Multi-workflow cases: {_pct(scored['multiSkill'].get('passRate'))}",
                f"- Spanish cases: {_pct(scored['spanish'].get('passRate'))}",
                f"- Cases that flipped across repetitions: "
                f"{len(scored.get('flippedCases', []))}",
                "",
            ]
        )
        lines.extend(_skill_table(scored))
        lines.append("")
        flipped = scored.get("flippedCases", [])
        if flipped:
            lines.extend(
                [
                    "Unstable cases (passed some repetitions and not others): "
                    + ", ".join(f"`{name}`" for name in flipped),
                    "",
                ]
            )
        reasons = _reasons(scored)
        if reasons:
            lines.append("Runs excluded as incomplete:")
            lines.append("")
            for reason, count in reasons:
                lines.append(f"- {count} x {reason}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _reasons(scored: Mapping[str, Any]) -> list[tuple[str, int]]:
    """Collect every stated incompleteness reason with a count.

    Reported per arm and never summed away: an arm that lost a third of its
    runs has not measured what the arm beside it measured, and that fact
    belongs next to its rate rather than in a footnote.
    """
    counts = dict(scored.get("overall", {}).get("reasons", {}) or {})
    incomplete = scored.get("overall", {}).get("incomplete", 0)
    if incomplete and not counts:
        counts["reason not recorded"] = incomplete
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))
