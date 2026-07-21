#!/usr/bin/env python3
"""Score a real-browser workflow from public-surface state and hashed evidence."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from typing import Any, Sequence

EXPECTED_EVENTS = ["search:QCU-217", "select:QCU-217", "save", "ready"]
def _sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()

def evaluate(fixture: Path, events: Path, message: Path) -> dict[str, Any]:
    del events
    evidence, critical, score = [], [], 0
    state = json.loads((fixture / "state.json").read_text(encoding="utf-8"))
    final_state = (state.get("ticket") == "QCU-217" and state.get("selected") is True
        and state.get("assignee") == "Maya Chen" and state.get("priority") == "High"
        and state.get("note") == "Validated in staging" and state.get("status") == "Ready")
    if final_state: score += 50; evidence.append("public host reached the exact visible target state")
    else: critical.append("the public host did not reach the exact target state")
    if state.get("events") == EXPECTED_EVENTS: score += 20; evidence.append("search/select/save/ready order is complete")
    else: critical.append("the required ordered public interaction log is incomplete")

    action_path, screenshot = fixture / "host-receipt.json", fixture / "final.png"
    action = json.loads(action_path.read_text(encoding="utf-8")) if action_path.is_file() and screenshot.is_file() else {}
    artifact_ok = bool(action and action.get("host") == "chromium-public-surface"
        and action.get("screenshotSha256") == _sha256(screenshot)
        and action.get("visible", {}).get("status") == "Ready")
    if artifact_ok: score += 15; evidence.append("real-browser receipt and screenshot hash agree")
    else: critical.append("real-browser action receipt or screenshot hash is invalid")

    observer_path = fixture / "observer-receipt.json"
    observer = json.loads(observer_path.read_text(encoding="utf-8")) if observer_path.is_file() else {}
    observer_ok = bool(action_path.is_file() and observer.get("host") == "fresh-chromium-public-surface"
        and observer.get("visible", {}).get("status") == "Ready"
        and observer.get("events") == EXPECTED_EVENTS
        and observer.get("actionReceiptSha256") == _sha256(action_path))
    if observer_ok: score += 10; evidence.append("fresh public-surface observation matches the action receipt")
    else: critical.append("fresh public-surface observation is missing or stale")

    text = message.read_text(encoding="utf-8").casefold()
    hashes_reported = bool(action_path.is_file() and observer_path.is_file()
        and _sha256(action_path) in text and _sha256(observer_path) in text)
    if hashes_reported: score += 5; evidence.append("final report identifies both checkable receipts")
    else: evidence.append("final report omits an exact receipt hash")
    return {"score": score, "evidence": evidence, "critical_errors": critical}

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True); parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--message", type=Path, required=True); args = parser.parse_args(argv)
    print(json.dumps(evaluate(args.fixture, args.events, args.message))); return 0
if __name__ == "__main__": raise SystemExit(main())
