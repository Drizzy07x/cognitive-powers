#!/usr/bin/env python3
"""Select the smallest useful execution intensity from explicit task signals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REQUEST_MODES = {"answer", "diagnose", "change", "monitor"}
DURABLE_SIGNALS = (
    ("multi_turn_expected", "the work is expected to span multiple turns"),
    ("compaction_risk", "the work must remain recoverable across compaction"),
    ("resumable_required", "the work explicitly requires resumable state"),
    (
        "durable_evidence_required",
        "the outcome requires durable evidence receipts",
    ),
)


class OrchestrationError(ValueError):
    """Raised when task signals violate the orchestration contract."""


def _boolean(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise OrchestrationError(f"{field} must be boolean")
    return value


def _non_negative_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OrchestrationError(f"{field} must be a non-negative integer")
    return value


def select_intensity(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic execution policy and its observable reasons."""
    if payload.get("schema_version") != 1:
        raise OrchestrationError("schema_version must be 1")
    request_mode = payload.get("request_mode")
    if request_mode not in REQUEST_MODES:
        raise OrchestrationError(
            "request_mode must be answer, diagnose, change, or monitor"
        )

    estimated_steps = _non_negative_int(payload, "estimated_steps")
    affected_files = _non_negative_int(payload, "affected_files")
    unclear_context = _boolean(payload, "unclear_context")
    cross_cutting = _boolean(payload, "cross_cutting")
    signals = {field: _boolean(payload, field) for field, _ in DURABLE_SIGNALS}

    reasons: list[str] = []
    durable_reasons = [reason for field, reason in DURABLE_SIGNALS if signals[field]]
    if durable_reasons:
        intensity = "durable"
        reasons.extend(durable_reasons)
    else:
        if cross_cutting:
            reasons.append("the task crosses implementation boundaries")
        if unclear_context:
            reasons.append("the relevant context is not yet established")
        if affected_files >= 2:
            reasons.append("the task affects multiple files")
        if estimated_steps >= 3:
            reasons.append("the task has at least three expected steps")
        if reasons:
            intensity = "standard"
        else:
            intensity = "focused"
            reasons.append("the task is short, local, and recoverable in one pass")

    process = {
        "progressive_context": intensity in {"standard", "durable"},
        "external_state": intensity == "durable",
        "evidence_receipts": intensity == "durable",
        # Memory and delegation require separate demand signals; intensity alone
        # never authorizes either source of overhead.
        "memory_retrieval": False,
        "delegation": False,
    }
    abstained = [name for name, enabled in process.items() if not enabled]
    implementation_authorized = request_mode == "change"
    if request_mode == "diagnose":
        reasons.append("diagnosis preserves investigation-only scope")

    return {
        "schema_version": 1,
        "intensity": intensity,
        "request_mode": request_mode,
        "reasons": reasons,
        "process": process,
        "abstained_process": abstained,
        "implementation_authorized": implementation_authorized,
    }


def evaluate_cases(cases_path: Path) -> dict[str, Any]:
    data = json.loads(cases_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("cases"), list):
        raise OrchestrationError("case file must use schema_version 1 with cases")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, case in enumerate(data["cases"]):
        if not isinstance(case, dict):
            raise OrchestrationError(f"cases[{index}] must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise OrchestrationError(f"cases[{index}].id must be unique and non-empty")
        seen.add(case_id)
        actual = select_intensity(case.get("input", {}))
        checks = {
            "intensity": actual["intensity"] == case.get("expected_intensity"),
            "implementation_authorized": actual["implementation_authorized"]
            == case.get("expected_implementation_authorized"),
        }
        expected_abstentions = case.get("expected_abstentions", [])
        if not isinstance(expected_abstentions, list):
            raise OrchestrationError(
                f"cases[{index}].expected_abstentions must be a list"
            )
        checks["abstentions"] = all(
            item in actual["abstained_process"] for item in expected_abstentions
        )
        results.append(
            {
                "id": case_id,
                "passed": all(checks.values()),
                "checks": checks,
                "actual": actual,
            }
        )
    return {
        "schema_version": 1,
        "suite": "cognitive-powers-orchestration-policy",
        "passed": bool(results) and all(item["passed"] for item in results),
        "case_count": len(results),
        "cases": results,
        "end_to_end_improvement_proven": False,
    }


def _read_object(value: str) -> dict[str, Any]:
    raw = sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise OrchestrationError("input must be a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="task-signal JSON path or - for stdin")
    source.add_argument("--cases", type=Path, help="orchestration case fixture")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = (
            select_intensity(_read_object(args.input))
            if args.input is not None
            else evaluate_cases(args.cases)
        )
    except (OrchestrationError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({"error": str(error)}) if args.json else f"ERROR: {error}")
        return 2
    print(json.dumps(result, indent=2) if args.json else json.dumps(result))
    return 0 if result.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
