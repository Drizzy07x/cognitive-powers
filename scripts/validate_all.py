#!/usr/bin/env python3
"""Run the declared Cognitive Powers validation surface and write a receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


IGNORED_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "benchmark-results",
}
TAIL_LIMIT = 4000


class ValidationCommand:
    def __init__(
        self, name: str, argv: tuple[str, ...], *, prepend_python: bool = True
    ) -> None:
        self.name = name
        self.argv = argv
        self.prepend_python = prepend_python


OFFLINE_COMMANDS: tuple[ValidationCommand, ...] = (
    ValidationCommand("skills", ("scripts/validate_skills.py",)),
    ValidationCommand(
        "skills-strict", ("scripts/validate_skills.py", "--strict-quality")
    ),
    ValidationCommand("tests", ("-m", "unittest", "discover", "-s", "tests", "-v")),
    ValidationCommand("core-benchmarks", ("scripts/run_benchmarks.py",)),
    ValidationCommand(
        "communication-benchmarks", ("scripts/run_communication_benchmarks.py",)
    ),
    ValidationCommand("design-benchmarks", ("scripts/run_design_benchmarks.py",)),
    ValidationCommand(
        "capability-benchmarks", ("scripts/run_capability_benchmarks.py",)
    ),
    ValidationCommand(
        "coordination-benchmarks", ("scripts/run_coordination_benchmarks.py",)
    ),
    ValidationCommand("qcu-benchmarks", ("scripts/run_qcu_benchmarks.py",)),
    ValidationCommand("skyvern-benchmarks", ("scripts/run_skyvern_benchmarks.py",)),
    ValidationCommand("extension-benchmarks", ("scripts/run_extension_benchmarks.py",)),
    ValidationCommand(
        "skill-routing-benchmarks", ("scripts/run_skill_routing_benchmarks.py",)
    ),
    ValidationCommand(
        "memory-benchmarks", ("scripts/run_memory_benchmarks.py", "--json")
    ),
    ValidationCommand("external-catalog", ("scripts/external_catalog.py", "validate")),
    ValidationCommand(
        "integration-adapters", ("scripts/integration_adapters.py", "all")
    ),
    ValidationCommand(
        "integration-evaluation",
        (
            "scripts/integration_evaluation.py",
            "--receipts",
            "benchmarks/integration_evaluation_cases.json",
        ),
    ),
    ValidationCommand(
        "durable-gate-mutations",
        (
            "skills/execute-durably/scripts/work_state_core/mutation_probe.py",
            "--root",
            ".",
        ),
    ),
    ValidationCommand(
        "doctor-installation",
        ("scripts/doctor.py", "--validate-installation", "--json"),
    ),
)


class ValidationError(ValueError):
    """Raised when a complete validation receipt cannot be produced."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def iter_source_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        yield path


def source_identity(root: Path) -> dict[str, Any]:
    root = root.resolve()
    aggregate = hashlib.sha256()
    count = 0
    for path in iter_source_files(root):
        relative = path.relative_to(root).as_posix()
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(file_hash.encode("ascii"))
        aggregate.update(b"\n")
        count += 1
    if count == 0:
        raise ValidationError("plugin root has no source files")
    return {"sha256": aggregate.hexdigest(), "fileCount": count}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as error:
        raise ValidationError(f"cannot execute git: {error}") from error


def git_identity(root: Path) -> dict[str, Any]:
    head = _git(root, "rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        raise ValidationError("plugin root must have a real Git HEAD")
    sha = head.stdout.strip()
    if len(sha) != 40 or any(character not in "0123456789abcdef" for character in sha):
        raise ValidationError("Git HEAD is not a full SHA-1")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        raise ValidationError("cannot inspect Git worktree state")
    entries = [line for line in status.stdout.splitlines() if line]
    return {"sha": sha, "dirty": bool(entries), "status": entries}


def _full_argv(python: str, command: ValidationCommand) -> list[str]:
    if command.prepend_python:
        return [python, *command.argv]
    return list(command.argv)


def run_command(
    root: Path, python: str, command: ValidationCommand, category: str
) -> dict[str, Any]:
    argv = _full_argv(python, command)
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            check=False,
        )
        exit_code: int | None = result.returncode
        stdout = result.stdout
        stderr = result.stderr
        error: str | None = None
    except OSError as exception:
        exit_code = None
        stdout = b""
        stderr = str(exception).encode("utf-8", errors="replace")
        error = str(exception)
    duration = time.monotonic() - started
    record: dict[str, Any] = {
        "name": command.name,
        "category": category,
        "command": argv,
        "exitCode": exit_code,
        "passed": exit_code == 0,
        "durationSeconds": round(duration, 6),
        "stdoutSha256": _sha256_bytes(stdout),
        "stderrSha256": _sha256_bytes(stderr),
        "stdoutTail": stdout.decode("utf-8", errors="replace")[-TAIL_LIMIT:],
        "stderrTail": stderr.decode("utf-8", errors="replace")[-TAIL_LIMIT:],
    }
    if error is not None:
        record["error"] = error
    return record


def _live_commands(values: Sequence[str]) -> tuple[ValidationCommand, ...]:
    commands: list[ValidationCommand] = []
    for index, raw in enumerate(values, start=1):
        try:
            argv = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValidationError(
                f"invalid --live-command-json #{index}: {error}"
            ) from error
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(value, str) and value for value in argv)
        ):
            raise ValidationError(
                f"--live-command-json #{index} must be a non-empty JSON string array"
            )
        commands.append(
            ValidationCommand(f"live-{index}", tuple(argv), prepend_python=False)
        )
    return tuple(commands)


def build_receipt(
    root: Path,
    *,
    python: str,
    run_offline: bool,
    run_live: bool,
    live_commands: Sequence[ValidationCommand] = (),
) -> dict[str, Any]:
    root = root.resolve()
    if not run_offline and not run_live:
        raise ValidationError("select --offline, --live, or both")
    if run_live and not live_commands:
        raise ValidationError(
            "--live requires at least one explicit --live-command-json argv"
        )

    started = time.monotonic()
    git_before = dict(git_identity(root))
    source_before = dict(source_identity(root))
    records: list[dict[str, Any]] = []
    if run_offline:
        records.extend(
            run_command(root, python, command, "offline")
            for command in OFFLINE_COMMANDS
        )
    if run_live:
        records.extend(
            run_command(root, python, command, "live") for command in live_commands
        )

    git = dict(git_identity(root))
    source = dict(source_identity(root))
    git["initialSha"] = git_before["sha"]
    git["identityStable"] = git_before == {
        "sha": git["sha"],
        "dirty": git["dirty"],
        "status": git["status"],
    }
    source["initialSha256"] = source_before["sha256"]
    source["identityStable"] = source_before == {
        "sha256": source["sha256"],
        "fileCount": source["fileCount"],
    }

    offline_records = [item for item in records if item["category"] == "offline"]
    live_records = [item for item in records if item["category"] == "live"]
    offline_complete = run_offline and len(offline_records) == len(OFFLINE_COMMANDS)
    offline_passed = offline_complete and all(
        item["passed"] for item in offline_records
    )
    live_complete = run_live and len(live_records) == len(live_commands)
    live_validated = (
        live_complete
        and bool(live_records)
        and all(item["passed"] for item in live_records)
    )
    passed = (
        (not run_offline or offline_passed)
        and (not run_live or live_validated)
        and not git["dirty"]
        and git["identityStable"]
        and source["identityStable"]
    )
    return {
        "schemaVersion": 1,
        "kind": "cognitive-powers-validation",
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "durationSeconds": round(time.monotonic() - started, 6),
        "git": git,
        "source": source,
        "commands": records,
        "offline": {
            "requested": run_offline,
            "complete": offline_complete,
            "passed": offline_passed,
            "expectedCommands": len(OFFLINE_COMMANDS) if run_offline else 0,
            "executedCommands": len(offline_records),
        },
        "live": {
            "requested": run_live,
            "complete": live_complete,
            "validated": live_validated,
            "expectedCommands": len(live_commands) if run_live else 0,
            "executedCommands": len(live_records),
        },
        "passed": passed,
    }


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--live-command-json",
        action="append",
        default=[],
        help="explicit live command argv as a JSON string array; repeat as needed",
    )
    parser.add_argument("--json-output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = args.root.resolve()
        output = args.json_output.resolve()
        try:
            output.relative_to(root)
        except ValueError:
            pass
        else:
            raise ValidationError("validation receipt must be outside the plugin root")
        payload = build_receipt(
            root,
            python=sys.executable,
            run_offline=args.offline,
            run_live=args.live,
            live_commands=_live_commands(args.live_command_json),
        )
        write_receipt(output, payload)
    except (OSError, ValidationError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "output": str(output),
                "passed": payload["passed"],
                "offlinePassed": payload["offline"]["passed"],
                "liveValidated": payload["live"]["validated"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
