#!/usr/bin/env python3
"""Run isolated, paired Codex live evaluations without inventing evidence."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.integration_evaluation import validate_task_contract
except ModuleNotFoundError:  # Direct script execution places scripts/ on sys.path.
    from integration_evaluation import validate_task_contract


IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache"}
CONTROLLER_MODES = {"forced-solo", "adaptive"}
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTROLLER_PROTOCOL = PLUGIN_ROOT / "benchmarks" / "controller_ab_protocol.json"


class LiveEvaluationError(ValueError):
    """Raised when a live evaluation cannot produce trustworthy receipts."""


def load_controller_protocol(path: Path) -> dict[str, str]:
    """Validate and fingerprint the frozen controller-specific experiment contract."""
    resolved = _resolved(path)
    if not resolved.is_file():
        raise LiveEvaluationError(f"controller protocol is missing: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LiveEvaluationError("controller protocol is not valid JSON") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise LiveEvaluationError("controller protocol must use schema_version 1")
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
    observed_assignments: list[dict[str, str]] = []
    for row in rows:
        if row.get("type") != "agent.lifecycle" or row.get("provenance") != "host":
            continue
        if all(
            isinstance(row.get(field), str) and row.get(field)
            for field in ("assignment_id", "actor_id", "role")
        ):
            observation = {
                "assignment_id": row["assignment_id"],
                "actor_id": row["actor_id"],
                "role": row["role"],
            }
            if observation not in observed_assignments:
                observed_assignments.append(observation)

    def visit(value: object) -> None:
        if isinstance(value, dict):
            candidate = value.get("agent_plan", value)
            if (
                isinstance(candidate, dict)
                and candidate.get("mode")
                in {"solo", "parallel-read-only", "parallel-packets", "staged-verify"}
                and isinstance(candidate.get("waves"), list)
            ):
                encoded = json.dumps(candidate, sort_keys=True, default=str)
                if all(
                    json.dumps(item, sort_keys=True, default=str) != encoded
                    for item in plan_receipts
                ):
                    plan_receipts.append(candidate)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)
        elif isinstance(value, str) and value.lstrip().startswith("{"):
            try:
                visit(json.loads(value))
            except json.JSONDecodeError:
                pass

    visit(rows)
    return {
        "usage": dict(usage),
        "tool_calls": tool_calls,
        "turns": len(completed),
        "tool_names": tool_names,
        "agent_events": agent_events,
        "agent_spawns": sum(item["event"] == "spawn_agent" for item in agent_events),
        "agent_joins": sum(item["event"] == "wait_agent" for item in agent_events),
        "agent_plans": plan_receipts,
        "observed_assignments": observed_assignments,
        "usage_includes_subagents": (
            usage.get("includes_subagents") is True
            or usage.get("scope") in {"task-tree", "all-agents", "aggregate"}
        ),
        "invalid_json_lines": invalid_lines,
    }


def classify_agent_decision(
    parsed: dict[str, Any], controller_mode: str
) -> dict[str, Any]:
    """Classify an explicit plan or the focused no-agent fast path."""
    observed_plan = parsed["agent_plans"][-1] if parsed["agent_plans"] else None
    implicit_solo = observed_plan is None and parsed["agent_spawns"] == 0
    actual_mode = (
        observed_plan.get("mode")
        if observed_plan
        else ("solo" if implicit_solo else None)
    )
    planned_assignments = (
        sum(
            len(wave.get("assignments", []))
            for wave in observed_plan.get("waves", [])
            if isinstance(wave, dict) and isinstance(wave.get("assignments", []), list)
        )
        if observed_plan is not None
        else 0
    )
    explicit_plan_complete = observed_plan is not None and (
        actual_mode == "solo"
        and planned_assignments == 0
        and parsed["agent_spawns"] == 0
        or actual_mode != "solo"
        and planned_assignments > 0
        and parsed["agent_spawns"] == planned_assignments
        and parsed["agent_joins"] == parsed["agent_spawns"]
        and len(parsed["observed_assignments"]) == parsed["agent_spawns"]
        and parsed["usage_includes_subagents"]
    )
    complete = (
        parsed["agent_spawns"] == 0 and actual_mode == "solo"
        if controller_mode == "forced-solo"
        else implicit_solo or explicit_plan_complete
    )
    return {
        "observed_plan": observed_plan,
        "actual_mode": actual_mode,
        "decision_observation": (
            "explicit-agent-plan"
            if observed_plan is not None
            else "implicit-solo-no-agent-events"
            if implicit_solo
            else "missing"
        ),
        "planned_assignment_count": planned_assignments,
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
    installed_hashes = tree_hashes(installed_root)
    if source_hashes != installed_hashes:
        changed = changed_paths(source_hashes, installed_hashes)
        raise LiveEvaluationError(
            "candidate installation differs from source: " + ", ".join(changed[:20])
        )
    identity = source_sha256(source_hashes)
    return {
        "version": version,
        "source_root": str(source_root),
        "installed_root": str(installed_root),
        "source_sha256": identity,
        "installed_sha256": identity,
        "file_count": len(source_hashes),
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
        for key in ("version", "source_sha256", "installed_sha256", "file_count")
    }
    if comparable != {
        key: candidate_identity[key]
        for key in ("version", "source_sha256", "installed_sha256", "file_count")
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
    command = [
        codex,
        "exec",
        "--ephemeral",
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
    ]
    command.extend(
        ["--dangerously-bypass-approvals-and-sandbox"]
        if bypass_sandbox
        else ["--sandbox", "workspace-write"]
    )
    command.append(prompt)
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
    return {
        "success": not critical,
        "critical_errors": critical,
        "codex_exit": completed.returncode,
        "hidden_exit": hidden.returncode,
        "hidden_stdout_tail": hidden.stdout[-1000:],
        "hidden_stderr_tail": hidden.stderr[-1000:],
        "changed_paths": changed,
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
        "agent_telemetry": {
            "source": "provider-event-stream",
            "controller_mode": controller_mode,
            "tool_names": parsed["tool_names"],
            "events": parsed["agent_events"],
            "spawn_count": parsed["agent_spawns"],
            "join_count": parsed["agent_joins"],
            "plan_receipts": parsed["agent_plans"],
            "observed_assignments": parsed["observed_assignments"],
            "actual_mode": decision["actual_mode"],
            "decision_observation": decision["decision_observation"],
            "planned_assignment_count": decision["planned_assignment_count"],
            "usage_includes_subagents": (
                parsed["agent_spawns"] == 0 or parsed["usage_includes_subagents"]
            ),
            "complete": decision["complete"],
        },
        "pre_evaluation_diff_sha256": source_sha256(
            {path: measured_hashes.get(path, "<deleted>") for path in changed}
        ),
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
            for field in ("version", "source_sha256", "installed_sha256", "file_count"):
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
            "experiment_identity": experiment_identity,
            "experiment_sha256": experiment_sha256,
            "controller_modes": controller_modes,
            "controller_protocol": controller_protocol,
            "plugin_version": plugin_version,
            "candidate_plugin": plugin_identity,
            "candidate_plugin_postflight": postflight_plugin_identity,
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
