#!/usr/bin/env python3
"""Run Playwright evidence benchmarks against an installed fixture copy."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Sequence


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "benchmarks" / "browser_cases.json"
ADAPTER_PATH = (
    PLUGIN_ROOT / "skills" / "verify-web-behavior" / "scripts" / "browser_evidence.py"
)


def load_adapter() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "browser_benchmark_adapter", ADAPTER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ADAPTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def evaluate_case(
    adapter: ModuleType,
    case: dict[str, object],
    *,
    playwright: str | None,
    fixture_root: Path | None,
    artifact_dir: Path,
) -> dict[str, object]:
    root = fixture_root or (DEFAULT_CASES.parent / str(case["root"])).resolve()
    try:
        receipt, _ = adapter.run_tests(
            root,
            executable=playwright,
            selectors=[str(case["test"])],
            projects=[str(case["project"])],
            artifact_dir=artifact_dir,
            trace="on",
            workers=1,
        )
    except adapter.BrowserEvidenceError as error:
        return {"id": case["id"], "passed": False, "error": str(error)}
    artifact_paths = {
        str(item.get("path"))
        for item in receipt.get("artifacts", [])
        if isinstance(item, dict)
    }
    required = [str(value) for value in case.get("required_artifact_suffixes", [])]
    artifact_coverage = {
        suffix: any(path.endswith(suffix) for path in artifact_paths)
        for suffix in required
    }
    stats = receipt.get("stats") if isinstance(receipt.get("stats"), dict) else {}
    expected = int(stats.get("expected", 0))
    passed = (
        receipt.get("provider") == "playwright"
        and receipt.get("passed") is True
        and expected >= int(case.get("expected_tests", 1))
        and all(artifact_coverage.values())
    )
    return {
        "id": case["id"],
        "passed": passed,
        "playwrightVersion": receipt.get("version"),
        "stats": stats,
        "artifactCoverage": artifact_coverage,
    }


def run(
    cases_path: Path,
    *,
    playwright: str | None = None,
    fixture_root: Path | None = None,
    artifact_root: Path | None = None,
) -> dict[str, object]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("browser benchmark cases must be a non-empty list")
    adapter = load_adapter()
    if artifact_root:
        artifact_root.mkdir(parents=True, exist_ok=True)
        results = [
            evaluate_case(
                adapter,
                case,
                playwright=playwright,
                fixture_root=fixture_root.resolve() if fixture_root else None,
                artifact_dir=artifact_root / str(case["id"]),
            )
            for case in cases
        ]
    else:
        with tempfile.TemporaryDirectory(
            prefix="cognitive-browser-benchmark-"
        ) as temporary:
            temporary_root = Path(temporary)
            results = [
                evaluate_case(
                    adapter,
                    case,
                    playwright=playwright,
                    fixture_root=fixture_root.resolve() if fixture_root else None,
                    artifact_dir=temporary_root / str(case["id"]),
                )
                for case in cases
            ]
    return {
        "suite": "playwright-browser-evidence",
        "passed": all(result["passed"] for result in results),
        "cases": results,
    }


def format_report(report: dict[str, object]) -> str:
    lines = ["Playwright browser evidence benchmark"]
    for case in report["cases"]:
        status = "PASS" if case["passed"] else "FAIL"
        detail = (
            case.get("error")
            or f"stats={case.get('stats')}, artifacts={case.get('artifactCoverage')}"
        )
        lines.append(f"{status} {case['id']}: {detail}")
    lines.append("PASS suite" if report["passed"] else "FAIL suite")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--playwright", help="Playwright executable path or command")
    parser.add_argument(
        "--fixture-root", type=Path, help="fixture copy with Playwright installed"
    )
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run(
            args.cases,
            playwright=args.playwright,
            fixture_root=args.fixture_root,
            artifact_root=args.artifact_root,
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
