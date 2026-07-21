#!/usr/bin/env python3
"""Run deterministic Context Lens recall and payload benchmarks."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "benchmarks" / "context_cases.json"
DEFAULT_EXTERNAL_CASES = PLUGIN_ROOT / "benchmarks" / "external_context_cases.json"
CONTEXT_LENS_PATH = (
    PLUGIN_ROOT / "skills" / "solve-efficiently" / "scripts" / "context_lens.py"
)
CONTEXT7_LOOKUP_PATH = (
    PLUGIN_ROOT / "skills" / "use-current-docs" / "scripts" / "context7_lookup.py"
)


def load_context_lens() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "cognitive_context_lens", CONTEXT_LENS_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Context Lens from {CONTEXT_LENS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_context7_lookup() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "cognitive_context7_lookup", CONTEXT7_LOOKUP_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Context7 lookup from {CONTEXT7_LOOKUP_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def evaluate_case(
    module: ModuleType, case: dict[str, object], cases_path: Path
) -> dict[str, object]:
    root = (cases_path.parent / str(case["root"])).resolve()
    result = module.select_context(
        root,
        str(case["query"]),
        max_files=int(case["max_files"]),
        max_chars=int(case["max_chars"]),
    )
    selected_paths = [str(item["path"]) for item in result["files"]]
    expected_paths = [str(path) for path in case["expected_paths"]]
    hits = [path for path in expected_paths if path in selected_paths]
    recall = 1.0 if not expected_paths else len(hits) / len(expected_paths)
    min_recall = float(case["min_recall"])
    min_reduction = float(case["min_char_reduction_pct"])
    passed = (
        recall >= min_recall and float(result["char_reduction_pct"]) >= min_reduction
    )
    return {
        "id": case["id"],
        "passed": passed,
        "recall": round(recall, 4),
        "minimum_recall": min_recall,
        "char_reduction_pct": result["char_reduction_pct"],
        "minimum_char_reduction_pct": min_reduction,
        "payload_chars": result["payload_chars"],
        "corpus_chars": result["corpus_chars"],
        "expected_paths": expected_paths,
        "selected_paths": selected_paths,
        "missing_paths": [path for path in expected_paths if path not in hits],
    }


def evaluate_external_case(
    module: ModuleType, case: dict[str, object]
) -> dict[str, object]:
    selected = module.select_library_candidate(
        case["candidates"], str(case["library"]), str(case["version"])
    )
    versioned = str(case["versioned_snippet"])
    generic = str(case["generic_snippet"])
    marker = str(case["expected_marker"])
    version_match = bool(selected["version_matched"])
    versioned_recall = 1.0 if marker in versioned else 0.0
    baseline_recall = 1.0 if marker in generic and generic != versioned else 0.0
    passed = (
        version_match and versioned_recall == 1.0 and baseline_recall < versioned_recall
    )
    return {
        "id": case["id"],
        "passed": passed,
        "selected_library_id": selected["id"],
        "version_match": version_match,
        "versioned_recall": versioned_recall,
        "generic_baseline_recall": baseline_recall,
        "recall_improvement": versioned_recall - baseline_recall,
    }


def run(
    cases_path: Path, external_cases_path: Path = DEFAULT_EXTERNAL_CASES
) -> dict[str, object]:
    module = load_context_lens()
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    results = [evaluate_case(module, case, cases_path) for case in cases]
    external_module = load_context7_lookup()
    external_cases = json.loads(external_cases_path.read_text(encoding="utf-8"))
    external_results = [
        evaluate_external_case(external_module, case) for case in external_cases
    ]
    return {
        "schema_version": 1,
        "suite": "cognitive-powers",
        "passed": all(result["passed"] for result in results + external_results),
        "context_selection": results,
        "external_context": external_results,
    }


def format_report(report: dict[str, object]) -> str:
    lines = ["Context selection benchmark"]
    for result in report["context_selection"]:
        status = "PASS" if result["passed"] else "FAIL"
        lines.append(
            f"{status} {result['id']}: recall={result['recall']:.2f}, "
            f"char reduction={result['char_reduction_pct']:.2f}%"
        )
        if result["missing_paths"]:
            lines.append(f"  missing: {', '.join(result['missing_paths'])}")
    lines.append("Version-aware external context benchmark")
    for result in report["external_context"]:
        status = "PASS" if result["passed"] else "FAIL"
        lines.append(
            f"{status} {result['id']}: version_match={result['version_match']}, "
            f"recall improvement={result['recall_improvement']:.2f}"
        )
    lines.append("PASS suite" if report["passed"] else "FAIL suite")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--external-cases", type=Path, default=DEFAULT_EXTERNAL_CASES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run(args.cases.resolve(), args.external_cases.resolve())
    print(json.dumps(report, indent=2) if args.json else format_report(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
