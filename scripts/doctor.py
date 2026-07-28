#!/usr/bin/env python3
"""Report Cognitive Powers installation state without changing or probing it."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from scripts.storage_policy import (
        DEFAULT_COPY_MAX_BYTES,
        DEFAULT_COPY_MAX_FILES,
        EXCLUDED_DIRECTORY_NAMES,
        EXCLUDED_FILE_NAMES,
        StoragePolicyError,
        enforce_budget,
        iter_tree_files,
        measure_files,
        source_identity as shared_source_identity,
    )
except ModuleNotFoundError:  # Direct script execution places scripts/ on sys.path.
    from storage_policy import (
        DEFAULT_COPY_MAX_BYTES,
        DEFAULT_COPY_MAX_FILES,
        EXCLUDED_DIRECTORY_NAMES,
        EXCLUDED_FILE_NAMES,
        StoragePolicyError,
        enforce_budget,
        iter_tree_files,
        measure_files,
        source_identity as shared_source_identity,
    )


try:
    from scripts import skill_frontmatter as _FRONTMATTER
except ModuleNotFoundError:  # Direct script execution places scripts/ on sys.path.
    import skill_frontmatter as _FRONTMATTER


SOURCE_IGNORED_PARTS = set(EXCLUDED_DIRECTORY_NAMES)
PACKAGE_IGNORED_PARTS = set(EXCLUDED_DIRECTORY_NAMES)
HOST_METADATA_FILES = set(EXCLUDED_FILE_NAMES)


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


def _iter_files(root: Path) -> Iterable[Path]:
    yield from iter_tree_files(root)


def iter_source_files(root: Path) -> Iterable[Path]:
    return _iter_files(root)


def iter_package_files(root: Path) -> Iterable[Path]:
    return _iter_files(root)


def source_identity(root: Path) -> dict[str, Any]:
    return dict(shared_source_identity(root))


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
                "--",
                ".",
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
    all_entries = [line for line in status.stdout.splitlines() if line]
    ignored_host_entries = [
        line for line in all_entries if line == "?? .codex-marketplace-install.json"
    ]
    entries = [line for line in all_entries if line not in ignored_host_entries]
    return {
        "available": True,
        "sha": sha,
        "dirty": bool(entries),
        "statusEntryCount": len(entries),
        "ignoredHostEntries": ignored_host_entries,
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


def durable_state_inventory(data_root: Path) -> dict[str, Any]:
    """Inspect durable sessions without creating, locking, or repairing anything."""
    root = data_root.expanduser().resolve()
    findings: list[dict[str, Any]] = []

    def add(code: str, severity: str, path: Path, action: str) -> None:
        try:
            evidence = path.relative_to(root).as_posix()
        except ValueError:
            evidence = str(path)
        findings.append(
            {
                "code": code,
                "severity": severity,
                "evidence": evidence,
                "recommendedAction": action,
            }
        )

    if root.is_dir():
        durable_path = (
            Path(__file__).resolve().parents[1]
            / "skills"
            / "execute-durably"
            / "scripts"
            / "work_state.py"
        )
        durable_module = None
        if durable_path.is_file():
            spec = importlib.util.spec_from_file_location(
                "_cognitive_doctor_work_state", durable_path
            )
            if spec is not None and spec.loader is not None:
                durable_module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = durable_module
                spec.loader.exec_module(durable_module)
        for session in sorted(root.glob("projects/*/sessions/*")):
            if not session.is_dir():
                continue
            state_path = session / "state.json"
            if state_path.exists():
                try:
                    if durable_module is None:
                        raise DoctorError("durable validator is unavailable")
                    durable_module.state_migration_report(session)
                    valid = True
                except (OSError, json.JSONDecodeError, RuntimeError, DoctorError):
                    valid = False
                if not valid:
                    add(
                        "durable.state-invalid",
                        "error",
                        state_path,
                        "Preserve the session and restore state.json from a verified ledger snapshot.",
                    )
            ledger = session / "ledger.jsonl"
            if ledger.exists():
                try:
                    if durable_module is None:
                        raise DoctorError("durable validator is unavailable")
                    durable_module._read_ledger_events(session)
                    valid = True
                except (OSError, json.JSONDecodeError, RuntimeError, DoctorError):
                    valid = False
                if not valid:
                    add(
                        "durable.ledger-invalid",
                        "error",
                        ledger,
                        "Preserve the ledger; do not truncate it or fabricate recovery evidence.",
                    )
            lock = session / ".state.lock"
            if lock.exists():
                try:
                    lock_value = json.loads(lock.read_text(encoding="utf-8"))
                    identified = isinstance(lock_value, dict) and isinstance(
                        lock_value.get("pid"), int
                    )
                except (OSError, json.JSONDecodeError):
                    identified = False
                if not identified:
                    add(
                        "durable.lock-unidentified",
                        "warning",
                        lock,
                        "Confirm no live owner exists before using the documented lock recovery procedure.",
                    )
            for residue in sorted(session.glob(".*.tmp")):
                add(
                    "durable.write-interrupted",
                    "warning",
                    residue,
                    "Keep the last verified state and inspect this interrupted atomic-write residue manually.",
                )
    findings.sort(key=lambda item: (item["code"], item["evidence"]))
    return {
        "dataRoot": str(root),
        "available": root.is_dir(),
        "migrationPolicy": "forward-only-with-verified-backup",
        "pendingMigrations": [],
        "findings": findings,
        "readOnly": True,
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
            "name": "storage-policy-runtime",
            "passed": (staged / "scripts" / "storage_policy.py").is_file(),
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
            # Two skill scripts resolve this by absolute path at runtime, so a
            # packaging change that dropped it would only surface when a
            # receipt is written.
            "name": "provider-usage-runtime",
            "passed": (staged / "scripts" / "provider_usage.py").is_file(),
        },
        {
            # Loaded by both release gates so they read frontmatter alike.
            "name": "skill-frontmatter-runtime",
            "passed": (staged / "scripts" / "skill_frontmatter.py").is_file(),
        },
        {
            # Declared by the Claude manifest's SessionStart hook.
            "name": "semantic-index-hook",
            "passed": (staged / "hooks" / "semantic_index.py").is_file(),
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
            "name": "controller-ab-finalizer-runtime",
            "passed": (
                staged / "scripts" / "finalize_controller_ab_evidence.py"
            ).is_file(),
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
    checks.append(_codex_hook_interpreter_check(staged))
    return checks


def _codex_hook_interpreter_check(staged: Path) -> dict[str, Any]:
    """Execute the exact interpreter spelling the Codex hook manifest uses.

    The Codex host carries no user-config expansion the way the Claude
    manifest does, so hooks/hooks.json names python3 (POSIX) or the py
    launcher (Windows) directly. Both are hard prerequisites: on Windows the
    Store alias resolves and then exits without running Python, so a
    resolution-only probe proves nothing -- the interpreter has to run, which
    is the same lesson the installer preflight already learned.
    """
    name = "codex-hook-interpreter"
    try:
        manifest = json.loads(
            (staged / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        commands = [
            hook
            for entries in manifest.get("hooks", {}).values()
            for entry in entries
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict) and hook.get("type") == "command"
        ]
    except (OSError, ValueError, json.JSONDecodeError, AttributeError) as error:
        return {
            "name": name,
            "passed": False,
            "detail": f"cannot read the hook manifest: {error}",
        }
    if not commands:
        return {
            "name": name,
            "passed": True,
            "detail": "the staged manifest declares no command hooks to probe",
        }
    try:
        entry = commands[0]
        spelled = entry["commandWindows"] if os.name == "nt" else entry["command"]
        interpreter = shlex.split(spelled, posix=os.name != "nt")[0]
    except (KeyError, IndexError, ValueError) as error:
        return {
            "name": name,
            "passed": False,
            "detail": f"cannot read the hook interpreter spelling: {error}",
        }
    argv = (
        [interpreter, "-3", "-c", "import sys; sys.exit(0)"]
        if interpreter == "py"
        else [interpreter, "-c", "import sys; sys.exit(0)"]
    )
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {
            "name": name,
            "passed": False,
            "detail": (
                f"the Codex hook interpreter {interpreter!r} does not run: {error}. "
                "Install Python 3.11+ so this spelling resolves, or Codex hooks "
                "will silently never fire."
            ),
        }
    if completed.returncode != 0:
        return {
            "name": name,
            "passed": False,
            "detail": (
                f"{interpreter!r} resolves but exits {completed.returncode}; on "
                "Windows the Microsoft Store alias is such a stub. Install "
                "Python 3.11+ or disable the alias, then re-run the doctor."
            ),
        }
    return {"name": name, "passed": True}


def validate_release_installation(
    root: Path,
    *,
    max_files: int = DEFAULT_COPY_MAX_FILES,
    max_bytes: int = DEFAULT_COPY_MAX_BYTES,
) -> dict[str, Any]:
    """Package and inspect a disposable local installation without publishing it."""
    root = root.resolve()
    files = list(iter_package_files(root))
    measurement = measure_files(files)
    enforce_budget(
        measurement,
        max_files=max_files,
        max_bytes=max_bytes,
        label="package",
    )
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
        "totalBytes": measurement.total_bytes,
        "checks": checks,
        "passed": bool(checks) and all(check["passed"] for check in checks),
    }


def _skill_frontmatter(path: Path) -> dict[str, str]:
    """Read flat scalar frontmatter keys without importing a YAML parser."""
    return _FRONTMATTER.read(path)


def _is_truthy(value: str | None) -> bool:
    """Match every boolean spelling Claude Code accepts in frontmatter."""
    return _FRONTMATTER.is_truthy(value)


def _unroutable_referenced_skills(root: Path, manual: set[str]) -> list[str]:
    """Return skills another skill tells the model to invoke but cannot.

    A skill body that says "invoke ``use-current-docs``" is dead guidance when
    that skill carries ``disable-model-invocation``: Claude Code never shows it
    to the model, so the instruction cannot be followed.
    """
    if not manual:
        return []
    referenced: set[str] = set()
    for path in sorted((root / "skills").glob("*/SKILL.md")):
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for name in manual:
            if name == path.parent.name:
                continue
            if re.search(rf"`{re.escape(name)}`", body):
                referenced.add(name)
    return sorted(referenced)


def host_surfaces(root: Path) -> dict[str, Any]:
    """Describe both packaging surfaces from disk without probing any host."""
    surfaces: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    versions: dict[str, str] = {}

    codex_path = root / ".codex-plugin" / "plugin.json"
    codex: dict[str, Any] = {
        "host": "codex",
        "manifest": ".codex-plugin/plugin.json",
        "present": codex_path.is_file(),
    }
    if codex["present"]:
        try:
            manifest = _read_object(codex_path, "codex plugin manifest")
        except DoctorError as error:
            codex["error"] = str(error)
        else:
            codex["version"] = manifest.get("version")
            codex["skillsDeclared"] = manifest.get("skills")
            codex["hooks"] = manifest.get("hooks")
            if isinstance(manifest.get("version"), str):
                versions["codex"] = manifest["version"]
    surfaces.append(codex)

    claude_path = root / ".claude-plugin" / "plugin.json"
    claude: dict[str, Any] = {
        "host": "claude-code",
        "manifest": ".claude-plugin/plugin.json",
        "present": claude_path.is_file(),
    }
    if claude["present"]:
        try:
            manifest = _read_object(claude_path, "claude plugin manifest")
        except DoctorError as error:
            claude["error"] = str(error)
        else:
            claude["version"] = manifest.get("version")
            claude["hooks"] = manifest.get("hooks")
            claude["agents"] = manifest.get("agents")
            claude["requiredUserConfig"] = sorted(
                key
                for key, option in (manifest.get("userConfig") or {}).items()
                if isinstance(option, dict) and option.get("required")
            )
            automatic: list[str] = []
            manual: list[str] = []
            for skill in sorted((root / "skills").glob("*/SKILL.md")):
                fields = _skill_frontmatter(skill)
                target = (
                    manual
                    if _is_truthy(fields.get("disable-model-invocation"))
                    else automatic
                )
                target.append(skill.parent.name)
            claude["skillsDiscovered"] = "skills/"
            claude["modelInvocableSkills"] = automatic
            claude["userInvocableOnlySkills"] = manual
            unroutable = _unroutable_referenced_skills(root, set(manual))
            for name in unroutable:
                findings.append(
                    {
                        "code": "claude-skill-unroutable",
                        "severity": "error",
                        "message": (
                            f"{name} is referenced by name from another skill "
                            "but carries disable-model-invocation, so Claude "
                            "Code cannot follow that instruction"
                        ),
                    }
                )
            if isinstance(manifest.get("version"), str):
                versions["claude-code"] = manifest["version"]
            if "skills" in manifest:
                findings.append(
                    {
                        "code": "claude-skills-declared",
                        "severity": "warning",
                        "message": (
                            "declaring skills adds to the default skills/ scan and "
                            "can expose duplicate skill names to Claude Code"
                        ),
                    }
                )
    else:
        findings.append(
            {
                "code": "claude-manifest-missing",
                "severity": "warning",
                "message": "no .claude-plugin/plugin.json; Claude Code cannot load this tree as a plugin",
            }
        )
    surfaces.append(claude)

    aligned = len(set(versions.values())) <= 1
    if not aligned:
        findings.append(
            {
                "code": "host-version-drift",
                "severity": "error",
                "message": f"host manifests declare different versions: {versions}",
            }
        )
    return {
        "probed": False,
        "note": "Packaging is read from disk; doctor never executes a host CLI.",
        "surfaces": surfaces,
        "versionsAligned": aligned,
        "findings": findings,
    }


def build_report(
    root: Path,
    *,
    validate_installation: bool = False,
    max_package_files: int = DEFAULT_COPY_MAX_FILES,
    max_package_bytes: int = DEFAULT_COPY_MAX_BYTES,
    data_root: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    manifest = _read_object(root / ".codex-plugin" / "plugin.json", "plugin manifest")
    durable = durable_state_inventory(
        data_root
        if data_root is not None
        else Path(
            os.environ.get("COGNITIVE_POWERS_DATA")
            or os.environ.get("PLUGIN_DATA")
            or Path.home() / ".codex" / "cognitive-powers"
        )
    )
    hosts = host_surfaces(root)
    report: dict[str, Any] = {
        "schemaVersion": 2,
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
        "hosts": hosts,
        "git": git_identity(root),
        "source": source_identity(root),
        "optionalProviders": provider_declarations(root),
        "validation": validation_inventory(root),
        "localUsageCounters": local_usage_counter_policy(),
        "identity": {
            "product": manifest.get("name"),
            "version": manifest.get("version"),
            "tag": f"v{manifest.get('version')}",
        },
        "installation": {
            "root": str(root),
            "origin": "checkout-or-installed-tree",
            "hostMetadataAllowed": sorted(HOST_METADATA_FILES),
        },
        "durableState": durable,
        "findings": durable["findings"] + hosts["findings"],
    }
    if validate_installation:
        report["installationValidation"] = validate_release_installation(
            root,
            max_files=max_package_files,
            max_bytes=max_package_bytes,
        )
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
    parser.add_argument("--data-root", type=Path)
    parser.add_argument(
        "--validate-installation",
        action="store_true",
        help="package and inspect a disposable local copy without publishing",
    )
    parser.add_argument("--max-package-files", type=int, default=DEFAULT_COPY_MAX_FILES)
    parser.add_argument("--max-package-bytes", type=int, default=DEFAULT_COPY_MAX_BYTES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(
            args.root,
            validate_installation=args.validate_installation,
            max_package_files=args.max_package_files,
            max_package_bytes=args.max_package_bytes,
            data_root=args.data_root,
        )
    except (DoctorError, StoragePolicyError) as error:
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
