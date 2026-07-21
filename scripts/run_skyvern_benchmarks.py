#!/usr/bin/env python3
"""Validate Skyvern normalization/handoff contracts and optionally run a live observation."""

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
DEFAULT_CASES = PLUGIN_ROOT / "benchmarks" / "skyvern_cases.json"
ADAPTER_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "explore-web-adaptively"
    / "scripts"
    / "skyvern_evidence.py"
)


def load_adapter() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "skyvern_benchmark_adapter", ADAPTER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ADAPTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def evaluate_contract_case(
    adapter: ModuleType,
    case: dict[str, object],
    *,
    workspace: Path,
    output_root: Path,
) -> dict[str, object]:
    fixture = DEFAULT_CASES.parent / str(case["fixture"])
    receipt, exit_code = adapter.ingest(
        workspace,
        fixture / "run-final.json",
        request_path=fixture / "request.json",
        timeline_path=fixture / "timeline.json",
        artifact_index_path=fixture / "artifact-index.json",
        artifact_dir=output_root / "ingested",
    )
    handoff = adapter.handoff(
        workspace,
        receipt["receipt"],
        output_dir=output_root / "handoff",
    )
    artifacts = receipt.get("artifacts", [])
    artifact_integrity = all(
        isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("sha256"), str)
        and len(str(item["sha256"])) == 64
        for item in artifacts
    )
    candidate_text = Path(str(handoff["candidate"])).read_text(encoding="utf-8")
    passed = (
        exit_code == 0
        and receipt.get("runId") == case["expected_run_id"]
        and receipt.get("stepCount") == case["expected_step_count"]
        and len(artifacts) == case["expected_artifact_count"]
        and receipt.get("navigationOnly") is True
        and receipt.get("verificationEligible") is False
        and handoff.get("failClosed") is True
        and "Skyvern discovery is not Playwright verification" in candidate_text
        and artifact_integrity
    )
    return {
        "id": case["id"],
        "passed": passed,
        "navigationOnly": receipt.get("navigationOnly"),
        "verificationEligible": receipt.get("verificationEligible"),
        "artifactCount": len(artifacts),
        "artifactIntegrity": artifact_integrity,
        "handoffFailsClosed": handoff.get("failClosed"),
    }


def run(
    cases_path: Path,
    *,
    live_url: str | None = None,
    live_prompt: str | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
) -> dict[str, object]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Skyvern benchmark cases must be a non-empty list")
    if bool(live_url) != bool(live_prompt):
        raise ValueError("--live-url and --live-prompt must be provided together")
    adapter = load_adapter()
    with tempfile.TemporaryDirectory(
        prefix="cognitive-skyvern-benchmark-"
    ) as temporary:
        temporary_root = Path(temporary)
        workspace = temporary_root / "workspace"
        workspace.mkdir()
        contract_results = [
            evaluate_contract_case(
                adapter,
                case,
                workspace=workspace,
                output_root=temporary_root / str(case["id"]),
            )
            for case in cases
        ]
        live_result: dict[str, object] | None = None
        if live_url and live_prompt:
            try:
                receipt, exit_code = adapter.run_task(
                    workspace,
                    prompt=live_prompt,
                    url=live_url,
                    execute=True,
                    side_effect_scope="observe",
                    max_steps=6,
                    api_base=api_base,
                    api_key=api_key,
                    artifact_dir=temporary_root / "live-run",
                )
                live_result = {
                    "passed": exit_code == 0
                    and receipt.get("discoveryCompleted") is True,
                    "runId": receipt.get("runId"),
                    "status": receipt.get("status"),
                    "stepCount": receipt.get("stepCount"),
                }
            except adapter.SkyvernEvidenceError as error:
                live_result = {"passed": False, "error": str(error)}
    contract_passed = all(result["passed"] for result in contract_results)
    live_validated = live_result is not None and live_result.get("passed") is True
    return {
        "suite": "skyvern-navigation-contract",
        "passed": contract_passed and (live_result is None or live_validated),
        "contractPassed": contract_passed,
        "liveProviderValidated": live_validated,
        "cases": contract_results,
        "live": live_result,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--live-url")
    parser.add_argument("--live-prompt")
    parser.add_argument("--api-base")
    parser.add_argument("--api-key")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run(
            args.cases,
            live_url=args.live_url,
            live_prompt=args.live_prompt,
            api_base=args.api_base,
            api_key=args.api_key,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(
            json.dumps({"error": str(error)}, ensure_ascii=False)
            if args.json
            else f"error: {error}"
        )
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("Skyvern navigation contract benchmark")
        for case in report["cases"]:
            print(f"{'PASS' if case['passed'] else 'FAIL'} {case['id']}: {case}")
        print(f"live provider validated: {report['liveProviderValidated']}")
        print("PASS suite" if report["passed"] else "FAIL suite")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
