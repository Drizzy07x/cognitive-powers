#!/usr/bin/env python3
"""Validate design-intent contracts without claiming visual quality."""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from types import ModuleType


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
INTENT_PATH = (
    PLUGIN_ROOT / "skills" / "design-intentionally" / "scripts" / "design_intent.py"
)
DEFAULT_CASES = PLUGIN_ROOT / "benchmarks" / "design_cases.json"
DEFAULT_FIXTURE = PLUGIN_ROOT / "benchmarks" / "fixtures" / "demo-repo"


def load_intent() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "cognitive_design_intent", INTENT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load design intent module: {INTENT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(cases_path: Path, fixture_root: Path) -> dict[str, object]:
    module = load_intent()
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("design benchmark requires a non-empty case list")
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for index, case in enumerate(cases, 1):
            brief = dict(case["brief"])
            brief["project_root"] = str(fixture_root)
            source = root / f"brief-{index}.json"
            source.write_text(json.dumps(brief), encoding="utf-8")
            intent = module.normalize_brief(brief, source)
            passed = (
                intent["readyToImplement"] is case["expected_ready"]
                and intent["foundationDecision"] == case["expected_foundation"]
                and intent["dials"] == case["expected_dials"]
                and intent["unresolvedChoices"] == case["expected_unresolved"]
            )
            results.append(
                {
                    "id": case["id"],
                    "passed": passed,
                    "readyToImplement": intent["readyToImplement"],
                    "foundationDecision": intent["foundationDecision"],
                    "dials": intent["dials"],
                    "unresolvedChoices": intent["unresolvedChoices"],
                }
            )
    contract_passed = all(item["passed"] for item in results)
    return {
        "suite": "design-intent-contract",
        "passed": contract_passed,
        "contractPassed": contract_passed,
        "liveRenderValidated": False,
        "visualQualityValidated": False,
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run(args.cases.resolve(), args.fixture_root.resolve())
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Design intent contract benchmark")
        for case in report["cases"]:
            print(f"{'PASS' if case['passed'] else 'FAIL'} {case['id']}: {case}")
        print("live render validated: False")
        print("visual quality validated: False")
        print("PASS suite" if report["passed"] else "FAIL suite")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
