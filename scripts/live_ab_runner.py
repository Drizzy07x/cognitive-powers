#!/usr/bin/env python3
"""Run isolated, paired Codex live evaluations without inventing evidence."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.integration_evaluation import validate_task_contract
except ModuleNotFoundError:  # Direct script execution places scripts/ on sys.path.
    from integration_evaluation import validate_task_contract


IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache"}
INSTALLED_SURFACE_DIRECTORIES = (
    ".codex-plugin",
    "assets",
    "hooks",
    "skills",
    "skills-core",
)
INSTALLED_SURFACE_FILES = (
    "scripts/orchestration_policy.py",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
)
CONTROLLER_MODES = {"forced-solo", "adaptive"}
AGENT_PLAN_MODES = {
    "solo",
    "parallel-read-only",
    "parallel-packets",
    "staged-verify",
}
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTROLLER_PROTOCOL = PLUGIN_ROOT / "benchmarks" / "controller_ab_protocol.json"
CONTROLLER_DIRECTIVE_VERSION = 5
CONTROLLER_DIRECTIVE_TEMPLATE = """[Cognitive Powers controller directive v{version}; mode={mode}]
This directive is the controller_mode treatment and the only intentional A/B difference.
{behavior}
For every spawned assignment use task_name equal to the normalized planned unit id: lowercase, with non-alphanumeric runs replaced by underscores. Emit exactly one complete canonical v2 agent_plan per task, as returned by the orchestration runtime, in a standalone JSON agent_message before any spawn; do not summarize, rewrite, infer, reconstruct, or replace it. Execute its waves in order. Between waves, evaluate its stop conditions without emitting a new plan or changing assignment ids; if the plan becomes invalid, stop delegation and report degradation. Never claim an agent ran unless the native host tool ran and was joined.
"""
SUPPORTED_EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "item.started",
    "item.updated",
    "item.completed",
    "agent.lifecycle",
    "error",
}


class LiveEvaluationError(ValueError):
    """Raised when a live evaluation cannot produce trustworthy receipts."""


def controller_directive(mode: str) -> dict[str, str]:
    if mode not in CONTROLLER_MODES:
        raise LiveEvaluationError(f"invalid controller mode: {mode}")
    behavior = (
        "Do not spawn, delegate, or call any agent tool. Complete the task in the parent."
        if mode == "forced-solo"
        else "For non-trivial work, consult and execute the Cognitive Powers orchestration policy; obey its solo/delegation decision and use native agent tools when it delegates."
    )
    text = CONTROLLER_DIRECTIVE_TEMPLATE.format(
        version=CONTROLLER_DIRECTIVE_VERSION, mode=mode, behavior=behavior
    )
    return {
        "version": str(CONTROLLER_DIRECTIVE_VERSION),
        "mode": mode,
        "text": text,
        "template_sha256": hashlib.sha256(
            CONTROLLER_DIRECTIVE_TEMPLATE.encode("utf-8")
        ).hexdigest(),
        "mode_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def compose_controller_prompt(
    base_prompt: str, mode: str
) -> tuple[str, dict[str, str]]:
    directive = controller_directive(mode)
    return base_prompt.rstrip() + "\n\n" + directive["text"], directive


def rollout_snapshot(home: Path) -> dict[str, str]:
    sessions = home / "sessions"
    if not sessions.exists():
        return {}
    return {
        str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sessions.rglob("*.jsonl")
        if path.is_file()
    }


def _rollout_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise LiveEvaluationError(f"rollout is not valid JSONL: {path}") from error
        if not isinstance(row, dict):
            raise LiveEvaluationError(f"rollout row is not an object: {path}")
        rows.append(row)
    return rows


def _final_rollout_usage(rows: Sequence[dict[str, Any]], path: Path) -> dict[str, int]:
    usages = [
        row.get("payload", {}).get("info", {}).get("total_token_usage")
        for row in rows
        if row.get("type") == "event_msg"
        and row.get("payload", {}).get("type") == "token_count"
    ]
    usage = usages[-1] if usages else None
    if not isinstance(usage, dict) or any(
        not isinstance(usage.get(field), int) or usage[field] < 0
        for field in ("input_tokens", "cached_input_tokens", "output_tokens")
    ):
        raise LiveEvaluationError(f"rollout lacks final provider usage: {path}")
    return {
        "input_tokens": usage["input_tokens"],
        "cached_input_tokens": usage["cached_input_tokens"],
        "output_tokens": usage["output_tokens"],
    }


def parse_new_rollouts(
    home: Path, before: Mapping[str, str], parent_thread_id: str
) -> dict[str, Any]:
    after = rollout_snapshot(home)
    modified = [path for path, digest in before.items() if after.get(path) != digest]
    if modified:
        raise LiveEvaluationError("pre-existing rollout files changed during session")
    new_paths = [Path(path) for path in after if path not in before]
    documents: list[dict[str, Any]] = []
    for path in new_paths:
        rows = _rollout_rows(path)
        if (
            not rows
            or rows[0].get("type") != "session_meta"
            or not isinstance(rows[0].get("payload"), dict)
        ):
            raise LiveEvaluationError(f"rollout lacks an initial session_meta: {path}")
        # A child created with fork_turns may contain inherited parent metadata later.
        # The first row is the file owner's host-written identity.
        meta = rows[0]["payload"]
        documents.append({"path": path, "rows": rows, "meta": meta})
    parents = [
        item
        for item in documents
        if (item["meta"].get("id") or item["meta"].get("session_id"))
        == parent_thread_id
    ]
    if len(parents) != 1:
        raise LiveEvaluationError(
            "new rollouts lack exactly one matching parent thread"
        )
    parent = parents[0]
    parent_rows = parent["rows"]
    spawn_calls: dict[str, str] = {}
    wait_completed = False
    activities: dict[str, dict[str, Any]] = {}
    for row in parent_rows:
        payload = row.get("payload", {})
        if (
            row.get("type") == "response_item"
            and payload.get("type") == "function_call"
        ):
            if payload.get("name") == "spawn_agent":
                try:
                    args = json.loads(payload.get("arguments", ""))
                except json.JSONDecodeError as error:
                    raise LiveEvaluationError(
                        "spawn_agent arguments are invalid"
                    ) from error
                task_name = args.get("task_name") if isinstance(args, dict) else None
                call_id = payload.get("call_id")
                if (
                    not isinstance(task_name, str)
                    or not task_name
                    or not isinstance(call_id, str)
                ):
                    raise LiveEvaluationError("spawn_agent lacks observable task_name")
                spawn_calls[call_id] = task_name
            elif payload.get("name") == "wait_agent":
                call_id = payload.get("call_id")
                outputs = [
                    nested.get("payload", {})
                    for nested in parent_rows
                    if nested.get("type") == "response_item"
                    and nested.get("payload", {}).get("type") == "function_call_output"
                    and nested.get("payload", {}).get("call_id") == call_id
                ]
                wait_completed = wait_completed or any(
                    '"timed_out":false'
                    in str(output.get("output", "")).replace(" ", "").lower()
                    for output in outputs
                )
        if (
            row.get("type") == "event_msg"
            and payload.get("type") == "sub_agent_activity"
        ):
            if payload.get("kind") == "started" and isinstance(
                payload.get("event_id"), str
            ):
                activities[payload["event_id"]] = payload
    if set(spawn_calls) != set(activities):
        raise LiveEvaluationError("spawn calls do not match host sub-agent activity")
    children_by_id = {
        item["meta"].get("id") or item["meta"].get("session_id"): item
        for item in documents
        if item is not parent
    }
    lifecycle: list[dict[str, Any]] = []
    for call_id, task_name in spawn_calls.items():
        activity = activities[call_id]
        child_id = activity.get("agent_thread_id")
        child = children_by_id.get(child_id)
        if child is None:
            raise LiveEvaluationError("spawned child rollout is missing")
        meta = child["meta"]
        source = meta.get("source")
        spawn = (
            source.get("subagent", {}).get("thread_spawn", {})
            if isinstance(source, dict)
            else {}
        )
        if (
            meta.get("parent_thread_id") != parent_thread_id
            or spawn.get("parent_thread_id") != parent_thread_id
            or spawn.get("depth") != 1
            or meta.get("thread_source") != "subagent"
        ):
            raise LiveEvaluationError(
                "child rollout is not linked to the parent thread"
            )
        has_result = any(
            row.get("type") == "event_msg"
            and row.get("payload", {}).get("type") == "agent_message"
            for row in child["rows"]
        )
        lifecycle.append(
            {
                "assignment_id": None,
                "task_name": task_name,
                "actor_id": child_id,
                "role": None,
                "parent_id": parent_thread_id,
                "delegation_depth": 1,
                "phases": ["spawned"]
                + (["joined"] if wait_completed else [])
                + (["result"] if has_result else []),
                "usage": _final_rollout_usage(child["rows"], child["path"]),
                "binding_provenance": "persistent-rollout-v3",
                "rollout_sha256": hashlib.sha256(
                    child["path"].read_bytes()
                ).hexdigest(),
            }
        )
    unrelated_children = [
        item
        for child_id, item in children_by_id.items()
        if child_id not in {x["actor_id"] for x in lifecycle}
    ]
    if unrelated_children:
        raise LiveEvaluationError(
            "new child rollout is not linked to an observed spawn"
        )
    parent_usage = _final_rollout_usage(parent_rows, parent["path"])
    aggregate = {
        field: parent_usage[field] + sum(item["usage"][field] for item in lifecycle)
        for field in ("input_tokens", "cached_input_tokens", "output_tokens")
    }
    return {
        "schema_version": 3,
        "source": "persistent-rollouts",
        "parent_thread_id": parent_thread_id,
        "parent_rollout_sha256": hashlib.sha256(
            parent["path"].read_bytes()
        ).hexdigest(),
        "lifecycle": lifecycle,
        "parent_usage": parent_usage,
        "aggregate_usage": aggregate,
        "new_rollout_count": len(new_paths),
    }


def load_controller_protocol(path: Path) -> dict[str, str]:
    """Validate and fingerprint the frozen controller-specific experiment contract."""
    resolved = _resolved(path)
    if not resolved.is_file():
        raise LiveEvaluationError(f"controller protocol is missing: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LiveEvaluationError("controller protocol is not valid JSON") from error
    if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2, 3}:
        raise LiveEvaluationError(
            "controller protocol must use schema_version 1, 2, or 3"
        )
    comparison = payload.get("comparison")
    design = payload.get("design")
    rounds = design.get("rounds") if isinstance(design, dict) else None
    expected = (
        isinstance(comparison, dict)
        and comparison.get("control_arm", {}).get("controller_mode") == "forced-solo"
        and comparison.get("candidate_arm", {}).get("controller_mode") == "adaptive"
        and comparison.get("only_intended_difference") == "controller_mode"
        and isinstance(rounds, dict)
        and design.get("repetitions_per_fixture_per_arm") == 3
        and rounds.get("pilot", {}).get("declared_fixture_count") == 20
        and rounds.get("promotion", {}).get("declared_fixture_count") == 60
        and payload.get("contains_execution_results") is False
        and payload.get("contains_provider_evidence") is False
        and payload.get("claim_status") == "not-proven"
    )
    protocol_id = payload.get("protocol_id")
    if not expected or not isinstance(protocol_id, str) or not protocol_id:
        raise LiveEvaluationError(
            "controller protocol does not match the confirmatory design"
        )
    return {
        "protocol_id": protocol_id,
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "path": str(resolved),
        "claim_status": "not-proven",
    }


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_layout(
    fixture: Path,
    output: Path,
    baseline_home: Path,
    candidate_home: Path,
) -> tuple[Path, Path, Path, Path]:
    fixture = _resolved(fixture)
    output = _resolved(output)
    baseline_home = _resolved(baseline_home)
    candidate_home = _resolved(candidate_home)
    if not fixture.is_dir():
        raise LiveEvaluationError(f"fixture is not a directory: {fixture}")
    for label, home in (("baseline", baseline_home), ("candidate", candidate_home)):
        if not home.is_dir():
            raise LiveEvaluationError(f"{label} CODEX_HOME is not a directory: {home}")
    if baseline_home == candidate_home:
        raise LiveEvaluationError("baseline and candidate CODEX_HOME must differ")
    if _is_within(output, fixture) or _is_within(fixture, output):
        raise LiveEvaluationError(
            "output and source fixture must not contain each other"
        )
    for home in (baseline_home, candidate_home):
        if _is_within(output, home) or _is_within(home, output):
            raise LiveEvaluationError(
                "output and CODEX_HOME must not contain each other"
            )
    return fixture, output, baseline_home, candidate_home


def tree_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(root).as_posix()
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def source_sha256(hashes: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path, value in sorted(hashes.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def require_measured_fixture_unchanged(
    fixture: Path, measured_hashes: Mapping[str, str]
) -> None:
    if tree_hashes(fixture) != dict(measured_hashes):
        raise LiveEvaluationError("evaluators modified the measured result fixture")


def git_identity(root: Path, *, required: bool = True) -> dict[str, Any] | None:
    """Bind a fixture to Git metadata excluded from the normal tree hash."""
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    lines = completed.stdout.splitlines()
    if completed.returncode != 0 or len(lines) != 2:
        if required:
            raise LiveEvaluationError("contract-bound fixture must be a Git checkout")
        return None
    top_level = _resolved(Path(lines[0]))
    if top_level != _resolved(root):
        raise LiveEvaluationError("fixture must be the root of its Git checkout")
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        raise LiveEvaluationError("cannot read fixture Git status")
    payload = {
        "head": lines[1].strip().lower(),
        "status_sha256": hashlib.sha256(status.stdout.encode("utf-8")).hexdigest(),
    }
    payload["sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def protected_roots(
    explicit: Sequence[Path], fixture: Path, plugin_identity: dict[str, Any]
) -> list[Path]:
    candidates = [
        *explicit,
        fixture,
        Path(plugin_identity["source_root"]),
        Path(
            plugin_identity.get(
                "baseline_installed_root", plugin_identity["installed_root"]
            )
        ),
        Path(
            plugin_identity.get(
                "candidate_installed_root", plugin_identity["installed_root"]
            )
        ),
    ]
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = _resolved(candidate)
        if not resolved.is_dir():
            raise LiveEvaluationError(f"guarded root is not a directory: {resolved}")
        key = os.path.normcase(str(resolved))
        if key not in seen:
            result.append(resolved)
            seen.add(key)
    return result


def snapshot_guards(roots: Sequence[Path]) -> dict[str, dict[str, str]]:
    return {str(root): tree_hashes(root) for root in roots}


def verify_guards(
    before: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    changes: dict[str, list[str]] = {}
    for path, initial in before.items():
        final = tree_hashes(Path(path))
        changed = changed_paths(initial, final)
        receipts.append(
            {
                "path": path,
                "before_sha256": source_sha256(initial),
                "after_sha256": source_sha256(final),
                "before_file_count": len(initial),
                "after_file_count": len(final),
                "stable": not changed,
            }
        )
        if changed:
            changes[path] = changed
    if changes:
        raise LiveEvaluationError(f"guarded roots changed: {changes}")
    return receipts


def command_identity(argv: Sequence[str]) -> dict[str, Any]:
    files: dict[str, str] = {}
    for value in argv:
        path = Path(value).expanduser()
        if path.is_file():
            resolved = path.resolve()
            files[str(resolved)] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    payload = json.dumps(
        {"argv": list(argv), "files": files}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "files": files,
    }


def codex_host_identity(codex: str) -> dict[str, Any]:
    """Freeze the executable and host capabilities used by both experiment arms."""
    executable = Path(shutil.which(codex) or codex).expanduser().resolve()
    if not executable.is_file():
        raise LiveEvaluationError(f"Codex executable is unavailable: {codex}")
    completed = subprocess.run(
        [str(executable), "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    version = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0 or not version:
        raise LiveEvaluationError("Codex version preflight failed")
    features_completed = subprocess.run(
        [str(executable), "-c", "features.multi_agent=true", "features", "list"],
        check=False,
        capture_output=True,
        text=True,
    )
    feature_states: dict[str, dict[str, Any]] = {}
    for line in features_completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-1] in {"true", "false"}:
            feature_states[fields[0]] = {
                "stage": " ".join(fields[1:-1]),
                "enabled": fields[-1] == "true",
            }
    if (
        features_completed.returncode != 0
        or feature_states.get("multi_agent", {}).get("enabled") is not True
    ):
        raise LiveEvaluationError("Codex multi_agent feature preflight failed")
    return {
        "executable": str(executable),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "version": version,
        "features": {"multi_agent": True},
        "effective_features": feature_states,
        "persistent_parent_thread": True,
        "event_schema_version": 1,
        "supported_event_types": sorted(SUPPORTED_EVENT_TYPES),
    }


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )


def unexpected_changes(paths: Sequence[str], allowed: Sequence[str]) -> list[str]:
    return [
        path
        for path in paths
        if not any(fnmatch.fnmatchcase(path, rule) for rule in allowed)
    ]


def arm_order(repetitions: int, seed: str) -> list[list[str]]:
    if repetitions < 2:
        raise LiveEvaluationError("repetitions must be at least 2")
    if not seed.strip():
        raise LiveEvaluationError("seed must not be empty")
    first = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % 2
    orders: list[list[str]] = []
    for repetition in range(repetitions):
        baseline_first = (repetition + first) % 2 == 0
        orders.append(
            ["baseline", "candidate"] if baseline_first else ["candidate", "baseline"]
        )
    return orders


def _content_id_matches(value: Mapping[str, Any], field: str, prefix: str) -> bool:
    claimed = value.get(field)
    if not isinstance(claimed, str):
        return False
    unhashed = dict(value)
    unhashed.pop(field, None)
    encoded = json.dumps(
        unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return claimed == f"{prefix}-{hashlib.sha256(encoded).hexdigest()}"


def _canonical_agent_plan_v2(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("kind") != "agent_plan":
        return None
    if (
        value.get("schema_version") != 2
        or value.get("mode") not in AGENT_PLAN_MODES
        or value.get("selected_mode") != value.get("mode")
        or value.get("executed_mode") is not None
        or value.get("outcome") != "planned"
        or not isinstance(value.get("waves"), list)
        or not _content_id_matches(value, "plan_id", "plan")
    ):
        raise LiveEvaluationError("emitted agent_plan is not a canonical v2 plan")
    for wave in value["waves"]:
        if (
            not isinstance(wave, dict)
            or not isinstance(wave.get("kind"), str)
            or not isinstance(wave.get("parallel"), bool)
            or not isinstance(wave.get("assignments"), list)
        ):
            raise LiveEvaluationError("emitted agent_plan contains an invalid wave")
        for assignment in wave["assignments"]:
            if not isinstance(assignment, dict) or not _content_id_matches(
                assignment, "assignment_id", "assignment"
            ):
                raise LiveEvaluationError(
                    "emitted agent_plan contains a non-canonical assignment"
                )
    return value


def _canonical_plan_transition(plans: Sequence[dict[str, Any]]) -> str | None:
    """Accept only a solo implementation phase followed by fresh verification."""
    if len(plans) <= 1:
        return None
    if len(plans) != 2:
        raise LiveEvaluationError("multiple distinct agent_plan receipts were emitted")

    initial, verification = plans
    initial_is_solo = (
        initial.get("valid_input") is True
        and initial.get("mode") == "solo"
        and initial.get("spawn_count") == 0
        and initial.get("total_planned_agents") == 0
        and initial.get("max_depth") == 0
        and initial.get("waves") == []
    )
    waves = verification.get("waves")
    verification_wave = (
        waves[0] if isinstance(waves, list) and len(waves) == 1 else None
    )
    assignments = (
        verification_wave.get("assignments")
        if isinstance(verification_wave, dict)
        else None
    )
    verifier = (
        assignments[0]
        if isinstance(assignments, list) and len(assignments) == 1
        else None
    )
    verification_is_fresh_read_only = (
        verification.get("valid_input") is True
        and verification.get("mode") == "staged-verify"
        and verification.get("spawn_count") == 0
        and verification.get("total_planned_agents") == 1
        and verification.get("max_depth") == 1
        and isinstance(verification_wave, dict)
        and verification_wave.get("kind") == "verification"
        and verification_wave.get("parallel") is False
        and isinstance(verifier, dict)
        and verifier.get("role") == "verifier"
        and verifier.get("permissions") == "read-only"
        and verifier.get("ownership") == []
        and verifier.get("dependencies") == []
        and verifier.get("delegation_depth") == 1
        and verifier.get("may_spawn") is False
        and verifier.get("may_verify_parent") is False
    )
    if not initial_is_solo or not verification_is_fresh_read_only:
        raise LiveEvaluationError("multiple distinct agent_plan receipts were emitted")
    return "solo-to-fresh-verification"


def _agent_message_json_values(item: Mapping[str, Any]) -> list[object]:
    if item.get("type") != "agent_message":
        return []
    texts: list[str] = []
    for field in ("text", "message", "output"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            texts.append(value)
    content = item.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str) and part.strip():
                texts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text)

    values: list[object] = []
    for text in texts:
        stripped = text.strip()
        payloads: list[str] = []
        if stripped.startswith("{"):
            payloads.append(stripped)
        payloads.extend(
            match.group(1).strip()
            for match in re.finditer(
                r"```(?:json)?\s*\r?\n?(.*?)```", text, re.IGNORECASE | re.DOTALL
            )
        )
        for payload in payloads:
            try:
                values.append(json.loads(payload))
            except json.JSONDecodeError as error:
                if "agent_plan" in payload:
                    raise LiveEvaluationError(
                        "emitted agent_plan JSON is malformed"
                    ) from error
    return values


def parse_events(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    invalid_lines = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
    unknown_types = sorted(
        {
            str(row.get("type"))
            for row in rows
            if row.get("type") not in SUPPORTED_EVENT_TYPES
        }
    )
    if unknown_types:
        raise LiveEvaluationError(
            f"{path} contains unsupported event schema types: "
            + ", ".join(unknown_types)
        )
    completed = [row for row in rows if row.get("type") == "turn.completed"]
    if invalid_lines:
        raise LiveEvaluationError(f"{path} contains {invalid_lines} non-JSON lines")
    if len(completed) != 1 or not isinstance(completed[0].get("usage"), dict):
        raise LiveEvaluationError(
            f"{path} must contain exactly one completed turn with usage"
        )
    usage = completed[0]["usage"]
    required = ("input_tokens", "cached_input_tokens", "output_tokens")
    if any(
        not isinstance(usage.get(field), int) or usage[field] < 0 for field in required
    ):
        raise LiveEvaluationError(f"{path} contains invalid provider usage")
    if usage["cached_input_tokens"] > usage["input_tokens"]:
        raise LiveEvaluationError(f"{path} cached input exceeds input tokens")
    item_types = [
        row.get("item", {}).get("type")
        for row in rows
        if row.get("type") == "item.completed" and isinstance(row.get("item"), dict)
    ]
    tool_calls = sum(
        item_type not in {None, "agent_message", "reasoning"}
        for item_type in item_types
    )
    tool_names: list[str] = []
    agent_events: list[dict[str, str]] = []
    for row in rows:
        item = row.get("item")
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("tool_name")
        if isinstance(name, str) and name:
            tool_names.append(name)
            if name in {
                "spawn_agent",
                "wait_agent",
                "send_message",
                "followup_task",
                "interrupt_agent",
            }:
                agent_events.append(
                    {"event": name, "item_type": str(item.get("type", "unknown"))}
                )
    plan_receipts: list[dict[str, Any]] = []
    lifecycle: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("type") != "agent.lifecycle" or row.get("provenance") != "host":
            continue
        if all(
            isinstance(row.get(field), str) and row.get(field)
            for field in ("assignment_id", "actor_id", "role")
        ):
            assignment_id = row["assignment_id"]
            observation = lifecycle.setdefault(
                assignment_id,
                {
                    "assignment_id": assignment_id,
                    "actor_id": row["actor_id"],
                    "role": row["role"],
                    "phases": [],
                    "usage": None,
                    "parent_id": None,
                    "delegation_depth": None,
                },
            )
            if (
                observation["actor_id"] != row["actor_id"]
                or observation["role"] != row["role"]
            ):
                raise LiveEvaluationError(
                    f"{path} rebinds host lifecycle identity for {assignment_id}"
                )
            parent_id = (
                row.get("parent_id")
                or row.get("parent_actor_id")
                or row.get("parent_thread_id")
            )
            if parent_id is not None:
                if not isinstance(parent_id, str) or not parent_id:
                    raise LiveEvaluationError(
                        f"{path} contains invalid lifecycle parent for {assignment_id}"
                    )
                if observation["parent_id"] not in {None, parent_id}:
                    raise LiveEvaluationError(
                        f"{path} rebinds lifecycle parent for {assignment_id}"
                    )
                observation["parent_id"] = parent_id
            depth = row.get("delegation_depth")
            if depth is not None:
                if not isinstance(depth, int) or isinstance(depth, bool) or depth < 1:
                    raise LiveEvaluationError(
                        f"{path} contains invalid lifecycle depth for {assignment_id}"
                    )
                if observation["delegation_depth"] not in {None, depth}:
                    raise LiveEvaluationError(
                        f"{path} rebinds lifecycle depth for {assignment_id}"
                    )
                observation["delegation_depth"] = depth
            phase = row.get("event") or row.get("phase") or row.get("status")
            if isinstance(phase, str) and phase:
                normalized_phase = {
                    "started": "spawned",
                    "created": "spawned",
                    "completed": "result",
                    "failed": "result",
                    "blocked": "result",
                    "confirmed": "result",
                    "rejected": "result",
                    "inconclusive": "result",
                }.get(phase, phase)
                if normalized_phase not in {"spawned", "joined", "result"}:
                    raise LiveEvaluationError(
                        f"{path} contains unsupported agent lifecycle phase: {phase}"
                    )
                if normalized_phase not in observation["phases"]:
                    observation["phases"].append(normalized_phase)
            descendant_usage = row.get("usage")
            if descendant_usage is not None:
                if not isinstance(descendant_usage, dict) or any(
                    not isinstance(descendant_usage.get(field), int)
                    or descendant_usage[field] < 0
                    for field in ("input_tokens", "output_tokens")
                ):
                    raise LiveEvaluationError(
                        f"{path} contains invalid descendant usage for {assignment_id}"
                    )
                cached = descendant_usage.get("cached_input_tokens", 0)
                if (
                    not isinstance(cached, int)
                    or cached < 0
                    or cached > descendant_usage["input_tokens"]
                ):
                    raise LiveEvaluationError(
                        f"{path} contains invalid descendant cache usage for {assignment_id}"
                    )
                observation["usage"] = {
                    "input_tokens": descendant_usage["input_tokens"],
                    "cached_input_tokens": cached,
                    "output_tokens": descendant_usage["output_tokens"],
                }

    for row in rows:
        if row.get("type") != "item.completed" or not isinstance(row.get("item"), dict):
            continue
        for emitted in _agent_message_json_values(row["item"]):
            if isinstance(emitted, dict) and "agent_plan" in emitted:
                candidate = emitted["agent_plan"]
                if not isinstance(candidate, dict):
                    raise LiveEvaluationError("emitted agent_plan must be an object")
            else:
                candidate = emitted
            canonical = _canonical_agent_plan_v2(candidate)
            if canonical is None:
                continue
            encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
            if all(
                json.dumps(item, sort_keys=True, separators=(",", ":")) != encoded
                for item in plan_receipts
            ):
                plan_receipts.append(canonical)
    plan_transition = _canonical_plan_transition(plan_receipts)
    thread_rows = [row for row in rows if row.get("type") == "thread.started"]
    thread_ids: set[str] = set()
    for row in thread_rows:
        nested_thread = row.get("thread")
        thread_id = row.get("thread_id") or (
            nested_thread.get("id") if isinstance(nested_thread, dict) else None
        )
        if isinstance(thread_id, str) and thread_id:
            thread_ids.add(thread_id)
    host_errors: list[str] = []
    for row in rows:
        if row.get("type") == "error":
            encoded = json.dumps(row, sort_keys=True, default=str)
            host_errors.append(encoded[-1000:])
        elif "no thread with id" in json.dumps(row, default=str).casefold():
            host_errors.append("no thread with id")
    if len(thread_ids) > 1:
        raise LiveEvaluationError(f"{path} contains multiple parent thread identities")
    observations = list(lifecycle.values())
    return {
        "usage": dict(usage),
        "tool_calls": tool_calls,
        "turns": len(completed),
        "tool_names": tool_names,
        "agent_events": agent_events,
        "agent_spawns": sum(item["event"] == "spawn_agent" for item in agent_events),
        "agent_joins": sum(item["event"] == "wait_agent" for item in agent_events),
        "agent_plans": plan_receipts,
        "plan_transition": plan_transition,
        "plan_receipt_count": len(plan_receipts),
        "observed_assignments": [
            {key: item[key] for key in ("assignment_id", "actor_id", "role")}
            for item in observations
        ],
        "agent_lifecycle": observations,
        "parent_thread_id": next(iter(thread_ids), None),
        "event_schema_version": 1,
        "event_types": sorted({str(row.get("type")) for row in rows}),
        "host_errors": host_errors,
        "usage_includes_subagents": (
            usage.get("includes_subagents") is True
            or usage.get("scope") in {"task-tree", "all-agents", "aggregate"}
        ),
        "invalid_json_lines": invalid_lines,
    }


def _execution_semantics(
    observed_plan: dict[str, Any] | None, lifecycle: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Bind host actors to the exact planned wave, role, dependencies and authority."""
    if observed_plan is None:
        return [], [], not lifecycle
    waves = observed_plan.get("waves")
    if not isinstance(waves, list):
        return [], [], False
    planned: list[dict[str, Any]] = []
    prior_unit_ids: set[str] = set()
    valid = True
    for wave_index, wave in enumerate(waves):
        if not isinstance(wave, dict) or not isinstance(wave.get("assignments"), list):
            return [], [], False
        wave_kind = wave.get("kind")
        wave_parallel = wave.get("parallel")
        if not isinstance(wave_kind, str) or not isinstance(wave_parallel, bool):
            valid = False
        wave_unit_ids: set[str] = set()
        for assignment in wave["assignments"]:
            if not isinstance(assignment, dict):
                valid = False
                continue
            assignment_id = assignment.get("assignment_id")
            unit_id = assignment.get("id")
            role = assignment.get("role")
            permissions = assignment.get("permissions")
            ownership = assignment.get("ownership")
            dependencies = assignment.get("dependencies")
            depth = assignment.get("delegation_depth")
            record = {
                "assignment_id": assignment_id,
                "unit_id": unit_id,
                "role": role,
                "wave_index": wave_index,
                "wave_kind": wave_kind,
                "wave_parallel": wave_parallel,
                "dependencies": dependencies,
                "ownership": ownership,
                "permissions": permissions,
                "delegation_depth": depth,
                "may_spawn": assignment.get("may_spawn"),
                "may_verify_parent": assignment.get("may_verify_parent"),
                "must_be_distinct_from": assignment.get("must_be_distinct_from", []),
            }
            planned.append(record)
            if (
                not isinstance(assignment_id, str)
                or not assignment_id
                or not isinstance(unit_id, str)
                or not unit_id
                or not isinstance(role, str)
                or permissions not in {"read-only", "write-owned-paths"}
                or not isinstance(ownership, list)
                or not all(isinstance(path, str) and path for path in ownership)
                or not isinstance(dependencies, list)
                or not all(isinstance(item, str) and item for item in dependencies)
                or not isinstance(depth, int)
                or isinstance(depth, bool)
                or depth < 1
                or assignment.get("may_spawn") is not False
                or assignment.get("may_verify_parent") is not False
                or not set(dependencies).issubset(prior_unit_ids)
                or (permissions == "write-owned-paths" and not ownership)
            ):
                valid = False
            wave_unit_ids.add(unit_id) if isinstance(unit_id, str) else None
        prior_unit_ids.update(wave_unit_ids)

    assignment_ids = [item["assignment_id"] for item in planned]
    unit_ids = [item["unit_id"] for item in planned]
    if len(assignment_ids) != len(set(assignment_ids)) or len(unit_ids) != len(
        set(unit_ids)
    ):
        valid = False
    write_paths: list[tuple[str, str]] = []
    for item in planned:
        if item["permissions"] != "write-owned-paths":
            continue
        for path in item["ownership"]:
            normalized = path.replace("\\", "/").strip("/").casefold()
            if any(
                other_id != item["assignment_id"]
                and (
                    normalized == other
                    or normalized.startswith(other + "/")
                    or other.startswith(normalized + "/")
                )
                for other_id, other in write_paths
            ):
                valid = False
            write_paths.append((item["assignment_id"], normalized))

    lifecycle_by_id = {item.get("assignment_id"): item for item in lifecycle}
    bindings: list[dict[str, Any]] = []
    actor_ids: list[str] = []
    for item in planned:
        observed = lifecycle_by_id.get(item["assignment_id"])
        if not isinstance(observed, dict):
            valid = False
            continue
        binding = {
            "assignment_id": item["assignment_id"],
            "actor_id": observed.get("actor_id"),
            "role_observed": observed.get("role"),
            "parent_id": observed.get("parent_id"),
            "delegation_depth": observed.get("delegation_depth"),
            "task_name": observed.get("task_name"),
            "binding_provenance": observed.get("binding_provenance"),
        }
        bindings.append(binding)
        if (
            (
                binding["role_observed"] is not None
                and binding["role_observed"] != item["role"]
            )
            or not isinstance(binding["actor_id"], str)
            or not binding["actor_id"]
            or not isinstance(binding["parent_id"], str)
            or not binding["parent_id"]
            or binding["delegation_depth"] != item["delegation_depth"]
        ):
            valid = False
        actor_ids.append(binding["actor_id"])
    if len(actor_ids) != len(set(actor_ids)):
        valid = False

    mode = observed_plan.get("mode")
    verifier_items = [item for item in planned if item["role"] == "verifier"]
    write_items = [
        item for item in planned if item["permissions"] == "write-owned-paths"
    ]
    if mode == "parallel-read-only":
        valid = valid and len(planned) >= 2 and not write_items and not verifier_items
    elif mode == "parallel-packets":
        implementation_waves = {
            item["wave_index"] for item in write_items if item["wave_parallel"] is True
        }
        valid = valid and len(write_items) >= 2 and len(implementation_waves) == 1
    elif mode == "staged-verify":
        verifier_only = (
            len(planned) == 1
            and len(verifier_items) == 1
            and verifier_items[0]["wave_kind"] == "verification"
            and verifier_items[0]["wave_index"] == 0
            and verifier_items[0]["dependencies"] == []
            and verifier_items[0]["permissions"] == "read-only"
            and verifier_items[0]["ownership"] == []
        )
        verifier_after_writes = (
            bool(write_items)
            and len(verifier_items) == 1
            and verifier_items[0]["wave_kind"] == "verification"
            and verifier_items[0]["wave_index"]
            > max(item["wave_index"] for item in write_items)
            and set(verifier_items[0]["dependencies"])
            == {item["unit_id"] for item in write_items}
        )
        valid = valid and (verifier_only or verifier_after_writes)
    elif mode == "solo":
        valid = valid and not planned and not lifecycle
    else:
        valid = False
    return planned, bindings, bool(valid)


def _normalized_task_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _bind_rollout_assignments(
    observed_plan: dict[str, Any] | None, lifecycle: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    bound = [dict(item) for item in lifecycle]
    if observed_plan is None:
        return bound
    aliases: dict[str, str] = {}
    for wave in observed_plan.get("waves", []):
        if not isinstance(wave, dict):
            continue
        for assignment in wave.get("assignments", []):
            if not isinstance(assignment, dict):
                continue
            assignment_id = assignment.get("assignment_id")
            for alias in (assignment.get("id"), assignment_id):
                if isinstance(alias, str) and isinstance(assignment_id, str):
                    normalized = _normalized_task_name(alias)
                    if normalized in aliases and aliases[normalized] != assignment_id:
                        aliases[normalized] = ""
                    else:
                        aliases[normalized] = assignment_id
    for item in bound:
        task_name = item.get("task_name")
        if isinstance(task_name, str):
            item["assignment_id"] = aliases.get(_normalized_task_name(task_name))
    return bound


def classify_agent_decision(
    parsed: dict[str, Any], controller_mode: str
) -> dict[str, Any]:
    """Classify an explicit plan or the focused no-agent fast path."""
    observed_plan = parsed["agent_plans"][-1] if parsed["agent_plans"] else None
    implicit_solo = (
        observed_plan is None
        and parsed["agent_spawns"] == 0
        and not parsed.get("agent_lifecycle", [])
    )
    selected_mode = (
        observed_plan.get("mode")
        if observed_plan
        else ("solo" if implicit_solo else None)
    )
    lifecycle = _bind_rollout_assignments(
        observed_plan, parsed.get("agent_lifecycle", [])
    )
    planned, lifecycle_bindings, semantic_binding = _execution_semantics(
        observed_plan, lifecycle
    )
    planned_ids = [item.get("assignment_id") for item in planned]
    valid_plan_ids = all(
        isinstance(item, str) and item for item in planned_ids
    ) and len(planned_ids) == len(set(planned_ids))
    lifecycle_with_invalid_ids = [
        item
        for item in lifecycle
        if any(
            phase in item.get("phases", []) for phase in ("spawned", "joined", "result")
        )
        and not (isinstance(item.get("assignment_id"), str) and item["assignment_id"])
    ]

    def phase_assignment_ids(phase: str) -> list[str]:
        return sorted(
            item["assignment_id"]
            for item in lifecycle
            if phase in item.get("phases", [])
            and isinstance(item.get("assignment_id"), str)
            and item["assignment_id"]
        )

    spawned_ids = phase_assignment_ids("spawned")
    joined_ids = phase_assignment_ids("joined")
    result_ids = phase_assignment_ids("result")
    usage_by_assignment = {
        item["assignment_id"]: item.get("usage")
        for item in lifecycle
        if item.get("usage") is not None
        and isinstance(item.get("assignment_id"), str)
        and item["assignment_id"]
    }
    exact_lifecycle = (
        valid_plan_ids
        and not lifecycle_with_invalid_ids
        and sorted(planned_ids) == spawned_ids == joined_ids == result_ids
        and set(usage_by_assignment) == set(planned_ids)
        and semantic_binding
    )
    host_persistent = bool(parsed.get("parent_thread_id")) and not parsed.get(
        "host_errors"
    )
    explicit_plan_complete = observed_plan is not None and (
        selected_mode == "solo"
        and not planned
        and not lifecycle
        and host_persistent
        or selected_mode != "solo"
        and bool(planned)
        and exact_lifecycle
        and parsed["usage_includes_subagents"]
        and host_persistent
    )
    complete = (
        parsed["agent_spawns"] == 0 and selected_mode == "solo" and host_persistent
        if controller_mode == "forced-solo"
        else (implicit_solo and host_persistent) or explicit_plan_complete
    )
    executed_mode = selected_mode if complete else "solo" if not spawned_ids else None
    outcome = (
        "completed"
        if complete and selected_mode == executed_mode
        else "degraded"
        if selected_mode in AGENT_PLAN_MODES
        else "invalid"
    )
    plan_sha256 = (
        hashlib.sha256(
            json.dumps(observed_plan, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if observed_plan is not None
        else None
    )
    execution_receipt = {
        "schema_version": 3,
        "plan_sha256": plan_sha256,
        "selected_mode": selected_mode,
        "executed_mode": executed_mode,
        "outcome": outcome,
        "parent_thread_id": parsed.get("parent_thread_id"),
        "planned_assignment_ids": planned_ids,
        "planned_assignments": planned,
        "lifecycle_bindings": lifecycle_bindings,
        "semantic_binding": semantic_binding,
        "spawned_assignment_ids": spawned_ids,
        "joined_assignment_ids": joined_ids,
        "result_assignment_ids": result_ids,
        "invalid_lifecycle_assignment_count": len(lifecycle_with_invalid_ids),
        "descendant_usage": usage_by_assignment,
        "descendant_total_tokens": sum(
            item["input_tokens"] + item["output_tokens"]
            for item in usage_by_assignment.values()
        ),
        "host_errors": list(parsed.get("host_errors", [])),
        "complete": complete,
    }
    return {
        "observed_plan": observed_plan,
        "selected_mode": selected_mode,
        "executed_mode": executed_mode,
        "actual_mode": executed_mode,
        "outcome": outcome,
        "decision_observation": (
            "explicit-agent-plan"
            if observed_plan is not None
            else "implicit-solo-no-agent-events"
            if implicit_solo
            else "missing"
        ),
        "planned_assignment_count": len(planned),
        "agent_execution_receipt": execution_receipt,
        "complete": complete,
    }


def _plugin_list(codex: str, home: Path) -> list[dict[str, Any]]:
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(home)
    completed = subprocess.run(
        [codex, "plugin", "list", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        raise LiveEvaluationError(
            f"plugin preflight failed for {home}: {(completed.stderr or completed.stdout)[-500:]}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise LiveEvaluationError(
            f"plugin preflight returned invalid JSON for {home}"
        ) from error
    installed = payload.get("installed")
    if not isinstance(installed, list) or not all(
        isinstance(item, dict) for item in installed
    ):
        raise LiveEvaluationError(
            f"plugin preflight returned invalid installed list for {home}"
        )
    return installed


def _candidate_identity(item: dict[str, Any], candidate_home: Path) -> dict[str, Any]:
    version = item.get("version")
    name = item.get("name")
    marketplace = item.get("marketplaceName")
    source = item.get("source")
    source_path = source.get("path") if isinstance(source, dict) else None
    if not all(
        isinstance(value, str) and value
        for value in (version, name, marketplace, source_path)
    ):
        raise LiveEvaluationError("candidate plugin identity is incomplete")
    source_root = _resolved(Path(source_path))
    installed_root = (
        candidate_home / "plugins" / "cache" / marketplace / name / version
    ).resolve()
    if not source_root.is_dir() or not installed_root.is_dir():
        raise LiveEvaluationError(
            "candidate source or installed plugin directory is missing"
        )
    source_hashes = tree_hashes(source_root)
    source_git = git_identity(source_root, required=True)
    installed_hashes = tree_hashes(installed_root)
    canonical_surface = {
        path: digest
        for path, digest in source_hashes.items()
        if path in INSTALLED_SURFACE_FILES
        or any(
            path == directory or path.startswith(f"{directory}/")
            for directory in INSTALLED_SURFACE_DIRECTORIES
        )
    }
    missing_roots = [
        root
        for root in (*INSTALLED_SURFACE_DIRECTORIES, *INSTALLED_SURFACE_FILES)
        if root not in canonical_surface
        and not any(path.startswith(f"{root}/") for path in canonical_surface)
    ]
    if missing_roots:
        raise LiveEvaluationError(
            "candidate source lacks canonical runtime surface: "
            + ", ".join(missing_roots)
        )
    if installed_hashes not in (source_hashes, canonical_surface):
        changed = changed_paths(canonical_surface, installed_hashes)
        raise LiveEvaluationError(
            "candidate installation differs from canonical runtime surface: "
            + ", ".join(changed[:20])
        )
    source_identity = source_sha256(source_hashes)
    installed_identity = source_sha256(installed_hashes)
    return {
        "version": version,
        "source_root": str(source_root),
        "installed_root": str(installed_root),
        "source_sha256": source_identity,
        "installed_sha256": installed_identity,
        "source_file_count": len(source_hashes),
        "file_count": len(installed_hashes),
        "source_commit": source_git["head"],
        "source_git": source_git,
    }


def validate_arm_plugins(
    codex: str, baseline_home: Path, candidate_home: Path
) -> dict[str, Any]:
    baseline = _plugin_list(codex, baseline_home)
    candidate = _plugin_list(codex, candidate_home)

    def cognitive(items: list[dict[str, Any]], label: str) -> dict[str, Any]:
        matches = [
            item
            for item in items
            if item.get("pluginId") == "cognitive-powers@personal"
            and item.get("installed") is True
            and item.get("enabled") is True
        ]
        if len(matches) != 1:
            raise LiveEvaluationError(
                f"{label} CODEX_HOME must contain one enabled Cognitive Powers"
            )
        return matches[0]

    baseline_cognitive = cognitive(baseline, "baseline")
    candidate_cognitive = cognitive(candidate, "candidate")
    baseline_ids = sorted(item.get("pluginId") for item in baseline)
    candidate_ids = sorted(item.get("pluginId") for item in candidate)
    if baseline_ids != candidate_ids:
        raise LiveEvaluationError("arms contain different plugin sets")
    baseline_identity = _candidate_identity(baseline_cognitive, baseline_home)
    candidate_identity = _candidate_identity(candidate_cognitive, candidate_home)
    comparable = {
        key: baseline_identity[key]
        for key in (
            "version",
            "source_sha256",
            "installed_sha256",
            "file_count",
            "source_commit",
            "source_git",
        )
    }
    if comparable != {
        key: candidate_identity[key]
        for key in (
            "version",
            "source_sha256",
            "installed_sha256",
            "file_count",
            "source_commit",
            "source_git",
        )
    }:
        raise LiveEvaluationError("Cognitive Powers differs between experiment arms")
    return {
        **candidate_identity,
        "baseline_installed_root": baseline_identity["installed_root"],
        "candidate_installed_root": candidate_identity["installed_root"],
    }


def load_task_binding(
    path: Path,
    *,
    task_id: str,
    prompt: str,
    repetitions: int,
    seed: str,
    batch_repetition: int | None = None,
) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LiveEvaluationError(f"cannot read task contract: {error}") from error
    try:
        contract = validate_task_contract(raw)
    except ValueError as error:
        raise LiveEvaluationError(f"invalid task contract: {error}") from error
    task = contract["tasks"].get(task_id)
    if task is None:
        raise LiveEvaluationError(f"task contract does not contain {task_id}")
    round_value = contract["rounds"][task["split"]]
    expected = {
        "prompt": task["prompt"],
        "repetitions": round_value["repetitions_per_task"],
        "seed": round_value["arm_order"]["seed"],
    }
    actual = {"prompt": prompt, "repetitions": repetitions, "seed": seed}
    if batch_repetition is None:
        mismatches = [field for field in expected if expected[field] != actual[field]]
    else:
        mismatches = [
            field for field in ("prompt", "seed") if expected[field] != actual[field]
        ]
        if repetitions != 1:
            mismatches.append("repetitions")
        if batch_repetition < 1 or batch_repetition > expected["repetitions"]:
            mismatches.append("batch_repetition")
    if mismatches:
        raise LiveEvaluationError(
            "task run does not match frozen contract: " + ", ".join(mismatches)
        )
    result = {
        "task_set_id": contract["task_set_id"],
        "task_version": task["version"],
        "split": task["split"],
        "fixture_id": task["fixture_id"],
        "randomization_seed": expected["seed"],
    }
    if batch_repetition is not None:
        result.update(
            {
                "batch_repetition": batch_repetition,
                "declared_repetitions": expected["repetitions"],
                "batch_arm_order": arm_order(
                    expected["repetitions"], f"{expected['seed']}\0{task_id}"
                )[batch_repetition - 1],
            }
        )
    return result


def _replace_fixture(argv: Sequence[str], fixture: Path) -> list[str]:
    return [value.replace("{fixture}", str(fixture)) for value in argv]


def normalize_quality_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LiveEvaluationError("quality check must return one JSON object")
    score = value.get("score")
    evidence = value.get("evidence")
    critical = value.get("critical_errors")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not 0 <= score <= 100
    ):
        raise LiveEvaluationError("quality score must be a number from 0 to 100")
    if (
        not isinstance(evidence, list)
        or not evidence
        or not all(isinstance(item, str) and item for item in evidence)
    ):
        raise LiveEvaluationError("quality evidence must be a non-empty string list")
    if not isinstance(critical, list) or not all(
        isinstance(item, str) and item for item in critical
    ):
        raise LiveEvaluationError("quality critical_errors must be a string list")
    return {
        "score": float(score),
        "evidence": list(evidence),
        "critical_errors": list(critical),
    }


def aggregate_results(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def metric_value(result: dict[str, Any], field: str) -> float:
        if field == "cached_input_tokens" and field not in result:
            return max(
                0,
                result["total_tokens"]
                - result["output_tokens"]
                - result["fresh_input_tokens"],
            )
        return result[field]

    by_repetition: dict[int, dict[str, dict[str, Any]]] = {}
    for result in results:
        repetition = result.get("repetition")
        variant = result.get("variant")
        if not isinstance(repetition, int) or variant not in {"baseline", "candidate"}:
            raise LiveEvaluationError(
                "results contain an invalid repetition or variant"
            )
        variants = by_repetition.setdefault(repetition, {})
        if variant in variants:
            raise LiveEvaluationError("results contain a duplicate arm")
        variants[variant] = result
    if not by_repetition or any(
        set(variants) != {"baseline", "candidate"}
        for variants in by_repetition.values()
    ):
        raise LiveEvaluationError("results must contain complete paired arms")

    successful = {
        repetition: variants
        for repetition, variants in by_repetition.items()
        if variants["baseline"].get("success") is True
        and variants["candidate"].get("success") is True
    }
    metrics: dict[str, Any] = {}
    for field in (
        "total_tokens",
        "cached_input_tokens",
        "fresh_input_tokens",
        "output_tokens",
        "tool_calls",
        "elapsed_seconds",
    ):
        if successful:
            baseline = statistics.median(
                metric_value(variants["baseline"], field)
                for variants in successful.values()
            )
            candidate = statistics.median(
                metric_value(variants["candidate"], field)
                for variants in successful.values()
            )
            metrics[field] = {
                "baseline_median": baseline,
                "candidate_median": candidate,
                "delta_percent": round(((candidate - baseline) / baseline) * 100, 3)
                if baseline
                else None,
            }
        else:
            metrics[field] = {
                "baseline_median": None,
                "candidate_median": None,
                "delta_percent": None,
            }
    pair_total_deltas = [
        round(
            (
                variants["candidate"]["total_tokens"]
                - variants["baseline"]["total_tokens"]
            )
            / variants["baseline"]["total_tokens"]
            * 100,
            3,
        )
        for variants in successful.values()
        if variants["baseline"]["total_tokens"]
    ]
    return {
        "pair_count": len(by_repetition),
        "successful_pair_count": len(successful),
        "failed_pair_count": len(by_repetition) - len(successful),
        "all_pairs_successful": all(
            variants[arm]["success"]
            for variants in by_repetition.values()
            for arm in ("baseline", "candidate")
        ),
        "metrics": metrics,
        "pair_total_token_delta_percent": pair_total_deltas,
        "worst_pair_total_token_delta_percent": (
            max(pair_total_deltas) if pair_total_deltas else None
        ),
    }


def build_codex_command(
    *,
    codex: str,
    fixture: Path,
    message: Path,
    prompt: str,
    model: str,
    reasoning_effort: str,
    bypass_sandbox: bool,
) -> list[str]:
    command = [
        codex,
        "exec",
        "--json",
        "--color",
        "never",
        "--skip-git-repo-check",
        "--cd",
        str(fixture),
        "--output-last-message",
        str(message),
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "-c",
        "features.multi_agent=true",
    ]
    command.extend(
        ["--dangerously-bypass-approvals-and-sandbox"]
        if bypass_sandbox
        else ["--sandbox", "workspace-write"]
    )
    command.append(prompt)
    return command


def _run_one(
    *,
    codex: str,
    home: Path,
    fixture: Path,
    artifact_prefix: Path,
    prompt: str,
    model: str,
    reasoning_effort: str,
    controller_mode: str,
    storage_dir: Path,
    agent_slots: int,
    hidden_check: Sequence[str],
    quality_check: Sequence[str] | None,
    allowed_changes: Sequence[str],
    bypass_sandbox: bool,
    session_timeout_seconds: int,
) -> dict[str, Any]:
    if controller_mode not in CONTROLLER_MODES:
        raise LiveEvaluationError(f"invalid controller mode: {controller_mode}")
    events = Path(f"{artifact_prefix}-events.jsonl")
    stderr = Path(f"{artifact_prefix}-stderr.log")
    message = Path(f"{artifact_prefix}-message.txt")
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(home)
    environment["COGNITIVE_POWERS_CONTROLLER_MODE"] = controller_mode
    environment["COGNITIVE_POWERS_DATA"] = str(storage_dir)
    environment["COGNITIVE_POWERS_AVAILABLE_AGENT_SLOTS"] = str(agent_slots)
    effective_prompt, directive = compose_controller_prompt(prompt, controller_mode)
    rollouts_before = rollout_snapshot(home)
    command = build_codex_command(
        codex=codex,
        fixture=fixture,
        message=message,
        prompt=effective_prompt,
        model=model,
        reasoning_effort=reasoning_effort,
        bypass_sandbox=bypass_sandbox,
    )
    started = time.monotonic()
    with (
        events.open("w", encoding="utf-8", newline="\n") as stream,
        stderr.open("w", encoding="utf-8", newline="\n") as error_stream,
    ):
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=stream,
                stderr=error_stream,
                text=True,
                env=environment,
                timeout=session_timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise LiveEvaluationError(
                f"Codex session exceeded {session_timeout_seconds} seconds"
            ) from error
    elapsed = time.monotonic() - started
    parsed = parse_events(events)
    rollout_telemetry = parse_new_rollouts(
        home, rollouts_before, parsed["parent_thread_id"]
    )
    parsed["agent_lifecycle"] = rollout_telemetry["lifecycle"]
    parsed["observed_assignments"] = [
        {
            "assignment_id": item.get("assignment_id"),
            "actor_id": item["actor_id"],
            "role": item.get("role"),
            "task_name": item["task_name"],
            "provenance": item["binding_provenance"],
        }
        for item in rollout_telemetry["lifecycle"]
    ]
    parsed["usage"] = rollout_telemetry["aggregate_usage"]
    parsed["usage_includes_subagents"] = bool(rollout_telemetry["lifecycle"])
    if not message.is_file() or not message.read_text(encoding="utf-8").strip():
        raise LiveEvaluationError(f"missing final message for {artifact_prefix.name}")
    before_path = Path(f"{artifact_prefix}-initial-hashes.json")
    initial = json.loads(before_path.read_text(encoding="utf-8"))
    changed = changed_paths(initial, tree_hashes(fixture))
    out_of_scope = unexpected_changes(changed, allowed_changes)

    hidden_fixture = Path(f"{artifact_prefix}-hidden-fixture")
    quality_fixture = Path(f"{artifact_prefix}-quality-fixture")
    shutil.copytree(fixture, hidden_fixture)
    shutil.copytree(fixture, quality_fixture)
    measured_hashes = tree_hashes(fixture)
    pre_evaluation_diff = {
        path: measured_hashes.get(path, "<deleted>") for path in changed
    }
    if (
        tree_hashes(hidden_fixture) != measured_hashes
        or tree_hashes(quality_fixture) != measured_hashes
    ):
        raise LiveEvaluationError(
            "evaluator fixture clone differs from measured result"
        )
    try:
        hidden = subprocess.run(
            _replace_fixture(hidden_check, hidden_fixture),
            check=False,
            capture_output=True,
            text=True,
            cwd=hidden_fixture,
            timeout=session_timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise LiveEvaluationError(
            f"hidden check exceeded {session_timeout_seconds} seconds"
        ) from error
    quality = {
        "score": 100.0 if hidden.returncode == 0 and not out_of_scope else 0.0,
        "evidence": ["default binary hidden-check and scope rubric"],
        "critical_errors": [],
    }
    if quality_check is not None:
        replacements = {
            "{fixture}": str(quality_fixture),
            "{events}": str(events),
            "{message}": str(message),
            "{stderr}": str(stderr),
        }
        quality_command = []
        for value in quality_check:
            next_value = value
            for old, new in replacements.items():
                next_value = next_value.replace(old, new)
            quality_command.append(next_value)
        try:
            quality_completed = subprocess.run(
                quality_command,
                check=False,
                capture_output=True,
                text=True,
                cwd=quality_fixture,
                timeout=session_timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise LiveEvaluationError(
                f"quality check exceeded {session_timeout_seconds} seconds"
            ) from error
        if quality_completed.returncode != 0:
            raise LiveEvaluationError(
                "quality check failed: "
                + (quality_completed.stderr or quality_completed.stdout)[-1000:]
            )
        try:
            quality_raw = json.loads(quality_completed.stdout)
        except json.JSONDecodeError as error:
            raise LiveEvaluationError("quality check returned invalid JSON") from error
        quality = normalize_quality_payload(quality_raw)
    require_measured_fixture_unchanged(fixture, measured_hashes)
    critical: list[str] = []
    if completed.returncode != 0:
        critical.append(f"codex exit {completed.returncode}")
    if hidden.returncode != 0:
        critical.append(f"hidden check exit {hidden.returncode}")
    if out_of_scope:
        critical.append("out-of-scope changes: " + ", ".join(out_of_scope))
    critical.extend(quality["critical_errors"])
    usage = parsed["usage"]
    decision = classify_agent_decision(parsed, controller_mode)
    if not decision["complete"]:
        critical.append(
            "agent execution telemetry is incomplete or violates controller mode"
        )
    if decision["executed_mode"] == "parallel-read-only" and changed:
        critical.append("read-only delegation changed the workspace")
    return {
        "success": not critical,
        "critical_errors": critical,
        "codex_exit": completed.returncode,
        "hidden_exit": hidden.returncode,
        "hidden_stdout_tail": hidden.stdout[-1000:],
        "hidden_stderr_tail": hidden.stderr[-1000:],
        "changed_paths": changed,
        "pre_evaluation_diff": pre_evaluation_diff,
        "out_of_scope_changes": out_of_scope,
        "quality_score": quality["score"] / 100.0,
        "quality_evidence": quality["evidence"],
        "input_tokens": usage["input_tokens"],
        "cached_input_tokens": usage["cached_input_tokens"],
        "fresh_input_tokens": usage["input_tokens"] - usage["cached_input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["input_tokens"] + usage["output_tokens"],
        "tool_calls": parsed["tool_calls"],
        "turns": parsed["turns"],
        "retries": sum(
            name in {"followup_task", "send_message"} for name in parsed["tool_names"]
        ),
        "controller_mode": controller_mode,
        "base_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "controller_directive": {
            key: directive[key]
            for key in ("version", "mode", "template_sha256", "mode_sha256")
        },
        "agent_telemetry": {
            "schema_version": 3,
            "source": "persistent-rollouts+provider-event-stream",
            "controller_mode": controller_mode,
            "tool_names": parsed["tool_names"],
            "events": parsed["agent_events"],
            "observed_tools": sorted(set(parsed["tool_names"])),
            "event_schema_version": parsed["event_schema_version"],
            "event_types": parsed["event_types"],
            "parent_thread_id": parsed["parent_thread_id"],
            "host_errors": parsed["host_errors"],
            "spawn_count": len(
                decision["agent_execution_receipt"]["spawned_assignment_ids"]
            ),
            "join_count": len(
                decision["agent_execution_receipt"]["joined_assignment_ids"]
            ),
            "result_count": len(
                decision["agent_execution_receipt"]["result_assignment_ids"]
            ),
            "plan_receipts": parsed["agent_plans"],
            "plan_transition": parsed["plan_transition"],
            "plan_receipt_count": parsed["plan_receipt_count"],
            "observed_assignments": decision["agent_execution_receipt"][
                "lifecycle_bindings"
            ],
            "selected_mode": decision["selected_mode"],
            "executed_mode": decision["executed_mode"],
            "outcome": decision["outcome"],
            "actual_mode": decision["actual_mode"],
            "decision_observation": decision["decision_observation"],
            "planned_assignment_count": decision["planned_assignment_count"],
            "usage_includes_subagents": (
                not decision["agent_execution_receipt"]["spawned_assignment_ids"]
                or parsed["usage_includes_subagents"]
            ),
            "agent_execution_receipt": decision["agent_execution_receipt"],
            "rollout_telemetry": rollout_telemetry,
            "workspace_change_check": {
                "changed_paths": changed,
                "allowed_paths": list(allowed_changes),
                "read_only_unchanged": (
                    decision["executed_mode"] != "parallel-read-only" or not changed
                ),
                "provenance": "pre-evaluator-tree-diff",
            },
            "complete": decision["complete"],
        },
        "pre_evaluation_diff_sha256": source_sha256(pre_evaluation_diff),
        "evaluation_fixtures": {
            "hidden": str(hidden_fixture),
            "quality": str(quality_fixture),
        },
        "elapsed_seconds": round(elapsed, 3),
        "events": str(events),
        "stderr": str(stderr),
        "message": str(message),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-home", type=Path, required=True)
    parser.add_argument("--candidate-home", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-contract", type=Path)
    parser.add_argument(
        "--controller-protocol", type=Path, default=DEFAULT_CONTROLLER_PROTOCOL
    )
    parser.add_argument("--source-commit")
    parser.add_argument("--source-git-sha256")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument(
        "--baseline-controller-mode",
        default="forced-solo",
        choices=sorted(CONTROLLER_MODES),
    )
    parser.add_argument(
        "--candidate-controller-mode",
        default="adaptive",
        choices=sorted(CONTROLLER_MODES),
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--batch-repetition",
        type=int,
        help="execute one frozen repetition while retaining the full task contract",
    )
    parser.add_argument("--seed", required=True)
    parser.add_argument("--hidden-check-json", required=True)
    parser.add_argument("--quality-check-json")
    parser.add_argument("--available-tool", action="append", default=[])
    parser.add_argument("--agent-slots", type=int, default=4)
    parser.add_argument("--allow-change", action="append", default=[])
    parser.add_argument("--guard-root", type=Path, action="append", default=[])
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--bypass-sandbox", action="store_true")
    parser.add_argument("--session-timeout-seconds", type=int, default=1800)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        fixture, output, baseline_home, candidate_home = validate_layout(
            args.fixture, args.output, args.baseline_home, args.candidate_home
        )
        controller_protocol = load_controller_protocol(args.controller_protocol)
        if args.baseline_controller_mode != "forced-solo":
            raise LiveEvaluationError("baseline controller mode must be forced-solo")
        if args.candidate_controller_mode != "adaptive":
            raise LiveEvaluationError("candidate controller mode must be adaptive")
        try:
            hidden_check = json.loads(args.hidden_check_json)
        except json.JSONDecodeError as error:
            raise LiveEvaluationError(
                "hidden check must be a JSON argv array"
            ) from error
        if (
            not isinstance(hidden_check, list)
            or not hidden_check
            or not all(isinstance(value, str) and value for value in hidden_check)
        ):
            raise LiveEvaluationError(
                "hidden check must be a non-empty JSON argv array"
            )
        if not args.allow_change:
            raise LiveEvaluationError("at least one --allow-change pattern is required")
        if args.session_timeout_seconds < 1:
            raise LiveEvaluationError("session timeout must be positive")
        if args.agent_slots < 1:
            raise LiveEvaluationError("agent slots must be positive")
        binding = (
            load_task_binding(
                args.task_contract,
                task_id=args.task_id,
                prompt=args.prompt,
                repetitions=args.repetitions,
                seed=args.seed,
                batch_repetition=args.batch_repetition,
            )
            if args.task_contract is not None
            else None
        )
        quality_check = None
        if args.quality_check_json is not None:
            try:
                quality_check = json.loads(args.quality_check_json)
            except json.JSONDecodeError as error:
                raise LiveEvaluationError(
                    "quality check must be a JSON argv array"
                ) from error
            if (
                not isinstance(quality_check, list)
                or not quality_check
                or not all(isinstance(value, str) and value for value in quality_check)
            ):
                raise LiveEvaluationError(
                    "quality check must be a non-empty JSON argv array"
                )
        if binding is not None and quality_check is None:
            raise LiveEvaluationError(
                "contract-bound runs require an external quality check"
            )
        if binding is not None and not args.available_tool:
            raise LiveEvaluationError(
                "contract-bound runs require declared --available-tool values"
            )
        hidden_identity = command_identity(hidden_check)
        quality_identity = (
            command_identity(quality_check) if quality_check is not None else None
        )
        if output.exists():
            raise LiveEvaluationError(f"output already exists: {output}")
        plugin_identity = validate_arm_plugins(
            args.codex, baseline_home, candidate_home
        )
        if binding is not None and (
            args.source_commit != plugin_identity["source_commit"]
            or args.source_git_sha256 != plugin_identity["source_git"]["sha256"]
        ):
            raise LiveEvaluationError(
                "frozen source Git identity differs from the installed plugin source"
            )
        host_identity = codex_host_identity(args.codex)
        guards = protected_roots(args.guard_root, fixture, plugin_identity)
        guard_before = snapshot_guards(guards)
        plugin_version = plugin_identity["version"]
        fixture_hashes = tree_hashes(fixture)
        fixture_sha = source_sha256(fixture_hashes)
        fixture_git = git_identity(fixture, required=binding is not None)
        allowed_changes_sha256 = hashlib.sha256(
            json.dumps(sorted(args.allow_change), separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        experiment_identity = {
            "fixture_sha256": fixture_sha,
            "fixture_git_sha256": fixture_git["sha256"] if fixture_git else None,
            "plugin_sha256": plugin_identity["source_sha256"],
            "source_commit": plugin_identity["source_commit"],
            "source_git_sha256": plugin_identity["source_git"]["sha256"],
            "hidden_check_sha256": hidden_identity["sha256"],
            "quality_check_sha256": quality_identity["sha256"]
            if quality_identity
            else None,
            "allowed_changes_sha256": allowed_changes_sha256,
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "available_tools": sorted(set(args.available_tool)),
            "agent_slots": args.agent_slots,
            "prompt_sha256": hashlib.sha256(args.prompt.encode("utf-8")).hexdigest(),
            "controller_directive_version": CONTROLLER_DIRECTIVE_VERSION,
            "controller_directive_template_sha256": controller_directive("forced-solo")[
                "template_sha256"
            ],
            "controller_directive_mode_sha256": {
                mode: controller_directive(mode)["mode_sha256"]
                for mode in sorted(CONTROLLER_MODES)
            },
            "host_identity_sha256": hashlib.sha256(
                json.dumps(
                    host_identity, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
        }
        experiment_sha256 = hashlib.sha256(
            json.dumps(
                experiment_identity, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        output.mkdir(parents=True)
        artifacts = output / "artifacts"
        runs_root = output / "runs"
        homes_root = output / "homes"
        storage_root = output / "storage"
        artifacts.mkdir()
        runs_root.mkdir()
        homes_root.mkdir()
        storage_root.mkdir()
        results: list[dict[str, Any]] = []
        scheduled_repetitions = (
            [(binding["batch_repetition"], binding["batch_arm_order"])]
            if binding is not None and "batch_repetition" in binding
            else list(enumerate(arm_order(args.repetitions, args.seed), start=1))
        )
        source_homes = {"baseline": baseline_home, "candidate": candidate_home}
        controller_modes = {
            "baseline": args.baseline_controller_mode,
            "candidate": args.candidate_controller_mode,
        }
        for repetition, order in scheduled_repetitions:
            homes: dict[str, Path] = {}
            for arm in ("baseline", "candidate"):
                run_home = homes_root / f"rep{repetition}-{arm}"
                shutil.copytree(source_homes[arm], run_home)
                homes[arm] = run_home
            run_plugin_identity = validate_arm_plugins(
                args.codex, homes["baseline"], homes["candidate"]
            )
            for field in (
                "version",
                "source_sha256",
                "installed_sha256",
                "file_count",
                "source_commit",
                "source_git",
            ):
                if run_plugin_identity[field] != plugin_identity[field]:
                    raise LiveEvaluationError(
                        f"fresh home plugin identity differs for repetition {repetition}"
                    )
            for arm in order:
                run_root = runs_root / f"rep{repetition}-{arm}"
                shutil.copytree(fixture, run_root)
                copied_hashes = tree_hashes(run_root)
                if copied_hashes != fixture_hashes:
                    raise LiveEvaluationError(
                        f"fresh fixture copy mismatch for rep{repetition}-{arm}"
                    )
                prefix = artifacts / f"rep{repetition}-{arm}"
                Path(f"{prefix}-initial-hashes.json").write_text(
                    json.dumps(copied_hashes, sort_keys=True), encoding="utf-8"
                )
                result = _run_one(
                    codex=args.codex,
                    home=homes[arm],
                    fixture=run_root,
                    artifact_prefix=prefix,
                    prompt=args.prompt,
                    model=args.model,
                    reasoning_effort=args.reasoning_effort,
                    controller_mode=controller_modes[arm],
                    storage_dir=storage_root / f"rep{repetition}-{arm}",
                    agent_slots=args.agent_slots,
                    hidden_check=hidden_check,
                    quality_check=quality_check,
                    allowed_changes=args.allow_change,
                    bypass_sandbox=args.bypass_sandbox,
                    session_timeout_seconds=args.session_timeout_seconds,
                )
                result.update(
                    {
                        "case_id": f"{args.task_id}-rep{repetition}",
                        "task_id": args.task_id,
                        "repetition": repetition,
                        "variant": arm,
                        "arm_order": order,
                        "source_sha256": fixture_sha,
                        "plugin_version": plugin_version,
                        "fixture_git_sha256": fixture_git["sha256"]
                        if fixture_git
                        else None,
                        "experiment_sha256": experiment_sha256,
                    }
                )
                results.append(result)
            post_run_identity = validate_arm_plugins(
                args.codex, homes["baseline"], homes["candidate"]
            )
            if any(
                post_run_identity[field] != plugin_identity[field]
                for field in (
                    "version",
                    "source_sha256",
                    "installed_sha256",
                    "file_count",
                )
            ):
                raise LiveEvaluationError(
                    f"plugin identity changed during repetition {repetition}"
                )
        postflight_plugin_identity = validate_arm_plugins(
            args.codex, baseline_home, candidate_home
        )
        if postflight_plugin_identity != plugin_identity:
            raise LiveEvaluationError(
                "candidate plugin identity changed between preflight and postflight"
            )
        guard_receipts = verify_guards(guard_before)
        receipts = []
        for result in results:
            receipt = {
                "schema_version": 2 if binding is not None else 1,
                "case_id": result["case_id"],
                "variant": result["variant"],
                "controller_protocol_id": controller_protocol["protocol_id"],
                "controller_protocol_sha256": controller_protocol["sha256"],
                "provider": (f"cognitive-powers-{plugin_version}"),
                "task": args.task_id,
                "success": result["success"],
                "critical_errors": result["critical_errors"],
                "quality_score": result["quality_score"],
                "input_tokens": result["input_tokens"],
                "cached_input_tokens": result["cached_input_tokens"],
                "fresh_input_tokens": result["fresh_input_tokens"],
                "output_tokens": result["output_tokens"],
                "elapsed_seconds": result["elapsed_seconds"],
                "evidence": [
                    f"hidden exit {result['hidden_exit']}",
                    "changed " + ",".join(result["changed_paths"]),
                    *result["quality_evidence"],
                ],
                "live_execution": True,
            }
            if binding is not None:
                receipt.update(
                    {
                        **binding,
                        "task_id": args.task_id,
                        "model": args.model,
                        "reasoning_effort": args.reasoning_effort,
                        "prompt": args.prompt,
                        "repetition": result["repetition"],
                        "source_sha256": result["source_sha256"],
                        "fixture_git_sha256": result["fixture_git_sha256"],
                        "source_commit": plugin_identity["source_commit"],
                        "source_git_sha256": plugin_identity["source_git"]["sha256"],
                        "experiment_sha256": result["experiment_sha256"],
                        "pre_evaluation_diff_sha256": result[
                            "pre_evaluation_diff_sha256"
                        ],
                        "controller_mode": result["controller_mode"],
                        "tools": sorted(set(args.available_tool)),
                        "agent_slots": args.agent_slots,
                        "permissions": [
                            "dangerously-bypass-approvals-and-sandbox"
                            if args.bypass_sandbox
                            else "workspace-write"
                        ],
                        "arm_order": result["arm_order"],
                        "independent_tests_passed": result["hidden_exit"] == 0,
                        "turns": result["turns"],
                        "tool_calls": result["tool_calls"],
                        "retries": result["retries"],
                        "agent_telemetry": result["agent_telemetry"],
                        "base_prompt_sha256": result["base_prompt_sha256"],
                        "controller_directive": result["controller_directive"],
                        "host_identity": host_identity,
                        "hidden_check_sha256": hidden_identity["sha256"],
                        "quality_check_sha256": quality_identity["sha256"],
                        "allowed_changes_sha256": allowed_changes_sha256,
                    }
                )
            receipts.append(receipt)
        (output / "results.json").write_text(
            json.dumps(results, indent=2) + "\n", encoding="utf-8"
        )
        (output / "receipts.json").write_text(
            json.dumps(receipts, indent=2) + "\n", encoding="utf-8"
        )
        summary = {
            "schema_version": 2 if binding is not None else 1,
            "task_id": args.task_id,
            "task_binding": binding,
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "seed": args.seed,
            "repetitions": args.repetitions,
            "batch_repetition": args.batch_repetition,
            "source_sha256": fixture_sha,
            "fixture_git": fixture_git,
            "source_commit": plugin_identity["source_commit"],
            "source_git": plugin_identity["source_git"],
            "experiment_identity": experiment_identity,
            "experiment_sha256": experiment_sha256,
            "controller_modes": controller_modes,
            "controller_protocol": controller_protocol,
            "plugin_version": plugin_version,
            "candidate_plugin": plugin_identity,
            "candidate_plugin_postflight": postflight_plugin_identity,
            "host_identity": host_identity,
            "hidden_check": hidden_identity,
            "quality_check": quality_identity,
            "guarded_roots": guard_receipts,
            "all_runs_successful": all(result["success"] for result in results),
            "comparison": aggregate_results(results),
            "end_to_end_improvement_proven": False,
            "results": str(output / "results.json"),
            "receipts": str(output / "receipts.json"),
        }
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary["all_runs_successful"] else 1
    except (OSError, LiveEvaluationError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
