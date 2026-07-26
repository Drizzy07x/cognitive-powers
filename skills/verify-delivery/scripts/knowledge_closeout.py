#!/usr/bin/env python3
"""Assess six knowledge surfaces without performing cleanup or memory writes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SURFACES = (
    "code",
    "tests",
    "documentation",
    "project_guidance",
    "release_notes",
    "durable_memory",
)
STATUSES = {"current", "pending", "not-applicable"}


class CloseoutError(ValueError):
    """Raised when a closeout packet is incomplete or inconsistent."""


def _strings(value: object, field: str, *, empty: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise CloseoutError(f"{field} must be a string list")
    result = list(dict.fromkeys(item.strip() for item in value))
    if not empty and not result:
        raise CloseoutError(f"{field} must not be empty")
    return result


def assess(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise CloseoutError("schema_version must be 1")
    mode = payload.get("mode")
    if mode not in {"light", "full"}:
        raise CloseoutError("mode must be light or full")
    source_identity = payload.get("source_identity")
    if not isinstance(source_identity, str) or not source_identity.strip():
        raise CloseoutError("source_identity must be non-empty")
    raw_surfaces = payload.get("surfaces")
    if not isinstance(raw_surfaces, dict):
        raise CloseoutError("surfaces must be an object")
    unknown = sorted(set(raw_surfaces) - set(SURFACES))
    if unknown:
        raise CloseoutError(f"unknown knowledge surfaces: {', '.join(unknown)}")
    if mode == "full" and set(raw_surfaces) != set(SURFACES):
        raise CloseoutError("full mode requires all six knowledge surfaces")
    if mode == "light" and not raw_surfaces:
        raise CloseoutError("light mode requires at least one relevant surface")

    surfaces: list[dict[str, Any]] = []
    pending: list[str] = []
    for name in SURFACES:
        item = raw_surfaces.get(name)
        if item is None:
            surfaces.append(
                {
                    "name": name,
                    "required": False,
                    "status": "not-assessed",
                    "evidence": [],
                }
            )
            continue
        if not isinstance(item, dict):
            raise CloseoutError(f"surfaces.{name} must be an object")
        required = item.get("required")
        status = item.get("status")
        if not isinstance(required, bool):
            raise CloseoutError(f"surfaces.{name}.required must be boolean")
        if status not in STATUSES:
            raise CloseoutError(f"surfaces.{name}.status is invalid")
        evidence = _strings(item.get("evidence"), f"surfaces.{name}.evidence")
        if required and status == "not-applicable":
            raise CloseoutError(f"required surface {name} cannot be not-applicable")
        if status == "current" and not evidence:
            raise CloseoutError(f"current surface {name} requires evidence")
        if required and status != "current":
            pending.append(name)
        surfaces.append(
            {"name": name, "required": required, "status": status, "evidence": evidence}
        )

    cleanup = _strings(payload.get("cleanup_requests", []), "cleanup_requests")
    memory_writes = _strings(
        payload.get("memory_write_requests", []), "memory_write_requests"
    )
    cleanup_authorized = payload.get("cleanup_authorized")
    memory_authorized = payload.get("memory_write_authorized")
    if not isinstance(cleanup_authorized, bool):
        raise CloseoutError("cleanup_authorized must be boolean")
    if not isinstance(memory_authorized, bool):
        raise CloseoutError("memory_write_authorized must be boolean")
    blocked_actions: list[dict[str, Any]] = []
    authorized_actions: list[dict[str, Any]] = []
    for kind, requests, authorized in (
        ("cleanup", cleanup, cleanup_authorized),
        ("memory-write", memory_writes, memory_authorized),
    ):
        target = authorized_actions if authorized else blocked_actions
        target.extend({"kind": kind, "request": request} for request in requests)

    ready = not pending and not blocked_actions
    return {
        "schema_version": 1,
        "kind": "knowledge_closeout",
        "mode": mode,
        "source_identity": source_identity.strip(),
        "surfaces": surfaces,
        "pending_surfaces": pending,
        "authorized_actions": authorized_actions,
        "blocked_actions": blocked_actions,
        "closeout_ready": ready,
        "writes_performed": False,
        "cleanup_performed": False,
        "memory_writes_performed": False,
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
            raise CloseoutError("input must be a JSON object")
        result = assess(payload)
    except (CloseoutError, UnicodeDecodeError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({"error": str(error)}) if args.json else f"ERROR: {error}")
        return 2
    print(json.dumps(result, indent=2) if args.json else json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
