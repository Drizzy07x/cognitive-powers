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
            mutant = mutation_root / "work_state.py"
            mutant.write_text(
                source.replace(mutation["old"], mutation["new"], 1),
                encoding="utf-8",
            )
            shutil.copytree(
                work_state.with_name("work_state_core"),
                mutation_root / "work_state_core",
            )
            environment = dict(os.environ)
            environment["COGNITIVE_WORK_STATE_SCRIPT"] = str(mutant)
            completed = subprocess.run(
                [python, "-m", "unittest", mutation["test"], "-q"],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            results.append(
                {
                    "id": mutation["id"],
                    "test": mutation["test"],
                    "killed": completed.returncode != 0,
                    "test_exit_code": completed.returncode,
                    "output_tail": (completed.stdout + completed.stderr)[-1000:],
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
