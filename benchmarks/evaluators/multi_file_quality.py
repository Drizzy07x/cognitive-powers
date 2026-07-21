#!/usr/bin/env python3
"""Score multi-file feature delivery from arm-neutral executable evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


def _events(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _run(fixture: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "src.tasker.cli", *args],
        cwd=fixture,
        check=False,
        capture_output=True,
        text=True,
    )


def evaluate(fixture: Path, events: Path, message: Path) -> dict[str, Any]:
    evidence: list[str] = []
    critical: list[str] = []
    score = 0

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = root / "tasks.json"
        added = _run(
            fixture,
            "--store",
            str(store),
            "add",
            "Release",
            "--priority",
            "high",
        )
        listed = _run(fixture, "--store", str(store), "list")
        try:
            added_row = json.loads(added.stdout)
            listed_rows = json.loads(listed.stdout)
            public_ok = (
                added.returncode == 0
                and listed.returncode == 0
                and added_row["priority"] == "high"
                and listed_rows == [added_row]
                and json.loads(store.read_text(encoding="utf-8"))[0]["priority"] == "high"
            )
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, OSError):
            public_ok = False

        legacy = root / "legacy.json"
        legacy.write_text('[{"title":"Legacy","completed":false}]', encoding="utf-8")
        legacy_result = _run(fixture, "--store", str(legacy), "list")
        try:
            legacy_ok = (
                legacy_result.returncode == 0
                and json.loads(legacy_result.stdout)[0]["priority"] == "normal"
            )
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            legacy_ok = False

        before_invalid = store.read_bytes() if store.exists() else b""
        invalid = _run(
            fixture,
            "--store",
            str(store),
            "add",
            "Broken",
            "--priority",
            "urgent",
        )
        after_invalid = store.read_bytes() if store.exists() else b""
        invalid_ok = invalid.returncode != 0 and after_invalid == before_invalid

    if public_ok:
        score += 25
        evidence.append("public CLI persists and lists a high-priority task")
    else:
        critical.append("priority does not work end to end through the public CLI")
    if legacy_ok:
        score += 15
        evidence.append("legacy JSON defaults missing priority to normal")
    else:
        critical.append("legacy serialized records are not backward compatible")
    if invalid_ok:
        score += 10
        evidence.append("invalid priority fails without mutating storage")
    else:
        critical.append("invalid priority is accepted or mutates storage")

    test_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted((fixture / "tests").rglob("test*.py"))
    ).casefold()
    focused_tests = (
        "priority" in test_text
        and "src.tasker.cli" in test_text
        and any(marker in test_text for marker in ("legacy", "backward", "compat"))
    )
    if focused_tests:
        score += 15
        evidence.append("focused tests cover the public CLI and compatibility boundary")
    else:
        evidence.append("focused public CLI and compatibility tests are incomplete")

    commands = [
        row["item"]
        for row in _events(events)
        if row.get("type") == "item.completed"
        and isinstance(row.get("item"), dict)
        and row["item"].get("type") == "command_execution"
    ]
    expected_failure_markers = ("urgent", "invalid", "priority")
    unexpected_failures = [
        item
        for item in commands
        if item.get("exit_code") not in {0, None}
        and not any(
            marker in str(item.get("command", "")).casefold()
            for marker in expected_failure_markers
        )
    ]
    if not unexpected_failures:
        score += 10
        evidence.append("no unexpected failed discovery or verification commands")
    else:
        evidence.append(f"{len(unexpected_failures)} unexpected command failures")

    unrelated_markers = (
        "invoke-pester",
        "npm test",
        "cargo test",
        "copy_cursor_pack",
        "src\\payments",
        "src/payments",
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
        "priority" in final_text
        and any(marker in final_text for marker in ("cli", "command"))
        and any(marker in final_text for marker in ("compat", "legacy", "backward"))
        and any(marker in final_text for marker in ("pass", "ok", "correct"))
    ):
        score += 15
        evidence.append("final report identifies public behavior, compatibility, and test outcome")
    else:
        evidence.append("final report lacks precise public, compatibility, and test evidence")

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
