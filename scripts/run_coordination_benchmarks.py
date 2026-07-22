#!/usr/bin/env python3
"""Run deterministic diagnostic, review, packet, and agent-plan benchmarks."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "benchmarks" / "coordination_cases.json"
DEFAULT_AGENT_CASES = PLUGIN_ROOT / "benchmarks" / "agent_plan_cases.json"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(
    cases_path: Path = DEFAULT_CASES,
    agent_cases_path: Path = DEFAULT_AGENT_CASES,
) -> dict[str, object]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    diagnostic = load_module(
        "benchmark_investigation_protocol",
        PLUGIN_ROOT
        / "skills"
        / "diagnose-systematically"
        / "scripts"
        / "investigation_protocol.py",
    )
    review = load_module(
        "benchmark_review_protocol",
        PLUGIN_ROOT / "skills" / "verify-delivery" / "scripts" / "review_protocol.py",
    )
    work_state = load_module(
        "benchmark_work_state_packets",
        PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "work_state.py",
    )
    orchestration = load_module(
        "benchmark_orchestration_policy",
        PLUGIN_ROOT / "scripts" / "orchestration_policy.py",
    )
    results: list[dict[str, object]] = []
    for case in cases["diagnostic_routes"]:
        actual = diagnostic.route(case["input"])["mode"]
        results.append(
            {
                "id": case["id"],
                "passed": actual == case["expected_mode"],
                "actual": actual,
                "expected": case["expected_mode"],
            }
        )
    for case in cases["review_routes"]:
        actual = review.select_angles(case["input"])["security_review_selected"]
        results.append(
            {
                "id": case["id"],
                "passed": actual is case["expected_security"],
                "actual": actual,
                "expected": case["expected_security"],
            }
        )
    for case in cases["packet_plans"]:
        try:
            work_state._validate_packet_plan(case["plan"])
            actual = True
        except work_state.WorkStateError:
            actual = False
        results.append(
            {
                "id": case["id"],
                "passed": actual is case["expected_valid"],
                "actual": actual,
                "expected": case["expected_valid"],
            }
        )
    agent_report = orchestration.evaluate_agent_cases(agent_cases_path)
    expected_agent_cases = {
        case["id"]: case
        for case in json.loads(agent_cases_path.read_text(encoding="utf-8"))["cases"]
    }
    for case in agent_report["cases"]:
        actual = case["actual"]
        fixture = expected_agent_cases[case["id"]]
        expected = {
            "mode": fixture["expected_mode"],
            "spawn_count": fixture["expected_spawn_count"],
        }
        observed = {
            "mode": actual["mode"],
            "spawn_count": actual["spawn_count"],
            "checks": case["checks"],
        }
        results.append(
            {
                "id": case["id"],
                "passed": case["passed"],
                "actual": observed,
                "expected": expected,
            }
        )
    return {
        "schema_version": 1,
        "suite": "adaptive-coordination-contract",
        "passed": all(result["passed"] for result in results),
        "end_to_end_improvement_proven": False,
        "cases": results,
    }


def format_report(report: dict[str, object]) -> str:
    lines = ["Adaptive coordination contract benchmark"]
    for result in report["cases"]:
        status = "PASS" if result["passed"] else "FAIL"
        lines.append(
            f"{status} {result['id']}: expected={result['expected']} actual={result['actual']}"
        )
    lines.append("End-to-end improvement proven: no")
    lines.append("PASS suite" if report["passed"] else "FAIL suite")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--agent-cases", type=Path, default=DEFAULT_AGENT_CASES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run(args.cases.resolve(), args.agent_cases.resolve())
    print(json.dumps(report, indent=2) if args.json else format_report(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
