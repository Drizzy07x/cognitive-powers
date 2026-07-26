#!/usr/bin/env python3
"""Evaluate paired baseline/candidate receipts without inventing live claims."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


def _load_evaluation_core():
    path = Path(__file__).resolve().with_name("integration_evaluation_core") / "core.py"
    identity = f"{__name__}:{path}"
    module_name = (
        "_cognitive_integration_evaluation_core_"
        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    )
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load integration evaluation core from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_CORE = _load_evaluation_core()
REQUIRED_CATEGORIES = _CORE.REQUIRED_CATEGORIES
LOCKED_PAIR_FIELDS = _CORE.LOCKED_PAIR_FIELDS
LIVE_IDENTITY_FIELDS = _CORE.LIVE_IDENTITY_FIELDS
CONTROLLER_MODES = _CORE.CONTROLLER_MODES
AGENT_PLAN_MODES = _CORE.AGENT_PLAN_MODES
TOTAL_TOKEN_RATIO_MAX = _CORE.TOTAL_TOKEN_RATIO_MAX
FRESH_INPUT_RATIO_MAX = _CORE.FRESH_INPUT_RATIO_MAX
SOLO_TOKEN_RATIO_MAX = _CORE.SOLO_TOKEN_RATIO_MAX
PLUGIN_ROOT = _CORE.PLUGIN_ROOT
DEFAULT_CONTROLLER_PROTOCOL = _CORE.DEFAULT_CONTROLLER_PROTOCOL
EXPECTED_PROMOTION_GATES = _CORE.EXPECTED_PROMOTION_GATES
EXPECTED_REQUIRED_ARTIFACTS = _CORE.EXPECTED_REQUIRED_ARTIFACTS
EXPECTED_TELEMETRY_FIELDS = _CORE.EXPECTED_TELEMETRY_FIELDS
EXPECTED_IDENTITY_FIELDS = _CORE.EXPECTED_IDENTITY_FIELDS
EvaluationError = _CORE.EvaluationError
load_controller_protocol = _CORE.load_controller_protocol
_contains_link_or_reparse = _CORE._contains_link_or_reparse
_artifact_sha256 = _CORE._artifact_sha256
_diff_manifest_sha256 = _CORE._diff_manifest_sha256
_valid_diff_path = _CORE._valid_diff_path
load_artifact_bundle = _CORE.load_artifact_bundle
_read_jsonl = _CORE._read_jsonl
_canonical_sha256 = _CORE._canonical_sha256
_analysis_binding_from_report = _CORE._analysis_binding_from_report
_validate_host_actor_binding = _CORE._validate_host_actor_binding
_validate_artifact_semantics = _CORE._validate_artifact_semantics
_number = _CORE._number
_integer = _CORE._integer
_string = _CORE._string
_string_list = _CORE._string_list
_sha256 = _CORE._sha256
validate_task_contract = _CORE.validate_task_contract
normalize_receipt = _CORE.normalize_receipt
_bind_receipt_to_task = _CORE._bind_receipt_to_task
_protocol_status = _CORE._protocol_status
_bootstrap_interval = _CORE._bootstrap_interval
_task_level_values = _CORE._task_level_values
_stratified_fixture_interval = _CORE._stratified_fixture_interval
_agent_plan_compliant = _CORE._agent_plan_compliant


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
        baseline_compliant = bool(
            baseline.get("agent_telemetry", {})
            .get("agent_execution_receipt", {})
            .get("complete")
        )
        candidate_compliant = bool(
            candidate.get("agent_telemetry", {})
            .get("agent_execution_receipt", {})
            .get("complete")
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
            "baseline_controller_compliant": baseline_compliant,
            "candidate_controller_compliant": candidate_compliant,
            "plan_compliant_pair": baseline_compliant and candidate_compliant,
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
    plan_compliant_scope = [
        pair for pair in evidence_scope if pair.get("plan_compliant_pair") is True
    ]
    analysis_populations = {
        "primary": "intention-to-treat",
        "itt_pair_count": len(evidence_scope),
        "plan_compliant_pair_count": len(plan_compliant_scope),
        "noncompliant_pair_count": len(evidence_scope) - len(plan_compliant_scope),
        "secondary": "plan-compliant-only",
        "secondary_exclusion_reasons": {
            "controller_noncompliance": len(evidence_scope) - len(plan_compliant_scope)
        },
    }
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
        "analysis_populations": analysis_populations,
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
        "analysis_populations": analysis_populations,
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
        receipts_text = args.receipts.read_text(encoding="utf-8")
        try:
            value = json.loads(receipts_text)
        except json.JSONDecodeError:
            lines = receipts_text.splitlines()
            if not lines or any(not line.strip() for line in lines):
                raise EvaluationError(
                    "receipt file must contain a JSON list or nonblank JSONL records"
                )
            value = []
            for index, line in enumerate(lines):
                try:
                    receipt = json.loads(line)
                except json.JSONDecodeError as error:
                    raise EvaluationError(
                        f"receipt JSONL record {index} is invalid JSON"
                    ) from error
                if not isinstance(receipt, dict):
                    raise EvaluationError(
                        f"receipt JSONL record {index} must be an object"
                    )
                value.append(receipt)
        if not isinstance(value, list) or not value:
            raise EvaluationError("receipt file must contain a non-empty list")
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
