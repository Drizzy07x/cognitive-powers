#!/usr/bin/env python3
"""Finalize one or more controller A/B coordinator outputs independently."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

try:
    from scripts.integration_evaluation import (
        DEFAULT_CONTROLLER_PROTOCOL,
        EXPECTED_REQUIRED_ARTIFACTS,
        EvaluationError,
        _artifact_sha256,
        _canonical_sha256,
        compare,
        load_artifact_bundle,
        load_controller_protocol,
        normalize_receipt,
        validate_task_contract,
    )
except ModuleNotFoundError:
    from integration_evaluation import (  # type: ignore[no-redef]
        DEFAULT_CONTROLLER_PROTOCOL,
        EXPECTED_REQUIRED_ARTIFACTS,
        EvaluationError,
        _artifact_sha256,
        _canonical_sha256,
        compare,
        load_artifact_bundle,
        load_controller_protocol,
        normalize_receipt,
        validate_task_contract,
    )


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASK_CONTRACT = PLUGIN_ROOT / "benchmarks" / "evaluation_tasks.json"
FINALIZER_ARTIFACTS = {"independent-verdict.json", "sha256-index.json"}
COORDINATOR_ARTIFACTS = EXPECTED_REQUIRED_ARTIFACTS - FINALIZER_ARTIFACTS
COORDINATOR_FILES = COORDINATOR_ARTIFACTS - {"pre-evaluator-diffs/"}
COORDINATOR_INDEX_FILE = "coordinator-sha256-index.json"
BATCH_STATUS_FILE = "batch-status.json"
BATCH_JOURNAL_FILE = "batch-journal.jsonl"


class EvidenceFinalizationError(ValueError):
    """Raised when coordinator evidence cannot be finalized without weakening it."""


@dataclass(frozen=True)
class CoordinatorEvidence:
    root: Path
    index_sha256: str
    manifest: dict[str, Any]
    schedule: dict[str, Any]
    receipts: tuple[dict[str, Any], ...]
    agent_events: tuple[dict[str, Any], ...]
    hidden_results: tuple[dict[str, Any], ...]
    quality_results: tuple[dict[str, Any], ...]
    diffs: tuple[tuple[str, bytes], ...]
    snapshot: dict[str, str]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceFinalizationError(f"{label} cannot be loaded") from error
    if not isinstance(value, dict):
        raise EvidenceFinalizationError(f"{label} must be an object")
    return value


def _read_jsonl(path: Path, label: str) -> tuple[dict[str, Any], ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise EvidenceFinalizationError(f"{label} cannot be loaded") from error
    if not lines:
        raise EvidenceFinalizationError(f"{label} is empty")
    rows: list[dict[str, Any]] = []
    for ordinal, line in enumerate(lines, start=1):
        if not line:
            raise EvidenceFinalizationError(f"{label} contains a blank record")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise EvidenceFinalizationError(
                f"{label} record {ordinal} is not valid JSON"
            ) from error
        if not isinstance(value, dict):
            raise EvidenceFinalizationError(
                f"{label} record {ordinal} must be an object"
            )
        rows.append(value)
    return tuple(rows)


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_relative_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or value.startswith("/")
        or ":" in value.split("/", 1)[0]
    ):
        return False
    parts = PurePosixPath(value).parts
    return bool(parts) and all(part not in {"", ".", ".."} for part in parts)


def _canonical_embedded_hash(value: Mapping[str, Any], label: str) -> str:
    embedded = value.get("sha256")
    payload = {key: item for key, item in value.items() if key != "sha256"}
    observed = _canonical_sha256(payload)
    if embedded != observed:
        raise EvidenceFinalizationError(f"{label} canonical SHA-256 is invalid")
    return observed


def _safe_child(root: Path, relative: str) -> Path:
    if not _valid_relative_path(relative):
        raise EvidenceFinalizationError(
            f"coordinator index path is invalid: {relative!r}"
        )
    target = (root / Path(*PurePosixPath(relative).parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise EvidenceFinalizationError(
            f"coordinator index path escapes its output: {relative}"
        ) from error
    return target


def _source_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise EvidenceFinalizationError(
                f"coordinator output contains a symbolic link: {relative}"
            )
        if path.is_file():
            snapshot[relative] = _sha256_bytes(path.read_bytes())
    return snapshot


def _indexed_digest(entry: object, relative: str) -> str:
    if isinstance(entry, str):
        digest = entry
    elif (
        isinstance(entry, dict)
        and entry.get("path") == relative
        and isinstance(entry.get("sha256"), str)
    ):
        digest = entry["sha256"]
    else:
        raise EvidenceFinalizationError(
            f"coordinator index entry is invalid: {relative}"
        )
    if not _valid_sha256(digest):
        raise EvidenceFinalizationError(
            f"coordinator index SHA-256 is invalid: {relative}"
        )
    return digest


def _protocol_fields(manifest: Mapping[str, Any]) -> tuple[object, object]:
    protocol_id = manifest.get("protocol_id", manifest.get("controller_protocol_id"))
    return protocol_id, manifest.get("controller_protocol_sha256")


def _schedule_entries(
    schedule: Mapping[str, Any],
) -> set[tuple[object, object, object, tuple[object, ...]]]:
    if isinstance(schedule.get("entries"), list):
        rows = schedule["entries"]
        if not all(isinstance(item, dict) for item in rows):
            raise EvidenceFinalizationError("coordinator schedule entries are invalid")
        return {
            (
                item.get("case_id"),
                item.get("task_id"),
                item.get("repetition"),
                tuple(item.get("arm_order", [])),
            )
            for item in rows
        }
    jobs = schedule.get("jobs")
    if not isinstance(jobs, list) or not all(isinstance(item, dict) for item in jobs):
        raise EvidenceFinalizationError("coordinator schedule is missing entries")
    entries: set[tuple[object, object, object, tuple[object, ...]]] = set()
    for job in jobs:
        orders = job.get("arm_orders")
        if (
            not isinstance(orders, list)
            or len(orders) != 1
            or orders[0]
            not in (
                ["baseline", "candidate"],
                ["candidate", "baseline"],
            )
        ):
            raise EvidenceFinalizationError("coordinator schedule arm order is invalid")
        case_id = job.get("case_id")
        if not isinstance(case_id, str):
            task_id = job.get("task_id")
            repetition = job.get("repetition")
            case_id = (
                f"{task_id}-rep{repetition}"
                if isinstance(task_id, str)
                else job.get("job_id")
            )
        entries.add(
            (
                case_id,
                job.get("task_id"),
                job.get("repetition"),
                tuple(orders[0]),
            )
        )
    return entries


def _receipt_entries(
    receipts: Sequence[Mapping[str, Any]],
) -> set[tuple[object, object, object, tuple[object, ...]]]:
    by_case: dict[str, tuple[object, object, tuple[object, ...]]] = {}
    variants: dict[str, set[object]] = {}
    for receipt in receipts:
        case_id = receipt.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise EvidenceFinalizationError("coordinator receipt case_id is invalid")
        projection = (
            receipt.get("task_id"),
            receipt.get("repetition"),
            tuple(receipt.get("arm_order", [])),
        )
        if case_id in by_case and by_case[case_id] != projection:
            raise EvidenceFinalizationError(
                f"coordinator receipt pair diverges: {case_id}"
            )
        by_case[case_id] = projection
        variants.setdefault(case_id, set()).add(receipt.get("variant"))
    if any(value != {"baseline", "candidate"} for value in variants.values()):
        raise EvidenceFinalizationError("coordinator receipts are not paired")
    return {
        (case_id, task_id, repetition, order)
        for case_id, (task_id, repetition, order) in by_case.items()
    }


def _load_coordinator_output(
    root: Path, protocol: Mapping[str, Any]
) -> CoordinatorEvidence:
    resolved = root.expanduser().resolve()
    if not resolved.is_dir():
        raise EvidenceFinalizationError(
            f"coordinator output is not a directory: {resolved}"
        )
    snapshot = _source_snapshot(resolved)
    index = _read_json(resolved / COORDINATOR_INDEX_FILE, "coordinator index")
    if (
        index.get("schema_version") != 1
        or index.get("scope") != "coordinator-evidence-before-independent-verification"
        or index.get("independent_verdict_included") is not False
    ):
        raise EvidenceFinalizationError("coordinator index contract is invalid")
    index_sha256 = _canonical_embedded_hash(index, "coordinator index")
    artifacts = index.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise EvidenceFinalizationError("coordinator index artifacts are invalid")

    diff_root = resolved / "pre-evaluator-diffs"
    if not diff_root.is_dir():
        raise EvidenceFinalizationError("pre-evaluator diff artifacts are missing")
    diff_entries = tuple(sorted(diff_root.iterdir()))
    if not diff_entries or any(
        not path.is_file() or path.suffix != ".json" for path in diff_entries
    ):
        raise EvidenceFinalizationError(
            "pre-evaluator diff artifacts must be direct JSON files"
        )
    diff_paths = diff_entries
    indexed_paths = set(artifacts)
    required_files = set(COORDINATOR_FILES) | {BATCH_JOURNAL_FILE}
    individual_diffs = {path.relative_to(resolved).as_posix() for path in diff_paths}
    directory_mode = "pre-evaluator-diffs/" in indexed_paths
    expected_indexed = required_files | (
        {"pre-evaluator-diffs/"} if directory_mode else individual_diffs
    )
    if indexed_paths != expected_indexed:
        raise EvidenceFinalizationError(
            "coordinator index does not contain the exact coordinator artifact set"
        )
    for relative, entry in artifacts.items():
        digest = _indexed_digest(entry, relative)
        target = (
            diff_root
            if relative == "pre-evaluator-diffs/"
            else _safe_child(resolved, relative)
        )
        observed = (
            _artifact_sha256(target)
            if relative == "pre-evaluator-diffs/"
            else _sha256_bytes(target.read_bytes())
            if target.is_file()
            else None
        )
        if observed != digest:
            raise EvidenceFinalizationError(
                f"coordinator artifact hash mismatch: {relative}"
            )

    status = _read_json(resolved / BATCH_STATUS_FILE, "batch status")
    if (
        status.get("schema_version") != 1
        or status.get("complete") is not True
        or status.get("independent_verification_pending") is not True
        or status.get("coordinator_index_sha256") != index_sha256
    ):
        raise EvidenceFinalizationError("coordinator batch is not complete")
    manifest = _read_json(resolved / "frozen-manifest.json", "frozen manifest")
    schedule = _read_json(resolved / "randomized-schedule.json", "schedule")
    manifest_sha256 = _canonical_embedded_hash(manifest, "frozen manifest")
    schedule_sha256 = _canonical_embedded_hash(schedule, "schedule")
    if (
        status.get("manifest_sha256") != manifest_sha256
        or status.get("schedule_sha256") != schedule_sha256
        or manifest.get("schedule_sha256") != schedule_sha256
    ):
        raise EvidenceFinalizationError("batch identity hashes do not match")
    protocol_id, protocol_sha256 = _protocol_fields(manifest)
    if protocol_id != protocol.get("protocol_id") or protocol_sha256 != protocol.get(
        "sha256"
    ):
        raise EvidenceFinalizationError(
            "coordinator output is not bound to the selected protocol"
        )

    receipts = _read_jsonl(resolved / "session-receipts.jsonl", "session receipts")
    if _schedule_entries(schedule) != _receipt_entries(receipts):
        raise EvidenceFinalizationError(
            "coordinator schedule does not match its receipts"
        )
    if status.get("session_count") != len(receipts):
        raise EvidenceFinalizationError(
            "batch status session_count does not match receipts"
        )
    diffs = tuple(
        (path.name, path.read_bytes())
        for path in diff_paths
        if path.is_file() and path.stat().st_size > 0
    )
    if len(diffs) != len(diff_paths):
        raise EvidenceFinalizationError("pre-evaluator diff artifact is empty")
    return CoordinatorEvidence(
        root=resolved,
        index_sha256=index_sha256,
        manifest=manifest,
        schedule=schedule,
        receipts=receipts,
        agent_events=_read_jsonl(resolved / "agent-events.jsonl", "agent events"),
        hidden_results=_read_jsonl(
            resolved / "hidden-check-results.jsonl", "hidden check results"
        ),
        quality_results=_read_jsonl(
            resolved / "quality-check-results.jsonl", "quality check results"
        ),
        diffs=diffs,
        snapshot=snapshot,
    )


def _load_verifier_receipt(
    path: Path, coordinator_index_sha256s: Sequence[str]
) -> tuple[dict[str, Any], str]:
    try:
        raw = path.expanduser().resolve().read_bytes()
        receipt = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceFinalizationError("verifier receipt cannot be loaded") from error
    if not isinstance(receipt, dict):
        raise EvidenceFinalizationError("verifier receipt must be an object")
    expected_indexes = sorted(coordinator_index_sha256s)
    if (
        receipt.get("schema_version") != 1
        or receipt.get("kind") != "controller-ab-independent-verifier-receipt"
        or receipt.get("provenance") != "host"
        or receipt.get("scope") != "experiment"
        or receipt.get("role") != "experiment-verifier"
        or receipt.get("verdict") != "confirmed"
        or receipt.get("independent") is not True
        or not isinstance(receipt.get("verifier_id"), str)
        or not receipt["verifier_id"].strip()
        or receipt.get("coordinator_index_sha256s") != expected_indexes
        or len(expected_indexes) != len(set(expected_indexes))
    ):
        raise EvidenceFinalizationError(
            "verifier receipt is invalid, incomplete, or not host-independent"
        )
    return receipt, _sha256_bytes(raw)


def _row_key(row: Mapping[str, Any], label: str) -> tuple[str, str]:
    case_id = row.get("case_id")
    variant = row.get("variant")
    if (
        not isinstance(case_id, str)
        or not case_id
        or variant not in {"baseline", "candidate"}
    ):
        raise EvidenceFinalizationError(f"{label} row identity is invalid")
    return case_id, variant


def _merge_unique_rows(
    sources: Sequence[Sequence[dict[str, Any]]], label: str
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for rows in sources:
        for row in rows:
            key = _row_key(row, label)
            grouped.setdefault(key, []).append(row)
    for key, rows in grouped.items():
        if len(rows) > 1 and any(row != rows[0] for row in rows[1:]):
            raise EvidenceFinalizationError(f"{label} diverges for {key[0]} {key[1]}")
    for key, rows in grouped.items():
        if len(rows) > 1:
            raise EvidenceFinalizationError(f"{label} duplicates for {key[0]} {key[1]}")
    return [grouped[key][0] for key in sorted(grouped)]


def _merge_agent_events(
    sources: Sequence[Sequence[dict[str, Any]]],
    verifier_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    identities: dict[tuple[object, ...], str] = {}
    executors: set[str] = set()
    for source in sources:
        for row in source:
            if (
                row.get("scope") == "experiment"
                and row.get("role") == "experiment-verifier"
            ):
                raise EvidenceFinalizationError(
                    "coordinator output already contains an independent verifier"
                )
            if (
                row.get("scope") == "experiment"
                and row.get("role") == "experiment-runner"
            ):
                if (
                    row.get("type") != "agent.lifecycle"
                    or row.get("provenance") != "host"
                    or not isinstance(row.get("actor_id"), str)
                    or not row["actor_id"]
                ):
                    raise EvidenceFinalizationError(
                        "experiment-runner event lacks host-backed identity"
                    )
                executors.add(row["actor_id"])
            identity = (
                row.get("type"),
                row.get("scope"),
                row.get("case_id"),
                row.get("variant"),
                row.get("assignment_id"),
                row.get("actor_id"),
                row.get("role"),
            )
            encoded = json.dumps(row, sort_keys=True, separators=(",", ":"))
            if identity in identities:
                relation = (
                    "duplicates" if identities[identity] == encoded else "diverges"
                )
                raise EvidenceFinalizationError(
                    f"agent events {relation} for one lifecycle identity"
                )
            identities[identity] = encoded
            rows.append(row)
    if not executors:
        raise EvidenceFinalizationError(
            "agent events contain no host experiment-runner identity"
        )
    if verifier_id in executors:
        raise EvidenceFinalizationError(
            "independent verifier must differ from every experiment runner"
        )
    rows.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return rows, sorted(executors)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _analysis_binding(report: Mapping[str, Any]) -> dict[str, Any]:
    quality = report["quality"]
    success = report["success"]
    tokens = report["token_gates"]
    routing = report["routing"]
    return {
        "quality": {
            "baseline_average": quality["baseline_average"],
            "candidate_average": quality["candidate_average"],
            "delta": quality["delta"],
            "ci95": quality["task_level_ci95"],
        },
        "success": {
            "paired_delta": success["paired_delta"],
            "ci95": success["task_level_ci95"],
        },
        "tokens": {
            "total_ratio": tokens["total_ratio"],
            "total_ci95": tokens["total_ratio_ci95"],
            "fresh_ratio": tokens["fresh_input_ratio"],
            "fresh_ci95": tokens["fresh_input_ratio_ci95"],
            "solo_ratio": tokens["solo_total_ratio"],
        },
        "routing": {
            "precision": routing["precision"],
            "recall": routing["recall"],
            "agent_plan_compliant": routing["agent_plan_compliant"],
        },
        "analysis_populations": report["analysis_populations"],
        "protocol_status": report["protocol"],
    }


def _assert_sources_unchanged(sources: Sequence[CoordinatorEvidence]) -> None:
    for source in sources:
        if _source_snapshot(source.root) != source.snapshot:
            raise EvidenceFinalizationError(
                f"coordinator output changed during finalization: {source.root}"
            )


def finalize_evidence(
    coordinator_outputs: Sequence[Path],
    bundle_output: Path,
    verifier_receipt_path: Path,
    *,
    controller_protocol_path: Path = DEFAULT_CONTROLLER_PROTOCOL,
    task_contract_path: Path = DEFAULT_TASK_CONTRACT,
    minimum_live_pairs: int = 3,
) -> dict[str, Any]:
    """Validate, combine, finalize, and atomically publish controller A/B evidence."""
    if not coordinator_outputs:
        raise EvidenceFinalizationError("at least one coordinator output is required")
    roots = [path.expanduser().resolve() for path in coordinator_outputs]
    if len(roots) != len(set(roots)):
        raise EvidenceFinalizationError("coordinator outputs must be unique")
    bundle = bundle_output.expanduser().resolve()
    if bundle.exists() and (not bundle.is_dir() or any(bundle.iterdir())):
        raise EvidenceFinalizationError(
            "bundle output must be absent or an empty directory"
        )
    if any(
        bundle == root or bundle in root.parents or root in bundle.parents
        for root in roots
    ):
        raise EvidenceFinalizationError(
            "bundle output must be separate from every coordinator output"
        )
    if minimum_live_pairs < 1:
        raise EvidenceFinalizationError("minimum_live_pairs must be at least 1")

    try:
        protocol = load_controller_protocol(controller_protocol_path)
        contract_payload = json.loads(
            task_contract_path.expanduser().resolve().read_text(encoding="utf-8")
        )
        contract = validate_task_contract(contract_payload)
    except (OSError, json.JSONDecodeError, EvaluationError) as error:
        raise EvidenceFinalizationError(
            "controller protocol or task contract is invalid"
        ) from error
    sources = tuple(_load_coordinator_output(root, protocol) for root in roots)
    ordered = tuple(sorted(sources, key=lambda item: item.index_sha256))
    coordinator_hashes = [source.index_sha256 for source in ordered]
    verifier_receipt, verifier_receipt_sha256 = _load_verifier_receipt(
        verifier_receipt_path, coordinator_hashes
    )

    receipts = _merge_unique_rows(
        [source.receipts for source in ordered], "session receipts"
    )
    hidden = _merge_unique_rows(
        [source.hidden_results for source in ordered], "hidden check results"
    )
    quality = _merge_unique_rows(
        [source.quality_results for source in ordered], "quality check results"
    )
    agent_events, executor_ids = _merge_agent_events(
        [source.agent_events for source in ordered],
        str(verifier_receipt["verifier_id"]).strip(),
    )
    diffs: dict[str, bytes] = {}
    for source in ordered:
        for name, data in source.diffs:
            if name in diffs:
                relation = "duplicates" if diffs[name] == data else "diverges"
                raise EvidenceFinalizationError(
                    f"pre-evaluator diffs {relation}: {name}"
                )
            diffs[name] = data
    if not diffs:
        raise EvidenceFinalizationError("no pre-evaluator diffs were collected")

    task_set_ids = {source.manifest.get("task_set_id") for source in ordered}
    if task_set_ids != {contract["task_set_id"]}:
        raise EvidenceFinalizationError(
            "coordinator outputs do not share the selected task contract"
        )
    try:
        normalized = [normalize_receipt(row) for row in receipts]
        report = compare(
            receipts,
            minimum_live_pairs=minimum_live_pairs,
            task_contract=contract_payload,
            controller_protocol=protocol,
        )
    except (EvaluationError, KeyError, TypeError) as error:
        raise EvidenceFinalizationError(
            "combined coordinator receipts cannot be evaluated"
        ) from error
    normalized.sort(key=lambda item: (item["case_id"], item["variant"]))
    receipt_set_sha256 = _canonical_sha256(normalized)
    entries_by_case: dict[str, dict[str, Any]] = {}
    for receipt in normalized:
        entry = {
            "case_id": receipt["case_id"],
            "task_id": receipt["task_id"],
            "repetition": receipt["repetition"],
            "arm_order": receipt["arm_order"],
        }
        previous = entries_by_case.setdefault(receipt["case_id"], entry)
        if previous != entry:
            raise EvidenceFinalizationError(
                f"combined receipt pair diverges: {receipt['case_id']}"
            )
    schedule_entries = [entries_by_case[key] for key in sorted(entries_by_case)]
    jobs: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    ordinal = 0
    for entry in sorted(
        schedule_entries,
        key=lambda item: (
            next(
                receipt["split"]
                for receipt in normalized
                if receipt["case_id"] == item["case_id"]
            ),
            item["task_id"],
            item["repetition"],
        ),
    ):
        receipt = next(
            item for item in normalized if item["case_id"] == entry["case_id"]
        )
        split = receipt["split"]
        round_contract = contract["rounds"][split]
        job_id = f"{split}-{entry['task_id']}-rep{entry['repetition']}"
        job = {
            "job_id": job_id,
            "task_id": entry["task_id"],
            "split": split,
            "seed": round_contract["arm_order"]["seed"],
            "runner_seed": round_contract["arm_order"]["seed"],
            "repetition": entry["repetition"],
            "repetitions": 1,
            "declared_repetitions": round_contract["repetitions_per_task"],
            "arm_orders": [entry["arm_order"]],
        }
        jobs.append(job)
        for arm in entry["arm_order"]:
            ordinal += 1
            sessions.append(
                {
                    "ordinal": ordinal,
                    "job_id": job_id,
                    "task_id": entry["task_id"],
                    "split": split,
                    "repetition": entry["repetition"],
                    "arm": arm,
                }
            )

    parent = bundle.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{bundle.name}.staging-", dir=parent))
    published = False
    try:
        manifest_payload = {
            "schema_version": 1,
            "task_set_id": contract["task_set_id"],
            "controller_protocol_id": protocol["protocol_id"],
            "controller_protocol_sha256": protocol["sha256"],
            "schedule_sha256": None,
            "coordinator_index_sha256s": coordinator_hashes,
        }
        schedule_payload = {
            "schema_version": 1,
            "task_set_id": contract["task_set_id"],
            "execution": "sequential-randomized-pairs",
            "jobs": jobs,
            "sessions": sessions,
        }
        schedule_sha256 = _canonical_sha256(schedule_payload)
        _write_json(
            stage / "randomized-schedule.json",
            {
                **schedule_payload,
                "sha256": schedule_sha256,
            },
        )
        manifest_payload["schedule_sha256"] = schedule_sha256
        _write_json(
            stage / "frozen-manifest.json",
            {
                **manifest_payload,
                "sha256": _canonical_sha256(manifest_payload),
            },
        )
        _write_jsonl(stage / "session-receipts.jsonl", receipts)
        _write_jsonl(stage / "agent-events.jsonl", agent_events)
        _write_jsonl(stage / "hidden-check-results.jsonl", hidden)
        _write_jsonl(stage / "quality-check-results.jsonl", quality)
        diff_output = stage / "pre-evaluator-diffs"
        diff_output.mkdir()
        for name in sorted(diffs):
            (diff_output / name).write_bytes(diffs[name])
        analysis_binding = _analysis_binding(report)
        analysis = {
            **report,
            "receipt_set_sha256": receipt_set_sha256,
            "analysis_sha256": _canonical_sha256(analysis_binding),
        }
        _write_json(
            stage / "analysis-with-ci95.json",
            analysis,
        )

        reviewed = {
            name: _artifact_sha256(stage / name)
            for name in sorted(COORDINATOR_ARTIFACTS)
        }
        evidence_root = _canonical_sha256(reviewed)
        verdict = {
            "schema_version": 1,
            "verdict": "confirmed",
            "independent": True,
            "protocol_id": protocol["protocol_id"],
            "controller_protocol_sha256": protocol["sha256"],
            "evidence_root_sha256": evidence_root,
            "verifier_id": str(verifier_receipt["verifier_id"]).strip(),
            "executor_ids": executor_ids,
            "verifier_receipt_sha256": verifier_receipt_sha256,
            "coordinator_index_sha256s": coordinator_hashes,
            "reviewed_artifact_sha256s": reviewed,
        }
        _write_json(stage / "independent-verdict.json", verdict)
        artifacts = {
            name: {
                "path": name,
                "sha256": _artifact_sha256(stage / name),
            }
            for name in sorted(EXPECTED_REQUIRED_ARTIFACTS - {"sha256-index.json"})
        }
        index = {
            "schema_version": 1,
            "protocol_id": protocol["protocol_id"],
            "controller_protocol_sha256": protocol["sha256"],
            "evidence_root_sha256": evidence_root,
            "artifacts": artifacts,
        }
        _write_json(stage / "sha256-index.json", index)

        loaded = load_artifact_bundle(stage / "sha256-index.json", protocol)
        final_report = compare(
            receipts,
            minimum_live_pairs=minimum_live_pairs,
            task_contract=contract_payload,
            controller_protocol=protocol,
            artifact_index=stage / "sha256-index.json",
        )
        semantic = (final_report.get("artifact_bundle") or {}).get("semantic_binding")
        if not isinstance(semantic, dict):
            raise EvidenceFinalizationError(
                "public comparison did not confirm artifact semantics"
            )
        _assert_sources_unchanged(ordered)
        if bundle.exists():
            bundle.rmdir()
        os.replace(stage, bundle)
        published = True
        return {
            "schema_version": 1,
            "bundle_output": str(bundle),
            "sha256_index": loaded["index_sha256"],
            "evidence_root_sha256": evidence_root,
            "coordinator_index_sha256s": coordinator_hashes,
            "verifier_id": verdict["verifier_id"],
            "executor_ids": executor_ids,
            "artifact_count": loaded["artifact_count"],
            "semantic_binding": semantic,
            "verdict": final_report["verdict"],
        }
    finally:
        if not published and stage.exists():
            shutil.rmtree(stage)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--coordinator-output",
        type=Path,
        action="append",
        required=True,
        help="complete coordinator evidence root; repeat for pilot and promotion",
    )
    parser.add_argument("--bundle-output", type=Path, required=True)
    parser.add_argument("--verifier-receipt", type=Path, required=True)
    parser.add_argument(
        "--controller-protocol",
        type=Path,
        default=DEFAULT_CONTROLLER_PROTOCOL,
    )
    parser.add_argument(
        "--task-contract",
        type=Path,
        default=DEFAULT_TASK_CONTRACT,
    )
    parser.add_argument("--minimum-live-pairs", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        result = finalize_evidence(
            args.coordinator_output,
            args.bundle_output,
            args.verifier_receipt,
            controller_protocol_path=args.controller_protocol,
            task_contract_path=args.task_contract,
            minimum_live_pairs=args.minimum_live_pairs,
        )
    except (EvidenceFinalizationError, EvaluationError, OSError) as error:
        print(json.dumps({"error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
