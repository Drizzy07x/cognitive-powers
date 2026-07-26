#!/usr/bin/env python3
"""Render board, timeline, blockers, and handoff data from durable state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence


BLOCKING_STATUSES = {"blocked", "failed", "rejected", "inconclusive", "stale"}
TERMINAL_STATUSES = {"complete", "completed", "verified"}


class ReportError(ValueError):
    """Raised when durable report input is malformed."""


def _objects(value: object, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ReportError(f"{label} must be a list of objects")
    return value


def render_report(
    state: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    if not isinstance(state, dict) or not isinstance(state.get("criteria"), list):
        raise ReportError("state must contain criteria")
    criteria = _objects(state.get("criteria"), "criteria")
    packets = _objects(state.get("work_packets", []), "work_packets")

    columns: dict[str, list[str]] = {}
    blockers: list[dict[str, str]] = []
    for item in [*criteria, *packets]:
        identity = str(item.get("id", "")).strip()
        status = str(item.get("status", "unknown")).strip().lower()
        if not identity:
            raise ReportError("criteria and packets require id")
        columns.setdefault(status, []).append(identity)
        if status in BLOCKING_STATUSES:
            blockers.append(
                {
                    "id": identity,
                    "status": status,
                    "reason": str(
                        item.get("reason")
                        or item.get("error")
                        or "status requires attention"
                    ),
                }
            )

    packet_by_id = {str(item["id"]): item for item in packets}
    ready: list[str] = []
    waiting: list[dict[str, object]] = []
    for packet_id, packet in packet_by_id.items():
        if str(packet.get("status", "")).lower() in TERMINAL_STATUSES:
            continue
        dependencies = [str(value) for value in packet.get("depends_on", [])]
        unmet = [
            dependency
            for dependency in dependencies
            if dependency not in packet_by_id
            or str(packet_by_id[dependency].get("status", "")).lower()
            not in TERMINAL_STATUSES
        ]
        if unmet:
            waiting.append({"id": packet_id, "unmetDependencies": unmet})
        elif str(packet.get("status", "pending")).lower() not in BLOCKING_STATUSES:
            ready.append(packet_id)

    timeline: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, dict):
            raise ReportError("events must contain objects")
        timeline.append(
            {
                key: value
                for key, value in event.items()
                if key
                in {
                    "seq",
                    "at",
                    "event",
                    "packet",
                    "criterion",
                    "actor",
                    "owner",
                    "reason",
                }
            }
        )
    timeline.sort(key=lambda item: (int(item.get("seq", 0)), str(item.get("at", ""))))

    return {
        "sessionId": state.get("session_id"),
        "status": state.get("status", "unknown"),
        "board": {key: sorted(value) for key, value in sorted(columns.items())},
        "blockers": blockers,
        "readyPackets": sorted(ready),
        "waitingPackets": waiting,
        "timeline": timeline,
        "handoff": {
            "objective": state.get("objective"),
            "lastSequence": state.get("last_seq", 0),
            "nextPackets": sorted(ready),
            "blockedCount": len(blockers),
            "sourceFingerprint": state.get("source_fingerprint"),
        },
    }


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReportError(f"JSON object required: {path}")
    return value


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ReportError(f"ledger line {line_number} is not an object")
        events.append(value)
    return events


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args(argv)
    try:
        state = read_json(args.state)
        events = read_events(args.ledger or args.state.with_name("ledger.jsonl"))
        report = render_report(state, events)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ReportError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
