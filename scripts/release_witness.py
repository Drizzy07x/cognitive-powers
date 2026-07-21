#!/usr/bin/env python3
"""Create and verify a release witness bound to plugin files and real receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
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
EXPECTED_OFFLINE_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("skills", ("scripts/validate_skills.py",)),
    ("skills-strict", ("scripts/validate_skills.py", "--strict-quality")),
    ("tests", ("-m", "unittest", "discover", "-s", "tests", "-v")),
    ("core-benchmarks", ("scripts/run_benchmarks.py",)),
    ("communication-benchmarks", ("scripts/run_communication_benchmarks.py",)),
    ("design-benchmarks", ("scripts/run_design_benchmarks.py",)),
    ("capability-benchmarks", ("scripts/run_capability_benchmarks.py",)),
    ("coordination-benchmarks", ("scripts/run_coordination_benchmarks.py",)),
    ("qcu-benchmarks", ("scripts/run_qcu_benchmarks.py",)),
    ("skyvern-benchmarks", ("scripts/run_skyvern_benchmarks.py",)),
    ("extension-benchmarks", ("scripts/run_extension_benchmarks.py",)),
    ("skill-routing-benchmarks", ("scripts/run_skill_routing_benchmarks.py",)),
    ("memory-benchmarks", ("scripts/run_memory_benchmarks.py", "--json")),
    ("external-catalog", ("scripts/external_catalog.py", "validate")),
    ("integration-adapters", ("scripts/integration_adapters.py", "all")),
    (
        "integration-evaluation",
        (
            "scripts/integration_evaluation.py",
            "--receipts",
            "benchmarks/integration_evaluation_cases.json",
        ),
    ),
    (
        "durable-gate-mutations",
        (
            "skills/execute-durably/scripts/work_state_core/mutation_probe.py",
            "--root",
            ".",
        ),
    ),
    (
        "doctor-installation",
        ("scripts/doctor.py", "--validate-installation", "--json"),
    ),
)


class WitnessError(ValueError):
    """Raised when a witness would claim unsupported release evidence."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_release_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        yield path


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
        raise WitnessError(f"cannot execute git: {error}") from error


def repository_identity(root: Path) -> dict[str, Any]:
    head = _git(root, "rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        raise WitnessError("plugin root must have a real Git HEAD")
    sha = head.stdout.strip()
    if len(sha) != 40 or any(character not in "0123456789abcdef" for character in sha):
        raise WitnessError("Git HEAD is not a full SHA-1")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        raise WitnessError("cannot inspect Git worktree state")
    entries = [line for line in status.stdout.splitlines() if line]
    return {"sha": sha, "dirty": bool(entries), "status": entries}


def source_records(root: Path) -> tuple[list[dict[str, Any]], str]:
    files = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in iter_release_files(root)
    ]
    aggregate = hashlib.sha256()
    for item in files:
        aggregate.update(item["path"].encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(item["sha256"].encode("ascii"))
        aggregate.update(b"\n")
    return files, aggregate.hexdigest()


def _validate_receipt(
    value: Any, *, label: str, git_sha: str, source_sha256: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WitnessError(f"validation receipt must be an object: {label}")
    if (
        value.get("schemaVersion") != 1
        or value.get("kind") != "cognitive-powers-validation"
        or not isinstance(value.get("createdAt"), str)
        or not value["createdAt"]
    ):
        raise WitnessError(f"unsupported validation receipt kind: {label}")
    git = value.get("git")
    if not isinstance(git, dict) or git.get("sha") != git_sha:
        raise WitnessError(f"validation receipt Git SHA is absent or stale: {label}")
    if git.get("dirty") is not False:
        raise WitnessError(
            f"validation receipt was produced from a dirty tree: {label}"
        )
    if git.get("identityStable") is not True:
        raise WitnessError(
            f"validation receipt Git identity changed during validation: {label}"
        )
    if git.get("initialSha") != git.get("sha"):
        raise WitnessError(f"validation receipt Git identity is inconsistent: {label}")
    source = value.get("source")
    if not isinstance(source, dict) or source.get("sha256") != source_sha256:
        raise WitnessError(
            f"validation receipt source identity is absent or stale: {label}"
        )
    if source.get("identityStable") is not True:
        raise WitnessError(
            f"validation receipt source changed during validation: {label}"
        )
    if source.get("initialSha256") != source.get("sha256"):
        raise WitnessError(
            f"validation receipt source identity is inconsistent: {label}"
        )
    if (
        not isinstance(source.get("fileCount"), int)
        or isinstance(source.get("fileCount"), bool)
        or source["fileCount"] <= 0
    ):
        raise WitnessError(f"validation receipt source file count is invalid: {label}")
    commands = value.get("commands")
    if not isinstance(commands, list) or not commands:
        raise WitnessError(f"validation receipt has no command results: {label}")
    for command in commands:
        exit_code = command.get("exitCode") if isinstance(command, dict) else None
        if (
            not isinstance(command, dict)
            or not isinstance(command.get("name"), str)
            or not command["name"]
            or not isinstance(command.get("command"), list)
            or not command["command"]
            or not all(isinstance(part, str) and part for part in command["command"])
            or not isinstance(exit_code, int)
            or isinstance(exit_code, bool)
            or command.get("passed") is not (exit_code == 0)
            or not isinstance(command.get("durationSeconds"), (int, float))
            or isinstance(command.get("durationSeconds"), bool)
            or command["durationSeconds"] < 0
            or not _is_sha256(command.get("stdoutSha256"))
            or not _is_sha256(command.get("stderrSha256"))
        ):
            raise WitnessError(
                f"validation receipt has malformed command result: {label}"
            )
        if command.get("category") not in {"offline", "live"}:
            raise WitnessError(
                f"validation receipt has unknown command category: {label}"
            )
    offline = value.get("offline")
    if (
        not isinstance(offline, dict)
        or offline.get("requested") is not True
        or offline.get("complete") is not True
        or offline.get("passed") is not True
        or offline.get("expectedCommands") != len(EXPECTED_OFFLINE_COMMANDS)
        or offline.get("executedCommands") != offline.get("expectedCommands")
    ):
        raise WitnessError(
            f"validation receipt has incomplete offline validation: {label}"
        )
    offline_results = [item for item in commands if item.get("category") == "offline"]
    if len(offline_results) != offline["expectedCommands"] or not all(
        item["passed"] for item in offline_results
    ):
        raise WitnessError(
            f"validation receipt offline commands are incomplete: {label}"
        )
    actual_signature = [
        (item["name"], tuple(item["command"][1:])) for item in offline_results
    ]
    if actual_signature != list(EXPECTED_OFFLINE_COMMANDS):
        raise WitnessError(f"validation receipt offline command set is stale: {label}")

    live = value.get("live")
    live_results = [item for item in commands if item.get("category") == "live"]
    if not isinstance(live, dict) or not isinstance(live.get("requested"), bool):
        raise WitnessError(f"validation receipt has malformed live summary: {label}")
    if live["requested"]:
        live_consistent = (
            live.get("complete") is True
            and live.get("validated") is True
            and isinstance(live.get("expectedCommands"), int)
            and not isinstance(live.get("expectedCommands"), bool)
            and live["expectedCommands"] > 0
            and live.get("executedCommands") == live["expectedCommands"]
            and len(live_results) == live["expectedCommands"]
            and all(item["passed"] for item in live_results)
        )
    else:
        live_consistent = (
            live.get("complete") is False
            and live.get("validated") is False
            and live.get("expectedCommands") == 0
            and live.get("executedCommands") == 0
            and not live_results
        )
    if not live_consistent:
        raise WitnessError(
            f"validation receipt has inconsistent live validation: {label}"
        )
    recomputed_passed = all(item["passed"] for item in commands)
    if value.get("passed") is not recomputed_passed or not recomputed_passed:
        raise WitnessError(f"validation receipt did not pass consistently: {label}")
    return value


def _receipt(path: Path, *, git_sha: str, source_sha256: str) -> dict[str, Any]:
    return _validate_receipt(
        json.loads(path.read_text(encoding="utf-8")),
        label=str(path),
        git_sha=git_sha,
        source_sha256=source_sha256,
    )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def create_witness(root: Path, receipt_paths: Sequence[Path]) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    git = repository_identity(root)
    if git["dirty"]:
        raise WitnessError("release witness requires a clean Git worktree")
    files, source_sha256 = source_records(root)
    receipts = [
        _receipt(path.resolve(), git_sha=git["sha"], source_sha256=source_sha256)
        for path in receipt_paths
    ]
    live_validated = bool(receipts) and all(
        receipt.get("live", {}).get("validated") is True for receipt in receipts
    )
    return {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "plugin": manifest["name"],
        "version": manifest["version"],
        "git": {"sha": git["sha"], "dirty": False},
        "files": files,
        "sourceSha256": source_sha256,
        "validations": receipts,
        "releaseReady": bool(receipts) and all(item["passed"] for item in receipts),
        "liveIntegrationsValidated": live_validated,
    }


def verify_witness(root: Path, witness: dict[str, Any]) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    try:
        git = repository_identity(root)
    except WitnessError as error:
        errors.append(str(error))
        git = None
    if git is not None:
        if git["dirty"]:
            errors.append("Git worktree is dirty")
        witness_git = witness.get("git")
        if not isinstance(witness_git, dict) or witness_git.get("sha") != git["sha"]:
            errors.append("witness Git SHA is absent or stale")
    files = witness.get("files")
    if not isinstance(files, list) or not files:
        return ["witness has no files"]
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            errors.append("malformed file record")
            continue
        path = (root / item["path"]).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            errors.append(f"file escapes plugin root: {item['path']}")
            continue
        if not path.is_file():
            errors.append(f"missing file: {item['path']}")
        elif sha256_file(path) != item.get("sha256"):
            errors.append(f"changed file: {item['path']}")
    _, source_sha256 = source_records(root)
    if witness.get("sourceSha256") != source_sha256:
        errors.append("witness source identity is stale")
    if witness.get("releaseReady") and not witness.get("validations"):
        errors.append("releaseReady cannot be true without validations")
    validations = witness.get("validations")
    if witness.get("releaseReady"):
        if not isinstance(validations, list):
            errors.append("releaseReady validations are malformed")
        else:
            expected_git_sha = git["sha"] if git is not None else ""
            for index, validation in enumerate(validations, start=1):
                try:
                    _validate_receipt(
                        validation,
                        label=f"embedded validation {index}",
                        git_sha=expected_git_sha,
                        source_sha256=source_sha256,
                    )
                except WitnessError as error:
                    errors.append(str(error))
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--receipt", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        output = args.output.resolve()
        try:
            output.relative_to(root)
        except ValueError:
            pass
        else:
            raise WitnessError("release witness output must be outside the plugin root")
        payload = create_witness(root, args.receipt)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary, output)
    except (OSError, json.JSONDecodeError, KeyError, WitnessError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {"output": str(output), "releaseReady": payload["releaseReady"]},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
