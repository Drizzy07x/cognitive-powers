#!/usr/bin/env python3
"""Validate adaptive communication contracts without claiming model improvement."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "communicate-efficiently"
    / "scripts"
    / "communication_contract.py"
)
DEFAULT_CASES = PLUGIN_ROOT / "benchmarks" / "communication_cases.json"


def load_contract() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "cognitive_communication_contract", CONTRACT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load contract module: {CONTRACT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(cases_path: Path) -> dict[str, object]:
    contract = load_contract()
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("communication benchmark requires a non-empty case list")
    results: list[dict[str, object]] = []
    for case in cases:
        selection = contract.select_profile(case["signals"])
        assessment = contract.assess_output(case, case["candidate"])
        results.append(
            {
                "id": case["id"],
                "passed": selection["profile"] == case["expected_profile"]
                and assessment["passed"],
                "selectedProfile": selection["profile"],
                "expectedProfile": case["expected_profile"],
                "integrityPassed": assessment["integrityPassed"],
                "budgetPassed": assessment["budgetPassed"],
                "fillerPassed": assessment["fillerPassed"],
                "wordCount": assessment["wordCount"],
            }
        )
    passed = all(item["passed"] for item in results)
    return {
        "suite": "adaptive-communication-contract",
        "passed": passed,
        "contractPassed": passed,
        "endToEndImprovementValidated": False,
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run(args.cases.resolve())
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Adaptive communication contract benchmark")
        for case in report["cases"]:
            label = "PASS" if case["passed"] else "FAIL"
            print(f"{label} {case['id']}: {case}")
        print("end-to-end improvement validated: False")
        print("PASS suite" if report["passed"] else "FAIL suite")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
