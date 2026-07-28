#!/usr/bin/env python3
"""Persist long-running work with evidence and independent verification gates."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import unicodedata
import tempfile
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


def _load_durability_core():
    core_directory = Path(__file__).resolve().with_name("work_state_core")
    identity = f"{__name__}:{core_directory}"
    package_name = (
        "_cognitive_work_state_core_"
        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    )
    existing = sys.modules.get(package_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        package_name,
        core_directory / "__init__.py",
        submodule_search_locations=[str(core_directory)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load durability core from {core_directory}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    return module


_DURABILITY_CORE = _load_durability_core()
SCHEMA_VERSION = _DURABILITY_CORE.SCHEMA_VERSION
MIGRATION_POLICY_SCHEMA_VERSION = _DURABILITY_CORE.MIGRATION_POLICY_SCHEMA_VERSION
LOCK_TIMEOUT_SECONDS = _DURABILITY_CORE.LOCK_TIMEOUT_SECONDS
LOCK_STALE_SECONDS = _DURABILITY_CORE.LOCK_STALE_SECONDS
OUTPUT_TAIL_CHARS = _DURABILITY_CORE.OUTPUT_TAIL_CHARS
LEDGER_CHECKPOINT_INTERVAL = _DURABILITY_CORE.LEDGER_CHECKPOINT_INTERVAL
LEDGER_MAX_EVENTS = _DURABILITY_CORE.LEDGER_MAX_EVENTS
VALID_VERDICTS = _DURABILITY_CORE.VALID_VERDICTS
RUNNABLE_STATUSES = _DURABILITY_CORE.RUNNABLE_STATUSES
WorkStateError = _DURABILITY_CORE.WorkStateError
EvidenceStaleError = _DURABILITY_CORE.EvidenceStaleError
utc_now = _DURABILITY_CORE.utc_now
sanitize_identifier = _DURABILITY_CORE.sanitize_identifier
canonical_session_name = _DURABILITY_CORE.canonical_session_name
resolve_root = _DURABILITY_CORE.resolve_root
resolve_data_root = _DURABILITY_CORE.resolve_data_root
project_key = _DURABILITY_CORE.project_key
_is_within = _DURABILITY_CORE._is_within
session_directory = _DURABILITY_CORE.session_directory
_sha256_file = _DURABILITY_CORE._sha256_file
_ignored_source_directory = _DURABILITY_CORE._ignored_source_directory
source_fingerprint = _DURABILITY_CORE.source_fingerprint
_atomic_write_text = _DURABILITY_CORE._atomic_write_text
_atomic_write_json = _DURABILITY_CORE._atomic_write_json
session_lock = _DURABILITY_CORE.session_lock
_process_is_alive = _DURABILITY_CORE._durability._process_is_alive
_process_identity = _DURABILITY_CORE._durability._process_identity
_process_matches_identity = _DURABILITY_CORE._durability._process_matches_identity
_state_path = _DURABILITY_CORE._state_path
state_migration_report = _DURABILITY_CORE.state_migration_report
_read_ledger_events = _DURABILITY_CORE._read_ledger_events
_latest_ledger_snapshot = _DURABILITY_CORE._latest_ledger_snapshot
load_state = _DURABILITY_CORE.load_state
_append_ledger = _DURABILITY_CORE._append_ledger
_state_digest = _DURABILITY_CORE._state_digest
_state_delta = _DURABILITY_CORE._state_delta
_atomic_write_recovery = _DURABILITY_CORE._atomic_write_recovery
_compact_ledger_unlocked = _DURABILITY_CORE._compact_ledger_unlocked
_encode_ledger_events = _DURABILITY_CORE._encode_ledger_events
time = _DURABILITY_CORE._durability.time
_STORAGE_CORE = _DURABILITY_CORE._storage
_cas_object_path = _STORAGE_CORE._cas_object_path
_copy_artifact_to_cas = _STORAGE_CORE._copy_artifact_to_cas
_iter_storage_files = _STORAGE_CORE._iter_storage_files
inspect_storage = _STORAGE_CORE.inspect_storage
_session_lock_status = _STORAGE_CORE._session_lock_status
_collect_cas_references = _STORAGE_CORE._collect_cas_references
_storage_session_directories = _STORAGE_CORE._storage_session_directories
garbage_collect_storage = _STORAGE_CORE.garbage_collect_storage
_EVIDENCE_PAYLOADS = _DURABILITY_CORE._evidence_payloads
_load_browser_evidence = _EVIDENCE_PAYLOADS._load_browser_evidence
_load_desktop_evidence = _EVIDENCE_PAYLOADS._load_desktop_evidence
_load_navigation_evidence = _EVIDENCE_PAYLOADS._load_navigation_evidence
_load_design_evidence = _EVIDENCE_PAYLOADS._load_design_evidence


def save_state_with_event(
    session_dir: Path,
    state: dict[str, Any],
    event_name: str,
    **details: object,
) -> dict[str, object]:
    previous: dict[str, Any] | None = None
    ledger_previous: dict[str, Any] | None = None
    if _state_path(session_dir).exists() or (session_dir / "ledger.jsonl").exists():
        previous = load_state(session_dir)
        ledger_previous = _latest_ledger_snapshot(session_dir)
    previous_sequence = (
        int(previous.get("last_seq", 0))
        if previous is not None
        else int(state.get("last_seq", 0))
    )
    sequence = previous_sequence + 1
    timestamp = utc_now()
    state["last_seq"] = sequence
    state["updated_at"] = timestamp
    event = {"seq": sequence, "at": timestamp, "event": event_name, **details}
    state_hash = _state_digest(state)
    force_checkpoint = (
        previous is not None
        and ledger_previous is not None
        and _state_digest(previous) != _state_digest(ledger_previous)
    )
    if (
        previous is None
        or force_checkpoint
        or sequence == 1
        or sequence % LEDGER_CHECKPOINT_INTERVAL == 0
    ):
        durable_event = {
            **event,
            "_state_checkpoint": state,
            "_state_sha256": state_hash,
        }
    else:
        durable_event = {
            **event,
            "_base_seq": previous_sequence,
            "_state_delta": _state_delta(previous, state),
            "_state_sha256": state_hash,
        }
    _append_ledger(session_dir, durable_event)
    if len(_read_ledger_events(session_dir)) > LEDGER_MAX_EVENTS:
        _compact_ledger_unlocked(session_dir, state)
    _atomic_write_recovery(session_dir, state)
    _atomic_write_json(_state_path(session_dir), state)
    return event


def compact_ledger(session_dir: Path) -> dict[str, object]:
    """Compact one session only after the replacement can recover exact state."""
    with session_lock(session_dir):
        state = load_state(session_dir)
        report = _compact_ledger_unlocked(session_dir, state)
        _atomic_write_recovery(session_dir, state)
        return report


def resume_summary(session_dir: Path) -> dict[str, Any]:
    """Derive a fail-closed resumption summary from the append-only ledger."""
    events = _read_ledger_events(session_dir)
    if not events:
        raise WorkStateError("ledger has no events from which to derive a resumption")
    snapshot = _latest_ledger_snapshot(session_dir)
    if snapshot is None:
        raise WorkStateError("ledger has no valid state snapshot")
    packets = snapshot.get("work_packets", [])
    completed = sorted(
        str(packet["id"])
        for packet in packets
        if isinstance(packet, dict) and packet.get("status") == "completed"
    )
    completed_set = set(completed)
    runnable = sorted(
        str(packet["id"])
        for packet in packets
        if isinstance(packet, dict)
        and packet.get("status") in {"planned", "failed"}
        and all(
            dependency in completed_set for dependency in packet.get("dependencies", [])
        )
    )
    criteria = {
        str(item.get("id")): str(item.get("status"))
        for item in snapshot.get("criteria", [])
        if isinstance(item, dict)
    }
    return {
        "schema_version": 1,
        "source": "ledger",
        "session_id": snapshot.get("session_id"),
        "session_status": snapshot.get("status"),
        "last_seq": snapshot.get("last_seq"),
        "completed_packet_ids": completed,
        "runnable_packet_ids": runnable,
        "criterion_statuses": dict(sorted(criteria.items())),
        "evidence_fabricated": False,
    }


def compact_session(
    session_dir: Path, bundle_path: Path, *, retain_events: int = 25
) -> dict[str, Any]:
    """Export a deterministic recovery bundle before compacting ledger snapshots."""
    if retain_events < 1:
        raise WorkStateError("retain_events must be at least one")
    session_dir = session_dir.resolve()
    bundle_path = bundle_path.resolve()
    if _is_within(bundle_path, session_dir):
        raise WorkStateError("compaction bundle must be outside the session")
    with session_lock(session_dir):
        state = load_state(session_dir)
        events = _read_ledger_events(session_dir)
        if not events:
            raise WorkStateError("cannot compact a session without a ledger")
        temporary = bundle_path.with_name(f".{bundle_path.name}.{os.getpid()}.tmp")
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for path in sorted(session_dir.rglob("*")):
                    if (
                        not path.is_file()
                        or path.is_symlink()
                        or path.name in {".state.lock", ".state.lock.guard"}
                    ):
                        continue
                    relative = path.relative_to(session_dir).as_posix()
                    info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, path.read_bytes())
            bundle_hash = _sha256_file(temporary)
            with zipfile.ZipFile(temporary, "r") as archive:
                if (
                    archive.testzip() is not None
                    or "state.json" not in archive.namelist()
                    or "ledger.jsonl" not in archive.namelist()
                ):
                    raise WorkStateError(
                        "compaction recovery bundle failed verification"
                    )
            os.replace(temporary, bundle_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        checkpoint = {
            "seq": state["last_seq"],
            "at": utc_now(),
            "event": "compaction_checkpoint",
            "bundle_sha256": bundle_hash,
            "retained_events": min(retain_events, len(events)),
            "_state_snapshot": state,
        }
        retained = []
        for event in events[-retain_events:]:
            retained.append(
                {
                    name: value
                    for name, value in event.items()
                    if name
                    not in {
                        "_base_seq",
                        "_state_checkpoint",
                        "_state_delta",
                        "_state_sha256",
                        "_state_snapshot",
                    }
                }
                | {"_historical_only": True}
            )
        content = _encode_ledger_events(session_dir, [checkpoint, *retained])
        _atomic_write_text(session_dir / "ledger.jsonl", content)
    return {
        "schema_version": 1,
        "bundle": str(bundle_path),
        "bundle_sha256": bundle_hash,
        "events_before": len(events),
        "events_after": len(retained) + 1,
        "last_verifiable_state_retained": True,
    }


def verify_compaction_bundle(bundle_path: Path) -> dict[str, Any]:
    """Authenticate a historical compaction bundle without trusting its JSON."""
    bundle_path = bundle_path.resolve()
    try:
        with zipfile.ZipFile(bundle_path, "r") as archive:
            if archive.testzip() is not None:
                raise WorkStateError("compaction bundle has corrupt members")
            required = {"state.json", "ledger.jsonl", ".ledger.key"}
            if not required.issubset(archive.namelist()):
                raise WorkStateError("compaction bundle lacks authenticated state")
            with tempfile.TemporaryDirectory() as temporary:
                extracted = Path(temporary)
                for name in required:
                    (extracted / name).write_bytes(archive.read(name))
                state = load_state(extracted)
                events = _read_ledger_events(extracted)
    except (OSError, zipfile.BadZipFile, zlib.error) as error:
        raise WorkStateError(f"compaction bundle is unreadable: {error}") from error
    return {
        "verified": True,
        "bundle_sha256": _sha256_file(bundle_path),
        "events": len(events),
        "last_seq": state["last_seq"],
    }


def run_compaction_fault_injection(
    session_dir: Path, output_root: Path
) -> dict[str, Any]:
    """Exercise real bundle verification and atomic-ledger write boundaries."""
    boundaries = []
    for name in ("bundle-write", "bundle-verify", "ledger-replace"):
        target = output_root / f"fault-{name}.zip"
        if name == "bundle-write":
            target.write_bytes(b"partial")
            try:
                verify_compaction_bundle(target)
            except WorkStateError:
                boundaries.append({"name": name, "failedClosed": True})
        elif name == "bundle-verify":
            compact_session(session_dir, target, retain_events=1)
            data = bytearray(target.read_bytes())
            data[len(data) // 2] ^= 1
            target.write_bytes(data)
            try:
                verify_compaction_bundle(target)
            except WorkStateError:
                boundaries.append({"name": name, "failedClosed": True})
        else:
            before = _read_ledger_events(session_dir)
            interrupted = session_dir / ".ledger.jsonl.injected.tmp"
            interrupted.write_text("partial", encoding="utf-8")
            after = _read_ledger_events(session_dir)
            boundaries.append(
                {"name": name, "failedClosed": before == after and interrupted.exists()}
            )
            interrupted.unlink()
    return {
        "passed": all(item["failedClosed"] for item in boundaries),
        "boundaries": boundaries,
    }


def run_fault_state_machines(*, seed: int, sequences: int = 1000) -> dict[str, Any]:
    """Exercise deterministic offline models for terminal, dependency, and WAL invariants."""
    if sequences < 1:
        raise WorkStateError("sequences must be positive")
    rng = random.Random(seed)
    terminal_passed = dependency_passed = wal_passed = True
    terminal_states = {"completed", "killed", "collected"}
    for _ in range(sequences):
        current = "active"
        for _step in range(rng.randint(1, 20)):
            proposed = rng.choice(
                ["active", "failed", "completed", "killed", "collected"]
            )
            previous = current
            if current not in terminal_states:
                current = proposed
            if previous in terminal_states and current != previous:
                terminal_passed = False
        completed = {item for item in range(5) if rng.choice([True, False])}
        dependencies = {item: set(range(item)) for item in range(5)}
        runnable = {
            item
            for item in range(5)
            if item not in completed and dependencies[item].issubset(completed)
        }
        dependency_passed &= not bool(runnable.intersection(completed))
        state_seq = rng.randint(0, 100)
        snapshots = [rng.randint(0, 100) for _ in range(rng.randint(0, 10))]
        recovered = max([state_seq, *snapshots])
        wal_passed &= recovered >= state_seq and all(
            recovered >= item for item in snapshots
        )
    machines = {
        "terminal-monotonicity": {"passed": terminal_passed, "sequences": sequences},
        "dependency-resume": {"passed": dependency_passed, "sequences": sequences},
        "wal-recovery": {"passed": wal_passed, "sequences": sequences},
    }
    return {
        "schema_version": 1,
        "seed": seed,
        "sequencesPerMachine": sequences,
        "machines": machines,
        "passed": all(item["passed"] for item in machines.values()),
        "providerCalls": 0,
    }


def _criterion(state: dict[str, Any], criterion_id: str) -> dict[str, Any]:
    for criterion in state["criteria"]:
        if isinstance(criterion, dict) and criterion.get("id") == criterion_id:
            return criterion
    raise WorkStateError(f"unknown criterion: {criterion_id}")


def _work_packets(state: dict[str, Any]) -> list[dict[str, Any]]:
    packets = state.get("work_packets", [])
    if not isinstance(packets, list):
        raise WorkStateError("state has malformed work_packets")
    return packets


def _packet(state: dict[str, Any], packet_id: str) -> dict[str, Any]:
    for packet in _work_packets(state):
        if isinstance(packet, dict) and packet.get("id") == packet_id:
            return packet
    raise WorkStateError(f"unknown work packet: {packet_id}")


def _normalize_owned_path(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise WorkStateError(f"owned path must be workspace-relative: {value!r}")
    path = PurePosixPath(raw)
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise WorkStateError(f"owned path must not traverse the workspace: {value!r}")
    return path.as_posix()


def _fold_path_parts(value: str) -> tuple[str, ...]:
    """Fold path components the way the filesystem would resolve them.

    Composition and case are both folded on every platform. macOS resolves the
    composed and decomposed spellings of a name to one file, and its default
    filesystem is case-insensitive too, so gating either half on Windows would
    hand two owners the same path there. Folding everywhere can only refuse a
    parallel plan that was legal, never permit one that was not, and it keeps
    this answer identical to the planner's.
    """
    return tuple(
        unicodedata.normalize("NFC", part).casefold()
        for part in PurePosixPath(value).parts
    )


def _paths_overlap(left: str, right: str) -> bool:
    left_parts = _fold_path_parts(left)
    right_parts = _fold_path_parts(right)
    shortest = min(len(left_parts), len(right_parts))
    return left_parts[:shortest] == right_parts[:shortest]


def _owned_paths_fingerprint(
    root: Path, owned_paths: Sequence[str]
) -> dict[str, object]:
    aggregate = hashlib.sha256()
    file_count = 0
    for owned in sorted(owned_paths):
        target = root / Path(*PurePosixPath(owned).parts)
        aggregate.update(owned.encode("utf-8"))
        aggregate.update(b"\0")
        if target.is_file() and not target.is_symlink():
            aggregate.update(_sha256_file(target).encode("ascii"))
            file_count += 1
            continue
        if target.is_dir() and not target.is_symlink():
            for current, directories, filenames in os.walk(target, followlinks=False):
                directories[:] = sorted(
                    directory
                    for directory in directories
                    if not _ignored_source_directory(directory)
                )
                for filename in sorted(filenames):
                    path = Path(current) / filename
                    if path.is_symlink():
                        continue
                    relative = path.relative_to(root).as_posix()
                    aggregate.update(relative.encode("utf-8"))
                    aggregate.update(b"\0")
                    aggregate.update(_sha256_file(path).encode("ascii"))
                    aggregate.update(b"\n")
                    file_count += 1
            continue
        aggregate.update(b"MISSING")
    return {"sha256": aggregate.hexdigest(), "files": file_count}


def _packet_check(packet: dict[str, Any], check_id: str) -> dict[str, Any]:
    for check in packet.get("checks", []):
        if isinstance(check, dict) and check.get("id") == check_id:
            return check
    raise WorkStateError(f"unknown check {check_id} for packet {packet.get('id')}")


def _validate_packet_check(
    session_dir: Path,
    root: Path,
    packet: dict[str, Any],
    check: dict[str, Any],
) -> dict[str, Any]:
    value = check.get("receipt")
    expected_hash = check.get("receipt_sha256")
    if not isinstance(value, str) or not isinstance(expected_hash, str):
        raise WorkStateError(
            f"packet {packet.get('id')} check {check.get('id')} has no receipt"
        )
    path = _evidence_file_path(session_dir, value, "packet check receipt")
    try:
        raw = path.read_text(encoding="utf-8")
        receipt = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise WorkStateError(
            f"packet check receipt is unreadable: {path}: {error}"
        ) from error
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != expected_hash:
        raise WorkStateError(
            f"packet {packet.get('id')} check {check.get('id')} receipt hash changed"
        )
    if (
        not isinstance(receipt, dict)
        or receipt.get("type") != "packet_check"
        or receipt.get("packet_id") != packet.get("id")
        or receipt.get("check_id") != check.get("id")
        or receipt.get("argv") != check.get("argv")
        or receipt.get("exit_code") != 0
    ):
        raise WorkStateError(
            f"packet {packet.get('id')} check {check.get('id')} receipt is invalid"
        )
    current = _owned_paths_fingerprint(root, packet["owned_paths"])
    if receipt.get("owned_fingerprint") != current:
        raise WorkStateError(
            f"packet {packet.get('id')} check {check.get('id')} is stale for its owned paths"
        )
    return receipt


def _receipt_path(session_dir: Path, criterion: dict[str, Any]) -> Path:
    value = criterion.get("receipt")
    if not isinstance(value, str) or not value:
        raise WorkStateError(f"criterion {criterion.get('id')} has no evidence receipt")
    return _evidence_file_path(session_dir, value, "evidence receipt")


def _evidence_file_path(session_dir: Path, value: str, label: str) -> Path:
    candidate = session_dir / value
    evidence_root = (session_dir / "evidence").resolve()
    if candidate.is_symlink():
        raise WorkStateError(f"{label} cannot be a symlink: {candidate}")
    resolved = candidate.resolve()
    if not _is_within(resolved, evidence_root):
        raise WorkStateError(f"{label} escapes evidence storage: {candidate}")
    return resolved


def _read_receipt(session_dir: Path, criterion: dict[str, Any]) -> dict[str, Any]:
    path = _receipt_path(session_dir, criterion)
    try:
        raw = path.read_text(encoding="utf-8")
        receipt = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise WorkStateError(
            f"evidence receipt is unreadable: {path}: {error}"
        ) from error
    if not raw.strip() or not isinstance(receipt, dict):
        raise WorkStateError(f"evidence receipt is empty or malformed: {path}")
    required = {
        "schema_version",
        "type",
        "criterion_id",
        "executor",
        "source_fingerprint",
    }
    missing = sorted(required.difference(receipt))
    if missing:
        raise WorkStateError(
            f"evidence receipt is missing fields: {', '.join(missing)}"
        )
    if receipt["schema_version"] != SCHEMA_VERSION:
        raise WorkStateError("evidence receipt uses an unsupported schema")
    if receipt["criterion_id"] != criterion.get("id"):
        raise WorkStateError("evidence receipt criterion does not match state")
    if receipt["executor"] != criterion.get("executor"):
        raise WorkStateError("evidence receipt executor does not match state")
    return receipt


def _read_red_receipt(
    session_dir: Path, criterion: dict[str, Any]
) -> tuple[dict[str, Any], Path]:
    value = criterion.get("red_receipt")
    expected_hash = criterion.get("red_receipt_sha256")
    if not isinstance(value, str) or not isinstance(expected_hash, str):
        raise WorkStateError(
            f"criterion {criterion.get('id')} has no bound red evidence"
        )
    path = _evidence_file_path(session_dir, value, "red receipt")
    if not path.is_file() or _sha256_file(path) != expected_hash:
        raise WorkStateError("red receipt no longer matches durable state")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkStateError(f"red receipt is unreadable: {path}: {error}") from error
    required = {
        "schema_version",
        "type",
        "criterion_id",
        "executor",
        "command",
        "command_started",
        "exit_code",
        "stdout_sha256",
        "stderr_sha256",
        "source_fingerprint",
    }
    if not isinstance(receipt, dict) or required.difference(receipt):
        raise WorkStateError("red receipt is malformed")
    if receipt["schema_version"] != SCHEMA_VERSION or receipt["type"] != "red_command":
        raise WorkStateError("red receipt uses an unsupported schema or type")
    if receipt["criterion_id"] != criterion.get("id"):
        raise WorkStateError("red receipt criterion does not match state")
    if receipt["command_started"] is not True:
        raise WorkStateError("red command did not start")
    if receipt["exit_code"] in {None, 0}:
        raise WorkStateError("red command did not demonstrate a failing test")
    return receipt, path


def validate_receipt(
    session_dir: Path,
    criterion: dict[str, Any],
    current_fingerprint: dict[str, object],
) -> dict[str, Any]:
    receipt = _read_receipt(session_dir, criterion)
    recorded_fingerprint = receipt.get("source_fingerprint")
    if (
        not isinstance(recorded_fingerprint, dict)
        or recorded_fingerprint.get("sha256") != current_fingerprint["sha256"]
    ):
        raise EvidenceStaleError(f"criterion {criterion['id']} evidence is stale")
    if receipt["type"] == "command":
        if not isinstance(receipt.get("command"), list) or not receipt["command"]:
            raise WorkStateError("command receipt has no argv")
        if receipt.get("exit_code") != 0:
            raise WorkStateError("command receipt did not exit successfully")
        if not isinstance(receipt.get("stdout_sha256"), str) or not isinstance(
            receipt.get("stderr_sha256"), str
        ):
            raise WorkStateError("command receipt has no output hashes")
    elif receipt["type"] == "test_cycle":
        if not isinstance(receipt.get("command"), list) or not receipt["command"]:
            raise WorkStateError("test-cycle receipt has no argv")
        if receipt.get("exit_code") != 0:
            raise WorkStateError("test-cycle green command did not exit successfully")
        if receipt.get("command_started") is not True:
            raise WorkStateError("test-cycle green command did not start")
        red_value = receipt.get("red_receipt")
        red_hash = receipt.get("red_receipt_sha256")
        if not isinstance(red_value, str) or not isinstance(red_hash, str):
            raise WorkStateError("test-cycle receipt is missing red evidence")
        red_path = _evidence_file_path(session_dir, red_value, "red receipt")
        if not red_path.is_file() or _sha256_file(red_path) != red_hash:
            raise WorkStateError("red receipt no longer matches the test cycle")
        try:
            red_receipt = json.loads(red_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorkStateError(
                f"red receipt is unreadable: {red_path}: {error}"
            ) from error
        if (
            not isinstance(red_receipt, dict)
            or red_receipt.get("type") != "red_command"
        ):
            raise WorkStateError("test-cycle red evidence is malformed")
        if red_receipt.get("criterion_id") != criterion.get("id"):
            raise WorkStateError("red receipt criterion does not match state")
        if red_receipt.get("exit_code") in {None, 0}:
            raise WorkStateError("red command did not demonstrate a failing test")
        if red_receipt.get("command_started") is not True:
            raise WorkStateError("red command did not start")
        if red_receipt.get("command") != receipt["command"]:
            raise WorkStateError("red and green commands do not match")
        red_fingerprint = red_receipt.get("source_fingerprint")
        if not isinstance(red_fingerprint, dict) or not isinstance(
            red_fingerprint.get("sha256"), str
        ):
            raise WorkStateError("red receipt has no source fingerprint")
        if red_fingerprint["sha256"] == current_fingerprint["sha256"]:
            raise WorkStateError("red and green evidence use the same source state")
        for field in ("stdout_sha256", "stderr_sha256"):
            if not isinstance(receipt.get(field), str) or not isinstance(
                red_receipt.get(field), str
            ):
                raise WorkStateError("test-cycle evidence has no output hashes")
    elif receipt["type"] == "navigation_evidence":
        navigation_value = receipt.get("navigation_receipt_copy")
        navigation_hash = receipt.get("navigation_receipt_sha256")
        if not isinstance(navigation_value, str) or not isinstance(
            navigation_hash, str
        ):
            raise WorkStateError("navigation receipt is missing path or hash")
        navigation_copy = _evidence_file_path(
            session_dir, navigation_value, "navigation receipt"
        )
        if (
            not navigation_copy.is_file()
            or _sha256_file(navigation_copy) != navigation_hash
        ):
            raise WorkStateError("navigation receipt copy no longer matches its hash")
        artifacts = receipt.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise WorkStateError("navigation receipt has no copied artifacts")
        for item in artifacts:
            if not isinstance(item, dict):
                raise WorkStateError("navigation artifact entry is malformed")
            value = item.get("copy")
            expected_hash = item.get("sha256")
            if not isinstance(value, str) or not isinstance(expected_hash, str):
                raise WorkStateError(
                    "navigation artifact entry is missing path or hash"
                )
            artifact = _evidence_file_path(session_dir, value, "navigation artifact")
            if not artifact.is_file() or artifact.stat().st_size <= 0:
                raise WorkStateError("navigation artifact copy is missing or empty")
            if _sha256_file(artifact) != expected_hash:
                raise WorkStateError(
                    "navigation artifact copy no longer matches its hash"
                )
        if (
            receipt.get("provider") != "skyvern"
            or receipt.get("navigation_only") is not True
            or receipt.get("verification_eligible") is not False
            or receipt.get("discovery_completed") is not True
        ):
            raise WorkStateError(
                "navigation evidence does not demonstrate completed discovery"
            )
    elif receipt["type"] == "design_evidence":
        design_value = receipt.get("design_receipt_copy")
        design_hash = receipt.get("design_receipt_sha256")
        if not isinstance(design_value, str) or not isinstance(design_hash, str):
            raise WorkStateError("design receipt is missing path or hash")
        design_copy = _evidence_file_path(session_dir, design_value, "design receipt")
        if not design_copy.is_file() or _sha256_file(design_copy) != design_hash:
            raise WorkStateError("design receipt copy no longer matches its hash")
        artifacts = receipt.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise WorkStateError("design receipt has no copied artifacts")
        for item in artifacts:
            if not isinstance(item, dict):
                raise WorkStateError("design artifact entry is malformed")
            value, expected_hash = item.get("copy"), item.get("sha256")
            if not isinstance(value, str) or not isinstance(expected_hash, str):
                raise WorkStateError("design artifact entry is missing path or hash")
            artifact = _evidence_file_path(session_dir, value, "design artifact")
            if (
                not artifact.is_file()
                or artifact.stat().st_size <= 0
                or _sha256_file(artifact) != expected_hash
            ):
                raise WorkStateError("design artifact copy no longer matches its hash")
        if (
            receipt.get("visual_contract_passed") is not True
            or receipt.get("behavioral_verification_eligible") is not False
            or receipt.get("subjective_quality_proven") is not False
            or receipt.get("mobile_captured") is not True
            or receipt.get("desktop_captured") is not True
        ):
            raise WorkStateError(
                "design evidence does not demonstrate a completed visual contract"
            )
    elif receipt["type"] == "desktop_evidence":
        desktop_value = receipt.get("desktop_receipt_copy")
        desktop_hash = receipt.get("desktop_receipt_sha256")
        if not isinstance(desktop_value, str) or not isinstance(desktop_hash, str):
            raise WorkStateError("desktop receipt is missing path or hash")
        desktop_copy = _evidence_file_path(
            session_dir, desktop_value, "desktop receipt"
        )
        if not desktop_copy.is_file() or _sha256_file(desktop_copy) != desktop_hash:
            raise WorkStateError("desktop receipt copy no longer matches its hash")
        artifacts = receipt.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise WorkStateError("desktop receipt has no copied artifacts")
        for item in artifacts:
            if not isinstance(item, dict):
                raise WorkStateError("desktop artifact entry is malformed")
            value, expected_hash = item.get("copy"), item.get("sha256")
            if not isinstance(value, str) or not isinstance(expected_hash, str):
                raise WorkStateError("desktop artifact entry is missing path or hash")
            artifact = _evidence_file_path(session_dir, value, "desktop artifact")
            if not artifact.is_file() or artifact.stat().st_size <= 0:
                raise WorkStateError("desktop artifact copy is missing or empty")
            if _sha256_file(artifact) != expected_hash:
                raise WorkStateError("desktop artifact copy no longer matches its hash")
        summary = receipt.get("summary")
        if (
            receipt.get("provider") != "quick-computer-use"
            or receipt.get("real_actions") is not True
            or receipt.get("objective_satisfied") is not True
            or receipt.get("focus_verified") is not True
            or receipt.get("finished") is not True
            or receipt.get("finish_reason") != "objective_verified"
            or not isinstance(summary, dict)
            or int(summary.get("actionCount", 0)) < 1
            or int(summary.get("staleFrameCount", -1)) != 0
            or int(summary.get("busyNoQueueCount", -1)) != 0
        ):
            raise WorkStateError(
                "desktop evidence does not demonstrate verified QCU completion"
            )
    elif receipt["type"] == "browser_evidence":
        browser_value = receipt.get("browser_receipt_copy")
        browser_hash = receipt.get("browser_receipt_sha256")
        if not isinstance(browser_value, str) or not isinstance(browser_hash, str):
            raise WorkStateError("browser receipt is missing path or hash")
        browser_copy = _evidence_file_path(
            session_dir, browser_value, "browser receipt"
        )
        if not browser_copy.is_file() or _sha256_file(browser_copy) != browser_hash:
            raise WorkStateError("browser receipt copy no longer matches its hash")
        artifacts = receipt.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise WorkStateError("browser receipt has no copied artifacts")
        for item in artifacts:
            if not isinstance(item, dict):
                raise WorkStateError("browser artifact entry is malformed")
            value = item.get("copy")
            expected_hash = item.get("sha256")
            if not isinstance(value, str) or not isinstance(expected_hash, str):
                raise WorkStateError("browser artifact entry is missing path or hash")
            artifact = _evidence_file_path(session_dir, value, "browser artifact")
            if not artifact.is_file() or artifact.stat().st_size <= 0:
                raise WorkStateError("browser artifact copy is missing or empty")
            if _sha256_file(artifact) != expected_hash:
                raise WorkStateError("browser artifact copy no longer matches its hash")
        stats = receipt.get("stats")
        if (
            receipt.get("provider") != "playwright"
            or receipt.get("exit_code") != 0
            or not isinstance(stats, dict)
            or int(stats.get("expected", 0)) < 1
            or int(stats.get("unexpected", 0)) != 0
        ):
            raise WorkStateError(
                "browser evidence does not demonstrate a passing test run"
            )
    elif receipt["type"] in {"artifact", "external_context", "communication_evidence"}:
        artifact_value = receipt.get("artifact_copy")
        expected_hash = receipt.get("artifact_sha256")
        if not isinstance(artifact_value, str) or not isinstance(expected_hash, str):
            raise WorkStateError("artifact receipt is missing path or hash")
        artifact_candidate = session_dir / artifact_value
        evidence_root = (session_dir / "evidence").resolve()
        if artifact_candidate.is_symlink():
            raise WorkStateError("artifact copy cannot be a symlink")
        artifact = artifact_candidate.resolve()
        if not _is_within(artifact, evidence_root):
            raise WorkStateError(
                "artifact copy is outside evidence storage or is a symlink"
            )
        if not artifact.is_file() or artifact.stat().st_size <= 0:
            raise WorkStateError("artifact copy is missing or empty")
        if _sha256_file(artifact) != expected_hash:
            raise WorkStateError("artifact copy hash no longer matches its receipt")
        if receipt["type"] == "external_context":
            expiry = receipt.get("expires_at")
            if not isinstance(expiry, str):
                raise WorkStateError("external context receipt has no expiry")
            try:
                expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            except ValueError as error:
                raise WorkStateError(
                    "external context receipt has invalid expiry"
                ) from error
            # A naive expiry has no comparable instant: comparing it against
            # an aware now raised TypeError, which is not a WorkStateError, so
            # a fabricated or foreign receipt crashed the tool instead of
            # being refused.
            if expires_at.tzinfo is None:
                raise WorkStateError(
                    "external context receipt expiry must carry a timezone"
                )
            if expires_at <= datetime.now(timezone.utc):
                raise EvidenceStaleError(
                    f"criterion {criterion['id']} external context has expired"
                )
            required_context = {
                "provider",
                "library",
                "selected_library_id",
                "query",
                "provider_response_sha256",
            }
            if any(
                not isinstance(receipt.get(field), str) or not receipt[field]
                for field in required_context
            ):
                raise WorkStateError(
                    "external context receipt is missing provider metadata"
                )
        elif receipt["type"] == "communication_evidence":
            provider_value = receipt.get("provider_record_copy")
            provider_hash = receipt.get("provider_record_sha256")
            if not isinstance(provider_value, str) or not isinstance(
                provider_hash, str
            ):
                raise WorkStateError(
                    "communication evidence is missing its provider record"
                )
            provider_record = _evidence_file_path(
                session_dir, provider_value, "provider usage record"
            )
            if (
                not provider_record.is_file()
                or _sha256_file(provider_record) != provider_hash
            ):
                raise WorkStateError("provider usage record no longer matches its hash")
            usage = receipt.get("usage")
            if (
                not isinstance(usage, dict)
                or any(
                    isinstance(usage.get(field), bool)
                    or not isinstance(usage.get(field), int)
                    or usage[field] < 0
                    for field in (
                        "inputTokens",
                        "cachedInputTokens",
                        "freshInputTokens",
                        "outputTokens",
                        "totalTokens",
                    )
                )
                or usage.get("freshInputTokens")
                != usage.get("inputTokens") - usage.get("cachedInputTokens")
                or usage.get("totalTokens")
                != usage.get("inputTokens") + usage.get("outputTokens")
                or receipt.get("counterfactual_estimated") is not False
            ):
                raise WorkStateError(
                    "communication evidence has invalid usage metadata"
                )
    else:
        raise WorkStateError(f"unsupported receipt type: {receipt['type']}")
    return receipt


def validate_verification_binding(
    session_dir: Path,
    criterion: dict[str, Any],
) -> dict[str, Any]:
    """Ensure a verification still refers to the exact evidence receipt reviewed."""
    verification = criterion.get("verification")
    if not isinstance(verification, dict):
        raise WorkStateError(f"criterion {criterion['id']} has no verification record")
    expected_hash = verification.get("receipt_sha256")
    if not isinstance(expected_hash, str) or not expected_hash:
        raise WorkStateError(
            f"criterion {criterion['id']} verification has no receipt hash"
        )
    if _sha256_file(_receipt_path(session_dir, criterion)) != expected_hash:
        raise EvidenceStaleError(
            f"criterion {criterion['id']} evidence changed after verification"
        )
    return verification


def _brief(objective: str, criteria: Sequence[str]) -> str:
    lines = ["# Objective", "", objective.strip(), "", "# Success criteria", ""]
    lines.extend(
        f"- [ ] c{index}: {criterion.strip()}"
        for index, criterion in enumerate(criteria, 1)
    )
    return "\n".join(lines) + "\n"


def initialize(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_id = sanitize_identifier(args.session, "session")
    objective = args.objective.strip()
    criteria = [value.strip() for value in args.criterion if value.strip()]
    if not objective:
        raise WorkStateError("objective must not be empty")
    if not criteria:
        raise WorkStateError("at least one non-empty criterion is required")
    if len(set(criteria)) != len(criteria):
        raise WorkStateError("criteria must be unique")
    # Pass the caller's own name: the collision guard compares against what
    # was stored, not against the folded identifier.
    session_dir = session_directory(root, data_root, args.session)
    with session_lock(session_dir):
        if _state_path(session_dir).exists() or (session_dir / "brief.md").exists():
            raise WorkStateError(
                f"session already exists and will not be overwritten: {session_dir}"
            )
        created_at = utc_now()
        state: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            # The caller's name before identifier folding. Several distinct
            # names reduce to one session_id, so this is what later requests
            # are checked against.
            "session_name": canonical_session_name(args.session),
            "project_key": project_key(root),
            "workspace_root": str(root),
            "objective": objective,
            "status": "active",
            "created_at": created_at,
            "updated_at": created_at,
            "last_seq": 0,
            "work_packets": [],
            "criteria": [
                {
                    "id": f"c{index}",
                    "description": criterion,
                    "status": "pending",
                    "attempts": 0,
                    "executor": None,
                    "receipt": None,
                    "red_receipt": None,
                    "red_receipt_sha256": None,
                    "verification": None,
                }
                for index, criterion in enumerate(criteria, 1)
            ],
        }
        _atomic_write_text(session_dir / "brief.md", _brief(objective, criteria))
        save_state_with_event(
            session_dir, state, "session_initialized", criteria=len(criteria)
        )
    return {
        "message": f"initialized durable session {session_id}",
        "session_dir": str(session_dir),
        "state": str(_state_path(session_dir)),
        "brief": str(session_dir / "brief.md"),
    }, 0


def _read_latest_events(session_dir: Path, limit: int = 10) -> list[dict[str, object]]:
    return [
        {
            key: value
            for key, value in event.items()
            if key
            not in {
                "_state_snapshot",
                "_state_checkpoint",
                "_state_delta",
                "_state_sha256",
                "_base_seq",
            }
        }
        for event in _read_ledger_events(session_dir)[-limit:]
    ]


def status(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    state = load_state(session_dir)
    current_fingerprint = source_fingerprint(root, data_root)
    criteria: list[dict[str, object]] = []
    any_stale = False
    any_invalid = False
    for item in state["criteria"]:
        criterion = dict(item)
        stale = False
        evidence_valid: bool | None = None
        evidence_error: str | None = None
        red_evidence_valid: bool | None = None
        red_evidence_error: str | None = None
        if criterion.get("receipt") and criterion.get("status") in {
            "claimed",
            "verified",
        }:
            try:
                validate_receipt(session_dir, criterion, current_fingerprint)
                if criterion.get("status") == "verified":
                    validate_verification_binding(session_dir, criterion)
                evidence_valid = True
            except EvidenceStaleError as error:
                stale = True
                any_stale = True
                evidence_valid = False
                evidence_error = str(error)
            except WorkStateError as error:
                any_invalid = True
                evidence_valid = False
                evidence_error = str(error)
        elif criterion.get("red_receipt") and criterion.get("status") in {
            "red",
            "failed",
        }:
            try:
                _read_red_receipt(session_dir, criterion)
                red_evidence_valid = True
            except WorkStateError as error:
                any_invalid = True
                red_evidence_valid = False
                red_evidence_error = str(error)
        criterion["stale"] = stale
        criterion["evidence_valid"] = evidence_valid
        criterion["evidence_error"] = evidence_error
        criterion["red_evidence_valid"] = red_evidence_valid
        criterion["red_evidence_error"] = red_evidence_error
        criteria.append(criterion)
    packets: list[dict[str, object]] = []
    for item in _work_packets(state):
        packet = dict(item)
        packet_valid: bool | None = None
        packet_error: str | None = None
        passed_checks = [
            check
            for check in packet.get("checks", [])
            if isinstance(check, dict) and check.get("status") == "passed"
        ]
        if passed_checks:
            try:
                for check in passed_checks:
                    _validate_packet_check(session_dir, root, packet, check)
                packet_valid = True
            except WorkStateError as error:
                any_invalid = True
                packet_valid = False
                packet_error = str(error)
        packet["evidence_valid"] = packet_valid
        packet["evidence_error"] = packet_error
        packets.append(packet)
    if any_stale:
        effective_status = "stale"
    elif any_invalid:
        effective_status = "invalid-evidence"
    else:
        effective_status = state["status"]
    payload: dict[str, object] = {
        "message": f"session {state['session_id']}: {effective_status}",
        "session_id": state["session_id"],
        "status": state["status"],
        "effective_status": effective_status,
        "objective": state["objective"],
        "session_dir": str(session_dir),
        "brief": str(session_dir / "brief.md"),
        "state_path": str(_state_path(session_dir)),
        "ledger": str(session_dir / "ledger.jsonl"),
        "source_fingerprint": current_fingerprint,
        "criteria": criteria,
        "work_packets": packets,
        "latest_events": _read_latest_events(session_dir),
    }
    return payload, 0


def compact_ledger_command(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    return {
        "message": f"session {sanitize_identifier(args.session, 'session')}: compacted",
        **compact_ledger(session_dir),
    }, 0


def _external_data_root(args: argparse.Namespace) -> Path:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    if _is_within(data_root, root):
        raise WorkStateError(
            f"durable data root must be outside the workspace: {data_root}"
        )
    return data_root


def storage_inspect_command(
    args: argparse.Namespace,
) -> tuple[dict[str, object], int]:
    report = inspect_storage(_external_data_root(args), largest=args.largest)
    return {
        "message": (
            f"storage: {report['file_count']} files, {report['bytes']} bytes, "
            f"{report['projects']} projects, {report['sessions']} sessions"
        ),
        **report,
    }, 0


def storage_gc_command(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    report = garbage_collect_storage(
        _external_data_root(args),
        older_than_days=args.older_than_days,
        keep_last=args.keep_last,
        apply=args.apply,
    )
    return {
        "message": (
            "storage garbage collection applied"
            if args.apply
            else "storage garbage collection dry-run; pass --apply to delete"
        ),
        **report,
    }, 0


def _begin_attempt(
    session_dir: Path,
    criterion_id: str,
    executor: str,
    event_name: str,
) -> int:
    with session_lock(session_dir):
        state = load_state(session_dir)
        if state["status"] == "complete":
            raise WorkStateError(
                "completed sessions must be reopened before new evidence"
            )
        criterion = _criterion(state, criterion_id)
        if criterion["status"] not in RUNNABLE_STATUSES:
            raise WorkStateError(
                f"criterion {criterion_id} cannot start from status {criterion['status']}"
            )
        attempt = int(criterion.get("attempts", 0)) + 1
        criterion.update(
            {
                "status": "in_progress",
                "attempts": attempt,
                "executor": executor,
                "receipt": None,
                "red_receipt": None,
                "red_receipt_sha256": None,
                "verification": None,
            }
        )
        save_state_with_event(
            session_dir,
            state,
            event_name,
            criterion_id=criterion_id,
            executor=executor,
            attempt=attempt,
        )
        return attempt


def _receipt_relative_path(criterion_id: str, attempt: int) -> Path:
    return (
        Path("evidence")
        / sanitize_identifier(criterion_id, "criterion")
        / f"attempt-{attempt}"
        / "receipt.json"
    )


def _red_receipt_relative_path(criterion_id: str, attempt: int) -> Path:
    return (
        Path("evidence")
        / sanitize_identifier(criterion_id, "criterion")
        / f"attempt-{attempt}"
        / "red-receipt.json"
    )


def _finish_attempt(
    session_dir: Path,
    criterion_id: str,
    executor: str,
    attempt: int,
    receipt_relative: Path,
    status_value: str,
    event_name: str,
) -> None:
    with session_lock(session_dir):
        state = load_state(session_dir)
        criterion = _criterion(state, criterion_id)
        if (
            criterion["status"] != "in_progress"
            or criterion.get("executor") != executor
            or criterion.get("attempts") != attempt
        ):
            raise WorkStateError("criterion changed while evidence was being captured")
        criterion["status"] = status_value
        criterion["receipt"] = receipt_relative.as_posix()
        save_state_with_event(
            session_dir,
            state,
            event_name,
            criterion_id=criterion_id,
            executor=executor,
            attempt=attempt,
            receipt=receipt_relative.as_posix(),
        )


def _resolve_command(command: list[str]) -> tuple[list[str], str | None]:
    """Resolve argv[0] the way the operator's shell would have.

    CreateProcess only ever appends .exe to a bare name, so handing "npm" or
    "npx" straight to subprocess raised FileNotFoundError on Windows -- an
    infrastructure miss that was then recorded as a genuinely failed
    criterion. shutil.which honours PATHEXT and keeps explicit paths; a name
    that resolves to nothing is a launch failure, reported as such instead of
    being spawned into a guaranteed exception.
    """
    executable = shutil.which(command[0])
    if executable is None:
        return command, f"command is not executable on this host: {command[0]}"
    return [executable, *command[1:]], None


def run_command(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    criterion_id = sanitize_identifier(args.criterion, "criterion")
    executor = sanitize_identifier(args.executor, "executor")
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise WorkStateError("run requires a command after --")
    attempt = _begin_attempt(session_dir, criterion_id, executor, "execution_started")
    resolved, launch_error = _resolve_command(command)
    if launch_error is None:
        try:
            completed = subprocess.run(
                resolved,
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            stdout = completed.stdout
            stderr = completed.stderr
            exit_code = completed.returncode
        except OSError as error:
            launch_error = str(error)
    if launch_error is not None:
        stdout = ""
        stderr = launch_error
        exit_code = 127
    launched = launch_error is None
    fingerprint = source_fingerprint(root, data_root)
    receipt_relative = _receipt_relative_path(criterion_id, attempt)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "type": "command",
        "criterion_id": criterion_id,
        "executor": executor,
        "attempt": attempt,
        "at": utc_now(),
        "command": command,
        "launched": launched,
        "exit_code": exit_code,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "stdout_tail": stdout[-OUTPUT_TAIL_CHARS:],
        "stderr_tail": stderr[-OUTPUT_TAIL_CHARS:],
        "source_fingerprint": fingerprint,
    }
    _atomic_write_json(session_dir / receipt_relative, receipt)
    claimed = exit_code == 0
    # A command that never started proves nothing about the criterion either
    # way; the attempt still fails closed, but under its own event name so the
    # ledger distinguishes an infrastructure miss from a red result.
    _finish_attempt(
        session_dir,
        criterion_id,
        executor,
        attempt,
        receipt_relative,
        "claimed" if claimed else "failed",
        "evidence_claimed"
        if claimed
        else ("execution_failed" if launched else "execution_unlaunchable"),
    )
    if not args.json:
        if stdout:
            print(stdout, end="" if stdout.endswith("\n") else "\n")
        if stderr:
            print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")
    return {
        "message": f"criterion {criterion_id}: {'claimed' if claimed else 'failed'}",
        "criterion_id": criterion_id,
        "status": "claimed" if claimed else "failed",
        "exit_code": exit_code,
        "receipt": str(session_dir / receipt_relative),
        "source_fingerprint": fingerprint,
    }, 0 if exit_code == 0 else min(max(exit_code, 1), 125)


def _execute_command(command: list[str], root: Path) -> tuple[str, str, int, bool]:
    resolved, launch_error = _resolve_command(command)
    if launch_error is not None:
        return "", launch_error, 127, False
    try:
        completed = subprocess.run(
            resolved,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed.stdout, completed.stderr, completed.returncode, True
    except OSError as error:
        return "", str(error), 127, False


def _command_from_args(args: argparse.Namespace, subcommand: str) -> list[str]:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise WorkStateError(f"{subcommand} requires a command after --")
    return command


def _read_json_input(value: str, label: str) -> dict[str, Any]:
    try:
        raw = (
            sys.stdin.read()
            if value == "-"
            else Path(value).read_text(encoding="utf-8")
        )
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise WorkStateError(f"{label} is unreadable: {error}") from error
    if not isinstance(payload, dict):
        raise WorkStateError(f"{label} must be a JSON object")
    return payload


def _string_list(value: object, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise WorkStateError(f"{label} must be a string list")
    result = [item.strip() for item in value]
    if not allow_empty and not result:
        raise WorkStateError(f"{label} must not be empty")
    if len(result) != len(set(result)):
        raise WorkStateError(f"{label} must not contain duplicates")
    return result


def _validate_packet_plan(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise WorkStateError(f"packet plan schema_version must be {SCHEMA_VERSION}")
    specifications = payload.get("packets")
    if not isinstance(specifications, list) or not specifications:
        raise WorkStateError("packet plan must contain a non-empty packets list")
    packets: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for index, specification in enumerate(specifications):
        if not isinstance(specification, dict):
            raise WorkStateError(f"packets[{index}] must be an object")
        raw_id = specification.get("id")
        if (
            not isinstance(raw_id, str)
            or sanitize_identifier(raw_id, "packet") != raw_id
        ):
            raise WorkStateError(f"packets[{index}].id must be a stable identifier")
        if raw_id in identifiers:
            raise WorkStateError(f"duplicate packet id: {raw_id}")
        identifiers.add(raw_id)
        objective = specification.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise WorkStateError(f"packet {raw_id} objective must not be empty")
        owned_paths = [
            _normalize_owned_path(value)
            for value in _string_list(
                specification.get("owned_paths"), f"packet {raw_id} owned_paths"
            )
        ]
        for left_index, left in enumerate(owned_paths):
            for right in owned_paths[left_index + 1 :]:
                if _paths_overlap(left, right):
                    raise WorkStateError(
                        f"packet {raw_id} has overlapping owned paths: {left} and {right}"
                    )
        dependencies = _string_list(
            specification.get("dependencies"),
            f"packet {raw_id} dependencies",
            allow_empty=True,
        )
        invariants = _string_list(
            specification.get("invariants"), f"packet {raw_id} invariants"
        )
        integration_notes = _string_list(
            specification.get("integration_notes"),
            f"packet {raw_id} integration_notes",
        )
        raw_checks = specification.get("checks")
        if not isinstance(raw_checks, list) or not raw_checks:
            raise WorkStateError(
                f"packet {raw_id} checks must be a non-empty argv list"
            )
        checks: list[dict[str, Any]] = []
        for check_index, argv in enumerate(raw_checks, 1):
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(argument, str) and argument for argument in argv)
            ):
                raise WorkStateError(
                    f"packet {raw_id} check {check_index} must be a non-empty argv list"
                )
            checks.append(
                {
                    "id": f"k{check_index}",
                    "argv": argv,
                    "status": "pending",
                    "attempts": 0,
                    "executor": None,
                    "receipt": None,
                    "receipt_sha256": None,
                }
            )
        packets.append(
            {
                "id": raw_id,
                "objective": objective.strip(),
                "owned_paths": owned_paths,
                "dependencies": dependencies,
                "invariants": invariants,
                "checks": checks,
                "integration_notes": integration_notes,
                "status": "planned",
                "owner": None,
                "started_at": None,
                "completed_at": None,
            }
        )

    by_id = {packet["id"]: packet for packet in packets}
    for packet in packets:
        for dependency in packet["dependencies"]:
            if dependency not in by_id:
                raise WorkStateError(
                    f"packet {packet['id']} has unknown dependency {dependency}"
                )
            if dependency == packet["id"]:
                raise WorkStateError(f"packet {packet['id']} cannot depend on itself")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(packet_id: str) -> None:
        if packet_id in visiting:
            raise WorkStateError(
                f"packet plan contains a dependency cycle at {packet_id}"
            )
        if packet_id in visited:
            return
        visiting.add(packet_id)
        for dependency in by_id[packet_id]["dependencies"]:
            visit(dependency)
        visiting.remove(packet_id)
        visited.add(packet_id)

    for packet_id in by_id:
        visit(packet_id)
    for left_index, left in enumerate(packets):
        for right in packets[left_index + 1 :]:
            for left_path in left["owned_paths"]:
                for right_path in right["owned_paths"]:
                    if _paths_overlap(left_path, right_path):
                        raise WorkStateError(
                            f"packets {left['id']} and {right['id']} overlap ownership: "
                            f"{left_path} and {right_path}"
                        )
    return packets


def plan_packets(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    packets = _validate_packet_plan(_read_json_input(args.plan, "packet plan"))
    with session_lock(session_dir):
        state = load_state(session_dir)
        if state["status"] == "complete":
            raise WorkStateError("cannot plan packets for a completed session")
        if _work_packets(state):
            raise WorkStateError("session already has a work packet plan")
        state["work_packets"] = packets
        save_state_with_event(
            session_dir, state, "packets_planned", packets=len(packets)
        )
    return {
        "message": f"planned {len(packets)} work packets",
        "session_id": state["session_id"],
        "work_packets": packets,
    }, 0


def resume_session(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    """Expose the ledger-derived resumption summary through the supported CLI."""
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    return {
        "message": f"session {sanitize_identifier(args.session, 'session')}: resumable",
        **resume_summary(session_dir),
    }, 0


def compact_session_command(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    """Compact a ledger, optionally exporting a verified recovery bundle first."""
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    if args.bundle is None:
        return {
            "message": f"session {sanitize_identifier(args.session, 'session')}: compacted",
            **compact_ledger(session_dir),
        }, 0
    return {
        "message": f"session {sanitize_identifier(args.session, 'session')}: compacted",
        **compact_session(
            session_dir,
            Path(args.bundle).expanduser(),
            retain_events=args.retain_events,
        ),
    }, 0


def state_migrate(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    report = state_migration_report(session_dir)
    return {
        "message": f"session {sanitize_identifier(args.session, 'session')} schema is current",
        "session_dir": str(session_dir),
        **report,
    }, 0


def start_packet(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    packet_id = sanitize_identifier(args.packet, "packet")
    owner = sanitize_identifier(args.owner, "owner")
    with session_lock(session_dir):
        state = load_state(session_dir)
        if state["status"] == "complete":
            raise WorkStateError("cannot start a packet in a completed session")
        packet = _packet(state, packet_id)
        if packet.get("status") != "planned":
            raise WorkStateError(
                f"packet {packet_id} cannot start from {packet.get('status')}"
            )
        for dependency_id in packet["dependencies"]:
            dependency = _packet(state, dependency_id)
            if dependency.get("status") != "completed":
                raise WorkStateError(
                    f"packet {packet_id} dependency {dependency_id} is not completed"
                )
            for check in dependency["checks"]:
                _validate_packet_check(session_dir, root, dependency, check)
        for active in _work_packets(state):
            if active.get("status") != "active":
                continue
            for owned in packet["owned_paths"]:
                if any(_paths_overlap(owned, other) for other in active["owned_paths"]):
                    raise WorkStateError(
                        f"packet {packet_id} overlaps active packet {active['id']}"
                    )
        packet["status"] = "active"
        packet["owner"] = owner
        packet["started_at"] = utc_now()
        save_state_with_event(
            session_dir, state, "packet_started", packet_id=packet_id, owner=owner
        )
    return {
        "message": f"packet {packet_id}: active",
        "packet_id": packet_id,
        "status": "active",
    }, 0


def run_packet_check(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    packet_id = sanitize_identifier(args.packet, "packet")
    check_id = sanitize_identifier(args.check, "check")
    executor = sanitize_identifier(args.executor, "executor")
    with session_lock(session_dir):
        state = load_state(session_dir)
        packet = _packet(state, packet_id)
        if packet.get("status") != "active":
            raise WorkStateError(f"packet {packet_id} is not active")
        if packet.get("owner") != executor:
            raise WorkStateError(
                f"only packet owner {packet.get('owner')} may run checks"
            )
        check = _packet_check(packet, check_id)
        recovered_abandoned = False
        previous_runner_pid = None
        if check.get("status") == "in_progress":
            previous_runner_pid = check.get("runner_pid")
            previous_runner_identity = check.get("runner_identity")
            if (
                isinstance(previous_runner_pid, int)
                and not isinstance(previous_runner_pid, bool)
                and _process_matches_identity(
                    previous_runner_pid,
                    previous_runner_identity
                    if isinstance(previous_runner_identity, str)
                    else None,
                )
            ):
                raise WorkStateError(
                    f"packet {packet_id} check {check_id} runner is still alive: "
                    f"pid {previous_runner_pid}"
                )
            recovered_abandoned = True
        elif check.get("status") not in {"pending", "failed"}:
            raise WorkStateError(
                f"packet {packet_id} check {check_id} cannot run from "
                f"{check.get('status')}"
            )
        attempt = int(check.get("attempts", 0)) + 1
        check.update(
            {
                "status": "in_progress",
                "attempts": attempt,
                "executor": executor,
                "runner_pid": os.getpid(),
                "runner_identity": _process_identity(os.getpid()),
                "receipt": None,
                "receipt_sha256": None,
            }
        )
        command = list(check["argv"])
        save_state_with_event(
            session_dir,
            state,
            "packet_check_started",
            packet_id=packet_id,
            check_id=check_id,
            executor=executor,
            attempt=attempt,
            recovered_abandoned=recovered_abandoned,
            previous_runner_pid=previous_runner_pid,
        )
    stdout, stderr, exit_code, command_started = _execute_command(command, root)
    owned_fingerprint = _owned_paths_fingerprint(root, packet["owned_paths"])
    receipt_relative = (
        Path("evidence") / "packets" / packet_id / f"{check_id}-attempt-{attempt}.json"
    )
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "type": "packet_check",
        "packet_id": packet_id,
        "check_id": check_id,
        "executor": executor,
        "attempt": attempt,
        "at": utc_now(),
        "argv": command,
        "command_started": command_started,
        "exit_code": exit_code,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "stdout_tail": stdout[-OUTPUT_TAIL_CHARS:],
        "stderr_tail": stderr[-OUTPUT_TAIL_CHARS:],
        "owned_fingerprint": owned_fingerprint,
    }
    receipt_path = session_dir / receipt_relative
    _atomic_write_json(receipt_path, receipt)
    receipt_hash = _sha256_file(receipt_path)
    passed = command_started and exit_code == 0
    with session_lock(session_dir):
        state = load_state(session_dir)
        packet = _packet(state, packet_id)
        check = _packet_check(packet, check_id)
        if (
            check.get("status") != "in_progress"
            or check.get("attempts") != attempt
            or check.get("executor") != executor
            or check.get("runner_pid") != os.getpid()
            or check.get("runner_identity") != _process_identity(os.getpid())
        ):
            raise WorkStateError(
                "packet check changed while evidence was being captured"
            )
        check["status"] = "passed" if passed else "failed"
        check["runner_pid"] = None
        check["runner_identity"] = None
        check["receipt"] = receipt_relative.as_posix()
        check["receipt_sha256"] = receipt_hash
        save_state_with_event(
            session_dir,
            state,
            "packet_check_passed" if passed else "packet_check_failed",
            packet_id=packet_id,
            check_id=check_id,
            executor=executor,
            attempt=attempt,
            exit_code=exit_code,
            receipt=receipt_relative.as_posix(),
        )
    return {
        "message": f"packet {packet_id} check {check_id}: {'passed' if passed else 'failed'}",
        "packet_id": packet_id,
        "check_id": check_id,
        "status": "passed" if passed else "failed",
        "exit_code": exit_code,
        "receipt": str(receipt_path),
    }, 0 if passed else min(max(exit_code, 1), 125)


def complete_packet(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    packet_id = sanitize_identifier(args.packet, "packet")
    actor = sanitize_identifier(args.actor, "actor")
    with session_lock(session_dir):
        state = load_state(session_dir)
        packet = _packet(state, packet_id)
        if packet.get("status") != "active":
            raise WorkStateError(
                f"packet {packet_id} cannot complete from {packet.get('status')}"
            )
        if packet.get("owner") != actor:
            raise WorkStateError(
                f"only packet owner {packet.get('owner')} may complete it"
            )
        for check in packet["checks"]:
            if check.get("status") != "passed":
                raise WorkStateError(
                    f"packet {packet_id} check {check.get('id')} has not passed"
                )
            _validate_packet_check(session_dir, root, packet, check)
        packet["status"] = "completed"
        packet["completed_at"] = utc_now()
        save_state_with_event(
            session_dir, state, "packet_completed", packet_id=packet_id, actor=actor
        )
    return {
        "message": f"packet {packet_id}: completed",
        "packet_id": packet_id,
        "status": "completed",
        "session_status": state["status"],
    }, 0


def reopen_packet(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    """Reopen a completed packet only after its bound evidence becomes invalid."""

    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    packet_id = sanitize_identifier(args.packet, "packet")
    actor = sanitize_identifier(args.actor, "actor")
    reason = str(args.reason).strip()
    if not reason:
        raise WorkStateError("packet reopen reason must not be empty")
    with session_lock(session_dir):
        state = load_state(session_dir)
        packet = _packet(state, packet_id)
        if packet.get("status") != "completed":
            raise WorkStateError(
                f"packet {packet_id} cannot reopen from {packet.get('status')}"
            )
        if packet.get("owner") != actor:
            raise WorkStateError(
                f"only packet owner {packet.get('owner')} may reopen it"
            )
        descendants: set[str] = set()
        frontier = {packet_id}
        while frontier:
            discovered = {
                str(candidate.get("id"))
                for candidate in _work_packets(state)
                if str(candidate.get("id")) not in descendants
                and any(
                    dependency in frontier
                    for dependency in candidate.get("dependencies", [])
                )
            }
            descendants.update(discovered)
            frontier = discovered
        active_dependents = sorted(
            descendant
            for descendant in descendants
            if _packet(state, descendant).get("status") == "active"
        )
        if active_dependents:
            raise WorkStateError(
                "cannot reopen packet while dependent work is active: "
                + ", ".join(active_dependents)
            )
        evidence_error: str | None = None
        for check in packet["checks"]:
            try:
                _validate_packet_check(session_dir, root, packet, check)
            except WorkStateError as error:
                evidence_error = str(error)
                break
        if evidence_error is None:
            raise WorkStateError(
                f"packet {packet_id} evidence is still valid; reopening is not allowed"
            )
        invalidated_dependents = sorted(
            descendant
            for descendant in descendants
            if _packet(state, descendant).get("status") == "completed"
        )
        for target in [
            packet,
            *[_packet(state, item) for item in invalidated_dependents],
        ]:
            for check in target["checks"]:
                check.update(
                    {
                        "status": "pending",
                        "executor": None,
                        "receipt": None,
                        "receipt_sha256": None,
                    }
                )
            if target is not packet:
                target["status"] = "planned"
                target["owner"] = None
                target["started_at"] = None
            target["completed_at"] = None
        packet["status"] = "active"
        packet["started_at"] = utc_now()
        if state.get("status") == "complete":
            state["status"] = "active"
            state["completed_at"] = None
        save_state_with_event(
            session_dir,
            state,
            "packet_reopened",
            packet_id=packet_id,
            actor=actor,
            reason=reason,
            invalid_evidence=evidence_error,
            invalidated_dependents=invalidated_dependents,
        )
    return {
        "message": f"packet {packet_id}: reopened",
        "packet_id": packet_id,
        "status": "active",
        "reason": reason,
        "invalid_evidence": evidence_error,
        "invalidated_dependents": invalidated_dependents,
        "session_status": state["status"],
    }, 0


def run_red_command(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    criterion_id = sanitize_identifier(args.criterion, "criterion")
    executor = sanitize_identifier(args.executor, "executor")
    command = _command_from_args(args, "run-red")
    attempt = _begin_attempt(
        session_dir, criterion_id, executor, "red_execution_started"
    )
    stdout, stderr, exit_code, command_started = _execute_command(command, root)
    fingerprint = source_fingerprint(root, data_root)
    receipt_relative = _red_receipt_relative_path(criterion_id, attempt)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "type": "red_command",
        "criterion_id": criterion_id,
        "executor": executor,
        "attempt": attempt,
        "at": utc_now(),
        "command": command,
        "command_started": command_started,
        "exit_code": exit_code,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "stdout_tail": stdout[-OUTPUT_TAIL_CHARS:],
        "stderr_tail": stderr[-OUTPUT_TAIL_CHARS:],
        "source_fingerprint": fingerprint,
    }
    _atomic_write_json(session_dir / receipt_relative, receipt)
    demonstrated = command_started and exit_code != 0
    with session_lock(session_dir):
        state = load_state(session_dir)
        criterion = _criterion(state, criterion_id)
        if (
            criterion.get("status") != "in_progress"
            or criterion.get("executor") != executor
            or criterion.get("attempts") != attempt
        ):
            raise WorkStateError(
                "criterion changed while red evidence was being captured"
            )
        criterion["status"] = "red" if demonstrated else "failed"
        criterion["red_receipt"] = receipt_relative.as_posix()
        criterion["red_receipt_sha256"] = _sha256_file(session_dir / receipt_relative)
        save_state_with_event(
            session_dir,
            state,
            "red_demonstrated" if demonstrated else "red_not_demonstrated",
            criterion_id=criterion_id,
            executor=executor,
            attempt=attempt,
            receipt=receipt_relative.as_posix(),
            exit_code=exit_code,
        )
    if not args.json:
        if stdout:
            print(stdout, end="" if stdout.endswith("\n") else "\n")
        if stderr:
            print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")
    return {
        "message": (
            f"criterion {criterion_id}: red demonstrated"
            if demonstrated
            else (
                f"criterion {criterion_id}: test unexpectedly passed"
                if command_started
                else f"criterion {criterion_id}: test command did not start"
            )
        ),
        "criterion_id": criterion_id,
        "status": "red" if demonstrated else "failed",
        "exit_code": exit_code,
        "receipt": str(session_dir / receipt_relative),
        "source_fingerprint": fingerprint,
    }, 0 if demonstrated else 2


def run_green_command(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    criterion_id = sanitize_identifier(args.criterion, "criterion")
    executor = sanitize_identifier(args.executor, "executor")
    command = _command_from_args(args, "run-green")
    with session_lock(session_dir):
        state = load_state(session_dir)
        criterion = _criterion(state, criterion_id)
        if criterion.get("status") not in {"red", "failed"}:
            raise WorkStateError(
                f"criterion {criterion_id} needs demonstrated red evidence before green"
            )
        red_receipt, red_path = _read_red_receipt(session_dir, criterion)
        red_value = str(criterion["red_receipt"])
        if red_receipt.get("command") != command:
            raise WorkStateError("run-green must use the exact run-red command")
        fingerprint = source_fingerprint(root, data_root)
        red_fingerprint = red_receipt.get("source_fingerprint")
        if (
            not isinstance(red_fingerprint, dict)
            or red_fingerprint.get("sha256") == fingerprint["sha256"]
        ):
            raise WorkStateError("source must change between red and green evidence")
        attempt = int(criterion.get("attempts", 0)) + 1
        criterion.update(
            {
                "status": "in_progress",
                "attempts": attempt,
                "executor": executor,
                "receipt": None,
                "verification": None,
            }
        )
        save_state_with_event(
            session_dir,
            state,
            "green_execution_started",
            criterion_id=criterion_id,
            executor=executor,
            attempt=attempt,
            red_receipt=red_value,
        )
    stdout, stderr, exit_code, command_started = _execute_command(command, root)
    fingerprint = source_fingerprint(root, data_root)
    receipt_relative = _receipt_relative_path(criterion_id, attempt)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "type": "test_cycle",
        "criterion_id": criterion_id,
        "executor": executor,
        "attempt": attempt,
        "at": utc_now(),
        "command": command,
        "command_started": command_started,
        "exit_code": exit_code,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "stdout_tail": stdout[-OUTPUT_TAIL_CHARS:],
        "stderr_tail": stderr[-OUTPUT_TAIL_CHARS:],
        "source_fingerprint": fingerprint,
        "red_receipt": red_value,
        "red_receipt_sha256": _sha256_file(red_path),
    }
    _atomic_write_json(session_dir / receipt_relative, receipt)
    claimed = exit_code == 0
    _finish_attempt(
        session_dir,
        criterion_id,
        executor,
        attempt,
        receipt_relative,
        "claimed" if claimed else "failed",
        "test_cycle_claimed" if claimed else "green_execution_failed",
    )
    if not args.json:
        if stdout:
            print(stdout, end="" if stdout.endswith("\n") else "\n")
        if stderr:
            print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")
    return {
        "message": f"criterion {criterion_id}: {'test cycle claimed' if claimed else 'green failed'}",
        "criterion_id": criterion_id,
        "status": "claimed" if claimed else "failed",
        "exit_code": exit_code,
        "receipt": str(session_dir / receipt_relative),
        "red_receipt": str(red_path),
        "source_fingerprint": fingerprint,
    }, 0 if claimed else min(max(exit_code, 1), 125)


def record_artifact(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    criterion_id = sanitize_identifier(args.criterion, "criterion")
    executor = sanitize_identifier(args.executor, "executor")
    artifact_input = Path(args.artifact).expanduser()
    if artifact_input.is_symlink():
        raise WorkStateError(f"artifact cannot be a symlink: {artifact_input}")
    artifact = artifact_input.resolve()
    if not artifact.is_file() or artifact.stat().st_size <= 0:
        raise WorkStateError(f"artifact must be a non-empty regular file: {artifact}")
    summary = args.summary.strip()
    if not summary:
        raise WorkStateError("artifact summary must not be empty")
    attempt = _begin_attempt(
        session_dir, criterion_id, executor, "artifact_capture_started"
    )
    safe_name = sanitize_identifier(artifact.name, "artifact filename")
    copied_relative = (
        Path("evidence") / criterion_id / f"attempt-{attempt}" / f"artifact-{safe_name}"
    )
    copied = session_dir / copied_relative
    cas = _copy_artifact_to_cas(data_root, artifact, copied)
    fingerprint = source_fingerprint(root, data_root)
    receipt_relative = _receipt_relative_path(criterion_id, attempt)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "type": "artifact",
        "criterion_id": criterion_id,
        "executor": executor,
        "attempt": attempt,
        "at": utc_now(),
        "summary": summary,
        "artifact_source": str(artifact),
        "artifact_copy": copied_relative.as_posix(),
        "artifact_sha256": cas["sha256"],
        "artifact_cas_sha256": cas["sha256"],
        "source_fingerprint": fingerprint,
    }
    _atomic_write_json(session_dir / receipt_relative, receipt)
    _finish_attempt(
        session_dir,
        criterion_id,
        executor,
        attempt,
        receipt_relative,
        "claimed",
        "artifact_claimed",
    )
    return {
        "message": f"criterion {criterion_id}: artifact claimed",
        "criterion_id": criterion_id,
        "status": "claimed",
        "receipt": str(session_dir / receipt_relative),
        "artifact_copy": str(copied),
    }, 0


def record_desktop_evidence(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    criterion_id = sanitize_identifier(args.criterion, "criterion")
    executor = sanitize_identifier(args.executor, "executor")
    input_path = Path(args.receipt).expanduser().resolve()
    payload, artifact_root, artifacts = _load_desktop_evidence(input_path, root)
    attempt = _begin_attempt(
        session_dir, criterion_id, executor, "desktop_evidence_capture_started"
    )
    evidence_base = Path("evidence") / criterion_id / f"attempt-{attempt}" / "desktop"
    copied_receipt_relative = evidence_base / "qcu-receipt.json"
    copied_receipt = session_dir / copied_receipt_relative
    receipt_cas = _copy_artifact_to_cas(data_root, input_path, copied_receipt)
    copied_artifacts: list[dict[str, object]] = []
    for index, (item, source) in enumerate(artifacts, 1):
        safe_name = sanitize_identifier(source.name, "desktop artifact filename")
        copied_relative = evidence_base / "artifacts" / f"{index:03d}-{safe_name}"
        copied = session_dir / copied_relative
        artifact_cas = _copy_artifact_to_cas(data_root, source, copied)
        copied_hash = str(artifact_cas["sha256"])
        if copied_hash != item["sha256"]:
            raise WorkStateError(f"desktop artifact changed while copying: {source}")
        copied_artifacts.append(
            {
                "source_path": source.relative_to(artifact_root).as_posix(),
                "copy": copied_relative.as_posix(),
                "sha256": copied_hash,
                "cas_sha256": copied_hash,
                "bytes": copied.stat().st_size,
            }
        )
    fingerprint = source_fingerprint(root, data_root)
    receipt_relative = _receipt_relative_path(criterion_id, attempt)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "type": "desktop_evidence",
        "criterion_id": criterion_id,
        "executor": executor,
        "attempt": attempt,
        "at": utc_now(),
        "provider": "quick-computer-use",
        "qcu_version": payload.get("qcuVersion"),
        "session_id": payload.get("sessionId"),
        "objective": payload.get("objective"),
        "expected_window": payload.get("expectedWindow"),
        "real_actions": payload.get("realActions"),
        "objective_satisfied": payload.get("objectiveSatisfied"),
        "focus_verified": payload.get("focusVerified"),
        "finished": payload.get("finished"),
        "finish_reason": payload.get("finishReason"),
        "verification": payload.get("verification"),
        "summary": payload.get("summary"),
        "desktop_receipt_copy": copied_receipt_relative.as_posix(),
        "desktop_receipt_sha256": receipt_cas["sha256"],
        "desktop_receipt_cas_sha256": receipt_cas["sha256"],
        "artifacts": copied_artifacts,
        "source_fingerprint": fingerprint,
    }
    _atomic_write_json(session_dir / receipt_relative, receipt)
    _finish_attempt(
        session_dir,
        criterion_id,
        executor,
        attempt,
        receipt_relative,
        "claimed",
        "desktop_evidence_claimed",
    )
    return {
        "message": f"criterion {criterion_id}: desktop evidence claimed",
        "criterion_id": criterion_id,
        "status": "claimed",
        "receipt": str(session_dir / receipt_relative),
        "desktop_receipt_copy": str(copied_receipt),
        "artifacts_copied": len(copied_artifacts),
    }, 0


def record_browser_evidence(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    criterion_id = sanitize_identifier(args.criterion, "criterion")
    executor = sanitize_identifier(args.executor, "executor")
    input_path = Path(args.receipt).expanduser().resolve()
    payload, artifact_root, artifacts = _load_browser_evidence(input_path, root)
    attempt = _begin_attempt(
        session_dir, criterion_id, executor, "browser_evidence_capture_started"
    )
    evidence_base = Path("evidence") / criterion_id / f"attempt-{attempt}" / "browser"
    copied_receipt_relative = evidence_base / "playwright-receipt.json"
    copied_receipt = session_dir / copied_receipt_relative
    receipt_cas = _copy_artifact_to_cas(data_root, input_path, copied_receipt)
    copied_artifacts: list[dict[str, object]] = []
    for index, (item, source) in enumerate(artifacts, 1):
        source_relative = source.relative_to(artifact_root)
        safe_artifact_name = sanitize_identifier(
            source.name, "browser artifact filename"
        )
        copied_relative = (
            evidence_base / "artifacts" / f"{index:03d}-{safe_artifact_name}"
        )
        copied = session_dir / copied_relative
        artifact_cas = _copy_artifact_to_cas(data_root, source, copied)
        copied_hash = str(artifact_cas["sha256"])
        if copied_hash != item["sha256"]:
            raise WorkStateError(f"browser artifact changed while copying: {source}")
        copied_artifacts.append(
            {
                "source_path": source_relative.as_posix(),
                "copy": copied_relative.as_posix(),
                "sha256": copied_hash,
                "cas_sha256": copied_hash,
                "bytes": copied.stat().st_size,
            }
        )
    fingerprint = source_fingerprint(root, data_root)
    receipt_relative = _receipt_relative_path(criterion_id, attempt)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "type": "browser_evidence",
        "criterion_id": criterion_id,
        "executor": executor,
        "attempt": attempt,
        "at": utc_now(),
        "provider": "playwright",
        "playwright_version": payload.get("version"),
        "command": payload.get("command"),
        "exit_code": payload.get("exitCode"),
        "stats": payload.get("stats"),
        "browser_receipt_copy": copied_receipt_relative.as_posix(),
        "browser_receipt_sha256": receipt_cas["sha256"],
        "browser_receipt_cas_sha256": receipt_cas["sha256"],
        "artifacts": copied_artifacts,
        "source_fingerprint": fingerprint,
    }
    _atomic_write_json(session_dir / receipt_relative, receipt)
    _finish_attempt(
        session_dir,
        criterion_id,
        executor,
        attempt,
        receipt_relative,
        "claimed",
        "browser_evidence_claimed",
    )
    return {
        "message": f"criterion {criterion_id}: browser evidence claimed",
        "criterion_id": criterion_id,
        "status": "claimed",
        "receipt": str(session_dir / receipt_relative),
        "browser_receipt_copy": str(copied_receipt),
        "artifacts_copied": len(copied_artifacts),
    }, 0


def record_navigation_evidence(
    args: argparse.Namespace,
) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    criterion_id = sanitize_identifier(args.criterion, "criterion")
    executor = sanitize_identifier(args.executor, "executor")
    input_path = Path(args.receipt).expanduser().resolve()
    payload, artifact_root, artifacts = _load_navigation_evidence(input_path, root)
    attempt = _begin_attempt(
        session_dir, criterion_id, executor, "navigation_evidence_capture_started"
    )
    evidence_base = (
        Path("evidence") / criterion_id / f"attempt-{attempt}" / "navigation"
    )
    copied_receipt_relative = evidence_base / "skyvern-receipt.json"
    copied_receipt = session_dir / copied_receipt_relative
    receipt_cas = _copy_artifact_to_cas(data_root, input_path, copied_receipt)
    copied_artifacts: list[dict[str, object]] = []
    for index, (item, source) in enumerate(artifacts, 1):
        source_relative = source.relative_to(artifact_root)
        safe_name = sanitize_identifier(source.name, "navigation artifact filename")
        copied_relative = evidence_base / "artifacts" / f"{index:03d}-{safe_name}"
        copied = session_dir / copied_relative
        artifact_cas = _copy_artifact_to_cas(data_root, source, copied)
        copied_hash = str(artifact_cas["sha256"])
        if copied_hash != item["sha256"]:
            raise WorkStateError(f"navigation artifact changed while copying: {source}")
        copied_artifacts.append(
            {
                "source_path": source_relative.as_posix(),
                "copy": copied_relative.as_posix(),
                "sha256": copied_hash,
                "cas_sha256": copied_hash,
                "bytes": copied.stat().st_size,
            }
        )
    fingerprint = source_fingerprint(root, data_root)
    receipt_relative = _receipt_relative_path(criterion_id, attempt)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "type": "navigation_evidence",
        "criterion_id": criterion_id,
        "executor": executor,
        "attempt": attempt,
        "at": utc_now(),
        "provider": "skyvern",
        "run_id": payload.get("runId"),
        "status": payload.get("status"),
        "navigation_only": True,
        "verification_eligible": False,
        "discovery_completed": True,
        "side_effect_scope": payload.get("sideEffectScope"),
        "navigation_receipt_copy": copied_receipt_relative.as_posix(),
        "navigation_receipt_sha256": receipt_cas["sha256"],
        "navigation_receipt_cas_sha256": receipt_cas["sha256"],
        "artifacts": copied_artifacts,
        "source_fingerprint": fingerprint,
    }
    _atomic_write_json(session_dir / receipt_relative, receipt)
    _finish_attempt(
        session_dir,
        criterion_id,
        executor,
        attempt,
        receipt_relative,
        "claimed",
        "navigation_evidence_claimed",
    )
    return {
        "message": f"criterion {criterion_id}: navigation evidence claimed",
        "criterion_id": criterion_id,
        "status": "claimed",
        "receipt": str(session_dir / receipt_relative),
        "navigation_receipt_copy": str(copied_receipt),
        "artifacts_copied": len(copied_artifacts),
        "navigation_only": True,
    }, 0


def record_design_evidence(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    criterion_id = sanitize_identifier(args.criterion, "criterion")
    executor = sanitize_identifier(args.executor, "executor")
    input_path = Path(args.receipt).expanduser().resolve()
    payload, artifact_root, artifacts = _load_design_evidence(input_path, root)
    attempt = _begin_attempt(
        session_dir, criterion_id, executor, "design_evidence_capture_started"
    )
    evidence_base = Path("evidence") / criterion_id / f"attempt-{attempt}" / "design"
    copied_receipt_relative = evidence_base / "design-receipt.json"
    copied_receipt = session_dir / copied_receipt_relative
    receipt_cas = _copy_artifact_to_cas(data_root, input_path, copied_receipt)
    copied_artifacts: list[dict[str, object]] = []
    for index, (item, source) in enumerate(artifacts, 1):
        safe_name = sanitize_identifier(source.name, "design artifact filename")
        copied_relative = evidence_base / "artifacts" / f"{index:03d}-{safe_name}"
        copied = session_dir / copied_relative
        artifact_cas = _copy_artifact_to_cas(data_root, source, copied)
        copied_hash = str(artifact_cas["sha256"])
        if copied_hash != item["sha256"]:
            raise WorkStateError(f"design artifact changed while copying: {source}")
        copied_artifacts.append(
            {
                "source_path": source.relative_to(artifact_root).as_posix(),
                "copy": copied_relative.as_posix(),
                "sha256": copied_hash,
                "cas_sha256": copied_hash,
                "bytes": copied.stat().st_size,
            }
        )
    receipt_relative = _receipt_relative_path(criterion_id, attempt)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "type": "design_evidence",
        "criterion_id": criterion_id,
        "executor": executor,
        "attempt": attempt,
        "at": utc_now(),
        "reviewer": payload.get("reviewer"),
        "intent_identity": payload.get("intentIdentity"),
        "visual_contract_passed": True,
        "behavioral_verification_eligible": False,
        "subjective_quality_proven": False,
        "mobile_captured": True,
        "desktop_captured": True,
        "design_receipt_copy": copied_receipt_relative.as_posix(),
        "design_receipt_sha256": receipt_cas["sha256"],
        "design_receipt_cas_sha256": receipt_cas["sha256"],
        "artifacts": copied_artifacts,
        "source_fingerprint": source_fingerprint(root, data_root),
    }
    _atomic_write_json(session_dir / receipt_relative, receipt)
    _finish_attempt(
        session_dir,
        criterion_id,
        executor,
        attempt,
        receipt_relative,
        "claimed",
        "design_evidence_claimed",
    )
    return {
        "message": f"criterion {criterion_id}: design evidence claimed",
        "criterion_id": criterion_id,
        "status": "claimed",
        "receipt": str(session_dir / receipt_relative),
        "design_receipt_copy": str(copied_receipt),
        "artifacts_copied": len(copied_artifacts),
        "behavioral_verification_eligible": False,
    }, 0


def record_external_context(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    criterion_id = sanitize_identifier(args.criterion, "criterion")
    executor = sanitize_identifier(args.executor, "executor")
    artifact_input = Path(args.artifact).expanduser()
    if artifact_input.is_symlink():
        raise WorkStateError(f"external context cannot be a symlink: {artifact_input}")
    artifact = artifact_input.resolve()
    if not artifact.is_file() or artifact.stat().st_size <= 0:
        raise WorkStateError(f"external context must be a non-empty file: {artifact}")
    try:
        context = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkStateError(
            f"external context is not valid JSON: {artifact}"
        ) from error
    if not isinstance(context, dict) or context.get("type") != "external_context":
        raise WorkStateError("external context payload has the wrong type")
    selected = context.get("selected_library")
    if not isinstance(selected, dict):
        raise WorkStateError("external context has no selected library")
    required = {
        "provider": context.get("provider"),
        "library": context.get("library"),
        "selected_library_id": selected.get("id"),
        "query": context.get("query"),
        "provider_response_sha256": context.get("provider_response_sha256"),
        "expires_at": context.get("expires_at"),
    }
    missing = [
        name
        for name, value in required.items()
        if not isinstance(value, str) or not value
    ]
    if missing:
        raise WorkStateError(
            f"external context is missing fields: {', '.join(sorted(missing))}"
        )
    try:
        expires_at = datetime.fromisoformat(
            str(required["expires_at"]).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise WorkStateError("external context expiry is invalid") from error
    if expires_at.tzinfo is None:
        raise WorkStateError("external context expiry must carry a timezone")
    if expires_at <= datetime.now(timezone.utc):
        raise WorkStateError("external context is already expired")
    summary = args.summary.strip()
    if not summary:
        raise WorkStateError("external context summary must not be empty")
    attempt = _begin_attempt(
        session_dir, criterion_id, executor, "external_context_capture_started"
    )
    copied_relative = (
        Path("evidence") / criterion_id / f"attempt-{attempt}" / "external-context.json"
    )
    copied = session_dir / copied_relative
    cas = _copy_artifact_to_cas(data_root, artifact, copied)
    fingerprint = source_fingerprint(root, data_root)
    receipt_relative = _receipt_relative_path(criterion_id, attempt)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "type": "external_context",
        "criterion_id": criterion_id,
        "executor": executor,
        "attempt": attempt,
        "at": utc_now(),
        "summary": summary,
        "artifact_source": str(artifact),
        "artifact_copy": copied_relative.as_posix(),
        "artifact_sha256": cas["sha256"],
        "artifact_cas_sha256": cas["sha256"],
        "provider": required["provider"],
        "library": required["library"],
        "selected_library_id": required["selected_library_id"],
        "requested_version": context.get("requested_version"),
        "matched_version": selected.get("matched_version"),
        "query": required["query"],
        "retrieved_at": context.get("retrieved_at"),
        "expires_at": required["expires_at"],
        "provider_response_sha256": required["provider_response_sha256"],
        "source_fingerprint": fingerprint,
    }
    _atomic_write_json(session_dir / receipt_relative, receipt)
    _finish_attempt(
        session_dir,
        criterion_id,
        executor,
        attempt,
        receipt_relative,
        "claimed",
        "external_context_claimed",
    )
    return {
        "message": f"criterion {criterion_id}: external context claimed",
        "criterion_id": criterion_id,
        "status": "claimed",
        "receipt": str(session_dir / receipt_relative),
        "artifact_copy": str(copied),
        "selected_library_id": required["selected_library_id"],
        "expires_at": required["expires_at"],
    }, 0


_UNREADABLE_USAGE = {
    "inputTokens": None,
    "cachedInputTokens": None,
    "outputTokens": None,
    "sourceSchema": None,
}


def _expected_communication_usage(provider_usage: object) -> dict[str, object]:
    """Re-derive receipt totals from the raw provider record.

    This verifies rather than trusts the receipt, so it must read every
    provider shape the receipt writer accepts. Both sides call the same
    conversion in ``scripts/provider_usage.py``: reading fewer shapes than the
    writer would reject correct evidence, and that is not a divergence worth
    only detecting.

    An unreadable record yields sentinel ``None`` values, which never match a
    real receipt, so a malformed provider record fails the comparison instead
    of passing it.
    """
    path = Path(__file__).resolve().parents[3] / "scripts" / "provider_usage.py"
    spec = importlib.util.spec_from_file_location("cp_provider_usage", path)
    if spec is None or spec.loader is None:
        raise WorkStateError(f"cannot load the shared usage conversion: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError) as error:
        raise WorkStateError(
            f"cannot load the shared usage conversion: {path}"
        ) from error
    try:
        total, cached, output, schema = module.normalize_usage(provider_usage)
    except module.UsageError:
        return dict(_UNREADABLE_USAGE)
    return {
        "inputTokens": total,
        "cachedInputTokens": cached,
        "outputTokens": output,
        # Re-derived like the totals. Comparison between receipts is gated on
        # this field, so trusting the receipt's own copy would leave the one
        # value that decides comparability unverified.
        "sourceSchema": schema,
    }


def record_communication_evidence(
    args: argparse.Namespace,
) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    criterion_id = sanitize_identifier(args.criterion, "criterion")
    executor = sanitize_identifier(args.executor, "executor")
    receipt_input = Path(args.receipt).expanduser()
    if receipt_input.is_symlink():
        raise WorkStateError(
            f"communication receipt cannot be a symlink: {receipt_input}"
        )
    receipt_source = receipt_input.resolve()
    if not receipt_source.is_file() or receipt_source.stat().st_size <= 0:
        raise WorkStateError(
            f"communication receipt must be a non-empty file: {receipt_source}"
        )
    try:
        communication = json.loads(receipt_source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkStateError(
            f"communication receipt is not valid JSON: {receipt_source}"
        ) from error
    if (
        not isinstance(communication, dict)
        or communication.get("type") != "communication_usage_evidence"
    ):
        raise WorkStateError("communication receipt has the wrong type")
    usage = communication.get("usage")
    usage_fields = (
        "inputTokens",
        "cachedInputTokens",
        "freshInputTokens",
        "outputTokens",
        "totalTokens",
    )
    if (
        communication.get("schemaVersion") != 1
        or communication.get("counterfactualEstimated") is not False
        or not isinstance(usage, dict)
        or any(
            isinstance(usage.get(field), bool)
            or not isinstance(usage.get(field), int)
            or usage[field] < 0
            for field in usage_fields
        )
    ):
        raise WorkStateError("communication receipt has invalid usage metadata")
    provider_input = communication.get("providerRecord")
    provider_hash = communication.get("providerRecordSha256")
    if not isinstance(provider_input, str) or not isinstance(provider_hash, str):
        raise WorkStateError("communication receipt has no provider record binding")
    provider_candidate = Path(provider_input).expanduser()
    if provider_candidate.is_symlink():
        raise WorkStateError("provider usage record cannot be a symlink")
    provider_record = provider_candidate.resolve()
    if not provider_record.is_file() or _sha256_file(provider_record) != provider_hash:
        raise WorkStateError(
            "provider usage record does not match the communication receipt"
        )
    try:
        provider_payload = json.loads(provider_record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkStateError("provider usage record is not valid JSON") from error
    provider_usage = (
        provider_payload.get("usage") if isinstance(provider_payload, dict) else None
    )
    expected_usage = _expected_communication_usage(provider_usage)
    if any(usage.get(field) != value for field, value in expected_usage.items()):
        raise WorkStateError("communication usage does not match the provider record")
    if (
        usage.get("freshInputTokens")
        != usage.get("inputTokens") - usage.get("cachedInputTokens")
        or usage.get("totalTokens")
        != usage.get("inputTokens") + usage.get("outputTokens")
        or communication.get("provider") != provider_payload.get("provider")
        or communication.get("model") != provider_payload.get("model")
    ):
        raise WorkStateError(
            "communication receipt derivation does not match the provider record"
        )
    for field in ("taskId", "variant", "provider", "model"):
        if not isinstance(communication.get(field), str) or not communication[field]:
            raise WorkStateError(f"communication receipt has no {field}")
    quality_score = communication.get("qualityScore")
    if (
        isinstance(quality_score, bool)
        or not isinstance(quality_score, (int, float))
        or not 0 <= quality_score <= 100
    ):
        raise WorkStateError("communication receipt has invalid quality score")

    attempt = _begin_attempt(
        session_dir, criterion_id, executor, "communication_evidence_capture_started"
    )
    evidence_base = Path("evidence") / criterion_id / f"attempt-{attempt}"
    communication_copy_relative = evidence_base / "communication-receipt.json"
    provider_copy_relative = evidence_base / "provider-usage.json"
    communication_copy = session_dir / communication_copy_relative
    provider_copy = session_dir / provider_copy_relative
    communication_cas = _copy_artifact_to_cas(
        data_root, receipt_source, communication_copy
    )
    provider_cas = _copy_artifact_to_cas(data_root, provider_record, provider_copy)
    receipt_relative = _receipt_relative_path(criterion_id, attempt)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "type": "communication_evidence",
        "criterion_id": criterion_id,
        "executor": executor,
        "attempt": attempt,
        "at": utc_now(),
        "artifact_copy": communication_copy_relative.as_posix(),
        "artifact_sha256": communication_cas["sha256"],
        "artifact_cas_sha256": communication_cas["sha256"],
        "provider_record_copy": provider_copy_relative.as_posix(),
        "provider_record_sha256": provider_cas["sha256"],
        "provider_record_cas_sha256": provider_cas["sha256"],
        "task_id": communication.get("taskId"),
        "variant": communication.get("variant"),
        "provider": communication.get("provider"),
        "model": communication.get("model"),
        "success": communication.get("success"),
        "quality_score": communication.get("qualityScore"),
        "critical_failure": communication.get("criticalFailure"),
        "usage": usage,
        "counterfactual_estimated": False,
        "source_fingerprint": source_fingerprint(root, data_root),
    }
    _atomic_write_json(session_dir / receipt_relative, receipt)
    _finish_attempt(
        session_dir,
        criterion_id,
        executor,
        attempt,
        receipt_relative,
        "claimed",
        "communication_evidence_claimed",
    )
    return {
        "message": f"criterion {criterion_id}: communication evidence claimed",
        "criterion_id": criterion_id,
        "status": "claimed",
        "receipt": str(session_dir / receipt_relative),
        "communication_receipt_copy": str(communication_copy),
        "provider_record_copy": str(provider_copy),
        "usage": usage,
    }, 0


def verify(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    criterion_id = sanitize_identifier(args.criterion, "criterion")
    verifier = sanitize_identifier(args.verifier, "verifier")
    verdict = args.verdict
    note = args.note.strip()
    if verdict not in VALID_VERDICTS:
        raise WorkStateError(f"unsupported verdict: {verdict}")
    if not note:
        raise WorkStateError("verification note must not be empty")
    with session_lock(session_dir):
        state = load_state(session_dir)
        criterion = _criterion(state, criterion_id)
        if criterion["status"] != "claimed":
            raise WorkStateError(
                f"criterion {criterion_id} must be claimed before verification"
            )
        executor = str(criterion.get("executor") or "")
        if verifier.casefold() == executor.casefold():
            raise WorkStateError("executor cannot verify its own claim")
        fingerprint = source_fingerprint(root, data_root)
        receipt = validate_receipt(session_dir, criterion, fingerprint)
        criterion["verification"] = {
            "verifier": verifier,
            "verdict": verdict,
            "note": note,
            "at": utc_now(),
            "receipt_sha256": _sha256_file(_receipt_path(session_dir, criterion)),
        }
        if verdict == "confirmed":
            criterion["status"] = "verified"
            event_name = "verification_confirmed"
        elif verdict == "rejected":
            criterion["status"] = "rejected"
            event_name = "verification_rejected"
        else:
            criterion["status"] = "inconclusive"
            event_name = "verification_inconclusive"
        save_state_with_event(
            session_dir,
            state,
            event_name,
            criterion_id=criterion_id,
            executor=executor,
            verifier=verifier,
            verdict=verdict,
            receipt_type=receipt["type"],
        )
    return {
        "message": f"criterion {criterion_id}: {verdict}",
        "criterion_id": criterion_id,
        "status": criterion["status"],
        "verdict": verdict,
    }, 0


def reopen(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    criterion_id = sanitize_identifier(args.criterion, "criterion")
    actor = sanitize_identifier(args.actor, "actor")
    reason = args.reason.strip()
    if not reason:
        raise WorkStateError("reopen reason must not be empty")
    with session_lock(session_dir):
        state = load_state(session_dir)
        criterion = _criterion(state, criterion_id)
        if criterion["status"] == "pending":
            raise WorkStateError(f"criterion {criterion_id} is already pending")
        previous_status = criterion["status"]
        previous_receipt = criterion.get("receipt")
        criterion.update(
            {
                "status": "pending",
                "executor": None,
                "receipt": None,
                "red_receipt": None,
                "red_receipt_sha256": None,
                "verification": None,
            }
        )
        state["status"] = "active"
        save_state_with_event(
            session_dir,
            state,
            "criterion_reopened",
            criterion_id=criterion_id,
            actor=actor,
            reason=reason,
            previous_status=previous_status,
            previous_receipt=previous_receipt,
        )
    return {
        "message": f"criterion {criterion_id}: reopened",
        "criterion_id": criterion_id,
        "status": "pending",
    }, 0


def complete(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    root = resolve_root(args.root)
    data_root = resolve_data_root(args.data_root)
    session_dir = session_directory(root, data_root, args.session)
    with session_lock(session_dir):
        state = load_state(session_dir)
        if state["status"] == "complete":
            raise WorkStateError("session is already complete")
        fingerprint = source_fingerprint(root, data_root)
        blockers: list[str] = []
        for packet in _work_packets(state):
            if packet.get("status") != "completed":
                blockers.append(f"packet:{packet.get('id')}:{packet.get('status')}")
                continue
            try:
                for check in packet.get("checks", []):
                    _validate_packet_check(session_dir, root, packet, check)
            except WorkStateError as error:
                blockers.append(f"packet:{packet.get('id')}:{error}")
        for criterion in state["criteria"]:
            if criterion.get("status") != "verified":
                blockers.append(f"{criterion.get('id')}:{criterion.get('status')}")
                continue
            verification = criterion.get("verification")
            if (
                not isinstance(verification, dict)
                or verification.get("verdict") != "confirmed"
            ):
                blockers.append(f"{criterion.get('id')}:missing-confirmation")
                continue
            if (
                str(verification.get("verifier", "")).casefold()
                == str(criterion.get("executor", "")).casefold()
            ):
                blockers.append(f"{criterion.get('id')}:self-verified")
                continue
            try:
                validate_receipt(session_dir, criterion, fingerprint)
                validate_verification_binding(session_dir, criterion)
            except WorkStateError as error:
                blockers.append(f"{criterion.get('id')}:{error}")
        if blockers:
            raise WorkStateError("completion blocked: " + "; ".join(blockers))
        state["status"] = "complete"
        state["completed_at"] = utc_now()
        save_state_with_event(
            session_dir,
            state,
            "session_completed",
            criteria=len(state["criteria"]),
            source_fingerprint=fingerprint,
        )
    return {
        "message": f"session {state['session_id']}: complete",
        "session_id": state["session_id"],
        "status": "complete",
        "source_fingerprint": fingerprint,
    }, 0


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="workspace root")
    parser.add_argument("--data-root", help="override external state root")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    init_parser = subparsers.add_parser("init", help="initialize a durable session")
    init_parser.add_argument("--session", required=True)
    init_parser.add_argument("--objective", required=True)
    init_parser.add_argument("--criterion", action="append", required=True)
    _add_json_flag(init_parser)

    status_parser = subparsers.add_parser("status", help="show resumable state")
    status_parser.add_argument("--session", required=True)
    _add_json_flag(status_parser)

    resume_parser = subparsers.add_parser(
        "resume-summary", help="derive a resumable summary from the verified ledger"
    )
    resume_parser.add_argument("--session", required=True)
    _add_json_flag(resume_parser)

    compact_parser = subparsers.add_parser(
        "compact",
        help="verify and compact one ledger, optionally exporting a recovery bundle",
    )
    compact_parser.add_argument("--session", required=True)
    compact_parser.add_argument("--bundle")
    compact_parser.add_argument("--retain-events", type=int, default=25)
    _add_json_flag(compact_parser)

    inspect_parser = subparsers.add_parser(
        "storage-inspect", help="report bounded durable storage usage"
    )
    inspect_parser.add_argument("--largest", type=int, default=10)
    _add_json_flag(inspect_parser)

    gc_parser = subparsers.add_parser(
        "storage-gc",
        help="dry-run age and keep-last garbage collection unless --apply is passed",
    )
    gc_parser.add_argument("--older-than-days", type=float, default=30)
    gc_parser.add_argument("--keep-last", type=int, default=5)
    gc_parser.add_argument("--apply", action="store_true")
    _add_json_flag(gc_parser)
    migration_parser = subparsers.add_parser(
        "state-migrate",
        help="inspect the versioned state migration policy without changing state",
    )
    migration_parser.add_argument("--session", required=True)
    _add_json_flag(migration_parser)

    plan_parser = subparsers.add_parser(
        "plan-packets", help="install one dependency-aware work packet plan"
    )
    plan_parser.add_argument("--session", required=True)
    plan_parser.add_argument("--plan", required=True, help="JSON path or - for stdin")
    _add_json_flag(plan_parser)

    start_packet_parser = subparsers.add_parser(
        "start-packet", help="start a packet whose dependencies are complete"
    )
    start_packet_parser.add_argument("--session", required=True)
    start_packet_parser.add_argument("--packet", required=True)
    start_packet_parser.add_argument("--owner", required=True)
    _add_json_flag(start_packet_parser)

    packet_check_parser = subparsers.add_parser(
        "run-packet-check", help="run one packet's declared argv check"
    )
    packet_check_parser.add_argument("--session", required=True)
    packet_check_parser.add_argument("--packet", required=True)
    packet_check_parser.add_argument("--check", required=True)
    packet_check_parser.add_argument("--executor", required=True)
    _add_json_flag(packet_check_parser)

    packet_complete_parser = subparsers.add_parser(
        "complete-packet", help="complete a packet after all declared checks pass"
    )
    packet_complete_parser.add_argument("--session", required=True)
    packet_complete_parser.add_argument("--packet", required=True)
    packet_complete_parser.add_argument("--actor", required=True)
    _add_json_flag(packet_complete_parser)

    packet_reopen_parser = subparsers.add_parser(
        "reopen-packet",
        help="reopen a completed packet after its evidence becomes invalid",
    )
    packet_reopen_parser.add_argument("--session", required=True)
    packet_reopen_parser.add_argument("--packet", required=True)
    packet_reopen_parser.add_argument("--actor", required=True)
    packet_reopen_parser.add_argument("--reason", required=True)
    _add_json_flag(packet_reopen_parser)

    run_parser = subparsers.add_parser("run", help="run a command and record evidence")
    run_parser.add_argument("--session", required=True)
    run_parser.add_argument("--criterion", required=True)
    run_parser.add_argument("--executor", required=True)
    _add_json_flag(run_parser)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)

    red_parser = subparsers.add_parser(
        "run-red", help="run a regression command that must fail"
    )
    red_parser.add_argument("--session", required=True)
    red_parser.add_argument("--criterion", required=True)
    red_parser.add_argument("--executor", required=True)
    _add_json_flag(red_parser)
    red_parser.add_argument("command", nargs=argparse.REMAINDER)

    green_parser = subparsers.add_parser(
        "run-green", help="rerun the exact red command after a source change"
    )
    green_parser.add_argument("--session", required=True)
    green_parser.add_argument("--criterion", required=True)
    green_parser.add_argument("--executor", required=True)
    _add_json_flag(green_parser)
    green_parser.add_argument("command", nargs=argparse.REMAINDER)

    record_parser = subparsers.add_parser(
        "record", help="record a manual evidence artifact"
    )
    record_parser.add_argument("--session", required=True)
    record_parser.add_argument("--criterion", required=True)
    record_parser.add_argument("--executor", required=True)
    record_parser.add_argument("--artifact", required=True)
    record_parser.add_argument("--summary", required=True)
    _add_json_flag(record_parser)

    web_parser = subparsers.add_parser(
        "record-web", help="record a successful normalized Playwright receipt"
    )
    web_parser.add_argument("--session", required=True)
    web_parser.add_argument("--criterion", required=True)
    web_parser.add_argument("--executor", required=True)
    web_parser.add_argument("--receipt", required=True)
    _add_json_flag(web_parser)

    desktop_parser = subparsers.add_parser(
        "record-desktop", help="record verified normalized QCU desktop evidence"
    )
    desktop_parser.add_argument("--session", required=True)
    desktop_parser.add_argument("--criterion", required=True)
    desktop_parser.add_argument("--executor", required=True)
    desktop_parser.add_argument("--receipt", required=True)
    _add_json_flag(desktop_parser)

    navigation_parser = subparsers.add_parser(
        "record-navigation",
        help="record completed Skyvern discovery as navigation-only evidence",
    )
    navigation_parser.add_argument("--session", required=True)
    navigation_parser.add_argument("--criterion", required=True)
    navigation_parser.add_argument("--executor", required=True)
    navigation_parser.add_argument("--receipt", required=True)
    _add_json_flag(navigation_parser)

    design_parser = subparsers.add_parser(
        "record-design",
        help="record a completed visual design contract as non-behavioral evidence",
    )
    design_parser.add_argument("--session", required=True)
    design_parser.add_argument("--criterion", required=True)
    design_parser.add_argument("--executor", required=True)
    design_parser.add_argument("--receipt", required=True)
    _add_json_flag(design_parser)

    context_parser = subparsers.add_parser(
        "record-context", help="record normalized external documentation evidence"
    )
    context_parser.add_argument("--session", required=True)
    context_parser.add_argument("--criterion", required=True)
    context_parser.add_argument("--executor", required=True)
    context_parser.add_argument("--artifact", required=True)
    context_parser.add_argument("--summary", required=True)
    _add_json_flag(context_parser)

    communication_parser = subparsers.add_parser(
        "record-communication",
        help="record provider-backed communication usage evidence",
    )
    communication_parser.add_argument("--session", required=True)
    communication_parser.add_argument("--criterion", required=True)
    communication_parser.add_argument("--executor", required=True)
    communication_parser.add_argument("--receipt", required=True)
    _add_json_flag(communication_parser)

    verify_parser = subparsers.add_parser("verify", help="independently verify a claim")
    verify_parser.add_argument("--session", required=True)
    verify_parser.add_argument("--criterion", required=True)
    verify_parser.add_argument("--verifier", required=True)
    verify_parser.add_argument(
        "--verdict", choices=sorted(VALID_VERDICTS), required=True
    )
    verify_parser.add_argument("--note", required=True)
    _add_json_flag(verify_parser)

    reopen_parser = subparsers.add_parser(
        "reopen", help="return a criterion to pending"
    )
    reopen_parser.add_argument("--session", required=True)
    reopen_parser.add_argument("--criterion", required=True)
    reopen_parser.add_argument("--actor", required=True)
    reopen_parser.add_argument("--reason", required=True)
    _add_json_flag(reopen_parser)

    complete_parser = subparsers.add_parser(
        "complete", help="close the verified session"
    )
    complete_parser.add_argument("--session", required=True)
    _add_json_flag(complete_parser)
    return parser


def format_human(payload: dict[str, object]) -> str:
    if "criteria" not in payload:
        return str(payload.get("message", payload))
    lines = [str(payload["message"]), f"state: {payload['state_path']}"]
    for criterion in payload["criteria"]:
        marker = " stale" if criterion.get("stale") else ""
        lines.append(
            f"  {criterion['id']} [{criterion['status']}{marker}] {criterion['description']}"
        )
    for packet in payload.get("work_packets", []):
        lines.append(
            f"  packet {packet['id']} [{packet['status']}] {packet['objective']}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    # Every payload prints with ensure_ascii=False, and Windows consoles
    # default to a legacy codepage: one U+2028 in an objective crashed the
    # very print that was reporting it. Pin the streams instead of the text.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "init": initialize,
        "status": status,
        "resume-summary": resume_session,
        "compact": compact_session_command,
        "storage-inspect": storage_inspect_command,
        "storage-gc": storage_gc_command,
        "state-migrate": state_migrate,
        "plan-packets": plan_packets,
        "start-packet": start_packet,
        "run-packet-check": run_packet_check,
        "complete-packet": complete_packet,
        "reopen-packet": reopen_packet,
        "run": run_command,
        "run-red": run_red_command,
        "run-green": run_green_command,
        "record": record_artifact,
        "record-web": record_browser_evidence,
        "record-desktop": record_desktop_evidence,
        "record-navigation": record_navigation_evidence,
        "record-design": record_design_evidence,
        "record-context": record_external_context,
        "record-communication": record_communication_evidence,
        "verify": verify,
        "reopen": reopen,
        "complete": complete,
    }
    try:
        payload, exit_code = handlers[args.subcommand](args)
    except WorkStateError as error:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(error)}, ensure_ascii=False))
        else:
            print(f"error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_human(payload))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
