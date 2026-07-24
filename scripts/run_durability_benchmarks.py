#!/usr/bin/env python3
"""Run deterministic offline durability contract checks."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WORK_STATE_PATH = (
    PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "work_state.py"
)
HOOK_PATH = PLUGIN_ROOT / "hooks" / "selective_hooks.py"


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _case(identifier: str, passed: bool, detail: str) -> dict[str, object]:
    return {"id": identifier, "passed": passed, "detail": detail}


def _write_state(path: Path, *, version: object = 1, sequence: object = 0) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "state.json").write_text(
        json.dumps(
            {
                "schema_version": version,
                "last_seq": sequence,
                "criteria": [],
                "work_packets": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def run() -> dict[str, object]:
    work_state = _load_module(WORK_STATE_PATH, "_durability_benchmark_work_state")
    hook = _load_module(HOOK_PATH, "_durability_benchmark_hook")
    cases: list[dict[str, object]] = []

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)

        current = root / "current"
        _write_state(current)
        before = (current / "state.json").read_bytes()
        report = work_state.state_migration_report(current)
        cases.append(
            _case(
                "current-schema-dry-run",
                report["status"] == "current"
                and report["mode"] == "dry-run"
                and (current / "state.json").read_bytes() == before,
                "Current schema is inspected without mutation.",
            )
        )

        future = root / "future"
        _write_state(future, version=2)
        try:
            work_state.state_migration_report(future)
        except work_state.WorkStateError:
            future_closed = True
        else:
            future_closed = False
        cases.append(
            _case(
                "future-schema-fail-closed",
                future_closed,
                "Unknown future schema is rejected without migration.",
            )
        )

        malformed = root / "malformed"
        _write_state(malformed, sequence="invalid")
        try:
            work_state.load_state(malformed)
        except work_state.WorkStateError:
            malformed_closed = True
        else:
            malformed_closed = False
        cases.append(
            _case(
                "malformed-state-fail-closed",
                malformed_closed,
                "Malformed sequence metadata is rejected.",
            )
        )

        invalid_paths = ("", ".", "..", "../file", "/absolute", "C:/absolute")
        traversal_closed = all(
            _rejects(
                lambda value=value: work_state._normalize_owned_path(value),
                work_state.WorkStateError,
            )
            for value in invalid_paths
        )
        cases.append(
            _case(
                "path-boundary-properties",
                traversal_closed,
                "Traversal and absolute owned paths are rejected.",
            )
        )

        ledger = root / "hook-ledger.jsonl"
        first = {
            "schema": hook.SCHEMA,
            "event": "PostToolUse",
            "previousEventHash": None,
        }
        first["eventHash"] = hashlib.sha256(hook._canonical(first)).hexdigest()
        second = {
            "schema": hook.SCHEMA,
            "event": "PostToolUse",
            "previousEventHash": "f" * 64,
        }
        second["eventHash"] = hashlib.sha256(hook._canonical(second)).hexdigest()
        ledger.write_text(
            json.dumps(first) + "\n" + json.dumps(second) + "\n",
            encoding="utf-8",
        )
        _events, chain_error = hook._read_ledger(ledger)
        cases.append(
            _case(
                "hash-chain-corruption",
                bool(chain_error and "breaks the hash chain" in chain_error),
                "A rehashed event with a forged predecessor is rejected.",
            )
        )

        atomic = root / "atomic.json"
        atomic.write_text("old\n", encoding="utf-8")
        durability = work_state._DURABILITY_CORE._durability
        try:
            with mock.patch.object(
                durability.os,
                "fsync",
                side_effect=OSError("injected fsync failure"),
            ):
                work_state._atomic_write_text(atomic, "new\n")
        except OSError:
            pass
        atomic_safe = atomic.read_text(encoding="utf-8") == "old\n" and not list(
            root.glob(".atomic.json.*.tmp")
        )
        cases.append(
            _case(
                "atomic-fsync-failure",
                atomic_safe,
                "Injected fsync failure preserves the prior file and removes temp state.",
            )
        )

        lock_session = root / "lock"
        lock_safe = True
        for _ in range(16):
            with work_state.session_lock(lock_session):
                lock_safe = lock_safe and (lock_session / ".state.lock").is_file()
            lock_safe = lock_safe and not (lock_session / ".state.lock").exists()
        cases.append(
            _case(
                "residual-lock-stress",
                lock_safe,
                "Repeated acquisition leaves no residual owner lock.",
            )
        )

    return {
        "schemaVersion": 1,
        "suite": "durability-offline-contract",
        "passed": all(case["passed"] for case in cases),
        "liveValidated": False,
        "providerModelImprovementProven": False,
        "cases": cases,
    }


def _rejects(operation, expected_exception: type[Exception]) -> bool:
    try:
        operation()
    except expected_exception:
        return True
    return False


def format_report(report: dict[str, object]) -> str:
    lines = ["Deterministic durability benchmark"]
    for case in report["cases"]:
        status = "PASS" if case["passed"] else "FAIL"
        lines.append(f"{status} {case['id']}: {case['detail']}")
    lines.append("PASS suite" if report["passed"] else "FAIL suite")
    lines.append("No provider or model improvement was evaluated.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run()
    print(json.dumps(report, indent=2) if args.json else format_report(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
