#!/usr/bin/env python3
"""Validate one evidence-backed capability lifecycle transition."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


STATES = ("observed", "candidate", "trial", "active", "retired")
FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")


class LifecycleError(ValueError):
    """Raised when a lifecycle transition is unsupported by its evidence."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _strings(value: object, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise LifecycleError(f"{field} must be a string list")
    result = list(dict.fromkeys(item.strip() for item in value))
    if not allow_empty and not result:
        raise LifecycleError(f"{field} must not be empty")
    return result


def _fingerprint(value: object, field: str) -> str:
    if not isinstance(value, str) or not FINGERPRINT.fullmatch(value):
        raise LifecycleError(f"{field} must be a lowercase sha256 fingerprint")
    return value


def _events(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw = payload.get("events")
    if not isinstance(raw, list) or not raw:
        raise LifecycleError("events must be a non-empty list")
    events: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, event in enumerate(raw):
        if not isinstance(event, dict):
            raise LifecycleError(f"events[{index}] must be an object")
        normalized: dict[str, str] = {}
        for field in ("event_id", "observed_at", "source"):
            value = event.get(field)
            if not isinstance(value, str) or not value.strip():
                raise LifecycleError(f"events[{index}].{field} must be non-empty")
            normalized[field] = value.strip()
        if normalized["event_id"] not in seen:
            events.append(normalized)
            seen.add(normalized["event_id"])
    return events


def _checks(payload: dict[str, Any], implementation: str) -> list[dict[str, Any]]:
    raw = payload.get("checks")
    if not isinstance(raw, list) or not raw:
        raise LifecycleError("checks must be a non-empty list")
    checks: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, check in enumerate(raw):
        if not isinstance(check, dict):
            raise LifecycleError(f"checks[{index}] must be an object")
        name = check.get("name")
        if not isinstance(name, str) or not name.strip() or name.strip() in names:
            raise LifecycleError(f"checks[{index}].name must be non-empty and unique")
        if not isinstance(check.get("passed"), bool):
            raise LifecycleError(f"checks[{index}].passed must be boolean")
        bound = _fingerprint(check.get("fingerprint"), f"checks[{index}].fingerprint")
        if bound != implementation:
            raise LifecycleError(f"checks[{index}] targets a different implementation")
        evidence = _strings(check.get("evidence"), f"checks[{index}].evidence")
        names.add(name.strip())
        checks.append(
            {
                "name": name.strip(),
                "passed": check["passed"],
                "fingerprint": bound,
                "evidence": evidence,
            }
        )
    return checks


def _approval(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise LifecycleError("approval must be an object")
    result: dict[str, str] = {}
    for field in ("approved_by", "approved_at", "evidence"):
        item = value.get(field)
        if not isinstance(item, str) or not item.strip():
            raise LifecycleError(f"approval.{field} must be non-empty")
        result[field] = item.strip()
    return result


def _rollback(value: object, implementation: str, *, executed: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LifecycleError("rollback must be an object")
    target = _fingerprint(
        value.get("target_fingerprint"), "rollback.target_fingerprint"
    )
    if target == implementation:
        raise LifecycleError(
            "rollback.target_fingerprint must differ from the implementation"
        )
    steps = _strings(value.get("steps"), "rollback.steps")
    did_execute = value.get("executed")
    if not isinstance(did_execute, bool):
        raise LifecycleError("rollback.executed must be boolean")
    if executed and not did_execute:
        raise LifecycleError("retirement requires an executed rollback")
    evidence = _strings(
        value.get("evidence"), "rollback.evidence", allow_empty=not executed
    )
    return {
        "target_fingerprint": target,
        "steps": steps,
        "executed": did_execute,
        "evidence": evidence,
    }


def transition(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise LifecycleError("schema_version must be 1")
    capability_id = payload.get("capability_id")
    if not isinstance(capability_id, str) or not capability_id.strip():
        raise LifecycleError("capability_id must be non-empty")
    current = payload.get("current_state")
    requested = payload.get("requested_state")
    if current is not None and current not in STATES:
        raise LifecycleError("current_state is invalid")
    if requested not in STATES:
        raise LifecycleError("requested_state is invalid")
    expected_index = 0 if current is None else STATES.index(current) + 1
    if expected_index >= len(STATES) or requested != STATES[expected_index]:
        expected = STATES[expected_index] if expected_index < len(STATES) else "none"
        raise LifecycleError(
            f"transition must advance exactly one state; expected {expected}"
        )

    events = _events(payload)
    evidence = _strings(payload.get("evidence"), "evidence")
    fingerprints = payload.get("fingerprints")
    if not isinstance(fingerprints, dict):
        raise LifecycleError("fingerprints must be an object")
    source = _fingerprint(fingerprints.get("source"), "fingerprints.source")
    implementation: str | None = None
    checks: list[dict[str, Any]] = []
    approval: dict[str, str] | None = None
    rollback: dict[str, Any] | None = None

    if STATES.index(requested) >= STATES.index("candidate") and len(events) < 2:
        raise LifecycleError("candidate requires two distinct observed events")
    if STATES.index(requested) >= STATES.index("trial"):
        implementation = _fingerprint(
            fingerprints.get("implementation"), "fingerprints.implementation"
        )
        checks = _checks(payload, implementation)
        if not all(check["passed"] for check in checks):
            raise LifecycleError("all checks must pass before trial or activation")
    if STATES.index(requested) >= STATES.index("active"):
        assert implementation is not None
        approval = _approval(payload.get("approval"))
        rollback = _rollback(payload.get("rollback"), implementation, executed=False)
    if requested == "retired":
        assert implementation is not None
        rollback = _rollback(payload.get("rollback"), implementation, executed=True)

    receipt_core = {
        "schema_version": 1,
        "kind": "capability_lifecycle_transition",
        "capability_id": capability_id.strip(),
        "from_state": current,
        "to_state": requested,
        "distinct_events": len(events),
        "fingerprints": {"source": source, "implementation": implementation},
        "checks": checks,
        "evidence": evidence,
        "approval": approval,
        "rollback": rollback,
    }
    return {
        **receipt_core,
        "receipt_fingerprint": "sha256:"
        + hashlib.sha256(_canonical(receipt_core)).hexdigest(),
        "transition_approved": True,
        "next_state": STATES[STATES.index(requested) + 1]
        if requested != "retired"
        else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="JSON path or - for stdin")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        raw = (
            sys.stdin.read()
            if args.input == "-"
            else Path(args.input).read_text(encoding="utf-8")
        )
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise LifecycleError("input must be a JSON object")
        result = transition(payload)
    except (LifecycleError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({"error": str(error)}) if args.json else f"ERROR: {error}")
        return 2
    print(json.dumps(result, indent=2) if args.json else json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
