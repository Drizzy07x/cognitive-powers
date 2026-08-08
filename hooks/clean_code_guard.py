#!/usr/bin/env python3
"""Report measurable readability violations on files the agent writes.

Two modes:

    PostToolUse hook   echo '<event json>' | python clean_code_guard.py
    Manual scan        python clean_code_guard.py --scan src/ app/main.py

Hook mode reads the tool event on stdin, analyses the written file and returns
the findings as extra context for the agent. It is advisory by default; set
CLEAN_CODE_GUARD_STRICT=1 to exit 2 instead, which forces the agent to react.

Limits and ignore patterns are configured through CLEAN_CODE_GUARD_* variables
(see clean_code_rules.DEFAULT_LIMITS). Standard library only.

A scan also reads `cleancode-accepted.txt` from the working directory, one
`path:line:rule` entry per line. Ignoring a whole file would hide the next
defect written into it, so acceptance is per finding and states which rule was
argued away. Hook mode does not consult the list: a file being edited right now
is the one case where a stale acceptance would be worth re-reading.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from pathlib import Path

# Hooks are executed as scripts from the user's working directory, so the
# sibling module is not importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import clean_code_rules as rules  # noqa: E402

WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"})
# apply_patch names its targets inside the patch body, not in tool_input, so a
# hook reading only file_path exited clean for every event on the host where
# apply_patch is the primary edit tool. Same format selective_hooks parses.
PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE)
REPORT_FOOTER = (
    "Fix the ones that change comprehension cost. Say which you skipped and why."
)
ACCEPTED_FILENAME = "cleancode-accepted.txt"
HOOK_EVENT = "post-tool-use"


def is_ignored(path: Path, patterns: tuple[str, ...]) -> bool:
    target = path.as_posix()
    return any(fnmatch.fnmatch(target, pattern) for pattern in patterns)


def read_source(path: Path) -> str | None:
    # ValueError as well as OSError: a path carrying an embedded NUL raises
    # ValueError from open() before any syscall, and that traceback reached the
    # user as a failed PostToolUse rather than as the silence an advisory hook
    # owes an input it cannot read.
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def collect_files(roots: list[str]) -> list[Path]:
    patterns = rules.ignore_patterns()
    files: list[Path] = []
    for root in roots:
        base = Path(root)
        candidates = base.rglob("*") if base.is_dir() else [base]
        files += [path for path in candidates if is_scannable(path, patterns)]
    return sorted(files)


def is_scannable(path: Path, patterns: tuple[str, ...]) -> bool:
    return (
        path.is_file() and rules.is_supported(path) and not is_ignored(path, patterns)
    )


def file_findings(path: Path, limits: dict[str, int]) -> list[rules.Finding]:
    source = read_source(path)
    if source is None:
        return []
    return rules.analyse(path, source, limits)


def render_findings(findings: list[rules.Finding]) -> str:
    return "\n".join(finding.render() for finding in findings)


def render_report(path: Path, findings: list[rules.Finding]) -> str:
    header = f"Clean code guard - {path.name}: {len(findings)} finding(s)"
    return f"{header}\n{render_findings(findings)}\n{REPORT_FOOTER}"


def emit_hook_result(report: str) -> int:
    if os.environ.get("CLEAN_CODE_GUARD_STRICT") == "1":
        print(report, file=sys.stderr)
        return 2
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": report,
        }
    }
    print(json.dumps(payload))
    return 0


def read_event() -> dict:
    # ``null``, ``[]`` and ``42`` are valid JSON that json.load returns without
    # raising, so decoding alone did not make the result a mapping and every
    # caller's .get() raised AttributeError. The other two stdin-reading hooks
    # already narrow to dict here; this one did not.
    try:
        payload = json.load(sys.stdin)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def candidate_paths(event: dict) -> list[str]:
    tool_input = event.get("tool_input") or {}
    raw_path = tool_input.get("file_path")
    candidates = [raw_path] if isinstance(raw_path, str) and raw_path else []
    if event.get("tool_name") == "apply_patch":
        for key in ("patch", "content", "input"):
            value = tool_input.get(key)
            if isinstance(value, str):
                candidates.extend(PATCH_PATH.findall(value))
    return list(dict.fromkeys(candidates))


def target_paths(event: dict) -> list[Path]:
    if event.get("tool_name") not in WRITE_TOOLS:
        return []
    patterns = rules.ignore_patterns()
    return [
        path
        for path in map(Path, candidate_paths(event))
        if rules.is_supported(path) and not is_ignored(path, patterns)
    ]


def run_hook(event: dict) -> int:
    # The event is passed in rather than read here: selective_hooks.py owns the
    # single PostToolUse registration and has already consumed stdin by the
    # time it calls this. read_event() stays for the standalone invocation.
    # One patch can name several files; a deleted target has no source to read
    # and file_findings returns nothing for it, which is the right silence.
    limits = rules.load_limits()
    reports = [
        render_report(path, findings)
        for path in target_paths(event)
        if (findings := file_findings(path, limits))
    ]
    if not reports:
        return 0
    return emit_hook_result("\n".join(reports))


def accepted_entry(line: str) -> str:
    return line.split("#", 1)[0].strip()


def accepted_keys() -> frozenset[str]:
    """Read the argued-away findings as `path:line:rule` keys."""
    listed = Path(os.environ.get("CLEAN_CODE_GUARD_ACCEPTED") or ACCEPTED_FILENAME)
    text = read_source(listed)
    if text is None:
        return frozenset()
    return frozenset(entry for entry in map(accepted_entry, text.splitlines()) if entry)


def finding_key(path: Path, finding: rules.Finding) -> str:
    return f"{path.as_posix()}:{finding.line}:{finding.rule}"


def unaccepted(
    path: Path, findings: list[rules.Finding], accepted: frozenset[str]
) -> list[rules.Finding]:
    return [f for f in findings if finding_key(path, f) not in accepted]


def run_scan(roots: list[str]) -> int:
    limits = rules.load_limits()
    accepted = accepted_keys()
    total = 0
    for path in collect_files(roots):
        findings = unaccepted(path, file_findings(path, limits), accepted)
        if findings:
            total += len(findings)
            print(f"\n{path}\n{render_findings(findings)}")
    print(f"\n{total} finding(s).")
    return 1 if total else 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--scan":
        return run_scan(argv[1:] or ["."])
    if argv and argv[0] != HOOK_EVENT:
        # Every hook in this plugin takes its event as a positional argument,
        # and both manifests declare one here. An unknown argument is a
        # manifest error, but this hook is advisory: refusing loudly would turn
        # a typo in packaging into a failed tool call for the user.
        return 0
    return run_hook(read_event())


if __name__ == "__main__":
    sys.exit(main())
