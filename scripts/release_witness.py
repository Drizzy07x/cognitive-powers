#!/usr/bin/env python3
"""Create and verify a release witness bound to plugin files and real receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Sequence

try:
    from scripts.storage_policy import (
        EXCLUDED_DIRECTORY_NAMES,
        SOURCE_IDENTITY_ALGORITHM,
        StoragePolicyError,
        iter_tree_files,
    )
except ModuleNotFoundError:  # Direct script execution places scripts/ on sys.path.
    from storage_policy import (
        EXCLUDED_DIRECTORY_NAMES,
        SOURCE_IDENTITY_ALGORITHM,
        StoragePolicyError,
        iter_tree_files,
    )

IGNORED_PARTS = set(EXCLUDED_DIRECTORY_NAMES)
EXPECTED_OFFLINE_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("skills", ("scripts/validate_skills.py",)),
    ("skills-strict", ("scripts/validate_skills.py", "--strict-quality")),
    (
        "controller-ab-targeted-tests",
        (
            "-m",
            "unittest",
            "tests.test_live_ab_runner",
            "tests.test_controller_ab_protocol",
            "tests.test_controller_ab_fixtures",
            "tests.test_controller_ab_batch",
            "tests.test_controller_ab_evidence",
        ),
    ),
    ("tests", ("-m", "unittest", "discover", "-s", "tests", "-v")),
    ("ruff-check", ("-m", "ruff", "check", ".")),
    ("ruff-format", ("-m", "ruff", "format", "--check", ".")),
    ("core-benchmarks", ("scripts/run_benchmarks.py",)),
    ("durability-benchmarks", ("scripts/run_durability_benchmarks.py",)),
    ("communication-benchmarks", ("scripts/run_communication_benchmarks.py",)),
    ("design-benchmarks", ("scripts/run_design_benchmarks.py",)),
    ("capability-benchmarks", ("scripts/run_capability_benchmarks.py",)),
    ("coordination-benchmarks", ("scripts/run_coordination_benchmarks.py",)),
    (
        "controller-ab-fixture-contract",
        ("scripts/controller_ab_fixtures.py", "validate"),
    ),
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
        "verify-installed-fixture",
        ("tests/fixtures/run_verify_installed_fixture.py",),
    ),
    (
        "compatibility-contract",
        (
            "scripts/build_compatibility_matrix.py",
            "--contract",
            "compatibility-contract.json",
            "--json-output",
            "compatibility-matrix.json",
            "--markdown-output",
            "docs/compatibility.md",
            "--check",
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
    try:
        yield from iter_tree_files(root)
    except StoragePolicyError as error:
        raise WitnessError(str(error)) from error


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


def release_tag_identity(root: Path, version: str) -> str:
    expected = f"v{version}"
    tags = _git(root, "tag", "--points-at", "HEAD")
    if tags.returncode != 0:
        raise WitnessError("cannot inspect release tags")
    exact = sorted(line.strip() for line in tags.stdout.splitlines() if line.strip())
    if exact != [expected]:
        raise WitnessError(f"release HEAD must be tagged exactly {expected}")
    return expected


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
        or not _is_utc_timestamp(value.get("createdAt"))
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
    # Digests from different identity schemes are not comparable, so a receipt
    # produced by another scheme cannot witness a release built with this one.
    if source.get("algorithm") != SOURCE_IDENTITY_ALGORITHM:
        raise WitnessError(
            "validation receipt uses source identity algorithm "
            f"{source.get('algorithm')!r}, expected "
            f"{SOURCE_IDENTITY_ALGORITHM!r}: {label}"
        )
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
            or not math.isfinite(float(command["durationSeconds"]))
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
    executable = offline_results[0]["command"][0]
    if not _is_python_executable(executable) or any(
        item["command"][0] != executable for item in offline_results
    ):
        raise WitnessError(
            f"validation receipt offline executable is inconsistent: {label}"
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


def _is_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(
        parsed
    )


def _is_python_executable(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    # The receipt being read was written on another machine, so the path in it
    # is spelled for that platform. Path here is the host's flavour, and on
    # POSIX a backslash is an ordinary character, so every Windows receipt
    # aggregated on Linux yielded the whole string as its "name" and was
    # rejected. PureWindowsPath accepts both separators, which is what taking
    # the basename of a foreign path requires.
    name = PureWindowsPath(value).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if not name.startswith("python"):
        return False
    suffix = name[len("python") :]
    return not suffix or all(
        character.isdigit() or character == "." for character in suffix
    )


def create_witness(
    root: Path,
    receipt_paths: Sequence[Path],
    release_manifest_path: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    git = repository_identity(root)
    if git["dirty"]:
        raise WitnessError("release witness requires a clean Git worktree")
    release_tag = release_tag_identity(root, manifest["version"])
    files, source_sha256 = source_records(root)
    receipts = [
        _receipt(path.resolve(), git_sha=git["sha"], source_sha256=source_sha256)
        for path in receipt_paths
    ]
    live_validated = bool(receipts) and all(
        receipt.get("live", {}).get("validated") is True for receipt in receipts
    )
    release_manifest = None
    if release_manifest_path is not None:
        raw = release_manifest_path.resolve().read_bytes()
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or value.get("commit") != git["sha"]
            or value.get("tag") != release_tag
            or not _is_sha256(value.get("archive", {}).get("sha256"))
        ):
            raise WitnessError("release manifest is absent, malformed, or stale")
        release_manifest = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "archiveSha256": value["archive"]["sha256"],
            "filesSha256": value.get("filesSha256"),
        }
    return {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "plugin": manifest["name"],
        "version": manifest["version"],
        "tag": release_tag,
        "git": {"sha": git["sha"], "dirty": False},
        "files": files,
        "sourceSha256": source_sha256,
        "releaseManifest": release_manifest,
        "validations": receipts,
        "releaseReady": bool(receipts) and all(item["passed"] for item in receipts),
        "liveIntegrationsValidated": live_validated,
    }


def verify_witness(root: Path, witness: dict[str, Any]) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    if not isinstance(witness, dict):
        return ["witness must be an object"]
    try:
        manifest = json.loads(
            (root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"cannot read plugin manifest: {error}")
        manifest = {}
    if witness.get("schemaVersion") != 1:
        errors.append("unsupported witness schema")
    if not _is_utc_timestamp(witness.get("createdAt")):
        errors.append("witness creation timestamp is invalid")
    if witness.get("plugin") != manifest.get("name"):
        errors.append("witness plugin identity is absent or stale")
    if witness.get("version") != manifest.get("version"):
        errors.append("witness plugin version is absent or stale")
    try:
        release_tag = release_tag_identity(root, manifest.get("version", ""))
    except WitnessError as error:
        errors.append(str(error))
        release_tag = None
    if release_tag is not None and witness.get("tag") != release_tag:
        errors.append("witness release tag is absent or stale")
    try:
        git = repository_identity(root)
    except WitnessError as error:
        errors.append(str(error))
        git = None
    if git is not None:
        if git["dirty"]:
            errors.append("Git worktree is dirty")
        witness_git = witness.get("git")
        if (
            not isinstance(witness_git, dict)
            or witness_git.get("sha") != git["sha"]
            or witness_git.get("dirty") is not False
        ):
            errors.append("witness Git SHA is absent or stale")
    current_files, source_sha256 = source_records(root)
    files = witness.get("files")
    if files != current_files:
        errors.append("witness file inventory is stale or incomplete")
    if witness.get("sourceSha256") != source_sha256:
        errors.append("witness source identity is stale")
    release_manifest = witness.get("releaseManifest")
    if release_manifest is not None and (
        not isinstance(release_manifest, dict)
        or not _is_sha256(release_manifest.get("sha256"))
        or not _is_sha256(release_manifest.get("archiveSha256"))
        or not _is_sha256(release_manifest.get("filesSha256"))
    ):
        errors.append("witness release manifest binding is malformed")
    validations = witness.get("validations")
    if not isinstance(validations, list):
        errors.append("witness validations are malformed")
        validations = []
    expected_git_sha = git["sha"] if git is not None else ""
    valid_receipts: list[dict[str, Any]] = []
    for index, validation in enumerate(validations, start=1):
        try:
            valid_receipts.append(
                _validate_receipt(
                    validation,
                    label=f"embedded validation {index}",
                    git_sha=expected_git_sha,
                    source_sha256=source_sha256,
                )
            )
        except WitnessError as error:
            errors.append(str(error))
    derived_release_ready = bool(validations) and len(valid_receipts) == len(
        validations
    )
    derived_live_validated = derived_release_ready and all(
        receipt.get("live", {}).get("validated") is True for receipt in valid_receipts
    )
    if (
        not isinstance(witness.get("releaseReady"), bool)
        or witness["releaseReady"] is not derived_release_ready
    ):
        errors.append("witness releaseReady flag is inconsistent")
    if (
        not isinstance(witness.get("liveIntegrationsValidated"), bool)
        or witness["liveIntegrationsValidated"] is not derived_live_validated
    ):
        errors.append("witness liveIntegrationsValidated flag is inconsistent")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--receipt", action="append", type=Path, default=[])
    parser.add_argument("--release-manifest", type=Path)
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
        payload = create_witness(root, args.receipt, args.release_manifest)
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
