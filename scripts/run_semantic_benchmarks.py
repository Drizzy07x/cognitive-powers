#!/usr/bin/env python3
"""Run CodeGraph-dependent impact and affected-test benchmarks."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Sequence


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "benchmarks" / "semantic_cases.json"
ADAPTER_PATH = (
    PLUGIN_ROOT / "skills" / "solve-efficiently" / "scripts" / "semantic_context.py"
)


def load_adapter() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "semantic_benchmark_adapter", ADAPTER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ADAPTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _recall(expected: set[str], observed: set[str]) -> float:
    if not expected:
        return 1.0
    return len(expected.intersection(observed)) / len(expected)


def evaluate_case(
    adapter: ModuleType,
    case: dict[str, object],
    *,
    codegraph: str | None,
    fixture_root: Path | None,
) -> dict[str, object]:
    root = fixture_root or (DEFAULT_CASES.parent / str(case["root"])).resolve()
    impact = adapter.impact(
        root,
        str(case["symbol"]),
        executable=codegraph,
        depth=int(case.get("depth", 2)),
    )
    affected = adapter.affected_tests(
        root,
        [str(value) for value in case.get("changed_files", [])],
        executable=codegraph,
        depth=int(case.get("depth", 5)),
    )
    semantic = (
        impact.get("provider") == "codegraph"
        and affected.get("provider") == "codegraph"
    )
    observed_impact = {
        str(item.get("filePath"))
        for item in impact.get("affected", [])
        if isinstance(item, dict) and item.get("filePath")
    }
    observed_tests = {
        str(value)
        for value in affected.get("affectedTests", [])
        if isinstance(value, str)
    }
    expected_impact = {str(value) for value in case.get("expected_impact_paths", [])}
    expected_tests = {str(value) for value in case.get("expected_test_paths", [])}
    impact_recall = _recall(expected_impact, observed_impact)
    test_recall = _recall(expected_tests, observed_tests)
    passed = semantic and impact_recall == 1.0 and test_recall == 1.0
    return {
        "id": case["id"],
        "passed": passed,
        "semantic_provider": semantic,
        "impact_recall": impact_recall,
        "test_recall": test_recall,
        "observed_impact_paths": sorted(observed_impact),
        "observed_test_paths": sorted(observed_tests),
        "codegraph_version": impact.get("version"),
    }


def run(
    cases_path: Path,
    *,
    codegraph: str | None = None,
    fixture_root: Path | None = None,
) -> dict[str, object]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("semantic benchmark cases must be a non-empty list")
    adapter = load_adapter()
    results = [
        evaluate_case(
            adapter,
            case,
            codegraph=codegraph,
            fixture_root=fixture_root.resolve() if fixture_root else None,
        )
        for case in cases
    ]
    return {
        "suite": "semantic-code-navigation",
        "passed": all(result["passed"] for result in results),
        "cases": results,
    }


def format_report(report: dict[str, object]) -> str:
    lines = ["Semantic CodeGraph benchmark"]
    for case in report["cases"]:
        status = "PASS" if case["passed"] else "FAIL"
        lines.append(
            f"{status} {case['id']}: impact recall={case['impact_recall']:.2f}, "
            f"test recall={case['test_recall']:.2f}, version={case['codegraph_version']}"
        )
    lines.append("PASS suite" if report["passed"] else "FAIL suite")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--codegraph", help="CodeGraph executable path or command")
    parser.add_argument(
        "--fixture-root",
        type=Path,
        help="indexed copy of the fixture; avoids writing .codegraph into the repository",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run(
            args.cases,
            codegraph=args.codegraph,
            fixture_root=args.fixture_root,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(
            json.dumps({"error": str(error)}, ensure_ascii=False)
            if args.json
            else f"error: {error}"
        )
        return 2
    print(
        json.dumps(report, indent=2, ensure_ascii=False)
        if args.json
        else format_report(report)
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
