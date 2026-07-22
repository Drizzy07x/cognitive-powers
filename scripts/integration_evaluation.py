#!/usr/bin/env python3
"""Evaluate paired baseline/candidate receipts without inventing live claims."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


REQUIRED_CATEGORIES = {
    "bug-fix",
    "multi-file-implementation",
    "current-source-research",
    "delivery-verification",
    "real-host-interaction",
}
LOCKED_PAIR_FIELDS = (
    "task_set_id",
    "task_id",
    "task_version",
    "split",
    "repetition",
    "model",
    "reasoning_effort",
    "prompt",
    "tools",
    "permissions",
    "fixture_id",
    "source_sha256",
    "randomization_seed",
    "arm_order",
)
LIVE_IDENTITY_FIELDS = (
    "fixture_git_sha256",
    "experiment_sha256",
    "hidden_check_sha256",
    "quality_check_sha256",
    "allowed_changes_sha256",
    "pre_evaluation_diff_sha256",
    "controller_protocol_sha256",
    "agent_slots",
    "controller_protocol_id",
)
CONTROLLER_MODES = {"forced-solo", "adaptive"}
AGENT_PLAN_MODES = {"solo", "parallel-read-only", "parallel-packets", "staged-verify"}
TOTAL_TOKEN_RATIO_MAX = 0.85
FRESH_INPUT_RATIO_MAX = 0.80
SOLO_TOKEN_RATIO_MAX = 1.05
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTROLLER_PROTOCOL = PLUGIN_ROOT / "benchmarks" / "controller_ab_protocol.json"
EXPECTED_PROMOTION_GATES = {
    "candidate-critical-failures": ("candidate_critical_failure_count", "eq", 0),
    "strict-success-observed": (
        "candidate_minus_control_strict_success_rate",
        "gte",
        0.0,
    ),
    "strict-success-noninferiority": (
        "candidate_minus_control_strict_success_rate_ci95_lower",
        "gt",
        -0.05,
    ),
    "quality-delta": ("candidate_minus_control_mean_quality_points", "gte", 5.0),
    "quality-confidence": (
        "candidate_minus_control_mean_quality_points_ci95_lower",
        "gt",
        0.0,
    ),
    "total-token-ratio": ("candidate_over_control_median_total_tokens", "lte", 0.85),
    "total-token-confidence": (
        "candidate_over_control_median_total_tokens_ci95_upper",
        "lt",
        0.85,
    ),
    "fresh-input-ratio": (
        "candidate_over_control_median_fresh_input_tokens",
        "lte",
        0.8,
    ),
    "fresh-input-confidence": (
        "candidate_over_control_median_fresh_input_tokens_ci95_upper",
        "lt",
        0.8,
    ),
    "solo-token-overhead": (
        "candidate_over_control_median_total_tokens_for_solo_tasks",
        "lte",
        1.05,
    ),
    "mode-precision": ("mode_selection_precision", "gte", 0.9),
    "delegation-recall": ("eligible_delegation_recall", "gte", 0.8),
    "safety-contract-compliance": (
        "write_depth_ownership_and_distinct_verifier_compliance",
        "eq",
        1.0,
    ),
    "failed-pairs-excluded-from-token-analysis": (
        "failed_pair_count_in_token_comparison",
        "eq",
        0,
    ),
}
EXPECTED_REQUIRED_ARTIFACTS = {
    "frozen-manifest.json",
    "randomized-schedule.json",
    "session-receipts.jsonl",
    "agent-events.jsonl",
    "pre-evaluator-diffs/",
    "hidden-check-results.jsonl",
    "quality-check-results.jsonl",
    "analysis-with-ci95.json",
    "sha256-index.json",
    "independent-verdict.json",
}
EXPECTED_TELEMETRY_FIELDS = {
    "agent_plan_input",
    "agent_plan_output",
    "observed_agents",
    "parent_ids",
    "roles",
    "waves",
    "ownership",
    "permissions",
    "joins",
    "retries",
    "verifier_identity",
    "per_agent_input_tokens",
    "per_agent_cached_input_tokens",
    "per_agent_output_tokens",
    "aggregate_input_tokens",
    "aggregate_cached_input_tokens",
    "aggregate_fresh_input_tokens",
    "aggregate_output_tokens",
    "aggregate_total_tokens",
    "changed_paths_before_evaluation",
}
EXPECTED_IDENTITY_FIELDS = {
    "source_commit",
    "source_tree_sha256",
    "plugin_sha256",
    "codex_cli_identity",
    "model",
    "reasoning_effort",
    "prompt_sha256",
    "tools_sha256",
    "permissions_sha256",
    "instructions_sha256",
    "fixture_id",
    "fixture_sha256",
    "git_identity",
    "evaluator_sha256",
    "hidden_checks_sha256",
    "allowed_paths_sha256",
    "seed",
}


class EvaluationError(ValueError):
    """Raised when paired evaluation evidence is incomplete or malformed."""


def load_controller_protocol(path: Path) -> dict[str, Any]:
    try:
        raw = path.resolve().read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError("controller protocol cannot be loaded") from error
    if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2}:
        raise EvaluationError("controller protocol must use schema_version 1 or 2")
    protocol_schema = payload["schema_version"]
    comparison = payload.get("comparison")
    design = payload.get("design")
    analysis = payload.get("analysis")
    confidence = (
        analysis.get("confidence_intervals") if isinstance(analysis, dict) else None
    )
    promotion_gates = payload.get("promotion_gates")
    if not isinstance(promotion_gates, list) or not all(
        isinstance(item, dict) for item in promotion_gates
    ):
        raise EvaluationError("controller protocol promotion gates are invalid")
    gate_map = {
        item.get("id"): (
            item.get("metric"),
            item.get("operator"),
            item.get("threshold"),
        )
        for item in promotion_gates
    }
    fail_closed = payload.get("fail_closed_requirements")
    identity_requirements = (
        fail_closed.get("identity") if isinstance(fail_closed, dict) else None
    )
    isolation = (
        fail_closed.get("evaluation_isolation")
        if isinstance(fail_closed, dict)
        else None
    )
    telemetry = fail_closed.get("telemetry") if isinstance(fail_closed, dict) else None
    execution_state = payload.get("execution_state")
    rounds = design.get("rounds") if isinstance(design, dict) else None
    execution = design.get("execution") if isinstance(design, dict) else None
    verdict = payload.get("verdict")
    if (
        not isinstance(comparison, dict)
        or comparison.get("control_arm", {}).get("controller_mode") != "forced-solo"
        or comparison.get("candidate_arm", {}).get("controller_mode") != "adaptive"
        or comparison.get("only_intended_difference") != "controller_mode"
        or not isinstance(confidence, dict)
        or confidence.get("method") != "paired-stratified-bootstrap-by-fixture"
        or confidence.get("resamples") != 10000
        or confidence.get("seed") != f"controller-ab-bootstrap-v{protocol_schema}"
        or len(gate_map) != len(promotion_gates)
        or gate_map != EXPECTED_PROMOTION_GATES
        or not isinstance(payload.get("required_artifacts"), list)
        or set(payload["required_artifacts"]) != EXPECTED_REQUIRED_ARTIFACTS
        or payload.get("status") != "planned"
        or payload.get("claim_status") != "not-proven"
        or payload.get("contains_fixture_definitions") is not False
        or payload.get("contains_execution_results") is not False
        or payload.get("contains_provider_evidence") is not False
        or not isinstance(design, dict)
        or set(design.get("modes", [])) != AGENT_PLAN_MODES
        or set(design.get("categories", [])) != REQUIRED_CATEGORIES
        or design.get("cells") != 20
        or design.get("repetitions_per_fixture_per_arm") != 3
        or design.get("arms_per_fixture") != 2
        or design.get("experimental_unit") != "fixture"
        or design.get("declared_total_fixture_count") != 80
        or design.get("declared_total_session_count") != 480
        or not isinstance(rounds, dict)
        or rounds.get("pilot", {}).get("declared_fixture_count") != 20
        or rounds.get("pilot", {}).get("declared_session_count") != 120
        or rounds.get("pilot", {}).get("fixtures_per_cell") != 1
        or rounds.get("pilot", {}).get("held_out") is not False
        or rounds.get("pilot", {}).get("fixture_status") != "ready"
        or rounds.get("promotion", {}).get("declared_fixture_count") != 60
        or rounds.get("promotion", {}).get("declared_session_count") != 360
        or rounds.get("promotion", {}).get("fixtures_per_cell") != 3
        or rounds.get("promotion", {}).get("held_out") is not True
        or rounds.get("promotion", {}).get("fixture_status") != "ready"
        or rounds.get("promotion", {}).get("must_be_new_relative_to_pilot") is not True
        or not isinstance(execution, dict)
        or not all(
            execution.get(field) is True
            for field in (
                "sessions_run_sequentially",
                "global_schedule_randomized",
                "fresh_home_per_session",
                "fresh_cognitive_powers_storage_per_session",
                "development_and_held_out_disjoint",
            )
        )
        or not isinstance(execution_state, dict)
        or (
            protocol_schema == 1
            and (
                execution_state.get("fixtures_created") != 80
                or execution_state.get("fixture_status") != "ready"
                or not isinstance(execution_state.get("fixture_lock_sha256"), str)
                or len(execution_state["fixture_lock_sha256"]) != 64
            )
        )
        or (
            protocol_schema == 2
            and (
                execution_state.get("fixtures_created") != 0
                or execution_state.get("fixture_status") != "pending-v2-materialization"
                or execution_state.get("fixture_lock_sha256") is not None
            )
        )
        or execution_state.get("sessions_completed") != 0
        or execution_state.get("results_available") is not False
        or execution_state.get("provider_evidence_available") is not False
        or not isinstance(identity_requirements, dict)
        or set(identity_requirements.get("required", [])) != EXPECTED_IDENTITY_FIELDS
        or identity_requirements.get("missing_or_mismatched") != "invalid"
        or not isinstance(telemetry, dict)
        or set(telemetry.get("required", [])) != EXPECTED_TELEMETRY_FIELDS
        or telemetry.get("descendant_tokens_included") is not True
        or telemetry.get("hardcoded_observation_counts_forbidden") is not True
        or telemetry.get("missing_or_inconsistent") != "invalid"
        or not isinstance(isolation, dict)
        or not all(
            isolation.get(field) is True
            for field in (
                "diff_captured_before_evaluators",
                "hidden_and_quality_checks_run_on_independent_clones",
                "evaluators_outside_writable_fixture",
            )
        )
        or isolation.get("violation") != "invalid"
        or not isinstance(verdict, dict)
        or set(verdict.get("allowed_values", [])) != {"proven", "not-proven", "invalid"}
        or verdict.get("all_gates_required_for_proven") is not True
        or verdict.get("missing_required_artifact") != "invalid"
        or verdict.get("missing_identity_or_telemetry") != "invalid"
        or verdict.get("failed_gate") != "not-proven"
        or verdict.get("current") is not None
        or analysis.get("primary") != "intention-to-treat"
        or analysis.get("secondary") != "plan-compliant-only"
        or analysis.get("aggregate_repetitions_within_fixture_first") is not True
        or analysis.get("token_comparisons") != "paired-successful-runs-only"
        or analysis.get(
            "failed_runs_remain_in_success_quality_and_critical_failure_metrics"
        )
        is not True
    ):
        raise EvaluationError(
            "controller protocol does not match the confirmatory design"
        )
    return {
        "payload": payload,
        "protocol_id": _string(payload.get("protocol_id"), "protocol_id"),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bootstrap_resamples": confidence["resamples"],
        "bootstrap_seed": confidence["seed"],
        "gates": {
            key: {"metric": value[0], "operator": value[1], "threshold": value[2]}
            for key, value in gate_map.items()
        },
    }


def _artifact_sha256(path: Path) -> str:
    if path.is_file():
        data = path.read_bytes()
        if not data:
            raise EvaluationError(f"artifact is empty: {path.name}")
        return hashlib.sha256(data).hexdigest()
    if path.is_dir():
        files: dict[str, str] = {}
        resolved_root = path.resolve()
        for item in sorted(path.rglob("*")):
            if not item.is_file():
                continue
            resolved_item = item.resolve()
            try:
                resolved_item.relative_to(resolved_root)
            except ValueError as error:
                raise EvaluationError(
                    f"artifact directory entry escapes its root: {item.name}"
                ) from error
            if item.is_symlink() or resolved_item != item.absolute():
                raise EvaluationError(
                    f"artifact directory contains a link or reparse alias: {item.name}"
                )
            files[item.relative_to(path).as_posix()] = hashlib.sha256(
                item.read_bytes()
            ).hexdigest()
        if not files:
            raise EvaluationError(f"artifact directory is empty: {path.name}")
        encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
    raise EvaluationError(f"artifact is missing: {path.name}")


def load_artifact_bundle(
    index_path: Path, controller_protocol: Mapping[str, Any]
) -> dict[str, Any]:
    resolved = index_path.resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError("artifact index cannot be loaded") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise EvaluationError("artifact index must use schema_version 1")
    if payload.get("protocol_id") != controller_protocol.get(
        "protocol_id"
    ) or payload.get("controller_protocol_sha256") != controller_protocol.get("sha256"):
        raise EvaluationError("artifact index is not bound to the controller protocol")
    artifacts = payload.get("artifacts")
    expected_entries = EXPECTED_REQUIRED_ARTIFACTS - {"sha256-index.json"}
    if not isinstance(artifacts, dict) or set(artifacts) != expected_entries:
        raise EvaluationError(
            "artifact index does not contain the required artifact set"
        )
    observed: dict[str, str] = {}
    root = resolved.parent
    for name in sorted(expected_entries):
        entry = artifacts[name]
        if not isinstance(entry, dict) or entry.get("path") != name:
            raise EvaluationError(f"artifact index entry is invalid: {name}")
        target = (root / name).resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise EvaluationError(f"artifact escapes the bundle: {name}") from error
        observed[name] = _artifact_sha256(target)
        if entry.get("sha256") != observed[name]:
            raise EvaluationError(f"artifact hash mismatch: {name}")
    evidence = {
        name: digest
        for name, digest in observed.items()
        if name != "independent-verdict.json"
    }
    evidence_root = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if payload.get("evidence_root_sha256") != evidence_root:
        raise EvaluationError("artifact evidence root does not match")
    verdict_path = root / "independent-verdict.json"
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError("independent verdict is not valid JSON") from error
    executor_ids = verdict.get("executor_ids")
    verifier_id = verdict.get("verifier_id")
    if (
        verdict.get("schema_version") != 1
        or verdict.get("verdict") != "confirmed"
        or verdict.get("protocol_id") != controller_protocol.get("protocol_id")
        or verdict.get("controller_protocol_sha256")
        != controller_protocol.get("sha256")
        or verdict.get("evidence_root_sha256") != evidence_root
        or not isinstance(verifier_id, str)
        or not verifier_id
        or not isinstance(executor_ids, list)
        or not executor_ids
        or not all(isinstance(item, str) and item for item in executor_ids)
        or len(executor_ids) != len(set(executor_ids))
        or verifier_id in executor_ids
    ):
        raise EvaluationError("independent verdict is invalid or not independent")
    return {
        "index": str(resolved),
        "index_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "evidence_root_sha256": evidence_root,
        "verifier_id": verifier_id,
        "artifact_count": len(EXPECTED_REQUIRED_ARTIFACTS),
        "root": str(root),
    }


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise EvaluationError(f"{label} cannot be read") from error
    for index, line in enumerate(lines):
        if not line.strip():
            raise EvaluationError(f"{label} contains a blank record")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise EvaluationError(f"{label} record {index} is invalid JSON") from error
        if not isinstance(value, dict):
            raise EvaluationError(f"{label} record {index} must be an object")
        rows.append(value)
    if not rows:
        raise EvaluationError(f"{label} is empty")
    return rows


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _validate_host_actor_binding(
    expected_lifecycle: set[tuple[str, str, str, str, str]],
    agent_rows: Sequence[dict[str, Any]],
    verdict: Mapping[str, Any],
) -> int:
    actual_lifecycle = {
        (
            item.get("case_id"),
            item.get("variant"),
            item.get("assignment_id"),
            item.get("actor_id"),
            item.get("role"),
        )
        for item in agent_rows
        if item.get("type") == "agent.lifecycle"
        and item.get("provenance") == "host"
        and item.get("scope") != "experiment"
    }
    if expected_lifecycle != actual_lifecycle:
        raise EvaluationError(
            "host lifecycle artifacts do not match evaluated telemetry"
        )
    experiment_executor_rows = [
        item
        for item in agent_rows
        if item.get("type") == "agent.lifecycle"
        and item.get("provenance") == "host"
        and item.get("scope") == "experiment"
        and item.get("role") == "experiment-runner"
    ]
    experiment_verifier_rows = [
        item
        for item in agent_rows
        if item.get("type") == "agent.lifecycle"
        and item.get("provenance") == "host"
        and item.get("scope") == "experiment"
        and item.get("role") == "experiment-verifier"
    ]
    experiment_executors = {item.get("actor_id") for item in experiment_executor_rows}
    experiment_verifiers = {item.get("actor_id") for item in experiment_verifier_rows}
    if (
        not experiment_executor_rows
        or not all(
            isinstance(item.get("actor_id"), str) and item.get("actor_id")
            for item in experiment_executor_rows
        )
        or len(experiment_executors) != len(experiment_executor_rows)
        or len(experiment_verifier_rows) != 1
        or not isinstance(experiment_verifier_rows[0].get("actor_id"), str)
        or not experiment_verifier_rows[0].get("actor_id")
        or len(experiment_verifiers) != 1
        or set(verdict.get("executor_ids", [])) != experiment_executors
        or verdict.get("verifier_id") not in experiment_verifiers
        or verdict.get("verifier_id") in experiment_executors
    ):
        raise EvaluationError("independent verdict actors lack host-backed identity")
    return len(actual_lifecycle)


def _validate_artifact_semantics(
    bundle: Mapping[str, Any],
    receipts: Sequence[dict[str, Any]],
    analysis_binding: Mapping[str, Any],
    controller_protocol: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(_string(bundle.get("root"), "artifact_bundle.root"))
    receipt_rows = _read_jsonl(root / "session-receipts.jsonl", "session receipts")
    artifact_receipts = [normalize_receipt(row) for row in receipt_rows]

    def receipt_key(item: dict[str, Any]) -> tuple[str, str]:
        return item["case_id"], item["variant"]

    expected_receipts = sorted(receipts, key=receipt_key)
    artifact_receipts = sorted(artifact_receipts, key=receipt_key)
    if artifact_receipts != expected_receipts:
        raise EvaluationError("artifact receipts do not match evaluated receipts")
    receipt_set_sha256 = _canonical_sha256(expected_receipts)

    try:
        manifest = json.loads(
            (root / "frozen-manifest.json").read_text(encoding="utf-8")
        )
        schedule = json.loads(
            (root / "randomized-schedule.json").read_text(encoding="utf-8")
        )
        analysis = json.loads(
            (root / "analysis-with-ci95.json").read_text(encoding="utf-8")
        )
        verdict = json.loads(
            (root / "independent-verdict.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError("semantic artifact JSON cannot be loaded") from error
    task_set_ids = {item.get("task_set_id") for item in expected_receipts}
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("protocol_id") != controller_protocol.get("protocol_id")
        or manifest.get("controller_protocol_sha256")
        != controller_protocol.get("sha256")
        or manifest.get("receipt_set_sha256") != receipt_set_sha256
        or len(task_set_ids) != 1
        or manifest.get("task_set_id") != next(iter(task_set_ids))
    ):
        raise EvaluationError("frozen manifest does not bind the evaluated receipts")

    schedule_entries: dict[str, dict[str, Any]] = {}
    for receipt in expected_receipts:
        schedule_entries.setdefault(
            receipt["case_id"],
            {
                "case_id": receipt["case_id"],
                "task_id": receipt["task_id"],
                "repetition": receipt["repetition"],
                "arm_order": receipt["arm_order"],
            },
        )
    expected_schedule = [schedule_entries[key] for key in sorted(schedule_entries)]
    if (
        not isinstance(schedule, dict)
        or schedule.get("schema_version") != 1
        or schedule.get("protocol_id") != controller_protocol.get("protocol_id")
        or schedule.get("entries") != expected_schedule
    ):
        raise EvaluationError("randomized schedule does not match evaluated receipts")

    expected_hidden = sorted(
        (
            {
                "case_id": item["case_id"],
                "variant": item["variant"],
                "check_sha256": item["hidden_check_sha256"],
                "passed": item["independent_tests_passed"],
            }
            for item in expected_receipts
        ),
        key=lambda item: (item["case_id"], item["variant"]),
    )
    expected_quality = sorted(
        (
            {
                "case_id": item["case_id"],
                "variant": item["variant"],
                "check_sha256": item["quality_check_sha256"],
                "quality_score": item["quality_score"],
            }
            for item in expected_receipts
        ),
        key=lambda item: (item["case_id"], item["variant"]),
    )
    hidden_rows = sorted(
        _read_jsonl(root / "hidden-check-results.jsonl", "hidden check results"),
        key=lambda item: (item.get("case_id"), item.get("variant")),
    )
    quality_rows = sorted(
        _read_jsonl(root / "quality-check-results.jsonl", "quality check results"),
        key=lambda item: (item.get("case_id"), item.get("variant")),
    )
    if hidden_rows != expected_hidden or quality_rows != expected_quality:
        raise EvaluationError("check artifacts do not match evaluated receipts")

    diff_rows: list[dict[str, Any]] = []
    for path in sorted((root / "pre-evaluator-diffs").glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise EvaluationError("pre-evaluator diff metadata is invalid") from error
        if not isinstance(value, dict):
            raise EvaluationError("pre-evaluator diff metadata must be an object")
        diff_rows.append(value)
    expected_diffs = sorted(
        (
            {
                "case_id": item["case_id"],
                "variant": item["variant"],
                "sha256": item["pre_evaluation_diff_sha256"],
            }
            for item in expected_receipts
        ),
        key=lambda item: (item["case_id"], item["variant"]),
    )
    diff_rows.sort(key=lambda item: (item.get("case_id"), item.get("variant")))
    if diff_rows != expected_diffs:
        raise EvaluationError("pre-evaluator diffs do not match evaluated receipts")

    agent_rows = _read_jsonl(root / "agent-events.jsonl", "agent events")
    expected_lifecycle = {
        (
            item["case_id"],
            item["variant"],
            observation["assignment_id"],
            observation["actor_id"],
            observation["role"],
        )
        for item in expected_receipts
        for observation in item["agent_telemetry"]["observed_assignments"]
    }
    lifecycle_count = _validate_host_actor_binding(
        expected_lifecycle, agent_rows, verdict
    )

    analysis_sha256 = _canonical_sha256(analysis_binding)
    if (
        not isinstance(analysis, dict)
        or analysis.get("schema_version") != 1
        or analysis.get("receipt_set_sha256") != receipt_set_sha256
        or analysis.get("analysis_sha256") != analysis_sha256
    ):
        raise EvaluationError("analysis artifact does not match calculated metrics")
    return {
        "receipt_set_sha256": receipt_set_sha256,
        "analysis_sha256": analysis_sha256,
        "host_lifecycle_count": lifecycle_count,
    }


def _number(value: object, field: str, *, minimum: float = 0.0) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EvaluationError(f"{field} must be a finite number >= {minimum}")
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise EvaluationError(
            f"{field} must be a finite number >= {minimum}"
        ) from error
    if not math.isfinite(number) or number < minimum:
        raise EvaluationError(f"{field} must be a finite number >= {minimum}")
    return number


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    number = _number(value, field, minimum=float(minimum))
    if not number.is_integer():
        raise EvaluationError(f"{field} must be an integer >= {minimum}")
    return int(number)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{field} must be non-empty")
    return value.strip()


def _string_list(value: object, field: str, *, non_empty: bool = False) -> list[str]:
    if (
        not isinstance(value, list)
        or (non_empty and not value)
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        suffix = "non-empty " if non_empty else ""
        raise EvaluationError(f"{field} must be a {suffix}string list")
    return [item.strip() for item in value]


def _sha256(value: object, field: str) -> str:
    digest = _string(value, field).lower()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise EvaluationError(f"{field} must be a SHA-256 hex digest")
    return digest


def validate_task_contract(value: object) -> dict[str, Any]:
    """Normalize the frozen v2 task-definition contract."""
    if not isinstance(value, dict):
        raise EvaluationError("task contract must be an object")
    if value.get("schema_version") not in {2, 3}:
        raise EvaluationError("task contract schema_version must be 2 or 3")
    task_set_id = _string(value.get("task_set_id"), "task_set_id")
    if value.get("contains_run_results") is not False:
        raise EvaluationError("task contract must declare contains_run_results false")

    protocol = value.get("protocol")
    if not isinstance(protocol, dict):
        raise EvaluationError("protocol must be an object")
    locked = _string_list(
        protocol.get("locked_between_arms"), "locked_between_arms", non_empty=True
    )
    required_locks = {
        "model",
        "reasoning_effort",
        "prompt",
        "tools",
        "permissions",
        "fixture_id",
        "source_sha256",
    }
    if not required_locks.issubset(locked):
        raise EvaluationError(
            "protocol does not lock every required arm identity field"
        )
    if protocol.get("quality_before_efficiency") is not True:
        raise EvaluationError("protocol must require quality_before_efficiency")
    if protocol.get("reject_critical_failures") is not True:
        raise EvaluationError("protocol must reject critical failures")
    minimum_quality_delta = _number(
        protocol.get("minimum_average_quality_delta"),
        "protocol.minimum_average_quality_delta",
        minimum=0.0,
    )
    if minimum_quality_delta > 1:
        raise EvaluationError("minimum_average_quality_delta must not exceed 1")

    rounds = value.get("rounds")
    if not isinstance(rounds, dict) or set(rounds) != {"pilot", "promotion"}:
        raise EvaluationError("rounds must define pilot and promotion")
    normalized_rounds: dict[str, dict[str, Any]] = {}
    round_task_ids: dict[str, set[str]] = {}
    for split, held_out in (("pilot", False), ("promotion", True)):
        round_value = rounds[split]
        if not isinstance(round_value, dict):
            raise EvaluationError(f"rounds.{split} must be an object")
        task_ids = _string_list(
            round_value.get("task_ids"), f"rounds.{split}.task_ids", non_empty=True
        )
        if len(task_ids) != len(set(task_ids)):
            raise EvaluationError(f"rounds.{split}.task_ids contains duplicates")
        repetitions = _integer(
            round_value.get("repetitions_per_task"),
            f"rounds.{split}.repetitions_per_task",
            minimum=2,
        )
        if round_value.get("held_out") is not held_out:
            raise EvaluationError(
                f"rounds.{split}.held_out must be {str(held_out).lower()}"
            )
        order = round_value.get("arm_order")
        if not isinstance(order, dict) or order.get("method") != "seeded-balanced":
            raise EvaluationError(f"rounds.{split}.arm_order must use seeded-balanced")
        seed = _string(order.get("seed"), f"rounds.{split}.arm_order.seed")
        normalized_rounds[split] = {
            "task_ids": task_ids,
            "repetitions_per_task": repetitions,
            "held_out": held_out,
            "arm_order": {"method": "seeded-balanced", "seed": seed},
        }
        round_task_ids[split] = set(task_ids)
    if round_task_ids["pilot"] & round_task_ids["promotion"]:
        raise EvaluationError("pilot and promotion task IDs must be disjoint")

    tasks_value = value.get("tasks")
    if not isinstance(tasks_value, list) or not tasks_value:
        raise EvaluationError("tasks must be a non-empty list")
    tasks: dict[str, dict[str, Any]] = {}
    split_categories: dict[str, set[str]] = {"pilot": set(), "promotion": set()}
    for index, task_value in enumerate(tasks_value):
        field = f"tasks[{index}]"
        if not isinstance(task_value, dict):
            raise EvaluationError(f"{field} must be an object")
        task_id = _string(task_value.get("task_id"), f"{field}.task_id")
        if task_id in tasks:
            raise EvaluationError(f"duplicate task_id {task_id}")
        version = _integer(task_value.get("version"), f"{field}.version", minimum=1)
        split = _string(task_value.get("split"), f"{field}.split")
        if split not in normalized_rounds:
            raise EvaluationError(f"{field}.split must be pilot or promotion")
        category = _string(task_value.get("category"), f"{field}.category")
        if category not in REQUIRED_CATEGORIES:
            raise EvaluationError(f"{field}.category is not recognized")
        prompt = _string(task_value.get("prompt"), f"{field}.prompt")
        initial_state = _string(
            task_value.get("initial_state"), f"{field}.initial_state"
        )
        expected_outcome = _string(
            task_value.get("expected_outcome"), f"{field}.expected_outcome"
        )
        independent_tests = _string_list(
            task_value.get("independent_tests"),
            f"{field}.independent_tests",
            non_empty=True,
        )
        critical = _string_list(
            task_value.get("critical_failures"),
            f"{field}.critical_failures",
            non_empty=True,
        )
        fixture_id = _string(task_value.get("fixture_id"), f"{field}.fixture_id")
        expected_mode_value = task_value.get("expected_mode")
        expected_mode = None
        if expected_mode_value is not None:
            expected_mode = _string(expected_mode_value, f"{field}.expected_mode")
            if expected_mode not in AGENT_PLAN_MODES:
                raise EvaluationError(f"{field}.expected_mode is not recognized")
        budgets = task_value.get("budgets")
        if not isinstance(budgets, dict):
            raise EvaluationError(f"{field}.budgets must be an object")
        normalized_budgets = {
            "max_total_tokens": _integer(
                budgets.get("max_total_tokens"),
                f"{field}.budgets.max_total_tokens",
                minimum=1,
            ),
            "max_elapsed_seconds": _number(
                budgets.get("max_elapsed_seconds"),
                f"{field}.budgets.max_elapsed_seconds",
                minimum=0.001,
            ),
        }
        tasks[task_id] = {
            "task_id": task_id,
            "version": version,
            "split": split,
            "category": category,
            "prompt": prompt,
            "initial_state": initial_state,
            "expected_outcome": expected_outcome,
            "independent_tests": independent_tests,
            "critical_failures": critical,
            "fixture_id": fixture_id,
            "expected_mode": expected_mode,
            "budgets": normalized_budgets,
        }
        split_categories[split].add(category)
    if set(tasks) != round_task_ids["pilot"] | round_task_ids["promotion"]:
        raise EvaluationError("round task IDs must exactly match task definitions")
    for task_id, task in tasks.items():
        if task_id not in round_task_ids[task["split"]]:
            raise EvaluationError(f"task {task_id} is assigned to the wrong round")
    for split, categories in split_categories.items():
        if categories != REQUIRED_CATEGORIES:
            raise EvaluationError(f"{split} must contain all five required categories")
    return {
        "schema_version": 2,
        "task_set_id": task_set_id,
        "contains_run_results": False,
        "protocol": {
            **protocol,
            "locked_between_arms": locked,
            "minimum_average_quality_delta": minimum_quality_delta,
        },
        "rounds": normalized_rounds,
        "tasks": tasks,
    }


def normalize_receipt(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError("receipt must be an object")
    result: dict[str, Any] = {}
    for field in ("case_id", "variant", "provider", "task"):
        result[field] = _string(value.get(field), field)
    if result["variant"] not in {"baseline", "candidate"}:
        raise EvaluationError("variant must be baseline or candidate")
    for field in ("success", "live_execution"):
        if not isinstance(value.get(field), bool):
            raise EvaluationError(f"{field} must be boolean")
        result[field] = value[field]
    result["critical_errors"] = _string_list(
        value.get("critical_errors"), "critical_errors"
    )
    result["evidence"] = _string_list(value.get("evidence"), "evidence", non_empty=True)
    result["quality_score"] = _number(value.get("quality_score"), "quality_score")
    if result["quality_score"] > 1:
        raise EvaluationError("quality_score must not exceed 1")
    for field in ("input_tokens", "output_tokens", "elapsed_seconds"):
        result[field] = _number(value.get(field), field)
    cached = value.get("cached_input_tokens", 0)
    result["cached_input_tokens"] = _number(cached, "cached_input_tokens")
    default_fresh = result["input_tokens"] - result["cached_input_tokens"]
    result["fresh_input_tokens"] = _number(
        value.get("fresh_input_tokens", default_fresh), "fresh_input_tokens"
    )
    if (
        result["cached_input_tokens"] + result["fresh_input_tokens"]
        != result["input_tokens"]
    ):
        raise EvaluationError("cached and fresh input tokens must sum to input_tokens")

    schema_version = value.get("schema_version", 1)
    if schema_version not in {1, 2}:
        raise EvaluationError("receipt schema_version must be 1 or 2")
    result["schema_version"] = schema_version
    if schema_version == 2:
        for field in (
            "task_set_id",
            "task_id",
            "split",
            "model",
            "reasoning_effort",
            "prompt",
            "fixture_id",
            "randomization_seed",
        ):
            result[field] = _string(value.get(field), field)
        if result["split"] not in {"pilot", "promotion"}:
            raise EvaluationError("split must be pilot or promotion")
        result["task_version"] = _integer(
            value.get("task_version"), "task_version", minimum=1
        )
        result["repetition"] = _integer(
            value.get("repetition"), "repetition", minimum=1
        )
        result["source_sha256"] = _sha256(value.get("source_sha256"), "source_sha256")
        result["tools"] = _string_list(value.get("tools"), "tools")
        result["permissions"] = _string_list(value.get("permissions"), "permissions")
        order = value.get("arm_order")
        if order not in (["baseline", "candidate"], ["candidate", "baseline"]):
            raise EvaluationError(
                "arm_order must contain baseline and candidate exactly once"
            )
        result["arm_order"] = list(order)
        if not isinstance(value.get("independent_tests_passed"), bool):
            raise EvaluationError("independent_tests_passed must be boolean")
        result["independent_tests_passed"] = value["independent_tests_passed"]
        for field in ("turns", "tool_calls", "retries"):
            result[field] = _integer(value.get(field), field)
        if "agent_slots" in value:
            result["agent_slots"] = _integer(
                value.get("agent_slots"), "agent_slots", minimum=1
            )
        for field in LIVE_IDENTITY_FIELDS:
            if field in {"agent_slots", "controller_protocol_id"}:
                continue
            if field in value:
                result[field] = _sha256(value.get(field), field)
        if "controller_protocol_id" in value:
            result["controller_protocol_id"] = _string(
                value.get("controller_protocol_id"), "controller_protocol_id"
            )
        controller_mode = value.get("controller_mode")
        if controller_mode is not None:
            result["controller_mode"] = _string(controller_mode, "controller_mode")
            if result["controller_mode"] not in CONTROLLER_MODES:
                raise EvaluationError("controller_mode is not recognized")
        telemetry = value.get("agent_telemetry")
        if telemetry is not None:
            if not isinstance(telemetry, dict):
                raise EvaluationError("agent_telemetry must be an object")
            telemetry_mode = _string(
                telemetry.get("controller_mode"), "agent_telemetry.controller_mode"
            )
            if telemetry_mode != result.get("controller_mode"):
                raise EvaluationError("agent telemetry controller mode does not match")
            spawn_count = _integer(
                telemetry.get("spawn_count"), "agent_telemetry.spawn_count"
            )
            join_count = _integer(
                telemetry.get("join_count"), "agent_telemetry.join_count"
            )
            if not isinstance(telemetry.get("complete"), bool):
                raise EvaluationError("agent_telemetry.complete must be boolean")
            actual_mode = telemetry.get("actual_mode")
            if actual_mode is not None:
                actual_mode = _string(actual_mode, "agent_telemetry.actual_mode")
                if actual_mode not in AGENT_PLAN_MODES:
                    raise EvaluationError(
                        "agent_telemetry.actual_mode is not recognized"
                    )
            result["agent_telemetry"] = {
                **telemetry,
                "controller_mode": telemetry_mode,
                "spawn_count": spawn_count,
                "join_count": join_count,
                "complete": telemetry["complete"],
                "actual_mode": actual_mode,
            }
            observations = telemetry.get("observed_assignments", [])
            if not isinstance(observations, list) or not all(
                isinstance(item, dict) for item in observations
            ):
                raise EvaluationError(
                    "agent_telemetry.observed_assignments must be a list"
                )
            normalized_observations: list[dict[str, str]] = []
            for index, observation in enumerate(observations):
                normalized_observations.append(
                    {
                        "assignment_id": _string(
                            observation.get("assignment_id"),
                            f"agent_telemetry.observed_assignments[{index}].assignment_id",
                        ),
                        "actor_id": _string(
                            observation.get("actor_id"),
                            f"agent_telemetry.observed_assignments[{index}].actor_id",
                        ),
                        "role": _string(
                            observation.get("role"),
                            f"agent_telemetry.observed_assignments[{index}].role",
                        ),
                    }
                )
            result["agent_telemetry"]["observed_assignments"] = normalized_observations
            result["agent_execution_claim_eligible"] = False
            execution = telemetry.get("agent_execution_receipt")
            if telemetry.get("schema_version") == 2 and isinstance(execution, dict):
                selected_mode = _string(
                    execution.get("selected_mode"),
                    "agent_execution_receipt.selected_mode",
                )
                executed_mode = _string(
                    execution.get("executed_mode"),
                    "agent_execution_receipt.executed_mode",
                )
                if (
                    selected_mode not in AGENT_PLAN_MODES
                    or executed_mode not in AGENT_PLAN_MODES
                ):
                    raise EvaluationError("agent execution mode is not recognized")
                assignment_fields = (
                    "planned_assignment_ids",
                    "spawned_assignment_ids",
                    "joined_assignment_ids",
                    "result_assignment_ids",
                )
                assignment_sets = {
                    field: _string_list(
                        execution.get(field), f"agent_execution_receipt.{field}"
                    )
                    for field in assignment_fields
                }
                planned_ids = assignment_sets["planned_assignment_ids"]
                exact_lifecycle = len(planned_ids) == len(set(planned_ids)) and all(
                    sorted(items) == sorted(planned_ids)
                    for items in assignment_sets.values()
                )
                usage = execution.get("descendant_usage")
                usage_complete = isinstance(usage, dict) and set(usage) == set(
                    planned_ids
                )
                planned_semantics = execution.get("planned_assignments")
                lifecycle_bindings = execution.get("lifecycle_bindings")
                semantic_complete = (
                    execution.get("semantic_binding") is True
                    and isinstance(planned_semantics, list)
                    and isinstance(lifecycle_bindings, list)
                    and all(isinstance(item, dict) for item in planned_semantics)
                    and all(isinstance(item, dict) for item in lifecycle_bindings)
                )
                if semantic_complete:
                    expected = {
                        item.get("assignment_id"): item for item in planned_semantics
                    }
                    observed = {
                        item.get("assignment_id"): item for item in lifecycle_bindings
                    }
                    actor_ids = [item.get("actor_id") for item in lifecycle_bindings]
                    semantic_complete = (
                        len(expected) == len(planned_semantics)
                        and len(observed) == len(lifecycle_bindings)
                        and set(expected) == set(planned_ids) == set(observed)
                        and len(actor_ids) == len(set(actor_ids))
                        and all(
                            observed[assignment_id].get("role") == item.get("role")
                            and observed[assignment_id].get("delegation_depth")
                            == item.get("delegation_depth")
                            and isinstance(
                                observed[assignment_id].get("parent_id"), str
                            )
                            and bool(observed[assignment_id]["parent_id"])
                            for assignment_id, item in expected.items()
                        )
                    )
                parent_thread_id = execution.get("parent_thread_id")
                host_identity = value.get("host_identity")
                host_valid = (
                    isinstance(host_identity, dict)
                    and isinstance(host_identity.get("version"), str)
                    and isinstance(host_identity.get("executable_sha256"), str)
                    and len(host_identity["executable_sha256"]) == 64
                    and host_identity.get("features", {}).get("multi_agent") is True
                    and host_identity.get("persistent_parent_thread") is True
                )
                result["host_identity"] = host_identity if host_valid else None
                result["agent_execution_claim_eligible"] = bool(
                    execution.get("schema_version") == 2
                    and execution.get("complete") is True
                    and execution.get("outcome") == "completed"
                    and selected_mode == executed_mode
                    and exact_lifecycle
                    and usage_complete
                    and semantic_complete
                    and isinstance(parent_thread_id, str)
                    and bool(parent_thread_id)
                    and host_valid
                )
        if result["live_execution"]:
            missing = [
                field
                for field in (
                    *LIVE_IDENTITY_FIELDS,
                    "controller_mode",
                    "agent_telemetry",
                )
                if field not in result
            ]
            if missing:
                raise EvaluationError(
                    "live schema-v2 receipt lacks frozen identity: "
                    + ", ".join(missing)
                )
            if not result["agent_telemetry"]["complete"]:
                raise EvaluationError("live agent telemetry is incomplete")
    return result


def _bind_receipt_to_task(receipt: dict[str, Any], contract: dict[str, Any]) -> None:
    if receipt["schema_version"] != 2:
        raise EvaluationError("a task contract requires schema_version 2 receipts")
    if receipt["task_set_id"] != contract["task_set_id"]:
        raise EvaluationError("receipt task_set_id does not match task contract")
    task = contract["tasks"].get(receipt["task_id"])
    if task is None:
        raise EvaluationError(f"unknown task_id {receipt['task_id']}")
    expected = {
        "task_version": task["version"],
        "split": task["split"],
        "prompt": task["prompt"],
        "fixture_id": task["fixture_id"],
    }
    for field, expected_value in expected.items():
        if receipt[field] != expected_value:
            raise EvaluationError(
                f"receipt {field} does not match task {receipt['task_id']}"
            )
    round_value = contract["rounds"][receipt["split"]]
    if receipt["randomization_seed"] != round_value["arm_order"]["seed"]:
        raise EvaluationError("receipt randomization_seed does not match its round")
    if receipt["repetition"] > round_value["repetitions_per_task"]:
        raise EvaluationError("receipt repetition exceeds the frozen round schedule")


def _protocol_status(
    pairs: list[dict[str, Any]], contract: dict[str, Any] | None
) -> dict[str, Any]:
    if contract is None:
        return {"complete": True, "rounds": {}}
    rounds: dict[str, Any] = {}
    complete = True
    for split, round_value in contract["rounds"].items():
        expected_repetitions = set(range(1, round_value["repetitions_per_task"] + 1))
        task_results: dict[str, Any] = {}
        for task_id in round_value["task_ids"]:
            selected = [pair for pair in pairs if pair.get("task_id") == task_id]
            repetitions = {pair["repetition"] for pair in selected}
            first_arms = Counter(pair["arm_order"][0] for pair in selected)
            balanced = (
                first_arms["baseline"] > 0
                and first_arms["candidate"] > 0
                and abs(first_arms["baseline"] - first_arms["candidate"]) <= 1
            )
            task_complete = repetitions == expected_repetitions and balanced
            task_results[task_id] = {
                "expected_repetitions": len(expected_repetitions),
                "observed_repetitions": sorted(repetitions),
                "balanced_arm_order": balanced,
                "complete": task_complete,
            }
            complete = complete and task_complete
        rounds[split] = {
            "held_out": round_value["held_out"],
            "complete": all(item["complete"] for item in task_results.values()),
            "tasks": task_results,
        }
    return {"complete": complete, "rounds": rounds}


def _bootstrap_interval(
    values: Sequence[float], *, median: bool, seed: str, samples: int = 2000
) -> list[float] | None:
    if not values:
        return None
    statistic = statistics.median if median else statistics.fmean
    if len(values) == 1:
        value = float(values[0])
        return [value, value]
    generator = random.Random(seed)
    estimates = sorted(
        float(statistic(generator.choices(values, k=len(values))))
        for _ in range(samples)
    )
    return [estimates[int(samples * 0.025)], estimates[int(samples * 0.975) - 1]]


def _task_level_values(
    pairs: Sequence[dict[str, Any]], field: str, *, median: bool
) -> list[float]:
    grouped: dict[str, list[float]] = {}
    for pair in pairs:
        value = pair.get(field)
        fixture_id = pair.get("fixture_id") or pair.get("task_id")
        if isinstance(value, (int, float)) and isinstance(fixture_id, str):
            grouped.setdefault(fixture_id, []).append(float(value))
    statistic = statistics.median if median else statistics.fmean
    return [float(statistic(values)) for _, values in sorted(grouped.items())]


def _stratified_fixture_interval(
    pairs: Sequence[dict[str, Any]],
    field: str,
    *,
    median: bool,
    seed: str,
    samples: int,
) -> list[float] | None:
    fixtures: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        value = pair.get(field)
        fixture_id = pair.get("fixture_id")
        category = pair.get("category")
        expected_mode = pair.get("expected_mode")
        if not isinstance(value, (int, float)) or not all(
            isinstance(item, str) and item
            for item in (fixture_id, category, expected_mode)
        ):
            continue
        fixture = fixtures.setdefault(
            fixture_id, {"stratum": f"{category}|{expected_mode}", "values": []}
        )
        if fixture["stratum"] != f"{category}|{expected_mode}":
            raise EvaluationError("fixture appears in more than one bootstrap stratum")
        fixture["values"].append(float(value))
    statistic = statistics.median if median else statistics.fmean
    strata: dict[str, list[float]] = {}
    for fixture in fixtures.values():
        strata.setdefault(fixture["stratum"], []).append(
            float(statistic(fixture["values"]))
        )
    if not strata:
        return None
    all_values = [value for values in strata.values() for value in values]
    if len(all_values) == 1:
        return [all_values[0], all_values[0]]
    generator = random.Random(seed)
    estimates: list[float] = []
    for _ in range(samples):
        sampled: list[float] = []
        for stratum in sorted(strata):
            values = strata[stratum]
            sampled.extend(generator.choices(values, k=len(values)))
        estimates.append(float(statistic(sampled)))
    estimates.sort()
    return [estimates[int(samples * 0.025)], estimates[int(samples * 0.975) - 1]]


def _agent_plan_compliant(
    telemetry: Mapping[str, Any], expected_mode: str | None
) -> bool:
    actual_mode = telemetry.get("actual_mode")
    spawns = telemetry.get("spawn_count")
    joins = telemetry.get("join_count")
    if (
        actual_mode not in AGENT_PLAN_MODES
        or not isinstance(spawns, int)
        or telemetry.get("usage_includes_subagents") is not True
    ):
        return False
    if expected_mode is not None and actual_mode != expected_mode:
        return False
    minimum_spawns = (
        0
        if actual_mode == "solo"
        else (2 if actual_mode.startswith("parallel-") else 1)
    )
    if spawns < minimum_spawns or (
        spawns and (not isinstance(joins, int) or joins < 1)
    ):
        return False
    plans = telemetry.get("plan_receipts")
    if not isinstance(plans, list) or len(plans) != 1 or not isinstance(plans[0], dict):
        return False
    plan = plans[0]
    waves = plan.get("waves")
    if not isinstance(waves, list):
        return False
    assignments: list[dict[str, Any]] = []
    for wave in waves:
        if not isinstance(wave, dict) or not isinstance(wave.get("assignments"), list):
            return False
        if not all(isinstance(item, dict) for item in wave["assignments"]):
            return False
        assignments.extend(wave["assignments"])
    identifiers = [item.get("assignment_id") or item.get("id") for item in assignments]
    if any(not isinstance(item, str) or not item for item in identifiers):
        return actual_mode == "solo" and not assignments
    if len(identifiers) != len(set(identifiers)):
        return False
    planned_agents = plan.get("total_planned_agents")
    if not isinstance(planned_agents, int) or planned_agents != spawns:
        return False
    verifier_ids = {
        identifiers[index]
        for index, item in enumerate(assignments)
        if item.get("role") == "verifier"
    }
    executor_ids = set(identifiers) - verifier_ids
    if verifier_ids & executor_ids:
        return False
    observed = telemetry.get("observed_assignments")
    if not isinstance(observed, list):
        return False
    observed_by_assignment: dict[str, dict[str, Any]] = {}
    for item in observed:
        if not isinstance(item, dict):
            return False
        assignment_id = item.get("assignment_id")
        actor_id = item.get("actor_id")
        role = item.get("role")
        if (
            not isinstance(assignment_id, str)
            or assignment_id in observed_by_assignment
            or not isinstance(actor_id, str)
            or not actor_id
            or not isinstance(role, str)
        ):
            return False
        observed_by_assignment[assignment_id] = item
    if set(observed_by_assignment) != set(identifiers):
        return actual_mode == "solo" and not assignments and not observed
    verifier_actors = {
        observed_by_assignment[item]["actor_id"] for item in verifier_ids
    }
    executor_actors = {
        observed_by_assignment[item]["actor_id"] for item in executor_ids
    }
    if verifier_actors & executor_actors:
        return False
    for identifier, assignment in zip(identifiers, assignments, strict=True):
        if observed_by_assignment[identifier]["role"] != assignment.get("role"):
            return False
    owned: list[tuple[str, str]] = []
    for identifier, item in zip(identifiers, assignments, strict=True):
        permissions = item.get("permissions")
        if permissions not in {"read-only", "write-owned-paths"}:
            return False
        can_write = permissions == "write-owned-paths"
        paths = item.get("ownership", [])
        if can_write:
            if not isinstance(paths, list) or not paths:
                return False
            for path in paths:
                if not isinstance(path, str) or not path:
                    return False
                normalized = path.replace("\\", "/").strip("/").casefold()
                for other_id, other in owned:
                    if identifier != other_id and (
                        normalized == other
                        or normalized.startswith(other + "/")
                        or other.startswith(normalized + "/")
                    ):
                        return False
                owned.append((identifier, normalized))
        if item.get("role") == "verifier" and can_write:
            return False
        depth = item.get("delegation_depth")
        if not isinstance(depth, int) or depth < 1 or depth > plan.get("max_depth", -1):
            return False
        if (
            item.get("may_spawn") is not False
            or item.get("may_verify_parent") is not False
        ):
            return False
    return True


def compare(
    receipts: Sequence[object],
    *,
    minimum_live_pairs: int = 3,
    task_contract: Mapping[str, Any] | None = None,
    controller_protocol: Mapping[str, Any] | None = None,
    artifact_index: Path | None = None,
) -> dict[str, Any]:
    if minimum_live_pairs < 1:
        raise EvaluationError("minimum_live_pairs must be >= 1")
    contract = (
        validate_task_contract(task_contract) if task_contract is not None else None
    )
    protocol_identity = (
        dict(controller_protocol) if controller_protocol is not None else None
    )
    if protocol_identity is not None and not all(
        key in protocol_identity
        for key in ("protocol_id", "sha256", "bootstrap_resamples", "bootstrap_seed")
    ):
        raise EvaluationError("controller protocol identity is incomplete")
    if artifact_index is not None and protocol_identity is None:
        raise EvaluationError("artifact bundle requires a controller protocol")
    artifact_bundle = (
        load_artifact_bundle(artifact_index, protocol_identity)
        if artifact_index is not None and protocol_identity is not None
        else None
    )
    normalized = [normalize_receipt(value) for value in receipts]
    if contract is not None:
        for receipt in normalized:
            _bind_receipt_to_task(receipt, contract)
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for receipt in normalized:
        variants = by_case.setdefault(receipt["case_id"], {})
        if receipt["variant"] in variants:
            raise EvaluationError(
                f"duplicate {receipt['variant']} receipt for {receipt['case_id']}"
            )
        variants[receipt["variant"]] = receipt

    pairs: list[dict[str, Any]] = []
    seen_task_repetitions: set[tuple[str, int]] = set()
    for case_id, variants in sorted(by_case.items()):
        if set(variants) != {"baseline", "candidate"}:
            raise EvaluationError(f"case {case_id} is not paired")
        baseline = variants["baseline"]
        candidate = variants["candidate"]
        if baseline["schema_version"] != candidate["schema_version"]:
            raise EvaluationError(f"case {case_id} mixes receipt schema versions")
        if baseline["schema_version"] == 2:
            for field in LOCKED_PAIR_FIELDS:
                if baseline[field] != candidate[field]:
                    raise EvaluationError(
                        f"case {case_id} differs in locked field {field}"
                    )
            key = (baseline["task_id"], baseline["repetition"])
            if key in seen_task_repetitions:
                raise EvaluationError(f"duplicate task repetition {key[0]} #{key[1]}")
            seen_task_repetitions.add(key)
            if baseline["live_execution"] or candidate["live_execution"]:
                for field in LIVE_IDENTITY_FIELDS:
                    if baseline[field] != candidate[field]:
                        raise EvaluationError(
                            f"case {case_id} differs in live identity field {field}"
                        )
                if baseline["provider"] != candidate["provider"]:
                    raise EvaluationError(
                        f"case {case_id} uses different Cognitive Powers builds"
                    )
                if baseline["controller_mode"] != "forced-solo":
                    raise EvaluationError(f"case {case_id} baseline is not forced-solo")
                if candidate["controller_mode"] != "adaptive":
                    raise EvaluationError(f"case {case_id} candidate is not adaptive")

        baseline_tokens = baseline["input_tokens"] + baseline["output_tokens"]
        candidate_tokens = candidate["input_tokens"] + candidate["output_tokens"]
        quality_preserved = candidate["quality_score"] >= baseline["quality_score"]
        independent_tests_passed = baseline.get(
            "independent_tests_passed", True
        ) and candidate.get("independent_tests_passed", True)
        budget_passed = True
        if contract is not None:
            task = contract["tasks"][baseline["task_id"]]
            budget = task["budgets"]
            budget_passed = (
                baseline_tokens <= budget["max_total_tokens"]
                and candidate_tokens <= budget["max_total_tokens"]
                and baseline["elapsed_seconds"] <= budget["max_elapsed_seconds"]
                and candidate["elapsed_seconds"] <= budget["max_elapsed_seconds"]
            )
        critical_failure = bool(
            baseline["critical_errors"] or candidate["critical_errors"]
        )
        quality_gate_passed = (
            baseline["success"]
            and candidate["success"]
            and not critical_failure
            and quality_preserved
            and independent_tests_passed
            and budget_passed
        )
        successful_pair = (
            baseline["success"] and candidate["success"] and not critical_failure
        )
        confirmatory_pair = (
            protocol_identity is not None
            and baseline["schema_version"] == 2
            and baseline["live_execution"]
            and candidate["live_execution"]
        )
        efficiency_eligible = (
            successful_pair if confirmatory_pair else quality_gate_passed
        )
        token_delta = candidate_tokens - baseline_tokens if successful_pair else None
        baseline_fresh = baseline["fresh_input_tokens"]
        candidate_fresh = candidate["fresh_input_tokens"]
        elapsed_delta = candidate["elapsed_seconds"] - baseline["elapsed_seconds"]
        pair: dict[str, Any] = {
            "case_id": case_id,
            "provider": candidate["provider"],
            "passed": quality_gate_passed,
            "status": "eligible" if quality_gate_passed else "rejected",
            "live": baseline["live_execution"] and candidate["live_execution"],
            "baseline_success": baseline["success"],
            "candidate_success": candidate["success"],
            "success_delta": float(candidate["success"]) - float(baseline["success"]),
            "critical_failure": critical_failure,
            "candidate_critical_failure": bool(candidate["critical_errors"]),
            "quality_preserved": quality_preserved,
            "baseline_quality_score": baseline["quality_score"],
            "candidate_quality_score": candidate["quality_score"],
            "quality_delta": candidate["quality_score"] - baseline["quality_score"],
            "independent_tests_passed": independent_tests_passed,
            "budget_passed": budget_passed,
            "efficiency_eligible": efficiency_eligible,
            "token_delta": token_delta,
            "token_reduction_ratio": (
                (baseline_tokens - candidate_tokens) / baseline_tokens
                if successful_pair and baseline_tokens
                else None
            ),
            "total_token_ratio": (
                candidate_tokens / baseline_tokens
                if successful_pair and baseline_tokens
                else None
            ),
            "fresh_input_ratio": (
                candidate_fresh / baseline_fresh
                if successful_pair and baseline_fresh
                else None
            ),
            "elapsed_delta": elapsed_delta,
            "efficiency": (
                {
                    "token_delta": token_delta,
                    "elapsed_delta": elapsed_delta,
                    "non_regressed": token_delta <= 0 and elapsed_delta <= 0,
                    "improved": (token_delta < 0 or elapsed_delta < 0)
                    and token_delta <= 0
                    and elapsed_delta <= 0,
                }
                if efficiency_eligible
                else None
            ),
        }
        if baseline["schema_version"] == 2:
            pair.update(
                {
                    "task_id": baseline["task_id"],
                    "task_version": baseline["task_version"],
                    "split": baseline["split"],
                    "repetition": baseline["repetition"],
                    "arm_order": baseline["arm_order"],
                    "source_sha256": baseline["source_sha256"],
                    "fixture_id": baseline["fixture_id"],
                    "category": (
                        contract["tasks"][baseline["task_id"]]["category"]
                        if contract is not None
                        else None
                    ),
                    "expected_mode": (
                        contract["tasks"][baseline["task_id"]]["expected_mode"]
                        if contract is not None
                        else None
                    ),
                    "actual_mode": candidate.get("agent_telemetry", {}).get(
                        "actual_mode"
                    ),
                }
            )
        pairs.append(pair)

    live_pairs = sum(1 for pair in pairs if pair["live"])
    all_quality = bool(pairs) and all(pair["passed"] for pair in pairs)
    protocol_status = _protocol_status(pairs, contract)
    eligible_efficiency = [
        pair["efficiency"] for pair in pairs if pair["efficiency"] is not None
    ]
    aggregate_efficiency_improved = (
        bool(eligible_efficiency)
        and all(metric["non_regressed"] for metric in eligible_efficiency)
        and any(metric["improved"] for metric in eligible_efficiency)
    )

    if contract is None:
        evidence_scope = pairs
        held_out_complete = True
    else:
        evidence_scope = [pair for pair in pairs if pair.get("split") == "promotion"]
        held_out_complete = protocol_status["rounds"]["promotion"]["complete"]
    scope_live = bool(evidence_scope) and all(pair["live"] for pair in evidence_scope)
    candidate_critical_failure_count = sum(
        pair["candidate_critical_failure"] for pair in evidence_scope
    )
    baseline_quality_average = (
        sum(pair["baseline_quality_score"] for pair in evidence_scope)
        / len(evidence_scope)
        if evidence_scope
        else 0.0
    )
    candidate_quality_average = (
        sum(pair["candidate_quality_score"] for pair in evidence_scope)
        / len(evidence_scope)
        if evidence_scope
        else 0.0
    )
    quality_delta = candidate_quality_average - baseline_quality_average
    gates = protocol_identity.get("gates", {}) if protocol_identity else {}
    minimum_quality_delta = (
        float(gates["quality-delta"]["threshold"]) / 100.0
        if gates
        else contract["protocol"]["minimum_average_quality_delta"]
        if contract is not None
        else 0.0
    )
    quality_improved = quality_delta >= minimum_quality_delta
    quality_by_task = _task_level_values(evidence_scope, "quality_delta", median=False)
    total_ratio_by_task = _task_level_values(
        evidence_scope, "total_token_ratio", median=True
    )
    fresh_ratio_by_task = _task_level_values(
        evidence_scope, "fresh_input_ratio", median=True
    )
    solo_scope = [
        pair for pair in evidence_scope if pair.get("expected_mode") == "solo"
    ]
    solo_ratio_by_task = _task_level_values(
        solo_scope, "total_token_ratio", median=True
    )
    bootstrap_samples = (
        int(protocol_identity["bootstrap_resamples"]) if protocol_identity else 2000
    )
    bootstrap_seed = (
        str(protocol_identity["bootstrap_seed"])
        if protocol_identity
        else "legacy-bootstrap"
    )
    if protocol_identity:
        quality_ci = _stratified_fixture_interval(
            evidence_scope,
            "quality_delta",
            median=False,
            seed=bootstrap_seed,
            samples=bootstrap_samples,
        )
        total_ci = _stratified_fixture_interval(
            evidence_scope,
            "total_token_ratio",
            median=True,
            seed=bootstrap_seed,
            samples=bootstrap_samples,
        )
        fresh_ci = _stratified_fixture_interval(
            evidence_scope,
            "fresh_input_ratio",
            median=True,
            seed=bootstrap_seed,
            samples=bootstrap_samples,
        )
    else:
        quality_ci = _bootstrap_interval(
            quality_by_task,
            median=False,
            seed=bootstrap_seed,
            samples=bootstrap_samples,
        )
        total_ci = _bootstrap_interval(
            total_ratio_by_task,
            median=True,
            seed=bootstrap_seed,
            samples=bootstrap_samples,
        )
        fresh_ci = _bootstrap_interval(
            fresh_ratio_by_task,
            median=True,
            seed=bootstrap_seed,
            samples=bootstrap_samples,
        )
    total_ratio = (
        statistics.median(total_ratio_by_task) if total_ratio_by_task else None
    )
    fresh_ratio = (
        statistics.median(fresh_ratio_by_task) if fresh_ratio_by_task else None
    )
    solo_ratio = statistics.median(solo_ratio_by_task) if solo_ratio_by_task else None
    total_ratio_max = float(
        gates.get("total-token-ratio", {}).get("threshold", TOTAL_TOKEN_RATIO_MAX)
    )
    fresh_ratio_max = float(
        gates.get("fresh-input-ratio", {}).get("threshold", FRESH_INPUT_RATIO_MAX)
    )
    solo_ratio_max = float(
        gates.get("solo-token-overhead", {}).get("threshold", SOLO_TOKEN_RATIO_MAX)
    )
    literal_efficiency_passed = (
        total_ratio is not None
        and fresh_ratio is not None
        and solo_ratio is not None
        and total_ratio <= total_ratio_max
        and fresh_ratio <= fresh_ratio_max
        and solo_ratio <= solo_ratio_max
        and total_ci is not None
        and fresh_ci is not None
        and total_ci[1] < total_ratio_max
        and fresh_ci[1] < fresh_ratio_max
    )
    success_by_task: dict[str, list[float]] = {}
    for pair in evidence_scope:
        task_id = pair.get("task_id")
        if isinstance(task_id, str):
            success_by_task.setdefault(task_id, []).append(
                float(pair["candidate_success"]) - float(pair["baseline_success"])
            )
    success_deltas = [statistics.fmean(items) for items in success_by_task.values()]
    success_delta = statistics.fmean(success_deltas) if success_deltas else None
    success_ci = (
        _stratified_fixture_interval(
            evidence_scope,
            "success_delta",
            median=False,
            seed=bootstrap_seed,
            samples=bootstrap_samples,
        )
        if protocol_identity
        else _bootstrap_interval(
            success_deltas, median=False, seed=bootstrap_seed, samples=bootstrap_samples
        )
    )
    success_margin = float(
        gates.get("strict-success-noninferiority", {}).get("threshold", -0.05)
    )
    success_noninferior = (
        success_delta is not None
        and success_delta >= 0
        and success_ci is not None
        and success_ci[0] > success_margin
    )

    routing_pairs = [
        pair for pair in evidence_scope if pair.get("expected_mode") in AGENT_PLAN_MODES
    ]
    true_positive = sum(
        pair["expected_mode"] != "solo" and pair.get("actual_mode") != "solo"
        for pair in routing_pairs
    )
    predicted_positive = sum(
        pair.get("actual_mode") != "solo" for pair in routing_pairs
    )
    eligible_positive = sum(pair["expected_mode"] != "solo" for pair in routing_pairs)
    routing_precision = (
        true_positive / predicted_positive if predicted_positive else 0.0
    )
    routing_recall = true_positive / eligible_positive if eligible_positive else 0.0
    candidate_telemetry = {
        receipt["case_id"]: receipt.get("agent_telemetry")
        for receipt in normalized
        if receipt["variant"] == "candidate"
    }
    agent_compliance = bool(routing_pairs) and all(
        isinstance(candidate_telemetry.get(pair["case_id"]), dict)
        and _agent_plan_compliant(
            candidate_telemetry[pair["case_id"]], pair["expected_mode"]
        )
        for pair in routing_pairs
    )
    precision_min = float(gates.get("mode-precision", {}).get("threshold", 0.90))
    recall_min = float(gates.get("delegation-recall", {}).get("threshold", 0.80))
    routing_passed = (
        bool(routing_pairs)
        and routing_precision >= precision_min
        and routing_recall >= recall_min
        and agent_compliance
    )
    protocol_bound = bool(protocol_identity) and all(
        receipt.get("controller_protocol_id") == protocol_identity["protocol_id"]
        and receipt.get("controller_protocol_sha256") == protocol_identity["sha256"]
        for receipt in normalized
        if receipt["live_execution"]
    )
    analysis_binding = {
        "quality": {
            "baseline_average": baseline_quality_average,
            "candidate_average": candidate_quality_average,
            "delta": quality_delta,
            "ci95": quality_ci,
        },
        "success": {"paired_delta": success_delta, "ci95": success_ci},
        "tokens": {
            "total_ratio": total_ratio,
            "total_ci95": total_ci,
            "fresh_ratio": fresh_ratio,
            "fresh_ci95": fresh_ci,
            "solo_ratio": solo_ratio,
        },
        "routing": {
            "precision": routing_precision,
            "recall": routing_recall,
            "agent_plan_compliant": agent_compliance,
        },
        "protocol_status": protocol_status,
    }
    semantic_artifact_binding = (
        _validate_artifact_semantics(
            artifact_bundle, normalized, analysis_binding, protocol_identity
        )
        if artifact_bundle is not None
        and protocol_identity is not None
        and protocol_bound
        else None
    )
    execution_receipts_eligible = bool(evidence_scope) and all(
        receipt.get("agent_execution_claim_eligible") is True
        for receipt in normalized
        if any(pair["case_id"] == receipt["case_id"] for pair in evidence_scope)
    )
    quality_confident = quality_ci is not None and quality_ci[0] > 0
    proven = (
        contract is not None
        and protocol_bound
        and semantic_artifact_binding is not None
        and len(evidence_scope) >= minimum_live_pairs
        and held_out_complete
        and scope_live
        and candidate_critical_failure_count == 0
        and quality_improved
        and quality_confident
        and success_noninferior
        and literal_efficiency_passed
        and routing_passed
        and execution_receipts_eligible
    )
    if proven:
        reason = None
    elif not pairs:
        reason = "requires paired receipts"
    elif contract is None:
        reason = "requires a versioned task contract and schema v2 receipts"
    elif contract is not None and not held_out_complete:
        reason = "requires the complete repeated, balanced, held-out promotion round"
    elif not scope_live or len(evidence_scope) < minimum_live_pairs:
        reason = "requires enough paired live executions"
    elif not protocol_bound:
        reason = "requires receipts bound to the frozen controller protocol"
    elif not execution_receipts_eligible:
        reason = "requires host-backed agent execution receipts"
    elif semantic_artifact_binding is None:
        reason = (
            "requires a complete hash-verified artifact bundle and independent verdict"
        )
    elif candidate_critical_failure_count:
        reason = "candidate produced a critical failure"
    elif not quality_improved:
        reason = (
            "average candidate quality did not meet the frozen improvement threshold"
        )
    elif not success_noninferior:
        reason = "candidate success did not meet the paired noninferiority gate"
    elif not literal_efficiency_passed:
        reason = "successful pairs did not meet the literal token thresholds"
    elif not routing_passed:
        reason = "routing precision, recall, or agent-plan compliance failed"
    else:
        reason = "quality confidence interval did not exclude zero"
    return {
        "schema_version": 2 if contract is not None else 1,
        "task_set_id": contract["task_set_id"] if contract is not None else None,
        "pairs": pairs,
        "minimum_live_pairs": minimum_live_pairs,
        "live_pairs": live_pairs,
        "all_quality_gates_passed": all_quality,
        "candidate_critical_failure_count": candidate_critical_failure_count,
        "quality": {
            "baseline_average": baseline_quality_average,
            "candidate_average": candidate_quality_average,
            "delta": quality_delta,
            "minimum_delta": minimum_quality_delta,
            "improved": quality_improved,
            "task_level_ci95": quality_ci,
        },
        "success": {
            "paired_delta": success_delta,
            "task_level_ci95": success_ci,
            "noninferior": success_noninferior,
        },
        "token_gates": {
            "successful_pairs_only": True,
            "total_ratio": total_ratio,
            "total_ratio_max": total_ratio_max,
            "total_ratio_ci95": total_ci,
            "fresh_input_ratio": fresh_ratio,
            "fresh_input_ratio_max": fresh_ratio_max,
            "fresh_input_ratio_ci95": fresh_ci,
            "solo_total_ratio": solo_ratio,
            "solo_total_ratio_max": solo_ratio_max,
            "passed": literal_efficiency_passed,
        },
        "routing": {
            "precision": routing_precision,
            "recall": routing_recall,
            "agent_plan_compliant": agent_compliance,
            "passed": routing_passed,
        },
        "protocol": protocol_status,
        "controller_protocol": protocol_identity,
        "artifact_bundle": (
            {**artifact_bundle, "semantic_binding": semantic_artifact_binding}
            if artifact_bundle is not None
            else None
        ),
        "efficiency_evaluated": bool(eligible_efficiency),
        "aggregate_efficiency_improved": aggregate_efficiency_improved,
        "confirmatory_efficiency_passed": literal_efficiency_passed,
        "host_execution_receipts_eligible": execution_receipts_eligible,
        "end_to_end_improvement_proven": proven,
        "verdict": "proven" if proven else "not-proven",
        "reason": reason,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument(
        "--tasks",
        type=Path,
        help="optional frozen schema-v2 task contract for experimental receipts",
    )
    parser.add_argument("--minimum-live-pairs", type=int, default=3)
    parser.add_argument(
        "--controller-protocol", type=Path, default=DEFAULT_CONTROLLER_PROTOCOL
    )
    parser.add_argument(
        "--artifact-index",
        type=Path,
        help="hash index for the complete confirmatory artifact bundle",
    )
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.receipts.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise EvaluationError("receipt file must contain a list")
        task_contract = (
            json.loads(args.tasks.read_text(encoding="utf-8"))
            if args.tasks is not None
            else None
        )
        controller_protocol = load_controller_protocol(args.controller_protocol)
        report = compare(
            value,
            minimum_live_pairs=args.minimum_live_pairs,
            task_contract=task_contract,
            controller_protocol=controller_protocol,
            artifact_index=args.artifact_index,
        )
    except (OSError, json.JSONDecodeError, EvaluationError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return (
        0
        if (
            report["all_quality_gates_passed"]
            or report["end_to_end_improvement_proven"]
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
