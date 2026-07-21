#!/usr/bin/env python3
"""Run deterministic capability-audit contract benchmarks."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "benchmarks" / "capability_cases.json"
AUDITOR_PATH = (
    PLUGIN_ROOT / "skills" / "audit-capabilities" / "scripts" / "capability_audit.py"
)


def load_auditor():
    spec = importlib.util.spec_from_file_location(
        "cognitive_capability_audit", AUDITOR_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load capability auditor from {AUDITOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(cases_path: Path = DEFAULT_CASES) -> dict[str, object]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    root = (cases_path.parent / cases["root"]).resolve()
    payload = {"schema_version": cases["schema_version"], "patterns": cases["patterns"]}
    report = load_auditor().assess(
        root,
        payload,
        as_of=datetime.strptime(cases["as_of"], "%Y-%m-%d").date(),
    )
    by_id = {item["id"]: item for item in report["recommendations"]}
    results = []
    for identifier, expected in cases["expected_actions"].items():
        actual = by_id[identifier]["action"]
        results.append(
            {
                "id": identifier,
                "expected_action": expected,
                "actual_action": actual,
                "passed": actual == expected,
            }
        )
    passed = all(item["passed"] for item in results)
    return {
        "schema_version": 1,
        "suite": "capability-audit-contract",
        "passed": passed,
        "quality_improvement_proven": False,
        "cases": results,
    }


def format_report(report: dict[str, object]) -> str:
    lines = ["Capability audit contract benchmark"]
    for result in report["cases"]:
        status = "PASS" if result["passed"] else "FAIL"
        lines.append(
            f"{status} {result['id']}: expected={result['expected_action']} "
            f"actual={result['actual_action']}"
        )
    lines.append("Quality improvement proven: no")
    lines.append("PASS suite" if report["passed"] else "FAIL suite")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run(args.cases.resolve())
    print(json.dumps(report, indent=2) if args.json else format_report(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
