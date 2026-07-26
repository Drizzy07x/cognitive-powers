"""Evaluate paired baseline/candidate receipts without inventing live claims."""

from __future__ import annotations

import hashlib
import json
import math
import random
import stat
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
    "controller_protocol_sha256",
    "agent_slots",
    "controller_protocol_id",
)
CONTROLLER_MODES = {"forced-solo", "adaptive"}
AGENT_PLAN_MODES = {"solo", "parallel-read-only", "parallel-packets", "staged-verify"}
TOTAL_TOKEN_RATIO_MAX = 0.85
FRESH_INPUT_RATIO_MAX = 0.80
SOLO_TOKEN_RATIO_MAX = 1.05
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
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
    "rollout_snapshot_before",
    "parent_rollout_sha256",
    "child_rollout_sha256s",
    "observed_agents",
    "observed_task_names",
    "observed_parent_ids",
    "observed_depths",
    "planned_roles",
    "waves",
    "planned_ownership",
    "planned_permissions",
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
    "controller_directive_template_sha256",
    "controller_directive_mode_sha256",
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
    if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2, 3}:
        raise EvaluationError("controller protocol must use schema_version 1, 2, or 3")
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
        or (protocol_schema == 3 and execution_state.get("sessions_completed") != 0)
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


def _contains_link_or_reparse(root: Path, item: Path) -> bool:
    current = root
    for part in item.relative_to(root).parts:
        current /= part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            return True
    return False


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
            if _contains_link_or_reparse(path, item):
                raise EvaluationError(
                    f"artifact directory contains a link or reparse alias: {item.name}"
                )
            if not item.is_file():
                continue
            resolved_item = item.resolve()
            try:
                resolved_item.relative_to(resolved_root)
            except ValueError as error:
                raise EvaluationError(
                    f"artifact directory entry escapes its root: {item.name}"
                ) from error
            files[item.relative_to(path).as_posix()] = hashlib.sha256(
                item.read_bytes()
            ).hexdigest()
        if not files:
            raise EvaluationError(f"artifact directory is empty: {path.name}")
        encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
    raise EvaluationError(f"artifact is missing: {path.name}")


def _diff_manifest_sha256(manifest: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for path, value in sorted(manifest.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _valid_diff_path(path: object) -> bool:
    return (
        isinstance(path, str)
        and bool(path)
        and "\\" not in path
        and not path.startswith("/")
        and ":" not in path.split("/", 1)[0]
        and all(part not in {"", ".", ".."} for part in path.split("/"))
    )


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
    coordinator_index_sha256s = verdict.get("coordinator_index_sha256s")
    if (
        verdict.get("schema_version") != 1
        or verdict.get("verdict") != "confirmed"
        or verdict.get("independent") is not True
        or verdict.get("protocol_id") != controller_protocol.get("protocol_id")
        or verdict.get("controller_protocol_sha256")
        != controller_protocol.get("sha256")
        or verdict.get("evidence_root_sha256") != evidence_root
        or not isinstance(verdict.get("verifier_receipt_sha256"), str)
        or len(verdict["verifier_receipt_sha256"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in verdict["verifier_receipt_sha256"].lower()
        )
        or not isinstance(coordinator_index_sha256s, list)
        or not coordinator_index_sha256s
        or not all(
            isinstance(item, str)
            and len(item) == 64
            and all(character in "0123456789abcdef" for character in item.lower())
            for item in coordinator_index_sha256s
        )
        or len(coordinator_index_sha256s) != len(set(coordinator_index_sha256s))
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


def _analysis_binding_from_report(report: Mapping[str, Any]) -> dict[str, Any]:
    try:
        quality = report["quality"]
        success = report["success"]
        tokens = report["token_gates"]
        routing = report["routing"]
        if not all(
            isinstance(item, Mapping) for item in (quality, success, tokens, routing)
        ):
            raise TypeError
        return {
            "quality": {
                "baseline_average": quality["baseline_average"],
                "candidate_average": quality["candidate_average"],
                "delta": quality["delta"],
                "ci95": quality["task_level_ci95"],
            },
            "success": {
                "paired_delta": success["paired_delta"],
                "ci95": success["task_level_ci95"],
            },
            "tokens": {
                "total_ratio": tokens["total_ratio"],
                "total_ci95": tokens["total_ratio_ci95"],
                "fresh_ratio": tokens["fresh_input_ratio"],
                "fresh_ci95": tokens["fresh_input_ratio_ci95"],
                "solo_ratio": tokens["solo_total_ratio"],
            },
            "routing": {
                "precision": routing["precision"],
                "recall": routing["recall"],
                "agent_plan_compliant": routing["agent_plan_compliant"],
            },
            "analysis_populations": report["analysis_populations"],
            "protocol_status": report["protocol"],
        }
    except (KeyError, TypeError) as error:
        raise EvaluationError("analysis artifact schema is invalid") from error


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
    if (
        not experiment_executor_rows
        or not all(
            isinstance(item.get("actor_id"), str) and item.get("actor_id")
            for item in experiment_executor_rows
        )
        or len(experiment_executors) != len(experiment_executor_rows)
        or experiment_verifier_rows
        or set(verdict.get("executor_ids", [])) != experiment_executors
        or not isinstance(verdict.get("verifier_id"), str)
        or not verdict.get("verifier_id")
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
    schedule_payload = (
        {key: value for key, value in schedule.items() if key != "sha256"}
        if isinstance(schedule, dict)
        else {}
    )
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("controller_protocol_id")
        != controller_protocol.get("protocol_id")
        or manifest.get("controller_protocol_sha256")
        != controller_protocol.get("sha256")
        or len(task_set_ids) != 1
        or manifest.get("task_set_id") != next(iter(task_set_ids))
        or not isinstance(schedule, dict)
        or schedule.get("schema_version") != 1
        or schedule.get("task_set_id") != next(iter(task_set_ids))
        or schedule.get("execution") != "sequential-randomized-pairs"
        or schedule.get("sha256") != _canonical_sha256(schedule_payload)
        or manifest.get("schedule_sha256") != schedule.get("sha256")
    ):
        raise EvaluationError("frozen manifest does not bind the evaluated receipts")

    receipt_cases: dict[str, list[dict[str, Any]]] = {}
    for receipt in expected_receipts:
        receipt_cases.setdefault(receipt["case_id"], []).append(receipt)
    expected_jobs: dict[tuple[str, str, int], list[str]] = {}
    for case_id, case_receipts in receipt_cases.items():
        variants = {item["variant"] for item in case_receipts}
        task_keys = {
            (item["task_id"], item["split"], item["repetition"])
            for item in case_receipts
        }
        arm_orders = {tuple(item["arm_order"]) for item in case_receipts}
        if (
            variants != {"baseline", "candidate"}
            or len(case_receipts) != 2
            or len(task_keys) != 1
            or len(arm_orders) != 1
        ):
            raise EvaluationError(
                f"evaluated receipt pair is not schedule-complete: {case_id}"
            )
        expected_jobs[next(iter(task_keys))] = list(next(iter(arm_orders)))
    jobs = schedule.get("jobs")
    sessions = schedule.get("sessions")
    if not isinstance(jobs, list) or not isinstance(sessions, list):
        raise EvaluationError("randomized schedule does not match evaluated receipts")
    actual_jobs: dict[tuple[str, str, int], dict[str, Any]] = {}
    expected_sessions: list[dict[str, Any]] = []
    ordinal = 0
    for job in jobs:
        if not isinstance(job, dict):
            raise EvaluationError(
                "randomized schedule does not match evaluated receipts"
            )
        key = (job.get("task_id"), job.get("split"), job.get("repetition"))
        if (
            key in actual_jobs
            or key not in expected_jobs
            or not isinstance(job.get("job_id"), str)
            or not job["job_id"]
            or job.get("repetitions") != 1
            or job.get("arm_orders") != [expected_jobs[key]]
        ):
            raise EvaluationError(
                "randomized schedule does not match evaluated receipts"
            )
        actual_jobs[key] = job
        for arm in expected_jobs[key]:
            ordinal += 1
            expected_sessions.append(
                {
                    "ordinal": ordinal,
                    "job_id": job["job_id"],
                    "task_id": key[0],
                    "split": key[1],
                    "repetition": key[2],
                    "arm": arm,
                }
            )
    if set(actual_jobs) != set(expected_jobs) or sessions != expected_sessions:
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
    expected_diffs = {
        (item["case_id"], item["variant"]): item for item in expected_receipts
    }
    observed_diff_keys: set[tuple[str, str]] = set()
    for row in diff_rows:
        if set(row) != {
            "case_id",
            "variant",
            "changed_paths",
            "manifest",
            "sha256",
        }:
            raise EvaluationError("pre-evaluator diff metadata schema is invalid")
        key = (row.get("case_id"), row.get("variant"))
        receipt = expected_diffs.get(key)
        manifest = row.get("manifest")
        changed_paths = row.get("changed_paths")
        if key in observed_diff_keys or receipt is None:
            raise EvaluationError("pre-evaluator diffs do not match evaluated receipts")
        if (
            not isinstance(manifest, dict)
            or not all(_valid_diff_path(path) for path in manifest)
            or not all(
                value == "<deleted>"
                or (
                    isinstance(value, str)
                    and len(value) == 64
                    and all(char in "0123456789abcdef" for char in value.lower())
                )
                for value in manifest.values()
            )
            or not isinstance(changed_paths, list)
            or changed_paths != sorted(manifest)
        ):
            raise EvaluationError("pre-evaluator diff manifest is invalid")
        digest = _sha256(row.get("sha256"), "pre-evaluator diff sha256")
        workspace = receipt["agent_telemetry"].get("workspace_change_check", {})
        if (
            _diff_manifest_sha256(manifest) != digest
            or receipt.get("pre_evaluation_diff_sha256") != digest
            or workspace.get("changed_paths") != changed_paths
        ):
            raise EvaluationError("pre-evaluator diffs do not match evaluated receipts")
        observed_diff_keys.add(key)
    if observed_diff_keys != set(expected_diffs):
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
        or analysis.get("schema_version") != 2
        or analysis.get("receipt_set_sha256") != receipt_set_sha256
        or analysis.get("analysis_sha256") != analysis_sha256
        or _analysis_binding_from_report(analysis) != analysis_binding
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
        if "pre_evaluation_diff_sha256" in value:
            result["pre_evaluation_diff_sha256"] = _sha256(
                value.get("pre_evaluation_diff_sha256"),
                "pre_evaluation_diff_sha256",
            )
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
            observation_complete = telemetry.get(
                "telemetry_observation_complete", telemetry["complete"]
            )
            if not isinstance(observation_complete, bool):
                raise EvaluationError(
                    "agent_telemetry.telemetry_observation_complete must be boolean"
                )
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
                "telemetry_observation_complete": observation_complete,
                "actual_mode": actual_mode,
            }
            observations = telemetry.get("observed_assignments", [])
            if not isinstance(observations, list) or not all(
                isinstance(item, dict) for item in observations
            ):
                raise EvaluationError(
                    "agent_telemetry.observed_assignments must be a list"
                )
            execution = telemetry.get("agent_execution_receipt")
            planned_roles: dict[str, str] = {}
            if isinstance(execution, dict):
                planned_assignments = execution.get("planned_assignments")
                if isinstance(planned_assignments, list):
                    for assignment in planned_assignments:
                        if not isinstance(assignment, dict):
                            continue
                        assignment_id = assignment.get("assignment_id")
                        role = assignment.get("role")
                        if (
                            isinstance(assignment_id, str)
                            and assignment_id
                            and isinstance(role, str)
                            and role
                        ):
                            planned_roles[assignment_id] = role
            normalized_observations: list[dict[str, Any]] = []
            for index, observation in enumerate(observations):
                assignment_id = _string(
                    observation.get("assignment_id"),
                    f"agent_telemetry.observed_assignments[{index}].assignment_id",
                )
                observed_role = observation.get(
                    "role_observed", observation.get("role")
                )
                if observed_role is not None:
                    observed_role = _string(
                        observed_role,
                        f"agent_telemetry.observed_assignments[{index}].role_observed",
                    )
                planned_role = planned_roles.get(assignment_id)
                if (
                    observed_role is not None
                    and planned_role is not None
                    and observed_role != planned_role
                ):
                    raise EvaluationError(
                        "agent telemetry observed role contradicts the planned role"
                    )
                resolved_role = observed_role or planned_role
                if resolved_role is None:
                    raise EvaluationError(
                        "agent telemetry observed assignment has no resolved role"
                    )
                normalized_observations.append(
                    {
                        "assignment_id": assignment_id,
                        "actor_id": _string(
                            observation.get("actor_id"),
                            f"agent_telemetry.observed_assignments[{index}].actor_id",
                        ),
                        "role": resolved_role,
                        "role_observed": observed_role,
                        "parent_id": observation.get("parent_id"),
                        "delegation_depth": observation.get("delegation_depth"),
                        "task_name": observation.get("task_name"),
                        "binding_provenance": observation.get("binding_provenance"),
                    }
                )
            result["agent_telemetry"]["observed_assignments"] = normalized_observations
            result["agent_execution_claim_eligible"] = False
            execution = telemetry.get("agent_execution_receipt")
            if telemetry.get("schema_version") in {2, 3} and isinstance(
                execution, dict
            ):
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
                            (
                                observed[assignment_id].get("role_observed") is None
                                or observed[assignment_id].get("role_observed")
                                == item.get("role")
                            )
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
                workspace_check = telemetry.get("workspace_change_check")
                workspace_valid = (
                    isinstance(workspace_check, dict)
                    and workspace_check.get("provenance") == "pre-evaluator-tree-diff"
                    and (
                        executed_mode != "parallel-read-only"
                        or workspace_check.get("read_only_unchanged") is True
                    )
                )
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
                    execution.get("schema_version") == 3
                    and execution.get("complete") is True
                    and execution.get("outcome") == "completed"
                    and selected_mode == executed_mode
                    and exact_lifecycle
                    and usage_complete
                    and semantic_complete
                    and isinstance(parent_thread_id, str)
                    and bool(parent_thread_id)
                    and host_valid
                    and workspace_valid
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
            if not result["agent_telemetry"]["telemetry_observation_complete"]:
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
        or (
            actual_mode != "solo"
            and telemetry.get("usage_includes_subagents") is not True
        )
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
        role = item.get("role_observed", item.get("role"))
        if (
            not isinstance(assignment_id, str)
            or assignment_id in observed_by_assignment
            or not isinstance(actor_id, str)
            or not actor_id
            or (
                role is None
                and item.get("binding_provenance") != "persistent-rollout-v3"
            )
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
        if observed_by_assignment[identifier].get(
            "role_observed", observed_by_assignment[identifier].get("role")
        ) is not None and observed_by_assignment[identifier].get(
            "role_observed", observed_by_assignment[identifier].get("role")
        ) != assignment.get("role"):
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
