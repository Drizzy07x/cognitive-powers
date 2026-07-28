#!/usr/bin/env python3
"""Run isolated, paired Codex live evaluations without inventing evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence


def _load_sibling_module(name: str):
    path = Path(__file__).resolve().with_name(f"{name}.py")
    module_name = (
        f"_cognitive_{name}_"
        + hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    )
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load sibling module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_EVALUATION = _load_sibling_module("integration_evaluation")
_STORAGE_POLICY = _load_sibling_module("storage_policy")
validate_task_contract = _EVALUATION.validate_task_contract
DEFAULT_COPY_MAX_BYTES = _STORAGE_POLICY.DEFAULT_COPY_MAX_BYTES
DEFAULT_COPY_MAX_FILES = _STORAGE_POLICY.DEFAULT_COPY_MAX_FILES
StoragePolicyError = _STORAGE_POLICY.StoragePolicyError
TreeMeasurement = _STORAGE_POLICY.TreeMeasurement
bounded_copy_tree = _STORAGE_POLICY.bounded_copy_tree
enforce_budget = _STORAGE_POLICY.enforce_budget
iter_tree_files = _STORAGE_POLICY.iter_tree_files
measure_tree = _STORAGE_POLICY.measure_tree
reject_large_excluded_trees = _STORAGE_POLICY.reject_large_excluded_trees


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
CONTROLLER_DIRECTIVE_VERSION = 6
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
DEFAULT_WORK_MAX_FILES = DEFAULT_COPY_MAX_FILES * 10
DEFAULT_WORK_MAX_BYTES = DEFAULT_COPY_MAX_BYTES * 10


def _load_live_runner_core():
    path = Path(__file__).resolve().with_name("live_ab_runner_core") / "core.py"
    identity = f"{__name__}:{path}"
    module_name = (
        "_cognitive_live_ab_runner_core_"
        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    )
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load live runner core from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_CORE = _load_live_runner_core()
shutil = _CORE.shutil
LiveEvaluationError = _CORE.LiveEvaluationError
resolve_codex_executable = _CORE.resolve_codex_executable
rollout_snapshot = _CORE.rollout_snapshot
_rollout_rows = _CORE._rollout_rows
_final_rollout_usage = _CORE._final_rollout_usage
parse_new_rollouts = _CORE.parse_new_rollouts
load_controller_protocol = _CORE.load_controller_protocol
_resolved = _CORE._resolved
_is_within = _CORE._is_within
validate_layout = _CORE.validate_layout
copy_fixture_tree = _CORE.copy_fixture_tree
copy_home_tree = _CORE.copy_home_tree
_raw_workdir_measurement = _CORE._raw_workdir_measurement
workdir_receipt = _CORE.workdir_receipt
finalize_workdir = _CORE.finalize_workdir
create_workdir = _CORE.create_workdir
projected_copy_budget = _CORE.projected_copy_budget
tree_hashes = _CORE.tree_hashes
source_sha256 = _CORE.source_sha256
require_measured_fixture_unchanged = _CORE.require_measured_fixture_unchanged
git_identity = _CORE.git_identity
protected_roots = _CORE.protected_roots
snapshot_guards = _CORE.snapshot_guards
verify_guards = _CORE.verify_guards
_local_python_dependencies = _CORE._local_python_dependencies
command_identity = _CORE.command_identity
codex_host_identity = _CORE.codex_host_identity
changed_paths = _CORE.changed_paths
unexpected_changes = _CORE.unexpected_changes
arm_order = _CORE.arm_order
_content_id_matches = _CORE._content_id_matches
_canonical_agent_plan_v2 = _CORE._canonical_agent_plan_v2
_canonical_plan_transition = _CORE._canonical_plan_transition
_agent_message_json_values = _CORE._agent_message_json_values
parse_events = _CORE.parse_events
_execution_semantics = _CORE._execution_semantics
_normalized_task_name = _CORE._normalized_task_name
_bind_rollout_assignments = _CORE._bind_rollout_assignments
classify_agent_decision = _CORE.classify_agent_decision
normalize_quality_payload = _CORE.normalize_quality_payload
aggregate_results = _CORE.aggregate_results


def controller_directive(
    mode: str, preflight_expected_mode: str | None = None
) -> dict[str, str]:
    if mode not in CONTROLLER_MODES:
        raise LiveEvaluationError(f"invalid controller mode: {mode}")
    behavior = (
        "Do not consult the orchestration planner, emit an agent_plan, spawn, delegate, or call any agent tool. Complete the task in the parent."
        if mode == "forced-solo"
        else "For non-trivial work, consult and execute the Cognitive Powers orchestration policy; obey its solo/delegation decision and use native agent tools when it delegates."
    )
    if preflight_expected_mode is not None and mode == "adaptive":
        if preflight_expected_mode not in AGENT_PLAN_MODES:
            raise LiveEvaluationError("preflight expected mode is invalid")
        behavior += (
            " This is a non-scored instrumental preflight: consult the canonical "
            f"planner and execute {preflight_expected_mode} exactly."
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
    base_prompt: str, mode: str, preflight_expected_mode: str | None = None
) -> tuple[str, dict[str, str]]:
    directive = controller_directive(mode, preflight_expected_mode)
    return base_prompt.rstrip() + "\n\n" + directive["text"], directive


def _plugin_list(codex: str, home: Path) -> list[dict[str, Any]]:
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(home)
    completed = subprocess.run(
        [resolve_codex_executable(codex), "plugin", "list", "--json"],
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


def _candidate_identity(
    item: dict[str, Any],
    candidate_home: Path,
    *,
    canonical_source: Path | None = None,
) -> dict[str, Any]:
    version = item.get("version")
    name = item.get("name")
    marketplace = item.get("marketplaceName")
    source = item.get("source")
    source_path = source.get("path") if isinstance(source, dict) else None
    if not all(
        isinstance(value, str) and value for value in (version, name, marketplace)
    ) or (
        canonical_source is None
        and (not isinstance(source_path, str) or not source_path)
    ):
        raise LiveEvaluationError("candidate plugin identity is incomplete")
    reported_source_root = (
        str(_resolved(Path(source_path)))
        if isinstance(source_path, str) and source_path
        else None
    )
    source_root = _resolved(
        canonical_source if canonical_source is not None else Path(str(source_path))
    )
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
        "reported_source_root": reported_source_root,
        "installed_root": str(installed_root),
        "source_sha256": source_identity,
        "installed_sha256": installed_identity,
        "source_file_count": len(source_hashes),
        "file_count": len(installed_hashes),
        "source_commit": source_git["head"],
        "source_git": source_git,
    }


def validate_arm_plugins(
    codex: str,
    baseline_home: Path,
    candidate_home: Path,
    *,
    canonical_source: Path | None = None,
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
    baseline_identity = _candidate_identity(
        baseline_cognitive,
        baseline_home,
        canonical_source=canonical_source,
    )
    candidate_identity = _candidate_identity(
        candidate_cognitive,
        candidate_home,
        canonical_source=canonical_source,
    )
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
        resolve_codex_executable(codex),
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
    copy_max_files: int,
    copy_max_bytes: int,
    allow_large_excluded_trees: bool,
    preflight_expected_mode: str | None = None,
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
    effective_prompt, directive = compose_controller_prompt(
        prompt, controller_mode, preflight_expected_mode
    )
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
    copy_fixture_tree(
        fixture,
        hidden_fixture,
        max_files=copy_max_files,
        max_bytes=copy_max_bytes,
        allow_large_excluded_trees=allow_large_excluded_trees,
    )
    copy_fixture_tree(
        fixture,
        quality_fixture,
        max_files=copy_max_files,
        max_bytes=copy_max_bytes,
        allow_large_excluded_trees=allow_large_excluded_trees,
    )
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
    if not decision["telemetry_observation_complete"]:
        critical.append("agent execution telemetry is incomplete")
    elif not decision["complete"]:
        critical.append("controller noncompliance: observed execution violated mode")
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
            "telemetry_observation_complete": decision[
                "telemetry_observation_complete"
            ],
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
        "execution_artifacts": {
            "events_sha256": hashlib.sha256(events.read_bytes()).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr.read_bytes()).hexdigest(),
            "message_sha256": hashlib.sha256(message.read_bytes()).hexdigest(),
        },
        "elapsed_seconds": round(elapsed, 3),
    }


def validate_materialized_evidence(
    output: Path, *, expected_result_count: int
) -> dict[str, str]:
    """Re-read compact runner evidence before permitting workdir deletion."""
    decoded: dict[str, Any] = {}
    hashes: dict[str, str] = {}
    for name in ("results.json", "receipts.json", "summary.json"):
        path = output / name
        try:
            decoded[name] = json.loads(path.read_text(encoding="utf-8"))
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, json.JSONDecodeError) as error:
            raise LiveEvaluationError(
                f"materialized runner evidence is invalid: {name}"
            ) from error
    results = decoded["results.json"]
    receipts = decoded["receipts.json"]
    summary = decoded["summary.json"]
    if (
        not isinstance(results, list)
        or len(results) != expected_result_count
        or not isinstance(receipts, list)
        or len(receipts) != expected_result_count
        or not isinstance(summary, dict)
        or summary.get("results") != str(output / "results.json")
        or summary.get("receipts") != str(output / "receipts.json")
    ):
        raise LiveEvaluationError("materialized runner evidence is incomplete")
    result_keys = {
        (row.get("case_id"), row.get("variant"))
        for row in results
        if isinstance(row, dict)
    }
    receipt_keys = {
        (row.get("case_id"), row.get("variant"))
        for row in receipts
        if isinstance(row, dict)
    }
    if (
        len(result_keys) != expected_result_count
        or result_keys != receipt_keys
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("execution_artifacts"), dict)
            for row in results
        )
    ):
        raise LiveEvaluationError("materialized runner evidence identities are invalid")
    return hashes


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
    parser.add_argument("--plugin-source", type=Path)
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
    parser.add_argument("--preflight-expected-mode", choices=sorted(AGENT_PLAN_MODES))
    parser.add_argument(
        "--work-root",
        type=Path,
        help="external ephemeral work root; must not exist or overlap --output",
    )
    parser.add_argument(
        "--retain-debug-workdirs",
        action="store_true",
        help="retain external work state after a validated run (default: false)",
    )
    parser.add_argument(
        "--max-work-files",
        "--max-copy-files",
        dest="max_work_files",
        type=int,
        default=DEFAULT_WORK_MAX_FILES,
    )
    parser.add_argument(
        "--max-work-bytes",
        "--max-copy-bytes",
        dest="max_work_bytes",
        type=int,
        default=DEFAULT_WORK_MAX_BYTES,
    )
    parser.add_argument(
        "--fixture-manifest-json",
        help="explicit JSON array of fixture-relative files or directories",
    )
    parser.add_argument(
        "--allow-large-excluded-trees",
        action="store_true",
        help="diagnostic override for excluded bulky fixture dependency trees",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    work_root: Path | None = None
    work_succeeded = False
    exit_code = 2
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
        if args.max_work_files < 0 or args.max_work_bytes < 0:
            raise LiveEvaluationError("workdir budgets must be non-negative")
        fixture_manifest = None
        if args.fixture_manifest_json is not None:
            try:
                fixture_manifest = json.loads(args.fixture_manifest_json)
            except json.JSONDecodeError as error:
                raise LiveEvaluationError(
                    "fixture manifest must be a JSON array"
                ) from error
            if (
                not isinstance(fixture_manifest, list)
                or not fixture_manifest
                or not all(
                    isinstance(value, str) and value for value in fixture_manifest
                )
            ):
                raise LiveEvaluationError(
                    "fixture manifest must be a non-empty string array"
                )
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
        if binding is not None and args.plugin_source is None:
            raise LiveEvaluationError(
                "contract-bound runs require canonical --plugin-source"
            )
        hidden_identity = command_identity(hidden_check)
        quality_identity = (
            command_identity(quality_check) if quality_check is not None else None
        )
        if output.exists():
            raise LiveEvaluationError(f"output already exists: {output}")
        plugin_identity = validate_arm_plugins(
            args.codex,
            baseline_home,
            candidate_home,
            canonical_source=args.plugin_source,
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
        scheduled_repetitions = (
            [(binding["batch_repetition"], binding["batch_arm_order"])]
            if binding is not None and "batch_repetition" in binding
            else list(enumerate(arm_order(args.repetitions, args.seed), start=1))
        )
        fixture_tracked_only = binding is not None and fixture_manifest is None
        copy_budget = projected_copy_budget(
            fixture=fixture,
            baseline_home=baseline_home,
            candidate_home=candidate_home,
            repetitions=len(scheduled_repetitions),
            fixture_manifest=fixture_manifest,
            fixture_tracked_only=fixture_tracked_only,
            max_files=args.max_work_files,
            max_bytes=args.max_work_bytes,
            allow_large_excluded_trees=args.allow_large_excluded_trees,
        )
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
                mode: controller_directive(
                    mode,
                    args.preflight_expected_mode if mode == "adaptive" else None,
                )["mode_sha256"]
                for mode in sorted(CONTROLLER_MODES)
            },
            "host_identity_sha256": hashlib.sha256(
                json.dumps(
                    host_identity, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
            "copy_budget": copy_budget,
        }
        experiment_sha256 = hashlib.sha256(
            json.dumps(
                experiment_identity, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        work_root = create_workdir(output, args.work_root)
        output.mkdir(parents=True)
        artifacts = work_root / "artifacts"
        runs_root = work_root / "runs"
        homes_root = work_root / "homes"
        storage_root = work_root / "storage"
        artifacts.mkdir()
        runs_root.mkdir()
        homes_root.mkdir()
        storage_root.mkdir()
        results: list[dict[str, Any]] = []
        source_homes = {"baseline": baseline_home, "candidate": candidate_home}
        controller_modes = {
            "baseline": args.baseline_controller_mode,
            "candidate": args.candidate_controller_mode,
        }
        for repetition, order in scheduled_repetitions:
            homes: dict[str, Path] = {}
            for arm in ("baseline", "candidate"):
                run_home = homes_root / f"rep{repetition}-{arm}"
                copy_home_tree(
                    source_homes[arm],
                    run_home,
                    max_files=args.max_work_files,
                    max_bytes=args.max_work_bytes,
                )
                homes[arm] = run_home
            run_plugin_identity = validate_arm_plugins(
                args.codex,
                homes["baseline"],
                homes["candidate"],
                canonical_source=args.plugin_source,
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
                copy_fixture_tree(
                    fixture,
                    run_root,
                    manifest=fixture_manifest,
                    tracked_only=fixture_tracked_only,
                    max_files=args.max_work_files,
                    max_bytes=args.max_work_bytes,
                    allow_large_excluded_trees=args.allow_large_excluded_trees,
                )
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
                    copy_max_files=args.max_work_files,
                    copy_max_bytes=args.max_work_bytes,
                    allow_large_excluded_trees=args.allow_large_excluded_trees,
                    preflight_expected_mode=(
                        args.preflight_expected_mode if arm == "candidate" else None
                    ),
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
                args.codex,
                homes["baseline"],
                homes["candidate"],
                canonical_source=args.plugin_source,
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
            args.codex,
            baseline_home,
            candidate_home,
            canonical_source=args.plugin_source,
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
            "copy_budget": copy_budget,
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
        validate_materialized_evidence(
            output, expected_result_count=len(scheduled_repetitions) * 2
        )
        work_succeeded = True
        print(json.dumps(summary, ensure_ascii=False))
        exit_code = 0 if summary["all_runs_successful"] else 1
    except (OSError, LiveEvaluationError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        exit_code = 2
    finally:
        if work_root is not None and work_root.exists():
            try:
                debug_receipt = finalize_workdir(
                    work_root,
                    succeeded=work_succeeded,
                    retain_debug_workdirs=args.retain_debug_workdirs,
                )
            except (OSError, LiveEvaluationError) as error:
                retained: dict[str, Any] = {"path": str(work_root.resolve())}
                try:
                    retained.update(workdir_receipt(work_root))
                except (OSError, LiveEvaluationError) as measurement_error:
                    retained["measurement_error"] = str(measurement_error)
                print(
                    json.dumps(
                        {
                            "debug_workdir_error": str(error),
                            "debug_workdir": retained,
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                )
                exit_code = 2
            else:
                if debug_receipt is not None:
                    print(
                        json.dumps(
                            {"debug_workdir": debug_receipt},
                            ensure_ascii=False,
                        ),
                        file=sys.stderr,
                    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
