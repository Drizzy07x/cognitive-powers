#!/usr/bin/env python3
"""Evaluate paired baseline/candidate receipts without inventing live claims."""

from __future__ import annotations

import argparse
import json
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


class EvaluationError(ValueError):
    """Raised when paired evaluation evidence is incomplete or malformed."""


def _number(value: object, field: str, *, minimum: float = 0.0) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or float(value) < minimum
    ):
        raise EvaluationError(f"{field} must be a number >= {minimum}")
    return float(value)


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
    if value.get("schema_version") != 2:
        raise EvaluationError("task contract schema_version must be 2")
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
        "protocol": {**protocol, "locked_between_arms": locked},
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


def compare(
    receipts: Sequence[object],
    *,
    minimum_live_pairs: int = 3,
    task_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if minimum_live_pairs < 1:
        raise EvaluationError("minimum_live_pairs must be >= 1")
    contract = (
        validate_task_contract(task_contract) if task_contract is not None else None
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
        token_delta = candidate_tokens - baseline_tokens
        elapsed_delta = candidate["elapsed_seconds"] - baseline["elapsed_seconds"]
        pair: dict[str, Any] = {
            "case_id": case_id,
            "provider": candidate["provider"],
            "passed": quality_gate_passed,
            "status": "eligible" if quality_gate_passed else "rejected",
            "live": baseline["live_execution"] and candidate["live_execution"],
            "critical_failure": critical_failure,
            "quality_preserved": quality_preserved,
            "independent_tests_passed": independent_tests_passed,
            "budget_passed": budget_passed,
            "efficiency_eligible": quality_gate_passed,
            # Raw deltas remain for backward-compatible audit output. They are not
            # treated as improvement unless efficiency_eligible is true.
            "token_delta": token_delta,
            "token_reduction_ratio": (
                (baseline_tokens - candidate_tokens) / baseline_tokens
                if baseline_tokens
                else 0.0
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
                if quality_gate_passed
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
    scope_quality = bool(evidence_scope) and all(
        pair["passed"] for pair in evidence_scope
    )
    scope_efficiency = (
        bool(evidence_scope)
        and all(
            pair["efficiency"] is not None and pair["efficiency"]["non_regressed"]
            for pair in evidence_scope
        )
        and any(
            pair["efficiency"] is not None and pair["efficiency"]["improved"]
            for pair in evidence_scope
        )
    )
    proven = (
        len(evidence_scope) >= minimum_live_pairs
        and held_out_complete
        and scope_live
        and scope_quality
        and scope_efficiency
    )
    if proven:
        reason = None
    elif not pairs:
        reason = "requires paired receipts"
    elif not all_quality:
        reason = "success, critical-failure, independent-test, budget, or quality gate failed"
    elif contract is not None and not held_out_complete:
        reason = "requires the complete repeated, balanced, held-out promotion round"
    elif not scope_live or len(evidence_scope) < minimum_live_pairs:
        reason = "requires enough paired live executions"
    else:
        reason = (
            "quality passed but measured efficiency did not improve without regression"
        )
    return {
        "schema_version": 2 if contract is not None else 1,
        "task_set_id": contract["task_set_id"] if contract is not None else None,
        "pairs": pairs,
        "minimum_live_pairs": minimum_live_pairs,
        "live_pairs": live_pairs,
        "all_quality_gates_passed": all_quality,
        "protocol": protocol_status,
        "efficiency_evaluated": all_quality and bool(eligible_efficiency),
        "aggregate_efficiency_improved": aggregate_efficiency_improved,
        "end_to_end_improvement_proven": proven,
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
        report = compare(
            value,
            minimum_live_pairs=args.minimum_live_pairs,
            task_contract=task_contract,
        )
    except (OSError, json.JSONDecodeError, EvaluationError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["all_quality_gates_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
