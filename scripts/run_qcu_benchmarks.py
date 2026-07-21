#!/usr/bin/env python3
"""Run deterministic offline QCU evidence-contract benchmarks."""

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
DEFAULT_CASES = PLUGIN_ROOT / "benchmarks" / "qcu_cases.json"
ADAPTER_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "operate-desktop-adaptively"
    / "scripts"
    / "qcu_evidence.py"
)


def load_adapter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("qcu_benchmark_adapter", ADAPTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ADAPTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(cases_path: Path) -> dict[str, object]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("QCU benchmark cases must be a non-empty list")
    adapter = load_adapter()
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="cognitive-qcu-benchmark-") as temporary:
        temporary_root = Path(temporary)
        workspace = temporary_root / "workspace"
        workspace.mkdir()
        for case in cases:
            expected = str(case["expected"])
            error: str | None = None
            try:
                receipt, _ = adapter.normalize(
                    workspace,
                    (PLUGIN_ROOT / "benchmarks" / str(case["fixture"])).resolve(),
                    artifact_dir=temporary_root / str(case["id"]),
                )
                actual = "pass" if receipt.get("objectiveSatisfied") is True else "fail"
            except adapter.QcuEvidenceError as caught:
                actual = "fail"
                error = str(caught)
            error_contains = str(case.get("error_contains") or "")
            passed = actual == expected and (
                not error_contains or error_contains in (error or "")
            )
            results.append(
                {"id": case["id"], "passed": passed, "actual": actual, "error": error}
            )
    return {
        "suite": "qcu-desktop-evidence-contract",
        "passed": all(item["passed"] for item in results),
        "liveDesktopValidated": False,
        "cases": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run(args.cases)
    except (OSError, RuntimeError, ValueError) as error:
        print(
            json.dumps({"error": str(error)}, ensure_ascii=False)
            if args.json
            else f"error: {error}"
        )
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
