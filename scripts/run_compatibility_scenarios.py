#!/usr/bin/env python3
"""Run named offline compatibility scenarios and bind results to real lifecycle evidence."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))


def _load_release_identity():
    path = Path(__file__).resolve().with_name("release_identity.py")
    spec = importlib.util.spec_from_file_location("cp_release_identity", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the shared release identity: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RELEASE = _load_release_identity()

SCENARIO_TESTS = {
    "corrupt-state": [
        "test_install_transaction.InstallTransactionTests.test_tag_preflight_fails_before_profile_query_or_mutation",
        "test_work_state.WorkStateTests.test_state_migration_entrypoint_fails_closed_on_corrupt_ledger",
    ],
    "legacy-copy": [
        "test_install_transaction.InstallTransactionTests.test_personal_only_is_restored_after_failure",
    ],
    "checkout-without-git": [
        "test_verify_installed.VerifyInstalledTests.test_checkout_without_git_is_rejected_fail_closed",
    ],
    "crlf-lf": [
        "test_verify_installed.VerifyInstalledTests.test_exact_install_accepts_git_normalized_crlf_and_only_host_metadata",
    ],
    "symlink": [
        "test_plugin_hooks.PluginHookTests.test_traversal_and_symlink_candidates_never_escape_cwd",
    ],
}


class EvidenceError(ValueError):
    pass


def build_evidence(real_path: Path, *, commit: str, tag: str) -> dict:
    # The candidate is whatever this checkout declares itself to be. Naming a
    # fixed tag here would refuse every later release instead of binding the
    # scenarios to the one under test.
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or tag != _RELEASE.release_tag():
        raise EvidenceError("candidate identity is malformed")
    real = json.loads(real_path.read_text(encoding="utf-8"))
    if (
        not isinstance(real, dict)
        or real.get("schemaVersion") != 1
        or real.get("product") != "cognitive-powers"
        or real.get("candidateCommit") != commit
        or real.get("candidateTag") != tag
    ):
        raise EvidenceError("real lifecycle evidence is not candidate-bound")
    scenarios = dict(real.get("scenarios", {}))
    for name, test_names in SCENARIO_TESTS.items():
        suite = unittest.defaultTestLoader.loadTestsFromNames(test_names)
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        if not result.wasSuccessful():
            raise EvidenceError(f"scenario {name} failed:\n{stream.getvalue()}")
        scenarios[name] = {
            "passed": not result.skipped,
            "skipped": bool(result.skipped),
            "finalTag": tag,
            "finalCommit": commit,
            "tests": test_names,
        }
    required = {
        "upgrade-v1.5.2",
        "rollback",
        "corrupt-state",
        "legacy-copy",
        "checkout-without-git",
        "crlf-lf",
        "symlink",
        "unicode-space-path",
    }
    if set(scenarios) != required:
        raise EvidenceError(f"scenario evidence set mismatch: {sorted(scenarios)}")
    return {
        "schemaVersion": 1,
        "product": "cognitive-powers",
        "candidateCommit": commit,
        "candidateTag": tag,
        "scenarios": scenarios,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-evidence", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        evidence = build_evidence(args.real_evidence, commit=args.commit, tag=args.tag)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except (OSError, json.JSONDecodeError, EvidenceError) as error:
        print(json.dumps({"error": str(error)}))
        return 2
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
