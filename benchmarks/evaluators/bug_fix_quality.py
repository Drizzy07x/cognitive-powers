#!/usr/bin/env python3
"""Score bug-fix delivery quality from arm-neutral executable evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


def _events(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def evaluate(fixture: Path, events: Path, message: Path) -> dict[str, Any]:
    evidence: list[str] = []
    critical: list[str] = []
    score = 0
    behavior = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from src.payments.retry_policy import RetryPolicy; "
                "p=RetryPolicy(maximum_attempts=3); "
                "assert p.should_retry(425,1); "
                "assert not p.should_retry(425,3)"
            ),
        ],
        cwd=fixture,
        check=False,
        capture_output=True,
        text=True,
    )
    if behavior.returncode == 0:
        score += 50
        evidence.append("HTTP 425 retries before but not at maximum_attempts")
    else:
        critical.append("required HTTP 425 behavior is not satisfied")

    test_text = (fixture / "tests" / "test_retry_policy.py").read_text(encoding="utf-8")
    has_regression = "425" in test_text and "should_retry" in test_text
    if has_regression:
        score += 15
        evidence.append("focused regression test covers HTTP 425")
    else:
        evidence.append("focused regression test for HTTP 425 is missing")

    commands = [
        row["item"]
        for row in _events(events)
        if row.get("type") == "item.completed"
        and isinstance(row.get("item"), dict)
        and row["item"].get("type") == "command_execution"
    ]
    unexpected_failures = [
        item
        for item in commands
        if item.get("exit_code") not in {0, None}
        and not any(
            marker in str(item.get("command", "")).casefold()
            for marker in ("425", "too_early")
        )
    ]
    if not unexpected_failures:
        score += 10
        evidence.append("no unexpected failed verification or discovery commands")
    else:
        evidence.append(f"{len(unexpected_failures)} unexpected command failures")

    unrelated_markers = (
        "invoke-pester",
        "copy_cursor_pack",
        "src\\config",
        "src/config",
        "src\\catalog",
        "src/catalog",
    )
    unrelated = [
        item
        for item in commands
        if any(
            marker in str(item.get("command", "")).casefold()
            for marker in unrelated_markers
        )
    ]
    if not unrelated:
        score += 10
        evidence.append("no unrelated module or test-framework validation")
    else:
        evidence.append(f"{len(unrelated)} unrelated command executions")

    final_text = message.read_text(encoding="utf-8").casefold()
    if (
        "425" in final_text
        and "test" in final_text
        and any(marker in final_text for marker in ("pass", "ok", "correct"))
    ):
        score += 15
        evidence.append("final report identifies HTTP 425 and executable test outcome")
    else:
        evidence.append("final report lacks precise behavior and test outcome")

    return {"score": score, "evidence": evidence, "critical_errors": critical}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--message", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(evaluate(args.fixture, args.events, args.message)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
