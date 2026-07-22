#!/usr/bin/env python3
"""Run the frozen controller A/B schedule sequentially with fail-closed resume."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from scripts.integration_evaluation import (
        compare,
        load_controller_protocol,
        validate_task_contract,
    )
    from scripts.live_ab_runner import arm_order
except ModuleNotFoundError:
    from integration_evaluation import (
        compare,
        load_controller_protocol,
        validate_task_contract,
    )
    from live_ab_runner import arm_order


class BatchError(ValueError):
    """Raised when a batch cannot continue without weakening its evidence."""


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _append_journal(path: Path, row: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def build_schedule(
    contract: Mapping[str, Any], round_name: str | None = None
) -> dict[str, Any]:
    """Create deterministic globally randomized repetition pairs and sessions."""
    jobs: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    ordinal = 0
    splits = (round_name,) if round_name is not None else ("pilot", "promotion")
    if any(split not in {"pilot", "promotion"} for split in splits):
        raise BatchError("round_name must be pilot or promotion")
    for split in splits:
        round_value = contract["rounds"][split]
        seed = round_value["arm_order"]["seed"]
        repetitions = round_value["repetitions_per_task"]
        pairs = [
            (task_id, repetition)
            for task_id in round_value["task_ids"]
            for repetition in range(1, repetitions + 1)
        ]
        pairs.sort(
            key=lambda pair: hashlib.sha256(
                f"{seed}\0{pair[0]}\0{pair[1]}".encode("utf-8")
            ).hexdigest()
        )
        for task_id, repetition in pairs:
            order = arm_order(repetitions, f"{seed}\0{task_id}")[repetition - 1]
            job_id = f"{split}-{task_id}-rep{repetition}"
            job = {
                "job_id": job_id,
                "task_id": task_id,
                "split": split,
                "seed": seed,
                "runner_seed": seed,
                "repetition": repetition,
                "repetitions": 1,
                "declared_repetitions": repetitions,
                "arm_orders": [order],
            }
            jobs.append(job)
            for arm in order:
                ordinal += 1
                sessions.append(
                    {
                        "ordinal": ordinal,
                        "job_id": job_id,
                        "task_id": task_id,
                        "split": split,
                        "repetition": repetition,
                        "arm": arm,
                    }
                )
    payload = {
        "schema_version": 1,
        "task_set_id": contract["task_set_id"],
        "execution": "sequential-randomized-pairs",
        "jobs": jobs,
        "sessions": sessions,
    }
    return {**payload, "sha256": canonical_sha256(payload)}


def build_preflight_schedule(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Select one pilot fixture per mode for eight non-scored sessions."""
    modes = ("solo", "parallel-read-only", "parallel-packets", "staged-verify")
    selected: dict[str, str] = {}
    pilot_ids = set(contract["rounds"]["pilot"]["task_ids"])
    for task_id, task in contract["tasks"].items():
        mode = task.get("expected_mode")
        if task_id in pilot_ids and mode in modes and mode not in selected:
            selected[mode] = task_id
    if set(selected) != set(modes):
        raise BatchError(
            "preflight requires one pilot fixture for every controller mode"
        )
    seed = f"{contract['task_set_id']}-instrumental-preflight-v1"
    runner_seed = contract["rounds"]["pilot"]["arm_order"]["seed"]
    jobs: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    ordinal = 0
    for mode_index, mode in enumerate(modes):
        task_id = selected[mode]
        order = (
            ["baseline", "candidate"]
            if mode_index % 2 == 0
            else ["candidate", "baseline"]
        )
        job_id = f"preflight-{mode}"
        jobs.append(
            {
                "job_id": job_id,
                "task_id": task_id,
                "split": "preflight",
                "seed": seed,
                "runner_seed": runner_seed,
                "repetition": 1,
                "repetitions": 1,
                "declared_repetitions": 1,
                "arm_orders": [order],
                "non_scored": True,
                "expected_mode": mode,
            }
        )
        for arm in order:
            ordinal += 1
            sessions.append(
                {
                    "ordinal": ordinal,
                    "job_id": job_id,
                    "task_id": task_id,
                    "split": "preflight",
                    "repetition": 1,
                    "arm": arm,
                    "non_scored": True,
                }
            )
    payload = {
        "schema_version": 1,
        "task_set_id": contract["task_set_id"],
        "execution": "sequential-instrumental-preflight",
        "non_scored": True,
        "jobs": jobs,
        "sessions": sessions,
    }
    return {**payload, "sha256": canonical_sha256(payload)}


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BatchError(f"{label} cannot be loaded") from error


def load_config(path: Path) -> dict[str, Any]:
    value = _load_json(path.resolve(), "batch config")
    if not isinstance(value, dict) or value.get("schema_version") not in {1, 2, 3}:
        raise BatchError("batch config must use schema_version 1, 2, or 3")
    if value["schema_version"] in {2, 3} and value.get("round_name") not in {
        "pilot",
        "promotion",
    }:
        raise BatchError("batch config v2/v3 requires round_name")
    value["claim_eligible"] = value["schema_version"] == 3
    required = {
        "task_contract",
        "controller_protocol",
        "baseline_home",
        "candidate_home",
        "model",
        "reasoning_effort",
        "available_tools",
        "agent_slots",
        "tasks",
    }
    if not required.issubset(value):
        raise BatchError("batch config is incomplete")
    root = path.resolve().parent
    for field in (
        "task_contract",
        "controller_protocol",
        "baseline_home",
        "candidate_home",
    ):
        target = Path(value[field])
        value[field] = str(
            (root / target).resolve() if not target.is_absolute() else target.resolve()
        )
    if (
        not Path(value["task_contract"]).is_file()
        or not Path(value["controller_protocol"]).is_file()
    ):
        raise BatchError("task contract or controller protocol is missing")
    if (
        not Path(value["baseline_home"]).is_dir()
        or not Path(value["candidate_home"]).is_dir()
    ):
        raise BatchError("both frozen CODEX_HOME directories must exist")
    if Path(value["baseline_home"]) == Path(value["candidate_home"]):
        raise BatchError("baseline and candidate CODEX_HOME must differ")
    if (
        not isinstance(value["available_tools"], list)
        or not value["available_tools"]
        or not all(isinstance(item, str) and item for item in value["available_tools"])
        or not isinstance(value["agent_slots"], int)
        or value["agent_slots"] < 1
        or not isinstance(value["tasks"], dict)
    ):
        raise BatchError("batch tools, slots, or task bindings are invalid")
    for task_id, binding in value["tasks"].items():
        if not isinstance(task_id, str) or not isinstance(binding, dict):
            raise BatchError("batch task binding is invalid")
        required_binding = {"fixture", "hidden_check", "quality_check", "allow_changes"}
        if not required_binding.issubset(binding):
            raise BatchError(f"task binding is incomplete: {task_id}")
        fixture = Path(binding["fixture"])
        binding["fixture"] = str(
            (root / fixture).resolve()
            if not fixture.is_absolute()
            else fixture.resolve()
        )
        if not Path(binding["fixture"]).is_dir():
            raise BatchError(f"task fixture is missing: {task_id}")
        for field in ("hidden_check", "quality_check", "allow_changes"):
            if (
                not isinstance(binding[field], list)
                or not binding[field]
                or not all(isinstance(item, str) and item for item in binding[field])
            ):
                raise BatchError(f"task {task_id} has invalid {field}")
        guards = binding.get("guard_roots", [])
        if not isinstance(guards, list) or not all(
            isinstance(item, str) for item in guards
        ):
            raise BatchError(f"task {task_id} has invalid guard_roots")
        binding["guard_roots"] = [
            str(
                (root / Path(item)).resolve()
                if not Path(item).is_absolute()
                else Path(item).resolve()
            )
            for item in guards
        ]
    return value


def validate_confirmatory_schema_binding(
    config: Mapping[str, Any],
    task_contract: Mapping[str, Any],
    controller_protocol: Mapping[str, Any],
) -> bool:
    """Keep v1 readable while preventing it from entering a confirmatory path."""
    claim_eligible = config.get("claim_eligible") is True
    if not claim_eligible and (
        task_contract.get("schema_version") == 3
        or controller_protocol.get("schema_version") in {2, 3}
    ):
        raise BatchError(
            "legacy batch config is not claim-eligible for protocol v2/v3 or task contract v3"
        )
    return claim_eligible


def build_manifest(
    config: Mapping[str, Any],
    contract: Mapping[str, Any],
    protocol: Mapping[str, Any],
    schedule: Mapping[str, Any],
    runner: Path,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "task_set_id": contract["task_set_id"],
        "controller_protocol_id": protocol["protocol_id"],
        "controller_protocol_sha256": protocol["sha256"],
        "task_contract_sha256": file_sha256(Path(config["task_contract"])),
        "runner_sha256": file_sha256(runner),
        "coordinator_sha256": file_sha256(Path(__file__)),
        "schedule_sha256": schedule["sha256"],
        "configuration_sha256": canonical_sha256(config),
        "model": config["model"],
        "reasoning_effort": config["reasoning_effort"],
        "available_tools": sorted(set(config["available_tools"])),
        "agent_slots": config["agent_slots"],
        "controller_modes": {"baseline": "forced-solo", "candidate": "adaptive"},
    }
    return {**payload, "sha256": canonical_sha256(payload)}


def read_journal(path: Path, known_jobs: set[str]) -> dict[str, str]:
    if not path.exists():
        return {}
    states: dict[str, str] = {}
    for index, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line:
            raise BatchError("journal contains a blank record")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise BatchError(f"journal record {index} is corrupt") from error
        job_id = row.get("job_id") if isinstance(row, dict) else None
        state = row.get("state") if isinstance(row, dict) else None
        if job_id not in known_jobs or state not in {
            "started",
            "completed",
            "failed",
            "interrupted",
        }:
            raise BatchError(f"journal record {index} is invalid")
        previous = states.get(job_id)
        allowed = (
            previous is None
            and state == "started"
            or previous == "started"
            and state
            in {
                "completed",
                "failed",
                "interrupted",
            }
        )
        if not allowed:
            raise BatchError(f"duplicate or invalid journal transition for {job_id}")
        states[job_id] = state
    return states


def _validate_execution_semantics(execution: Mapping[str, Any]) -> None:
    planned = execution.get("planned_assignments")
    bindings = execution.get("lifecycle_bindings")
    if (
        execution.get("semantic_binding") is not True
        or not isinstance(planned, list)
        or not isinstance(bindings, list)
        or not all(isinstance(item, dict) for item in (*planned, *bindings))
    ):
        raise BatchError("runner execution receipt lacks semantic binding")
    planned_by_id = {item.get("assignment_id"): item for item in planned}
    bindings_by_id = {item.get("assignment_id"): item for item in bindings}
    if (
        len(planned_by_id) != len(planned)
        or None in planned_by_id
        or len(bindings_by_id) != len(bindings)
        or None in bindings_by_id
        or set(planned_by_id) != set(bindings_by_id)
    ):
        raise BatchError("runner assignment semantic identities are incomplete")
    actor_ids: list[str] = []
    prior_units: set[str] = set()
    write_paths: list[tuple[str, str]] = []
    wave_indexes = {item.get("wave_index") for item in planned}
    if any(
        not isinstance(index, int) or isinstance(index, bool) for index in wave_indexes
    ):
        raise BatchError("runner assignment wave is invalid")
    ordered = sorted(planned, key=lambda item: item["wave_index"])
    for wave_index in sorted(wave_indexes):
        wave_items = [item for item in ordered if item.get("wave_index") == wave_index]
        wave_units: set[str] = set()
        for item in wave_items:
            assignment_id = item.get("assignment_id")
            unit_id = item.get("unit_id")
            role = item.get("role")
            permissions = item.get("permissions")
            ownership = item.get("ownership")
            dependencies = item.get("dependencies")
            depth = item.get("delegation_depth")
            binding = bindings_by_id.get(assignment_id, {})
            actor_id = binding.get("actor_id")
            if (
                wave_index < 0
                or not isinstance(item.get("wave_kind"), str)
                or not isinstance(item.get("wave_parallel"), bool)
                or not isinstance(unit_id, str)
                or not unit_id
                or not isinstance(role, str)
                or permissions not in {"read-only", "write-owned-paths"}
                or not isinstance(ownership, list)
                or not all(isinstance(path, str) and path for path in ownership)
                or not isinstance(dependencies, list)
                or not set(dependencies).issubset(prior_units)
                or not isinstance(depth, int)
                or isinstance(depth, bool)
                or depth < 1
                or item.get("may_spawn") is not False
                or item.get("may_verify_parent") is not False
                or (
                    binding.get("role_observed") is not None
                    and binding.get("role_observed") != role
                )
                or binding.get("delegation_depth") != depth
                or not isinstance(binding.get("parent_id"), str)
                or not binding["parent_id"]
                or not isinstance(actor_id, str)
                or not actor_id
                or (permissions == "write-owned-paths" and not ownership)
            ):
                raise BatchError("runner assignment semantic binding is invalid")
            actor_ids.append(actor_id)
            wave_units.add(unit_id)
            if permissions == "write-owned-paths":
                for path in ownership:
                    normalized = path.replace("\\", "/").strip("/").casefold()
                    if any(
                        other_id != assignment_id
                        and (
                            normalized == other
                            or normalized.startswith(other + "/")
                            or other.startswith(normalized + "/")
                        )
                        for other_id, other in write_paths
                    ):
                        raise BatchError("runner write ownership overlaps")
                    write_paths.append((assignment_id, normalized))
        prior_units.update(wave_units)
    if len(actor_ids) != len(set(actor_ids)):
        raise BatchError("runner verifier or worker reuses an actor identity")
    selected = execution.get("selected_mode")
    verifiers = [item for item in planned if item.get("role") == "verifier"]
    writers = [
        item for item in planned if item.get("permissions") == "write-owned-paths"
    ]
    if selected == "solo" and (planned or bindings):
        raise BatchError("solo execution contains agent assignments")
    if selected == "parallel-read-only" and (len(planned) < 2 or writers or verifiers):
        raise BatchError("parallel read-only execution semantics are invalid")
    if selected == "parallel-packets" and (
        len(writers) < 2
        or len(
            {item.get("wave_index") for item in writers if item.get("wave_parallel")}
        )
        != 1
    ):
        raise BatchError("parallel packet execution semantics are invalid")
    if selected == "staged-verify" and (
        not writers
        or len(verifiers) != 1
        or verifiers[0].get("wave_kind") != "verification"
        or verifiers[0].get("wave_index")
        <= max(item.get("wave_index", -1) for item in writers)
        or set(verifiers[0].get("dependencies", []))
        != {item.get("unit_id") for item in writers}
    ):
        raise BatchError("staged verifier execution semantics are invalid")


def validate_job_output(path: Path, job: Mapping[str, Any]) -> dict[str, Any]:
    summary = _load_json(path / "summary.json", "runner summary")
    receipts = _load_json(path / "receipts.json", "runner receipts")
    results = _load_json(path / "results.json", "runner results")
    expected = job["repetitions"] * 2
    if (
        not isinstance(summary, dict)
        or summary.get("task_id") != job["task_id"]
        or summary.get("repetitions") != job["repetitions"]
        or summary.get("controller_modes")
        != {"baseline": "forced-solo", "candidate": "adaptive"}
        or not isinstance(receipts, list)
        or len(receipts) != expected
        or not isinstance(results, list)
        or len(results) != expected
    ):
        raise BatchError(f"runner output is incomplete for {job['job_id']}")
    keys = {
        (item.get("case_id"), item.get("variant"))
        for item in receipts
        if isinstance(item, dict)
    }
    if len(keys) != expected:
        raise BatchError(f"runner receipts contain duplicates for {job['job_id']}")
    frozen_host = summary.get("host_identity")
    if not isinstance(frozen_host, dict):
        raise BatchError(f"runner summary lacks host identity for {job['job_id']}")
    for receipt in receipts:
        telemetry = (
            receipt.get("agent_telemetry") if isinstance(receipt, dict) else None
        )
        execution = (
            telemetry.get("agent_execution_receipt")
            if isinstance(telemetry, dict)
            else None
        )
        workspace_check = (
            telemetry.get("workspace_change_check")
            if isinstance(telemetry, dict)
            else None
        )
        if (
            receipt.get("controller_protocol_sha256") is None
            or receipt.get("experiment_sha256") is None
            or not isinstance(telemetry, dict)
            or telemetry.get("schema_version") != 3
            or telemetry.get("complete") is not True
            or receipt.get("host_identity") != frozen_host
            or not isinstance(execution, dict)
            or execution.get("schema_version") != 3
            or execution.get("complete") is not True
            or not isinstance(workspace_check, dict)
            or workspace_check.get("provenance") != "pre-evaluator-tree-diff"
        ):
            raise BatchError(
                f"runner receipt lacks identity or telemetry for {job['job_id']}"
            )
        if (
            receipt.get("variant") == "candidate"
            and job.get("expected_mode") is not None
            and telemetry.get("selected_mode") != job["expected_mode"]
        ):
            raise BatchError(
                f"preflight candidate did not exercise {job['expected_mode']} "
                f"for {job['job_id']}"
            )
        planned = execution.get("planned_assignment_ids")
        spawned = execution.get("spawned_assignment_ids")
        joined = execution.get("joined_assignment_ids")
        results_seen = execution.get("result_assignment_ids")
        usage = execution.get("descendant_usage")
        if not all(
            isinstance(items, list)
            for items in (planned, spawned, joined, results_seen)
        ):
            raise BatchError(
                f"runner execution receipt is malformed for {job['job_id']}"
            )
        if not (
            len(planned) == len(set(planned))
            and sorted(planned)
            == sorted(spawned)
            == sorted(joined)
            == sorted(results_seen)
        ):
            raise BatchError(f"runner lifecycle is incomplete for {job['job_id']}")
        if set(usage or {}) != set(planned):
            raise BatchError(
                f"runner descendant usage is incomplete for {job['job_id']}"
            )
        if execution.get("selected_mode") != execution.get("executed_mode"):
            raise BatchError(f"runner execution degraded for {job['job_id']}")
        if execution.get("outcome") != "completed":
            raise BatchError(f"runner execution did not complete for {job['job_id']}")
        if (
            execution.get("executed_mode") == "parallel-read-only"
            and workspace_check.get("read_only_unchanged") is not True
        ):
            raise BatchError(f"read-only delegation changed files for {job['job_id']}")
        _validate_execution_semantics(execution)
    hashes = {
        name: file_sha256(path / name)
        for name in ("summary.json", "receipts.json", "results.json")
    }
    return {
        "summary": summary,
        "receipts": receipts,
        "results": results,
        "hashes": hashes,
    }


def materialize(
    output: Path, jobs: Sequence[Mapping[str, Any]], completed: set[str]
) -> None:
    receipts: list[dict[str, Any]] = []
    agents: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    diffs = output / "pre-evaluator-diffs"
    diffs.mkdir(exist_ok=True)
    for job in jobs:
        if job["job_id"] not in completed:
            continue
        validated = validate_job_output(output / "sessions" / job["job_id"], job)
        receipts.extend(validated["receipts"])
        for result in validated["results"]:
            key = f"{result['case_id']}-{result['variant']}"
            agents.append(
                {
                    "case_id": result["case_id"],
                    "variant": result["variant"],
                    "agent_telemetry": result.get("agent_telemetry"),
                }
            )
            hidden.append(
                {
                    "case_id": result["case_id"],
                    "variant": result["variant"],
                    "exit_code": result.get("hidden_exit"),
                    "stdout_tail": result.get("hidden_stdout_tail"),
                    "stderr_tail": result.get("hidden_stderr_tail"),
                }
            )
            quality.append(
                {
                    "case_id": result["case_id"],
                    "variant": result["variant"],
                    "score": result.get("quality_score"),
                    "evidence": result.get("quality_evidence"),
                    "critical_errors": result.get("critical_errors"),
                }
            )
            _write_json(
                diffs / f"{key}.json",
                {
                    "case_id": result["case_id"],
                    "variant": result["variant"],
                    "changed_paths": result.get("changed_paths"),
                    "pre_evaluation_diff_sha256": result.get(
                        "pre_evaluation_diff_sha256"
                    ),
                },
            )
    _write_jsonl(output / "session-receipts.jsonl", receipts)
    _write_jsonl(output / "agent-events.jsonl", agents)
    _write_jsonl(output / "hidden-check-results.jsonl", hidden)
    _write_jsonl(output / "quality-check-results.jsonl", quality)


def materialize_coordinator_index(output: Path) -> dict[str, Any]:
    files = (
        "frozen-manifest.json",
        "randomized-schedule.json",
        "batch-journal.jsonl",
        "session-receipts.jsonl",
        "agent-events.jsonl",
        "hidden-check-results.jsonl",
        "quality-check-results.jsonl",
        "analysis-with-ci95.json",
    )
    entries = {name: file_sha256(output / name) for name in files}
    diff_entries = {
        path.relative_to(output).as_posix(): file_sha256(path)
        for path in sorted((output / "pre-evaluator-diffs").glob("*.json"))
    }
    if not diff_entries:
        raise BatchError("pre-evaluator diff artifacts are missing")
    payload = {
        "schema_version": 1,
        "scope": "coordinator-evidence-before-independent-verification",
        "artifacts": {**entries, **diff_entries},
        "independent_verdict_included": False,
    }
    index = {**payload, "sha256": canonical_sha256(payload)}
    _write_json(output / "coordinator-sha256-index.json", index)
    return index


def materialize_invalid_bundle(output: Path, reason: str) -> dict[str, Any]:
    """Close an instrumentally invalid experiment with hash-verifiable artifacts."""
    journal = output / "batch-journal.jsonl"
    attempted: list[str] = []
    if journal.is_file():
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("state") == "started" and isinstance(row.get("job_id"), str):
                attempted.append(row["job_id"])
    verdict = {
        "schema_version": 3,
        "verdict": "invalid",
        "reason": reason,
        "attempted_job_ids": sorted(set(attempted)),
        "attempted_session_count": len(set(attempted)) * 2,
        "independent": False,
    }
    _write_json(output / "independent-verdict.json", verdict)
    _write_json(
        output / "analysis-with-ci95.json",
        {"schema_version": 3, "verdict": "invalid", "reason": reason},
    )
    status = {
        "schema_version": 3,
        "complete": False,
        "verdict": "invalid",
        "reason": reason,
        "attempted_job_ids": verdict["attempted_job_ids"],
        "attempted_session_count": verdict["attempted_session_count"],
    }
    _write_json(output / "batch-status.json", status)
    artifacts = {
        path.relative_to(output).as_posix(): file_sha256(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "sha256-index.json"
    }
    index_payload = {
        "schema_version": 3,
        "verdict": "invalid",
        "artifacts": artifacts,
    }
    index = {**index_payload, "sha256": canonical_sha256(index_payload)}
    _write_json(output / "sha256-index.json", index)
    return {**status, "sha256_index": index["sha256"]}


def runner_command(
    python: str,
    runner: Path,
    config: Mapping[str, Any],
    job: Mapping[str, Any],
    binding: Mapping[str, Any],
    destination: Path,
) -> list[str]:
    command = [
        python,
        str(runner),
        "--fixture",
        binding["fixture"],
        "--output",
        str(destination),
        "--baseline-home",
        config["baseline_home"],
        "--candidate-home",
        config["candidate_home"],
        "--prompt",
        config["task_prompts"][job["task_id"]],
        "--task-id",
        job["task_id"],
        "--task-contract",
        config["task_contract"],
        "--controller-protocol",
        config["controller_protocol"],
        "--model",
        config["model"],
        "--reasoning-effort",
        config["reasoning_effort"],
        "--repetitions",
        str(job["repetitions"]),
        "--batch-repetition",
        str(job["repetition"]),
        "--seed",
        job["runner_seed"],
        "--hidden-check-json",
        json.dumps(binding["hidden_check"]),
        "--quality-check-json",
        json.dumps(binding["quality_check"]),
        "--agent-slots",
        str(config["agent_slots"]),
    ]
    for tool in config["available_tools"]:
        command.extend(("--available-tool", tool))
    for pattern in binding["allow_changes"]:
        command.extend(("--allow-change", pattern))
    for root in binding["guard_roots"]:
        command.extend(("--guard-root", root))
    if config.get("codex"):
        command.extend(("--codex", config["codex"]))
    if config.get("bypass_sandbox") is True:
        command.append("--bypass-sandbox")
    return command


def run_batch(
    config_path: Path,
    output: Path,
    runner: Path,
    *,
    preflight: bool = False,
    invoke: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if not runner.resolve().is_file():
        raise BatchError("live A/B runner is missing")
    config = load_config(config_path)
    contract_raw = _load_json(Path(config["task_contract"]), "task contract")
    protocol_raw = _load_json(
        Path(config["controller_protocol"]), "controller protocol"
    )
    claim_eligible = validate_confirmatory_schema_binding(
        config, contract_raw, protocol_raw
    )
    contract = validate_task_contract(contract_raw)
    protocol = load_controller_protocol(Path(config["controller_protocol"]))
    expected_tasks = (
        set(contract["rounds"][config["round_name"]]["task_ids"])
        if config["schema_version"] in {2, 3}
        else set(contract["tasks"])
    )
    if set(config["tasks"]) != expected_tasks:
        raise BatchError("batch task bindings must exactly match the selected round")
    config["task_prompts"] = {
        task_id: task["prompt"] for task_id, task in contract["tasks"].items()
    }
    config["claim_eligible"] = claim_eligible
    schedule = (
        build_preflight_schedule(contract)
        if preflight
        else build_schedule(contract, config.get("round_name"))
    )
    manifest = build_manifest(config, contract, protocol, schedule, runner)
    output = output.resolve()
    if output.exists():
        frozen = _load_json(output / "frozen-manifest.json", "frozen manifest")
        observed_schedule = _load_json(
            output / "randomized-schedule.json", "frozen schedule"
        )
        if frozen != manifest or observed_schedule != schedule:
            raise BatchError("resume configuration differs from frozen batch identity")
    else:
        output.mkdir(parents=True)
        (output / "sessions").mkdir()
        _write_json(output / "frozen-manifest.json", manifest)
        _write_json(output / "randomized-schedule.json", schedule)
        (output / "batch-journal.jsonl").touch()
    jobs = schedule["jobs"]
    known_jobs = {job["job_id"] for job in jobs}
    journal = output / "batch-journal.jsonl"
    states = read_journal(journal, known_jobs)
    ambiguous = [job_id for job_id, state in states.items() if state != "completed"]
    if ambiguous:
        error = BatchError(
            "batch contains an unfinished or failed provider job; refusing duplicate: "
            + ", ".join(sorted(ambiguous))
        )
        materialize_invalid_bundle(output, str(error))
        raise error
    completed = set(states)
    for job in jobs:
        job_id = job["job_id"]
        destination = output / "sessions" / job_id
        if job_id in completed:
            try:
                validate_job_output(destination, job)
            except BatchError as error:
                materialize_invalid_bundle(output, str(error))
                raise
            continue
        if destination.exists():
            error = BatchError(f"untracked runner output exists for {job_id}")
            materialize_invalid_bundle(output, str(error))
            raise error
        _append_journal(journal, {"job_id": job_id, "state": "started"})
        command = runner_command(
            sys.executable,
            runner,
            config,
            job,
            config["tasks"][job["task_id"]],
            destination,
        )
        try:
            result = invoke(command, check=False, text=True)
        except BaseException:
            _append_journal(journal, {"job_id": job_id, "state": "interrupted"})
            raise
        if result.returncode not in {0, 1}:
            _append_journal(
                journal,
                {"job_id": job_id, "state": "failed", "exit_code": result.returncode},
            )
            error = BatchError(
                f"runner failed closed for {job_id}: exit {result.returncode}"
            )
            materialize_invalid_bundle(output, str(error))
            raise error
        try:
            validated = validate_job_output(destination, job)
        except BatchError as error:
            _append_journal(
                journal,
                {"job_id": job_id, "state": "failed", "reason": str(error)},
            )
            materialize_invalid_bundle(output, str(error))
            raise
        _append_journal(
            journal,
            {
                "job_id": job_id,
                "state": "completed",
                "exit_code": result.returncode,
                "artifact_hashes": validated["hashes"],
            },
        )
        completed.add(job_id)
        try:
            materialize(output, jobs, completed)
        except (BatchError, ValueError, OSError) as error:
            materialize_invalid_bundle(output, str(error))
            raise
    try:
        materialize(output, jobs, completed)
        analysis = compare(
            json.loads(
                "["
                + ",".join(
                    (output / "session-receipts.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                )
                + "]"
            ),
            minimum_live_pairs=1,
            task_contract=contract_raw,
            controller_protocol=protocol,
        )
    except (BatchError, ValueError, OSError, json.JSONDecodeError) as error:
        materialize_invalid_bundle(output, str(error))
        raise
    _write_json(output / "analysis-with-ci95.json", analysis)
    status = {
        "schema_version": 1,
        "complete": completed == known_jobs,
        "completed_jobs": len(completed),
        "total_jobs": len(known_jobs),
        "session_count": len(schedule["sessions"]),
        "manifest_sha256": manifest["sha256"],
        "schedule_sha256": schedule["sha256"],
        "analysis_verdict": analysis.get("verdict", "invalid"),
        "independent_verification_pending": True,
    }
    index = materialize_coordinator_index(output)
    status["coordinator_index_sha256"] = index["sha256"]
    _write_json(output / "batch-status.json", status)
    return status


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--runner",
        type=Path,
        default=Path(__file__).resolve().parent / "live_ab_runner.py",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="run eight non-scored instrumental sessions, one A/B pair per mode",
    )
    args = parser.parse_args(argv)
    try:
        status = run_batch(
            args.config,
            args.output,
            args.runner.resolve(),
            preflight=args.preflight,
        )
    except (OSError, BatchError, ValueError) as error:
        print(json.dumps({"error": str(error)}))
        return 2
    print(json.dumps(status, sort_keys=True))
    return 0 if status["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
