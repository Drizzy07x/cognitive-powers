#!/usr/bin/env python3
"""Report Cognitive Powers installation state without changing or probing it."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


SOURCE_IGNORED_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "benchmark-results",
}
PACKAGE_IGNORED_PARTS = SOURCE_IGNORED_PARTS | {
    "blob-report",
    "playwright-report",
    "test-results",
}


class DoctorError(ValueError):
    """Raised when the requested plugin root cannot be diagnosed."""


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DoctorError(f"cannot read {label}: {path}: {error}") from error
    if not isinstance(value, dict):
        raise DoctorError(f"{label} must contain a JSON object: {path}")
    return value


def _iter_files(root: Path, ignored_parts: set[str]) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in ignored_parts for part in relative.parts):
            continue
        if path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        yield path


def iter_source_files(root: Path) -> Iterable[Path]:
    return _iter_files(root, SOURCE_IGNORED_PARTS)


def iter_package_files(root: Path) -> Iterable[Path]:
    return _iter_files(root, PACKAGE_IGNORED_PARTS)


def source_identity(root: Path) -> dict[str, Any]:
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
    return {"sha256": aggregate.hexdigest(), "fileCount": count}


def git_identity(root: Path) -> dict[str, Any]:
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        status = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as error:
        return {"available": False, "reason": str(error)}
    sha = head.stdout.strip()
    if (
        head.returncode != 0
        or status.returncode != 0
        or len(sha) != 40
        or any(character not in "0123456789abcdef" for character in sha)
    ):
        return {"available": False, "reason": "root has no readable Git HEAD"}
    entries = [line for line in status.stdout.splitlines() if line]
    return {
        "available": True,
        "sha": sha,
        "dirty": bool(entries),
        "statusEntryCount": len(entries),
    }


def skill_inventory(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    declared = manifest.get("skills")
    try:
        exposed_root = (
            _declared_path(root, declared) if isinstance(declared, str) else None
        )
    except DoctorError as error:
        return {"count": 0, "names": [], "declared": declared, "error": str(error)}
    names = (
        sorted(path.parent.name for path in exposed_root.glob("*/SKILL.md"))
        if exposed_root is not None and exposed_root.is_dir()
        else []
    )
    internal_names = sorted(
        path.parent.name for path in (root / "skills").glob("*/SKILL.md")
    )
    return {
        "count": len(names),
        "names": names,
        "declared": declared,
        "internalCount": len(internal_names),
        "internalNames": internal_names,
    }


def hook_inventory(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    declared = manifest.get("hooks")
    result: dict[str, Any] = {
        "declared": declared if isinstance(declared, str) else None,
        "available": False,
        "events": [],
    }
    if not isinstance(declared, str):
        return result
    try:
        path = _declared_path(root, declared)
    except DoctorError as error:
        result["error"] = str(error)
        return result
    if not path.is_file():
        return result
    try:
        payload = _read_object(path, "hook configuration")
    except DoctorError as error:
        result["error"] = str(error)
        return result
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        result["error"] = "hook configuration has no hooks object"
        return result
    result["available"] = True
    result["events"] = sorted(str(name) for name in hooks)
    result["scriptAvailable"] = (root / "hooks" / "selective_hooks.py").is_file()
    return result


def provider_declarations(root: Path) -> dict[str, Any]:
    catalog_path = root / "integrations" / "catalog.json"
    providers: list[dict[str, Any]] = []
    if catalog_path.is_file():
        catalog = _read_object(catalog_path, "integration catalog")
        sources = catalog.get("sources", [])
        if isinstance(sources, list):
            for source in sources:
                if not isinstance(source, dict) or source.get("kind") != "provider":
                    continue
                providers.append(
                    {
                        "name": source.get("name"),
                        "status": source.get("status"),
                        "decision": source.get("decision"),
                        "capabilities": source.get("capabilities", []),
                    }
                )
    return {
        "catalog": "integrations/catalog.json",
        "declared": providers,
        "networkProbed": False,
        "executablesProbed": False,
        "installationAttempted": False,
        "availabilityUnknown": True,
    }


def validation_inventory(root: Path) -> dict[str, Any]:
    paths = {
        "offlineEntrypoint": "scripts/validate_all.py",
        "releaseWitness": "scripts/release_witness.py",
        "ciWorkflow": ".github/workflows/validate.yml",
    }
    available = {name: (root / relative).is_file() for name, relative in paths.items()}
    return {
        "available": all(available.values()),
        "paths": paths,
        "components": available,
        "liveValidated": False,
        "note": "Availability is structural; doctor does not execute validation.",
    }


def local_usage_counter_policy() -> dict[str, Any]:
    """Report the deliberate absence of a privacy-safe local counter seam."""
    return {
        "status": "abstained",
        "reasonCode": "no-privacy-safe-natural-seam",
        "implemented": False,
        "writesLocalState": False,
        "prohibitedFields": [
            "prompts",
            "commands",
            "outputs",
            "paths",
            "identifiers",
        ],
        "controls": [],
        "reason": (
            "Existing hooks and durable receipts contain prohibited contextual "
            "fields; adding counters there would create misleading telemetry or "
            "broaden the plugin's write surface."
        ),
    }


def _declared_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise DoctorError(f"declared path escapes plugin root: {value}") from error
    return path


def _interface_asset_checks(
    root: Path, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    interface = manifest.get("interface")
    if interface is None:
        return []
    if not isinstance(interface, dict):
        return [
            {
                "name": "interface-assets",
                "passed": False,
                "detail": "manifest interface must be an object",
            }
        ]
    declarations: list[tuple[str, Any]] = [
        (field, interface.get(field))
        for field in ("composerIcon", "logo", "logoDark")
        if field in interface
    ]
    screenshots = interface.get("screenshots", [])
    if not isinstance(screenshots, list):
        return [
            {
                "name": "interface-assets",
                "passed": False,
                "detail": "interface screenshots must be a list",
            }
        ]
    for index, item in enumerate(screenshots):
        value = item.get("src") if isinstance(item, dict) else item
        declarations.append((f"screenshots[{index}]", value))

    checks: list[dict[str, Any]] = []
    for field, value in declarations:
        check: dict[str, Any] = {"name": f"interface-asset:{field}"}
        if not isinstance(value, str) or not value:
            check.update(passed=False, detail="declared asset path must be a string")
        else:
            try:
                path = _declared_path(root, value)
            except DoctorError as error:
                check.update(passed=False, detail=str(error))
            else:
                check.update(passed=path.is_file(), detail=value)
        checks.append(check)
    return checks


def _installation_checks(staged: Path) -> list[dict[str, Any]]:
    manifest_path = staged / ".codex-plugin" / "plugin.json"
    try:
        manifest = _read_object(manifest_path, "staged plugin manifest")
    except DoctorError as error:
        return [{"name": "manifest", "passed": False, "detail": str(error)}]
    try:
        skills_path = _declared_path(staged, str(manifest.get("skills", "")))
        hooks_path = _declared_path(staged, str(manifest.get("hooks", "")))
    except DoctorError as error:
        return [{"name": "declared-paths", "passed": False, "detail": str(error)}]
    hooks = hook_inventory(staged, manifest)
    validation = validation_inventory(staged)
    checks = [
        {
            "name": "manifest",
            "passed": bool(manifest.get("name") and manifest.get("version")),
        },
        {
            "name": "skills",
            "passed": skills_path.is_dir() and any(skills_path.glob("*/SKILL.md")),
        },
        {
            "name": "hooks",
            "passed": hooks_path.is_file() and hooks.get("available") is True,
        },
        {
            "name": "hook-script",
            "passed": hooks.get("scriptAvailable") is True,
        },
        {"name": "doctor", "passed": (staged / "scripts" / "doctor.py").is_file()},
        {
            "name": "offline-validation",
            "passed": validation["components"]["offlineEntrypoint"],
        },
        {
            "name": "release-witness",
            "passed": validation["components"]["releaseWitness"],
        },
        {
            "name": "ci-workflow",
            "passed": validation["components"]["ciWorkflow"],
        },
        {
            "name": "orchestration-runtime",
            "passed": (staged / "scripts" / "orchestration_policy.py").is_file(),
        },
        {
            "name": "orchestration-wrapper",
            "passed": (
                staged
                / "skills"
                / "solve-efficiently"
                / "scripts"
                / "orchestration_policy.py"
            ).is_file(),
        },
        {
            "name": "controller-ab-protocol",
            "passed": (staged / "benchmarks" / "controller_ab_protocol.json").is_file(),
        },
        {
            "name": "controller-ab-corpus",
            "passed": (
                staged / "benchmarks" / "confirmatory" / "controller_ab_corpus.json"
            ).is_file(),
        },
        {
            "name": "controller-ab-fixture-runtime",
            "passed": (staged / "scripts" / "controller_ab_fixtures.py").is_file(),
        },
        {
            "name": "controller-ab-batch-runtime",
            "passed": (staged / "scripts" / "controller_ab_batch.py").is_file(),
        },
        {
            "name": "controller-ab-home-runtime",
            "passed": (staged / "scripts" / "prepare_controller_ab_homes.py").is_file(),
        },
    ]
    checks.extend(_interface_asset_checks(staged, manifest))
    orchestration_path = staged / "scripts" / "orchestration_policy.py"
    if orchestration_path.is_file():
        smoke_input = {
            "schema_version": 1,
            "request_mode": "change",
            "phase": "verify",
            "authorization": "read-only",
            "boundaries_clear": True,
            "cheap_local_step_available": False,
            "symptom_reproduced": True,
            "durable_or_release_critical": False,
            "quality_claim": False,
            "delegated_change": True,
            "packet_plan_valid": False,
            "previous_worker_failed": False,
            "failure_classified": False,
            "available_agent_slots": 2,
            "current_depth": 0,
            "retry_attempts": 0,
            "completed_unit_ids": [],
            "units": [],
        }
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(orchestration_path),
                    "--agent-plan",
                    "-",
                    "--json",
                ],
                cwd=staged,
                input=json.dumps(smoke_input),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=30,
            )
            planned = json.loads(completed.stdout) if completed.returncode == 0 else {}
            executed = (
                isinstance(planned, dict)
                and planned.get("valid_input") is True
                and planned.get("mode") == "staged-verify"
                and planned.get("total_planned_agents") == 1
                and planned.get("receipt_policy", {}).get(
                    "end_to_end_improvement_proven"
                )
                is False
            )
            detail = None if executed else (completed.stderr or completed.stdout)[-500:]
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            executed = False
            detail = str(error)
        orchestration_check: dict[str, Any] = {
            "name": "orchestration-execution",
            "passed": executed,
        }
        if detail:
            orchestration_check["detail"] = detail
        checks.append(orchestration_check)
    doctor_path = staged / "scripts" / "doctor.py"
    if doctor_path.is_file():
        try:
            completed = subprocess.run(
                [sys.executable, str(doctor_path), "--root", str(staged), "--json"],
                cwd=staged,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=30,
            )
            diagnosed = (
                json.loads(completed.stdout) if completed.returncode == 0 else {}
            )
            diagnosed_plugin = (
                diagnosed.get("plugin", {}) if isinstance(diagnosed, dict) else {}
            )
            diagnosed_skills = (
                diagnosed.get("skills", {}) if isinstance(diagnosed, dict) else {}
            )
            diagnosed_hooks = (
                diagnosed.get("hooks", {}) if isinstance(diagnosed, dict) else {}
            )
            diagnosed_validation = (
                diagnosed.get("validation", {}) if isinstance(diagnosed, dict) else {}
            )
            diagnosed_providers = (
                diagnosed.get("optionalProviders", {})
                if isinstance(diagnosed, dict)
                else {}
            )
            executed = (
                isinstance(diagnosed, dict)
                and isinstance(diagnosed_plugin, dict)
                and diagnosed_plugin.get("name") == manifest.get("name")
                and diagnosed_plugin.get("version") == manifest.get("version")
                and diagnosed.get("readOnly") is True
                and isinstance(diagnosed_skills, dict)
                and isinstance(diagnosed_skills.get("count"), int)
                and diagnosed_skills["count"] > 0
                and isinstance(diagnosed_hooks, dict)
                and diagnosed_hooks.get("available") is True
                and diagnosed_hooks.get("scriptAvailable") is True
                and isinstance(diagnosed_validation, dict)
                and diagnosed_validation.get("available") is True
                and isinstance(diagnosed_providers, dict)
                and diagnosed_providers.get("networkProbed") is False
                and diagnosed_providers.get("executablesProbed") is False
                and diagnosed_providers.get("installationAttempted") is False
            )
            detail = None if executed else (completed.stderr or completed.stdout)[-500:]
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            executed = False
            detail = str(error)
        check: dict[str, Any] = {"name": "doctor-execution", "passed": executed}
        if detail:
            check["detail"] = detail
        checks.append(check)
    return checks


def validate_release_installation(root: Path) -> dict[str, Any]:
    """Package and inspect a disposable local installation without publishing it."""
    root = root.resolve()
    files = list(iter_package_files(root))
    with tempfile.TemporaryDirectory(prefix="cognitive-powers-install-") as temporary:
        temporary_root = Path(temporary)
        archive = temporary_root / "cognitive-powers.zip"
        staged = temporary_root / "installed"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for path in files:
                package.write(path, path.relative_to(root).as_posix())
        with zipfile.ZipFile(archive, "r") as package:
            package.extractall(staged)
        checks = _installation_checks(staged)
    return {
        "requested": True,
        "temporaryCopy": True,
        "published": False,
        "fileCount": len(files),
        "checks": checks,
        "passed": bool(checks) and all(check["passed"] for check in checks),
    }


def build_report(root: Path, *, validate_installation: bool = False) -> dict[str, Any]:
    root = root.resolve()
    manifest = _read_object(root / ".codex-plugin" / "plugin.json", "plugin manifest")
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "readOnly": True,
        "plugin": {
            "name": manifest.get("name"),
            "version": manifest.get("version"),
            "root": str(root),
        },
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "skills": skill_inventory(root, manifest),
        "hooks": hook_inventory(root, manifest),
        "git": git_identity(root),
        "source": source_identity(root),
        "optionalProviders": provider_declarations(root),
        "validation": validation_inventory(root),
        "localUsageCounters": local_usage_counter_policy(),
    }
    if validate_installation:
        report["installationValidation"] = validate_release_installation(root)
    return report


def _print_human(report: dict[str, Any]) -> None:
    plugin = report["plugin"]
    git = report["git"]
    print(f"{plugin['name']} {plugin['version']}")
    print(f"root: {plugin['root']}")
    print(f"python: {report['python']['version']} ({report['python']['executable']})")
    print(f"skills: {report['skills']['count']}")
    print(f"hooks: {'available' if report['hooks']['available'] else 'unavailable'}")
    if git["available"]:
        print(f"git: {git['sha']} ({'dirty' if git['dirty'] else 'clean'})")
    else:
        print("git: unavailable")
    print(f"source: {report['source']['sha256']}")
    print(
        "validation: "
        f"{'available' if report['validation']['available'] else 'incomplete'}"
    )
    print(f"local usage counters: {report['localUsageCounters']['status']}")
    if "installationValidation" in report:
        status = "passed" if report["installationValidation"]["passed"] else "failed"
        print(f"temporary installation: {status}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--validate-installation",
        action="store_true",
        help="package and inspect a disposable local copy without publishing",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(
            args.root, validate_installation=args.validate_installation
        )
    except DoctorError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_human(report)
    installation = report.get("installationValidation")
    return 1 if isinstance(installation, dict) and not installation["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
