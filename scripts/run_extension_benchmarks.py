#!/usr/bin/env python3
"""Run deterministic contracts for planning, prompts, hooks, and frontend review."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "benchmarks" / "extension_cases.json"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(cases_path: Path = DEFAULT_CASES) -> dict[str, object]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    plan = load_module(
        "extension_plan_compiler",
        PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "plan_compiler.py",
    )
    prompt = load_module(
        "extension_prompt_contract",
        PLUGIN_ROOT / "skills" / "engineer-prompts" / "scripts" / "prompt_contract.py",
    )
    frontend = load_module(
        "extension_frontend_performance",
        PLUGIN_ROOT
        / "skills"
        / "design-intentionally"
        / "scripts"
        / "frontend_performance.py",
    )
    results: list[dict[str, object]] = []

    for case in cases["plan_cases"]:
        try:
            plan.compile_markdown(case["markdown"])
            actual = True
        except plan.PlanCompilerError:
            actual = False
        results.append(
            {
                "id": case["id"],
                "passed": actual is case["expected_valid"],
                "expected": case["expected_valid"],
                "actual": actual,
            }
        )

    for case in cases["prompt_cases"]:
        try:
            normalized = prompt.validate_contract(case["contract"])
            actual = True
            model_defaulted = (
                "target_model" in normalized and "target_model" not in case["contract"]
            )
        except prompt.ContractError:
            actual = False
            model_defaulted = False
        results.append(
            {
                "id": case["id"],
                "passed": actual is case["expected_valid"] and not model_defaulted,
                "expected": case["expected_valid"],
                "actual": actual,
                "modelDefaulted": model_defaulted,
            }
        )

    for case in cases["frontend_cases"]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(
                json.dumps({"dependencies": case["dependencies"]}), encoding="utf-8"
            )
            for relative, content in case["files"].items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            audit = frontend.audit(root)
        actual_rules = sorted(item["rule"] for item in audit["findings"])
        expected_rules = sorted(case["expected_rules"])
        results.append(
            {
                "id": case["id"],
                "passed": actual_rules == expected_rules
                and not audit["measuredRuntimePerformance"]
                and not audit["optimizationProven"],
                "expected": expected_rules,
                "actual": actual_rules,
            }
        )

    hooks = json.loads(
        (PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    post = hooks["hooks"]["PostToolUse"][0]
    handler = post["hooks"][0]
    expected_hook = cases["hook_contract"]
    static_hook_passed = (
        post["matcher"] == expected_hook["expected_post_tool_matcher"]
        and handler["commandWindows"].startswith(
            expected_hook["expected_windows_prefix"]
        )
        and "Bash" not in post["matcher"]
    )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        repository = root / "repo"
        data = root / "data"
        source = repository / "src" / "sample.py"
        source.parent.mkdir(parents=True)
        source.write_text("value = 1\n", encoding="utf-8")
        environment = os.environ.copy()
        environment.update({"PLUGIN_ROOT": str(PLUGIN_ROOT), "PLUGIN_DATA": str(data)})
        payload = {
            "session_id": "extension-benchmark",
            "turn_id": "t1",
            "cwd": str(repository),
            "tool_name": "apply_patch",
            "tool_input": {"patch": "*** Update File: src/sample.py"},
        }
        executed = subprocess.run(
            [
                sys.executable,
                str(PLUGIN_ROOT / "hooks" / "selective_hooks.py"),
                "post-tool-use",
            ],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )
        ledgers = list((data / "hooks" / "events").glob("*.jsonl"))
        event = (
            json.loads(ledgers[0].read_text(encoding="utf-8"))
            if len(ledgers) == 1
            else {}
        )
        recorded_files = (
            event.get("files") if isinstance(event.get("files"), list) else []
        )
        offline_hook_executed = (
            executed.returncode == 0
            and event.get("tool") == "apply_patch"
            and bool(recorded_files)
            and recorded_files[0].get("path") == "src/sample.py"
        )
    hook_passed = static_hook_passed and offline_hook_executed
    results.append(
        {
            "id": "selective-cross-platform-hook",
            "passed": hook_passed,
            "expected": expected_hook["expected_post_tool_matcher"],
            "actual": {
                "matcher": post["matcher"],
                "offlineHookExecuted": offline_hook_executed,
            },
        }
    )

    return {
        "schema_version": 1,
        "suite": "cognitive-powers-extension-contract",
        "passed": all(item["passed"] for item in results),
        "liveCodexHookValidated": False,
        "runtimePerformanceMeasured": False,
        "semanticPromptQualityProven": False,
        "endToEndImprovementProven": False,
        "cases": results,
    }


def format_report(report: dict[str, object]) -> str:
    lines = ["Cognitive Powers extension contract benchmark"]
    for result in report["cases"]:
        status = "PASS" if result["passed"] else "FAIL"
        lines.append(
            f"{status} {result['id']}: expected={result['expected']} actual={result['actual']}"
        )
    lines.extend(
        [
            "Live Codex hook validated: no",
            "Runtime performance measured: no",
            "Semantic prompt quality proven: no",
            "End-to-end improvement proven: no",
            "PASS suite" if report["passed"] else "FAIL suite",
        ]
    )
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
