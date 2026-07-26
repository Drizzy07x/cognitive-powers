#!/usr/bin/env python3
"""Run bounded source mutations against critical durable-state gates."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


MUTATIONS = (
    {
        "id": "completion-blockers-disabled",
        "old": 'if blockers:\n            raise WorkStateError("completion blocked: " + "; ".join(blockers))',
        "new": 'if False and blockers:\n            raise WorkStateError("completion blocked: " + "; ".join(blockers))',
        "test": "tests.test_work_state.WorkStateTests.test_verified_criterion_cannot_bypass_pending_packet",
    },
    {
        "id": "self-verification-enabled",
        "old": "if verifier.casefold() == executor.casefold():",
        "new": "if False:",
        "test": "tests.test_work_state.WorkStateTests.test_real_command_requires_independent_verification_before_completion",
    },
)


def _materialize_variant(
    variant_root: Path,
    source: str,
    *,
    work_state: Path,
    plugin_root: Path,
) -> Path:
    scripts_root = variant_root / "skills" / "execute-durably" / "scripts"
    scripts_root.mkdir(parents=True)
    variant = scripts_root / "work_state.py"
    variant.write_text(source, encoding="utf-8")
    shutil.copytree(
        work_state.with_name("work_state_core"),
        scripts_root / "work_state_core",
    )
    policy_source = plugin_root / "scripts" / "storage_policy.py"
    if not policy_source.is_file():
        raise FileNotFoundError(f"storage policy is missing: {policy_source}")
    policy_target = variant_root / "scripts" / "storage_policy.py"
    policy_target.parent.mkdir(parents=True)
    shutil.copy2(policy_source, policy_target)
    return variant


def _run_test(
    root: Path,
    work_state: Path,
    python: str,
    test: str,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["COGNITIVE_WORK_STATE_SCRIPT"] = str(work_state)
    return subprocess.run(
        [python, "-m", "unittest", test, "-q"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _assertion_failure(completed: subprocess.CompletedProcess[str]) -> bool:
    output = completed.stdout + completed.stderr
    infrastructure_markers = (
        "FileNotFoundError",
        "ModuleNotFoundError",
        "ImportError",
        "FAILED (errors=",
    )
    return (
        completed.returncode != 0
        and not any(marker in output for marker in infrastructure_markers)
        and ("AssertionError" in output or "FAILED (failures=" in output)
    )


def run_mutations(root: Path, work_state: Path, python: str) -> dict[str, object]:
    source = work_state.read_text(encoding="utf-8")
    results = []
    with tempfile.TemporaryDirectory(prefix="cp-work-state-mutations-") as temporary:
        temporary_root = Path(temporary)
        for mutation in MUTATIONS:
            if source.count(mutation["old"]) != 1:
                results.append(
                    {
                        "id": mutation["id"],
                        "killed": False,
                        "error": "mutation target was not found exactly once",
                    }
                )
                continue
            mutation_root = temporary_root / mutation["id"]
            mutation_root.mkdir()
            baseline = _materialize_variant(
                mutation_root / "baseline",
                source,
                work_state=work_state,
                plugin_root=root,
            )
            mutant = _materialize_variant(
                mutation_root / "mutant",
                source.replace(mutation["old"], mutation["new"], 1),
                work_state=work_state,
                plugin_root=root,
            )
            baseline_completed = _run_test(
                root, baseline, python, str(mutation["test"])
            )
            completed = _run_test(root, mutant, python, str(mutation["test"]))
            baseline_output = baseline_completed.stdout + baseline_completed.stderr
            output = completed.stdout + completed.stderr
            results.append(
                {
                    "id": mutation["id"],
                    "test": mutation["test"],
                    "baseline_passed": baseline_completed.returncode == 0,
                    "baseline_exit_code": baseline_completed.returncode,
                    "killed": baseline_completed.returncode == 0
                    and _assertion_failure(completed),
                    "test_exit_code": completed.returncode,
                    "baseline_output_tail": baseline_output[-1000:],
                    "output_tail": output[-1000:],
                }
            )
    return {
        "schema_version": 1,
        "kind": "selective_gate_mutations",
        "mutations": results,
        "all_mutations_killed": bool(results)
        and all(item.get("killed") is True for item in results),
        "end_to_end_improvement_proven": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--work-state",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "work_state.py",
    )
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    payload = run_mutations(
        args.root.resolve(), args.work_state.resolve(), str(args.python)
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["all_mutations_killed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
