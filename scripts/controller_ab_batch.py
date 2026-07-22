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


def build_schedule(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Create deterministic globally randomized repetition pairs and sessions."""
    jobs: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    ordinal = 0
    for split in ("pilot", "promotion"):
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
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise BatchError("batch config must use schema_version 1")
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
    for receipt in receipts:
        telemetry = (
            receipt.get("agent_telemetry") if isinstance(receipt, dict) else None
        )
        if (
            receipt.get("controller_protocol_sha256") is None
            or receipt.get("experiment_sha256") is None
            or not isinstance(telemetry, dict)
            or telemetry.get("complete") is not True
        ):
            raise BatchError(
                f"runner receipt lacks identity or telemetry for {job['job_id']}"
            )
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
    contract = validate_task_contract(contract_raw)
    protocol = load_controller_protocol(Path(config["controller_protocol"]))
    if set(config["tasks"]) != set(contract["tasks"]):
        raise BatchError("batch task bindings must exactly match the frozen contract")
    config["task_prompts"] = {
        task_id: task["prompt"] for task_id, task in contract["tasks"].items()
    }
    schedule = (
        build_preflight_schedule(contract) if preflight else build_schedule(contract)
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
        raise BatchError(
            "batch contains an unfinished or failed provider job; refusing duplicate: "
            + ", ".join(sorted(ambiguous))
        )
    completed = set(states)
    for job in jobs:
        job_id = job["job_id"]
        destination = output / "sessions" / job_id
        if job_id in completed:
            validate_job_output(destination, job)
            continue
        if destination.exists():
            raise BatchError(f"untracked runner output exists for {job_id}")
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
            raise BatchError(
                f"runner failed closed for {job_id}: exit {result.returncode}"
            )
        validated = validate_job_output(destination, job)
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
        materialize(output, jobs, completed)
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
