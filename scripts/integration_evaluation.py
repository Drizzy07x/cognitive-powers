#!/usr/bin/env python3
"""Compare baseline and optional-provider receipts without inventing live claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence


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


def normalize_receipt(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError("receipt must be an object")
    result: dict[str, Any] = {}
    for field in ("case_id", "variant", "provider", "task"):
        item = value.get(field)
        if not isinstance(item, str) or not item.strip():
            raise EvaluationError(f"{field} must be non-empty")
        result[field] = item.strip()
    if result["variant"] not in {"baseline", "candidate"}:
        raise EvaluationError("variant must be baseline or candidate")
    for field in ("success", "live_execution"):
        if not isinstance(value.get(field), bool):
            raise EvaluationError(f"{field} must be boolean")
        result[field] = value[field]
    errors = value.get("critical_errors")
    evidence = value.get("evidence")
    if not isinstance(errors, list) or not all(
        isinstance(item, str) for item in errors
    ):
        raise EvaluationError("critical_errors must be a string list")
    if (
        not isinstance(evidence, list)
        or not evidence
        or not all(isinstance(item, str) and item.strip() for item in evidence)
    ):
        raise EvaluationError("evidence must be a non-empty string list")
    result["critical_errors"] = errors
    result["evidence"] = evidence
    result["quality_score"] = _number(value.get("quality_score"), "quality_score")
    if result["quality_score"] > 1:
        raise EvaluationError("quality_score must not exceed 1")
    for field in ("input_tokens", "output_tokens", "elapsed_seconds"):
        result[field] = _number(value.get(field), field)
    return result


def compare(
    receipts: Sequence[object], *, minimum_live_pairs: int = 3
) -> dict[str, Any]:
    normalized = [normalize_receipt(value) for value in receipts]
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for receipt in normalized:
        variants = by_case.setdefault(receipt["case_id"], {})
        if receipt["variant"] in variants:
            raise EvaluationError(
                f"duplicate {receipt['variant']} receipt for {receipt['case_id']}"
            )
        variants[receipt["variant"]] = receipt

    pairs: list[dict[str, Any]] = []
    for case_id, variants in sorted(by_case.items()):
        if set(variants) != {"baseline", "candidate"}:
            raise EvaluationError(f"case {case_id} is not paired")
        baseline = variants["baseline"]
        candidate = variants["candidate"]
        baseline_tokens = baseline["input_tokens"] + baseline["output_tokens"]
        candidate_tokens = candidate["input_tokens"] + candidate["output_tokens"]
        quality_preserved = candidate["quality_score"] >= baseline["quality_score"]
        passed = (
            baseline["success"]
            and candidate["success"]
            and not baseline["critical_errors"]
            and not candidate["critical_errors"]
            and quality_preserved
        )
        pairs.append(
            {
                "case_id": case_id,
                "provider": candidate["provider"],
                "passed": passed,
                "live": baseline["live_execution"] and candidate["live_execution"],
                "quality_preserved": quality_preserved,
                "token_delta": candidate_tokens - baseline_tokens,
                "token_reduction_ratio": (
                    (baseline_tokens - candidate_tokens) / baseline_tokens
                    if baseline_tokens
                    else 0.0
                ),
                "elapsed_delta": candidate["elapsed_seconds"]
                - baseline["elapsed_seconds"],
            }
        )
    live_pairs = sum(1 for pair in pairs if pair["live"])
    proven = (
        len(pairs) >= minimum_live_pairs
        and live_pairs == len(pairs)
        and all(pair["passed"] for pair in pairs)
    )
    return {
        "schema_version": 1,
        "pairs": pairs,
        "minimum_live_pairs": minimum_live_pairs,
        "live_pairs": live_pairs,
        "all_quality_gates_passed": bool(pairs)
        and all(pair["passed"] for pair in pairs),
        "end_to_end_improvement_proven": proven,
        "reason": None
        if proven
        else "requires enough paired live executions with successful, error-free, non-regressed quality",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--minimum-live-pairs", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.receipts.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise EvaluationError("receipt file must contain a list")
        report = compare(value, minimum_live_pairs=args.minimum_live_pairs)
    except (OSError, json.JSONDecodeError, EvaluationError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["all_quality_gates_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
