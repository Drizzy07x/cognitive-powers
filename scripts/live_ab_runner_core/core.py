"""Run isolated, paired Codex live evaluations without inventing evidence."""

from __future__ import annotations

import ast
import errno
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


def _load_sibling_module(name: str):
    path = Path(__file__).resolve().parents[1] / f"{name}.py"
    module_name = (
        f"_cognitive_{name}_"
        + hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    )
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load sibling module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_EVALUATION = _load_sibling_module("integration_evaluation")
_STORAGE_POLICY = _load_sibling_module("storage_policy")
validate_task_contract = _EVALUATION.validate_task_contract
DEFAULT_COPY_MAX_BYTES = _STORAGE_POLICY.DEFAULT_COPY_MAX_BYTES
DEFAULT_COPY_MAX_FILES = _STORAGE_POLICY.DEFAULT_COPY_MAX_FILES
StoragePolicyError = _STORAGE_POLICY.StoragePolicyError
TreeMeasurement = _STORAGE_POLICY.TreeMeasurement
bounded_copy_tree = _STORAGE_POLICY.bounded_copy_tree
enforce_budget = _STORAGE_POLICY.enforce_budget
iter_tree_files = _STORAGE_POLICY.iter_tree_files
measure_tree = _STORAGE_POLICY.measure_tree
reject_large_excluded_trees = _STORAGE_POLICY.reject_large_excluded_trees


INSTALLED_SURFACE_DIRECTORIES = (
    ".codex-plugin",
    "assets",
    "hooks",
    "skills",
    "skills-core",
)
INSTALLED_SURFACE_FILES = (
    "scripts/orchestration_policy.py",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
)
CONTROLLER_MODES = {"forced-solo", "adaptive"}
AGENT_PLAN_MODES = {
    "solo",
    "parallel-read-only",
    "parallel-packets",
    "staged-verify",
}
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTROLLER_PROTOCOL = PLUGIN_ROOT / "benchmarks" / "controller_ab_protocol.json"
CONTROLLER_DIRECTIVE_VERSION = 6
CONTROLLER_DIRECTIVE_TEMPLATE = """[Cognitive Powers controller directive v{version}; mode={mode}]
This directive is the controller_mode treatment and the only intentional A/B difference.
{behavior}
For every spawned assignment use task_name equal to the normalized planned unit id: lowercase, with non-alphanumeric runs replaced by underscores. Emit exactly one complete canonical v2 agent_plan per task, as returned by the orchestration runtime, in a standalone JSON agent_message before any spawn; do not summarize, rewrite, infer, reconstruct, or replace it. Execute its waves in order. Between waves, evaluate its stop conditions without emitting a new plan or changing assignment ids; if the plan becomes invalid, stop delegation and report degradation. Never claim an agent ran unless the native host tool ran and was joined.
"""
SUPPORTED_EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "item.started",
    "item.updated",
    "item.completed",
    "agent.lifecycle",
    "error",
}
DEFAULT_WORK_MAX_FILES = DEFAULT_COPY_MAX_FILES * 10
DEFAULT_WORK_MAX_BYTES = DEFAULT_COPY_MAX_BYTES * 10


class LiveEvaluationError(ValueError):
    """Raised when a live evaluation cannot produce trustworthy receipts."""


def rollout_snapshot(home: Path) -> dict[str, str]:
    sessions = home / "sessions"
    if not sessions.exists():
        return {}
    return {
        str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sessions.rglob("*.jsonl")
        if path.is_file()
    }


def _rollout_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise LiveEvaluationError(f"rollout is not valid JSONL: {path}") from error
        if not isinstance(row, dict):
            raise LiveEvaluationError(f"rollout row is not an object: {path}")
        rows.append(row)
    return rows


def _final_rollout_usage(rows: Sequence[dict[str, Any]], path: Path) -> dict[str, int]:
    usages = [
        row.get("payload", {}).get("info", {}).get("total_token_usage")
        for row in rows
        if row.get("type") == "event_msg"
        and row.get("payload", {}).get("type") == "token_count"
    ]
    usage = usages[-1] if usages else None
    if not isinstance(usage, dict) or any(
        not isinstance(usage.get(field), int) or usage[field] < 0
        for field in ("input_tokens", "cached_input_tokens", "output_tokens")
    ):
        raise LiveEvaluationError(f"rollout lacks final provider usage: {path}")
    return {
        "input_tokens": usage["input_tokens"],
        "cached_input_tokens": usage["cached_input_tokens"],
        "output_tokens": usage["output_tokens"],
    }


def parse_new_rollouts(
    home: Path, before: Mapping[str, str], parent_thread_id: str
) -> dict[str, Any]:
    after = rollout_snapshot(home)
    modified = [path for path, digest in before.items() if after.get(path) != digest]
    if modified:
        raise LiveEvaluationError("pre-existing rollout files changed during session")
    new_paths = [Path(path) for path in after if path not in before]
    documents: list[dict[str, Any]] = []
    for path in new_paths:
        rows = _rollout_rows(path)
        if (
            not rows
            or rows[0].get("type") != "session_meta"
            or not isinstance(rows[0].get("payload"), dict)
        ):
            raise LiveEvaluationError(f"rollout lacks an initial session_meta: {path}")
        # A child created with fork_turns may contain inherited parent metadata later.
        # The first row is the file owner's host-written identity.
        meta = rows[0]["payload"]
        documents.append({"path": path, "rows": rows, "meta": meta})
    parents = [
        item
        for item in documents
        if (item["meta"].get("id") or item["meta"].get("session_id"))
        == parent_thread_id
    ]
    if len(parents) != 1:
        raise LiveEvaluationError(
            "new rollouts lack exactly one matching parent thread"
        )
    parent = parents[0]
    parent_rows = parent["rows"]
    spawn_calls: dict[str, str] = {}
    activities: dict[str, dict[str, Any]] = {}
    wait_call_indexes: dict[str, int] = {}
    wait_outputs: list[tuple[int, str]] = []
    parent_final_messages: list[tuple[int, str, str]] = []
    for row_index, row in enumerate(parent_rows):
        payload = row.get("payload", {})
        if (
            row.get("type") == "response_item"
            and payload.get("type") == "function_call"
        ):
            if payload.get("name") == "spawn_agent":
                try:
                    args = json.loads(payload.get("arguments", ""))
                except json.JSONDecodeError as error:
                    raise LiveEvaluationError(
                        "spawn_agent arguments are invalid"
                    ) from error
                task_name = args.get("task_name") if isinstance(args, dict) else None
                call_id = payload.get("call_id")
                if (
                    not isinstance(task_name, str)
                    or not task_name
                    or not isinstance(call_id, str)
                ):
                    raise LiveEvaluationError("spawn_agent lacks observable task_name")
                if call_id in spawn_calls:
                    raise LiveEvaluationError("duplicate spawn call identity")
                spawn_calls[call_id] = task_name
            elif payload.get("name") == "wait_agent":
                call_id = payload.get("call_id")
                if not isinstance(call_id, str) or not call_id:
                    raise LiveEvaluationError("wait_agent lacks observable call_id")
                if call_id in wait_call_indexes:
                    raise LiveEvaluationError("duplicate wait call identity")
                wait_call_indexes[call_id] = row_index
        if (
            row.get("type") == "response_item"
            and payload.get("type") == "function_call_output"
            and payload.get("call_id") in wait_call_indexes
        ):
            output = payload.get("output")
            try:
                decoded = json.loads(output) if isinstance(output, str) else output
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict) and decoded.get("timed_out") is False:
                wait_outputs.append((row_index, payload["call_id"]))
        if (
            row.get("type") == "response_item"
            and payload.get("type") == "agent_message"
        ):
            author = payload.get("author")
            content = payload.get("content")
            is_final = isinstance(content, list) and any(
                isinstance(item, dict)
                and item.get("type") == "input_text"
                and isinstance(item.get("text"), str)
                and item["text"].lstrip().startswith("Message Type: FINAL_ANSWER")
                for item in content
            )
            if is_final and isinstance(author, str) and author.startswith("/root/"):
                message_id = payload.get("id")
                if not isinstance(message_id, str) or not message_id:
                    encoded = json.dumps(
                        {
                            "row_index": row_index,
                            "author": author,
                            "payload": payload,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    message_id = "sha256:" + hashlib.sha256(encoded).hexdigest()
                parent_final_messages.append((row_index, author, message_id))
        if (
            row.get("type") == "event_msg"
            and payload.get("type") == "sub_agent_activity"
        ):
            if payload.get("kind") == "started" and isinstance(
                payload.get("event_id"), str
            ):
                if payload["event_id"] in activities:
                    raise LiveEvaluationError("duplicate sub-agent activity identity")
                activities[payload["event_id"]] = payload
    if set(spawn_calls) != set(activities):
        raise LiveEvaluationError("spawn calls do not match host sub-agent activity")
    if len(set(spawn_calls.values())) != len(spawn_calls):
        raise LiveEvaluationError("duplicate task identity breaks one-to-one binding")

    child_binding_by_path: dict[str, dict[str, str]] = {}
    actor_ids: set[str] = set()
    for call_id, task_name in spawn_calls.items():
        activity = activities[call_id]
        child_id = activity.get("agent_thread_id")
        agent_path = activity.get("agent_path")
        if not isinstance(child_id, str) or not child_id:
            raise LiveEvaluationError("spawn activity lacks observable child identity")
        if child_id in actor_ids:
            raise LiveEvaluationError(
                "duplicate actor identity breaks one-to-one binding"
            )
        actor_ids.add(child_id)
        expected_path = f"/root/{task_name}"
        if agent_path != expected_path or expected_path in child_binding_by_path:
            raise LiveEvaluationError(
                "spawn task and actor paths lack a one-to-one binding"
            )
        child_binding_by_path[expected_path] = {
            "call_id": call_id,
            "task_name": task_name,
            "actor_id": child_id,
        }

    successful_waits = [
        {"row_index": row_index, "call_id": call_id, "consumed": False}
        for row_index, call_id in sorted(wait_outputs)
    ]
    joins_by_path: dict[str, dict[str, str | None]] = {}
    for row_index, author, message_id in parent_final_messages:
        if author not in child_binding_by_path:
            raise LiveEvaluationError("observable join references an unknown child")
        if author in joins_by_path:
            raise LiveEvaluationError(
                "duplicate observable join breaks one-to-one binding"
            )
        eligible_waits = [
            item
            for item in successful_waits
            if not item["consumed"] and item["row_index"] < row_index
        ]
        matched_wait = eligible_waits[-1] if eligible_waits else None
        if matched_wait is not None:
            matched_wait["consumed"] = True
        joins_by_path[author] = {
            "join_call_id": (
                str(matched_wait["call_id"]) if matched_wait is not None else None
            ),
            "join_source": (
                "wait-agent-final-answer"
                if matched_wait is not None
                else "parent-final-answer"
            ),
            "parent_result_message_id": message_id,
        }

    children_by_id: dict[str, dict[str, Any]] = {}
    for item in documents:
        if item is parent:
            continue
        child_id = item["meta"].get("id") or item["meta"].get("session_id")
        if not isinstance(child_id, str) or not child_id:
            raise LiveEvaluationError("child rollout lacks an observable identity")
        if child_id in children_by_id:
            raise LiveEvaluationError("duplicate child rollout identity")
        children_by_id[child_id] = item

    lifecycle: list[dict[str, Any]] = []
    for call_id, task_name in spawn_calls.items():
        activity = activities[call_id]
        child_id = activity.get("agent_thread_id")
        child = children_by_id.get(child_id)
        if child is None:
            raise LiveEvaluationError("spawned child rollout is missing")
        meta = child["meta"]
        source = meta.get("source")
        spawn = (
            source.get("subagent", {}).get("thread_spawn", {})
            if isinstance(source, dict)
            else {}
        )
        if (
            meta.get("parent_thread_id") != parent_thread_id
            or spawn.get("parent_thread_id") != parent_thread_id
            or spawn.get("depth") != 1
            or meta.get("thread_source") != "subagent"
            or meta.get("agent_path") != activity.get("agent_path")
            or spawn.get("agent_path") != activity.get("agent_path")
        ):
            raise LiveEvaluationError(
                "child rollout is not linked to the parent thread"
            )
        child_results = [
            (row_index, row.get("payload", {}))
            for row_index, row in enumerate(child["rows"])
            if row.get("type") == "event_msg"
            and row.get("payload", {}).get("type") == "agent_message"
        ]
        has_result = bool(child_results)
        result_message_id = None
        if child_results:
            result_row_index, result_payload = child_results[-1]
            result_message_id = result_payload.get("id")
            if not isinstance(result_message_id, str) or not result_message_id:
                encoded = json.dumps(
                    {
                        "actor_id": child_id,
                        "row_index": result_row_index,
                        "payload": result_payload,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                result_message_id = "sha256:" + hashlib.sha256(encoded).hexdigest()
        join = joins_by_path.get(activity.get("agent_path"))
        lifecycle.append(
            {
                "assignment_id": None,
                "task_name": task_name,
                "actor_id": child_id,
                "role": None,
                "parent_id": parent_thread_id,
                "delegation_depth": 1,
                "phases": ["spawned"]
                + (["joined"] if join is not None else [])
                + (["result"] if has_result else []),
                "spawn_call_id": call_id,
                "join_call_id": (join["join_call_id"] if join is not None else None),
                "join_source": join["join_source"] if join is not None else None,
                "parent_result_message_id": (
                    join["parent_result_message_id"] if join is not None else None
                ),
                "result_message_id": result_message_id,
                "usage": _final_rollout_usage(child["rows"], child["path"]),
                "binding_provenance": "persistent-rollout-v3",
                "rollout_sha256": hashlib.sha256(
                    child["path"].read_bytes()
                ).hexdigest(),
            }
        )
    unrelated_children = [
        item
        for child_id, item in children_by_id.items()
        if child_id not in {x["actor_id"] for x in lifecycle}
    ]
    if unrelated_children:
        raise LiveEvaluationError(
            "new child rollout is not linked to an observed spawn"
        )
    parent_usage = _final_rollout_usage(parent_rows, parent["path"])
    aggregate = {
        field: parent_usage[field] + sum(item["usage"][field] for item in lifecycle)
        for field in ("input_tokens", "cached_input_tokens", "output_tokens")
    }
    return {
        "schema_version": 3,
        "source": "persistent-rollouts",
        "parent_thread_id": parent_thread_id,
        "parent_rollout_sha256": hashlib.sha256(
            parent["path"].read_bytes()
        ).hexdigest(),
        "lifecycle": lifecycle,
        "parent_usage": parent_usage,
        "aggregate_usage": aggregate,
        "new_rollout_count": len(new_paths),
    }


def load_controller_protocol(path: Path) -> dict[str, str]:
    """Validate and fingerprint the frozen controller-specific experiment contract."""
    resolved = _resolved(path)
    if not resolved.is_file():
        raise LiveEvaluationError(f"controller protocol is missing: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LiveEvaluationError("controller protocol is not valid JSON") from error
    if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2, 3}:
        raise LiveEvaluationError(
            "controller protocol must use schema_version 1, 2, or 3"
        )
    comparison = payload.get("comparison")
    design = payload.get("design")
    rounds = design.get("rounds") if isinstance(design, dict) else None
    expected = (
        isinstance(comparison, dict)
        and comparison.get("control_arm", {}).get("controller_mode") == "forced-solo"
        and comparison.get("candidate_arm", {}).get("controller_mode") == "adaptive"
        and comparison.get("only_intended_difference") == "controller_mode"
        and isinstance(rounds, dict)
        and design.get("repetitions_per_fixture_per_arm") == 3
        and rounds.get("pilot", {}).get("declared_fixture_count") == 20
        and rounds.get("promotion", {}).get("declared_fixture_count") == 60
        and payload.get("contains_execution_results") is False
        and payload.get("contains_provider_evidence") is False
        and payload.get("claim_status") == "not-proven"
    )
    protocol_id = payload.get("protocol_id")
    if not expected or not isinstance(protocol_id, str) or not protocol_id:
        raise LiveEvaluationError(
            "controller protocol does not match the confirmatory design"
        )
    return {
        "protocol_id": protocol_id,
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "path": str(resolved),
        "claim_status": "not-proven",
    }


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_layout(
    fixture: Path,
    output: Path,
    baseline_home: Path,
    candidate_home: Path,
) -> tuple[Path, Path, Path, Path]:
    fixture = _resolved(fixture)
    output = _resolved(output)
    baseline_home = _resolved(baseline_home)
    candidate_home = _resolved(candidate_home)
    if not fixture.is_dir():
        raise LiveEvaluationError(f"fixture is not a directory: {fixture}")
    for label, home in (("baseline", baseline_home), ("candidate", candidate_home)):
        if not home.is_dir():
            raise LiveEvaluationError(f"{label} CODEX_HOME is not a directory: {home}")
    if baseline_home == candidate_home:
        raise LiveEvaluationError("baseline and candidate CODEX_HOME must differ")
    if _is_within(output, fixture) or _is_within(fixture, output):
        raise LiveEvaluationError(
            "output and source fixture must not contain each other"
        )
    for home in (baseline_home, candidate_home):
        if _is_within(output, home) or _is_within(home, output):
            raise LiveEvaluationError(
                "output and CODEX_HOME must not contain each other"
            )
    return fixture, output, baseline_home, candidate_home


def copy_fixture_tree(
    source: Path,
    destination: Path,
    *,
    manifest: Sequence[str] | None = None,
    tracked_only: bool = False,
    max_files: int = DEFAULT_COPY_MAX_FILES,
    max_bytes: int = DEFAULT_COPY_MAX_BYTES,
    allow_large_excluded_trees: bool = False,
) -> TreeMeasurement:
    """Copy one fixture through the shared exclusions and fail before overflow."""
    try:
        return bounded_copy_tree(
            source,
            destination,
            manifest=manifest,
            tracked_only=tracked_only,
            max_files=max_files,
            max_bytes=max_bytes,
            fixture_mode=True,
            allow_large_excluded_trees=allow_large_excluded_trees,
        )
    except StoragePolicyError as error:
        raise LiveEvaluationError(str(error)) from error


def copy_home_tree(
    source: Path,
    destination: Path,
    *,
    max_files: int,
    max_bytes: int,
) -> TreeMeasurement:
    """Copy one CODEX_HOME through the same bounded generated-tree policy."""
    try:
        return bounded_copy_tree(
            source,
            destination,
            max_files=max_files,
            max_bytes=max_bytes,
        )
    except StoragePolicyError as error:
        raise LiveEvaluationError(str(error)) from error


def _raw_workdir_measurement(root: Path) -> TreeMeasurement:
    """Measure every regular workdir file, including provider-written state."""
    files = 0
    total_bytes = 0
    pending = [root.resolve()]
    while pending:
        directory = pending.pop()
        try:
            children = list(directory.iterdir())
        except OSError as error:
            raise LiveEvaluationError(
                f"cannot inspect debug workdir {directory}: {error}"
            ) from error
        for path in children:
            if path.is_symlink():
                continue
            try:
                if path.is_dir():
                    pending.append(path)
                elif path.is_file():
                    files += 1
                    total_bytes += path.stat().st_size
            except OSError as error:
                raise LiveEvaluationError(
                    f"cannot inspect debug workdir path {path}: {error}"
                ) from error
    return TreeMeasurement(file_count=files, total_bytes=total_bytes)


def workdir_receipt(root: Path) -> dict[str, Any]:
    measured = _raw_workdir_measurement(root)
    return {
        "path": str(root.resolve()),
        "file_count": measured.file_count,
        "total_bytes": measured.total_bytes,
    }


def finalize_workdir(
    root: Path,
    *,
    succeeded: bool,
    retain_debug_workdirs: bool,
) -> dict[str, Any] | None:
    """Delete validated success state; preserve and measure diagnostic state."""
    if succeeded and not retain_debug_workdirs:

        def is_transient_cleanup_error(error: BaseException) -> bool:
            return isinstance(error, FileNotFoundError) or (
                isinstance(error, OSError)
                and (
                    error.errno == errno.ENOTEMPTY
                    or getattr(error, "winerror", None) in {32, 33, 145}
                )
            )

        def ignore_transient_cleanup_error(
            _function: Any,
            _path: str,
            exc_info: tuple[Any, BaseException, Any],
        ) -> None:
            error = exc_info[1]
            if is_transient_cleanup_error(error):
                return
            raise error

        attempts = 50
        for attempt in range(attempts):
            try:
                shutil.rmtree(root, onerror=ignore_transient_cleanup_error)
            except OSError as error:
                if not is_transient_cleanup_error(error):
                    raise
            if not root.exists():
                for _ in range(3):
                    time.sleep(0.1)
                    if root.exists():
                        break
                else:
                    return None
            if attempt + 1 < attempts:
                time.sleep(0.1)
        residual = workdir_receipt(root)
        if residual["file_count"] == 0 and residual["total_bytes"] == 0:
            return residual
        raise LiveEvaluationError(
            "material files remain after bounded successful-workdir cleanup: "
            f"{root} ({residual['file_count']} files, "
            f"{residual['total_bytes']} bytes)"
        )
    return workdir_receipt(root)


def create_workdir(output: Path, requested: Path | None = None) -> Path:
    """Create an external work root which can never be final evidence."""
    output = output.resolve()
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="cognitive-powers-live-ab-")).resolve()
    workdir = requested.expanduser().resolve()
    if _is_within(workdir, output) or _is_within(output, workdir):
        raise LiveEvaluationError(
            "ephemeral workdir and final evidence output must not contain each other"
        )
    if workdir.exists():
        raise LiveEvaluationError(f"ephemeral workdir already exists: {workdir}")
    workdir.mkdir(parents=True)
    return workdir


def projected_copy_budget(
    *,
    fixture: Path,
    baseline_home: Path,
    candidate_home: Path,
    repetitions: int,
    fixture_manifest: Sequence[str] | None,
    fixture_tracked_only: bool,
    max_files: int,
    max_bytes: int,
    allow_large_excluded_trees: bool,
) -> dict[str, Any]:
    """Measure every planned tree copy before creating a copy destination."""
    try:
        reject_large_excluded_trees(
            fixture,
            manifest=fixture_manifest,
            allow_override=allow_large_excluded_trees,
        )
        fixture_measurement = measure_tree(
            fixture,
            manifest=fixture_manifest,
            tracked_only=fixture_tracked_only,
        )
        baseline_measurement = measure_tree(baseline_home)
        candidate_measurement = measure_tree(candidate_home)
        # Per repetition: two home copies, two actor fixture copies, and two
        # isolated evaluator copies for each actor (six fixture copies total).
        projected = TreeMeasurement(
            file_count=repetitions
            * (
                baseline_measurement.file_count
                + candidate_measurement.file_count
                + (6 * fixture_measurement.file_count)
            ),
            total_bytes=repetitions
            * (
                baseline_measurement.total_bytes
                + candidate_measurement.total_bytes
                + (6 * fixture_measurement.total_bytes)
            ),
        )
        enforce_budget(
            projected,
            max_files=max_files,
            max_bytes=max_bytes,
            label="projected workdir copy",
        )
    except StoragePolicyError as error:
        raise LiveEvaluationError(str(error)) from error
    return {
        "fixture": fixture_measurement.as_dict(),
        "baseline_home": baseline_measurement.as_dict(),
        "candidate_home": candidate_measurement.as_dict(),
        "projected": projected.as_dict(),
        "fixture_copy_strategy": (
            "manifest"
            if fixture_manifest is not None
            else "git-tracked"
            if fixture_tracked_only
            else "shared-policy-tree"
        ),
    }


def tree_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for path in iter_tree_files(root):
            relative = path.relative_to(root).as_posix()
            try:
                result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                raise LiveEvaluationError(
                    f"cannot hash source-oriented file {relative}: {error}"
                ) from error
    except StoragePolicyError as error:
        raise LiveEvaluationError(str(error)) from error
    return result


def source_sha256(hashes: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path, value in sorted(hashes.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def require_measured_fixture_unchanged(
    fixture: Path, measured_hashes: Mapping[str, str]
) -> None:
    if tree_hashes(fixture) != dict(measured_hashes):
        raise LiveEvaluationError("evaluators modified the measured result fixture")


def git_identity(root: Path, *, required: bool = True) -> dict[str, Any] | None:
    """Bind a fixture to Git metadata excluded from the normal tree hash."""
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    lines = completed.stdout.splitlines()
    if completed.returncode != 0 or len(lines) != 2:
        if required:
            raise LiveEvaluationError("contract-bound fixture must be a Git checkout")
        return None
    top_level = _resolved(Path(lines[0]))
    if top_level != _resolved(root):
        raise LiveEvaluationError("fixture must be the root of its Git checkout")
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        raise LiveEvaluationError("cannot read fixture Git status")
    payload = {
        "head": lines[1].strip().lower(),
        "status_sha256": hashlib.sha256(status.stdout.encode("utf-8")).hexdigest(),
    }
    payload["sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def protected_roots(
    explicit: Sequence[Path], fixture: Path, plugin_identity: dict[str, Any]
) -> list[Path]:
    candidates = [
        *explicit,
        fixture,
        Path(plugin_identity["source_root"]),
        Path(
            plugin_identity.get(
                "baseline_installed_root", plugin_identity["installed_root"]
            )
        ),
        Path(
            plugin_identity.get(
                "candidate_installed_root", plugin_identity["installed_root"]
            )
        ),
    ]
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = _resolved(candidate)
        if not resolved.is_dir():
            raise LiveEvaluationError(f"guarded root is not a directory: {resolved}")
        key = os.path.normcase(str(resolved))
        if key not in seen:
            result.append(resolved)
            seen.add(key)
    return result


def snapshot_guards(roots: Sequence[Path]) -> dict[str, dict[str, str]]:
    return {str(root): tree_hashes(root) for root in roots}


def verify_guards(
    before: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    changes: dict[str, list[str]] = {}
    for path, initial in before.items():
        final = tree_hashes(Path(path))
        changed = changed_paths(initial, final)
        receipts.append(
            {
                "path": path,
                "before_sha256": source_sha256(initial),
                "after_sha256": source_sha256(final),
                "before_file_count": len(initial),
                "after_file_count": len(final),
                "stable": not changed,
            }
        )
        if changed:
            changes[path] = changed
    if changes:
        raise LiveEvaluationError(f"guarded roots changed: {changes}")
    return receipts


def _local_python_dependencies(source: Path) -> list[Path]:
    root = source.resolve().parent
    pending = [source.resolve()]
    discovered: set[Path] = set()
    companion = root / f"{source.stem}_core"
    if companion.is_dir():
        pending.extend(sorted(companion.rglob("*.py")))
    while pending:
        current = pending.pop()
        if current in discovered or not current.is_file():
            continue
        discovered.add(current)
        try:
            tree = ast.parse(current.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        for module in modules:
            parts = module.split(".")
            for candidate in (
                root.joinpath(*parts).with_suffix(".py"),
                root.joinpath(*parts, "__init__.py"),
            ):
                try:
                    candidate.resolve().relative_to(root)
                except ValueError:
                    continue
                if candidate.is_file() and candidate.resolve() not in discovered:
                    pending.append(candidate.resolve())
    return sorted(discovered)


def command_identity(argv: Sequence[str]) -> dict[str, Any]:
    files: dict[str, str] = {}
    for value in argv:
        path = Path(value).expanduser()
        if path.is_file():
            dependencies = (
                _local_python_dependencies(path)
                if path.suffix.casefold() == ".py"
                else [path.resolve()]
            )
            for dependency in dependencies:
                files[str(dependency)] = hashlib.sha256(
                    dependency.read_bytes()
                ).hexdigest()
    payload = json.dumps(
        {"argv": list(argv), "files": files}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "files": files,
    }


def resolve_codex_executable(codex: str) -> str:
    """Resolve the Codex CLI once, before any subprocess receives the name.

    The CLI is an npm shim, so on Windows it is codex.cmd, and CreateProcess
    only ever appends .exe to a bare name: handing "codex" straight to
    subprocess raised WinError 2 on a host where the CLI works. shutil.which
    honours PATHEXT; an explicit path survives unchanged.
    """
    executable = Path(shutil.which(codex) or codex).expanduser().resolve()
    if not executable.is_file():
        raise LiveEvaluationError(f"Codex executable is unavailable: {codex}")
    return str(executable)


def codex_host_identity(codex: str) -> dict[str, Any]:
    """Freeze the executable and host capabilities used by both experiment arms."""
    executable = Path(resolve_codex_executable(codex))
    completed = subprocess.run(
        [str(executable), "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    version = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0 or not version:
        raise LiveEvaluationError("Codex version preflight failed")
    features_completed = subprocess.run(
        [str(executable), "-c", "features.multi_agent=true", "features", "list"],
        check=False,
        capture_output=True,
        text=True,
    )
    feature_states: dict[str, dict[str, Any]] = {}
    for line in features_completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-1] in {"true", "false"}:
            feature_states[fields[0]] = {
                "stage": " ".join(fields[1:-1]),
                "enabled": fields[-1] == "true",
            }
    if (
        features_completed.returncode != 0
        or feature_states.get("multi_agent", {}).get("enabled") is not True
    ):
        raise LiveEvaluationError("Codex multi_agent feature preflight failed")
    return {
        "executable": str(executable),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "version": version,
        "features": {"multi_agent": True},
        "effective_features": feature_states,
        "persistent_parent_thread": True,
        "event_schema_version": 1,
        "supported_event_types": sorted(SUPPORTED_EVENT_TYPES),
    }


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )


def unexpected_changes(paths: Sequence[str], allowed: Sequence[str]) -> list[str]:
    return [
        path
        for path in paths
        if not any(fnmatch.fnmatchcase(path, rule) for rule in allowed)
    ]


def arm_order(repetitions: int, seed: str) -> list[list[str]]:
    if repetitions < 2:
        raise LiveEvaluationError("repetitions must be at least 2")
    if not seed.strip():
        raise LiveEvaluationError("seed must not be empty")
    first = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % 2
    orders: list[list[str]] = []
    for repetition in range(repetitions):
        baseline_first = (repetition + first) % 2 == 0
        orders.append(
            ["baseline", "candidate"] if baseline_first else ["candidate", "baseline"]
        )
    return orders


def _content_id_matches(value: Mapping[str, Any], field: str, prefix: str) -> bool:
    claimed = value.get(field)
    if not isinstance(claimed, str):
        return False
    unhashed = dict(value)
    unhashed.pop(field, None)
    encoded = json.dumps(
        unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return claimed == f"{prefix}-{hashlib.sha256(encoded).hexdigest()}"


def _canonical_agent_plan_v2(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("kind") != "agent_plan":
        return None
    if (
        value.get("schema_version") != 2
        or value.get("mode") not in AGENT_PLAN_MODES
        or value.get("selected_mode") != value.get("mode")
        or value.get("executed_mode") is not None
        or value.get("outcome") != "planned"
        or not isinstance(value.get("waves"), list)
        or not _content_id_matches(value, "plan_id", "plan")
    ):
        raise LiveEvaluationError("emitted agent_plan is not a canonical v2 plan")
    for wave in value["waves"]:
        if (
            not isinstance(wave, dict)
            or not isinstance(wave.get("kind"), str)
            or not isinstance(wave.get("parallel"), bool)
            or not isinstance(wave.get("assignments"), list)
        ):
            raise LiveEvaluationError("emitted agent_plan contains an invalid wave")
        for assignment in wave["assignments"]:
            if not isinstance(assignment, dict) or not _content_id_matches(
                assignment, "assignment_id", "assignment"
            ):
                raise LiveEvaluationError(
                    "emitted agent_plan contains a non-canonical assignment"
                )
    return value


def _canonical_plan_transition(plans: Sequence[dict[str, Any]]) -> str | None:
    """Accept only a solo implementation phase followed by fresh verification."""
    if len(plans) <= 1:
        return None
    if len(plans) != 2:
        raise LiveEvaluationError("multiple distinct agent_plan receipts were emitted")

    initial, verification = plans
    initial_is_solo = (
        initial.get("valid_input") is True
        and initial.get("mode") == "solo"
        and initial.get("spawn_count") == 0
        and initial.get("total_planned_agents") == 0
        and initial.get("max_depth") == 0
        and initial.get("waves") == []
    )
    waves = verification.get("waves")
    verification_wave = (
        waves[0] if isinstance(waves, list) and len(waves) == 1 else None
    )
    assignments = (
        verification_wave.get("assignments")
        if isinstance(verification_wave, dict)
        else None
    )
    verifier = (
        assignments[0]
        if isinstance(assignments, list) and len(assignments) == 1
        else None
    )
    verification_is_fresh_read_only = (
        verification.get("valid_input") is True
        and verification.get("mode") == "staged-verify"
        and verification.get("spawn_count") == 0
        and verification.get("total_planned_agents") == 1
        and verification.get("max_depth") == 1
        and isinstance(verification_wave, dict)
        and verification_wave.get("kind") == "verification"
        and verification_wave.get("parallel") is False
        and isinstance(verifier, dict)
        and verifier.get("role") == "verifier"
        and verifier.get("permissions") == "read-only"
        and verifier.get("ownership") == []
        and verifier.get("dependencies") == []
        and verifier.get("delegation_depth") == 1
        and verifier.get("may_spawn") is False
        and verifier.get("may_verify_parent") is False
    )
    if not initial_is_solo or not verification_is_fresh_read_only:
        raise LiveEvaluationError("multiple distinct agent_plan receipts were emitted")
    return "solo-to-fresh-verification"


def _agent_message_json_values(item: Mapping[str, Any]) -> list[object]:
    if item.get("type") != "agent_message":
        return []
    texts: list[str] = []
    for field in ("text", "message", "output"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            texts.append(value)
    content = item.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str) and part.strip():
                texts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text)

    values: list[object] = []
    for text in texts:
        stripped = text.strip()
        payloads: list[str] = []
        if stripped.startswith("{"):
            payloads.append(stripped)
        payloads.extend(
            match.group(1).strip()
            for match in re.finditer(
                r"```(?:json)?\s*\r?\n?(.*?)```", text, re.IGNORECASE | re.DOTALL
            )
        )
        for payload in payloads:
            try:
                values.append(json.loads(payload))
            except json.JSONDecodeError as error:
                if "agent_plan" in payload:
                    raise LiveEvaluationError(
                        "emitted agent_plan JSON is malformed"
                    ) from error
    return values


def parse_events(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    invalid_lines = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
    unknown_types = sorted(
        {
            str(row.get("type"))
            for row in rows
            if row.get("type") not in SUPPORTED_EVENT_TYPES
        }
    )
    if unknown_types:
        raise LiveEvaluationError(
            f"{path} contains unsupported event schema types: "
            + ", ".join(unknown_types)
        )
    completed = [row for row in rows if row.get("type") == "turn.completed"]
    if invalid_lines:
        raise LiveEvaluationError(f"{path} contains {invalid_lines} non-JSON lines")
    if len(completed) != 1 or not isinstance(completed[0].get("usage"), dict):
        raise LiveEvaluationError(
            f"{path} must contain exactly one completed turn with usage"
        )
    usage = completed[0]["usage"]
    required = ("input_tokens", "cached_input_tokens", "output_tokens")
    if any(
        not isinstance(usage.get(field), int) or usage[field] < 0 for field in required
    ):
        raise LiveEvaluationError(f"{path} contains invalid provider usage")
    if usage["cached_input_tokens"] > usage["input_tokens"]:
        raise LiveEvaluationError(f"{path} cached input exceeds input tokens")
    item_types = [
        row.get("item", {}).get("type")
        for row in rows
        if row.get("type") == "item.completed" and isinstance(row.get("item"), dict)
    ]
    tool_calls = sum(
        item_type not in {None, "agent_message", "reasoning"}
        for item_type in item_types
    )
    tool_names: list[str] = []
    agent_events: list[dict[str, str]] = []
    for row in rows:
        item = row.get("item")
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("tool_name")
        if isinstance(name, str) and name:
            tool_names.append(name)
            if name in {
                "spawn_agent",
                "wait_agent",
                "send_message",
                "followup_task",
                "interrupt_agent",
            }:
                agent_events.append(
                    {"event": name, "item_type": str(item.get("type", "unknown"))}
                )
    plan_receipts: list[dict[str, Any]] = []
    lifecycle: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("type") != "agent.lifecycle" or row.get("provenance") != "host":
            continue
        if all(
            isinstance(row.get(field), str) and row.get(field)
            for field in ("assignment_id", "actor_id", "role")
        ):
            assignment_id = row["assignment_id"]
            observation = lifecycle.setdefault(
                assignment_id,
                {
                    "assignment_id": assignment_id,
                    "actor_id": row["actor_id"],
                    "role": row["role"],
                    "phases": [],
                    "usage": None,
                    "parent_id": None,
                    "delegation_depth": None,
                },
            )
            if (
                observation["actor_id"] != row["actor_id"]
                or observation["role"] != row["role"]
            ):
                raise LiveEvaluationError(
                    f"{path} rebinds host lifecycle identity for {assignment_id}"
                )
            parent_id = (
                row.get("parent_id")
                or row.get("parent_actor_id")
                or row.get("parent_thread_id")
            )
            if parent_id is not None:
                if not isinstance(parent_id, str) or not parent_id:
                    raise LiveEvaluationError(
                        f"{path} contains invalid lifecycle parent for {assignment_id}"
                    )
                if observation["parent_id"] not in {None, parent_id}:
                    raise LiveEvaluationError(
                        f"{path} rebinds lifecycle parent for {assignment_id}"
                    )
                observation["parent_id"] = parent_id
            depth = row.get("delegation_depth")
            if depth is not None:
                if not isinstance(depth, int) or isinstance(depth, bool) or depth < 1:
                    raise LiveEvaluationError(
                        f"{path} contains invalid lifecycle depth for {assignment_id}"
                    )
                if observation["delegation_depth"] not in {None, depth}:
                    raise LiveEvaluationError(
                        f"{path} rebinds lifecycle depth for {assignment_id}"
                    )
                observation["delegation_depth"] = depth
            phase = row.get("event") or row.get("phase") or row.get("status")
            if isinstance(phase, str) and phase:
                normalized_phase = {
                    "started": "spawned",
                    "created": "spawned",
                    "completed": "result",
                    "failed": "result",
                    "blocked": "result",
                    "confirmed": "result",
                    "rejected": "result",
                    "inconclusive": "result",
                }.get(phase, phase)
                if normalized_phase not in {"spawned", "joined", "result"}:
                    raise LiveEvaluationError(
                        f"{path} contains unsupported agent lifecycle phase: {phase}"
                    )
                if normalized_phase not in observation["phases"]:
                    observation["phases"].append(normalized_phase)
            descendant_usage = row.get("usage")
            if descendant_usage is not None:
                if not isinstance(descendant_usage, dict) or any(
                    not isinstance(descendant_usage.get(field), int)
                    or descendant_usage[field] < 0
                    for field in ("input_tokens", "output_tokens")
                ):
                    raise LiveEvaluationError(
                        f"{path} contains invalid descendant usage for {assignment_id}"
                    )
                cached = descendant_usage.get("cached_input_tokens", 0)
                if (
                    not isinstance(cached, int)
                    or cached < 0
                    or cached > descendant_usage["input_tokens"]
                ):
                    raise LiveEvaluationError(
                        f"{path} contains invalid descendant cache usage for {assignment_id}"
                    )
                observation["usage"] = {
                    "input_tokens": descendant_usage["input_tokens"],
                    "cached_input_tokens": cached,
                    "output_tokens": descendant_usage["output_tokens"],
                }

    for row in rows:
        if row.get("type") != "item.completed" or not isinstance(row.get("item"), dict):
            continue
        for emitted in _agent_message_json_values(row["item"]):
            if isinstance(emitted, dict) and "agent_plan" in emitted:
                candidate = emitted["agent_plan"]
                if not isinstance(candidate, dict):
                    raise LiveEvaluationError("emitted agent_plan must be an object")
            else:
                candidate = emitted
            canonical = _canonical_agent_plan_v2(candidate)
            if canonical is None:
                continue
            encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
            if all(
                json.dumps(item, sort_keys=True, separators=(",", ":")) != encoded
                for item in plan_receipts
            ):
                plan_receipts.append(canonical)
    plan_transition = _canonical_plan_transition(plan_receipts)
    thread_rows = [row for row in rows if row.get("type") == "thread.started"]
    thread_ids: set[str] = set()
    for row in thread_rows:
        nested_thread = row.get("thread")
        thread_id = row.get("thread_id") or (
            nested_thread.get("id") if isinstance(nested_thread, dict) else None
        )
        if isinstance(thread_id, str) and thread_id:
            thread_ids.add(thread_id)
    host_errors: list[str] = []
    for row in rows:
        if row.get("type") == "error":
            encoded = json.dumps(row, sort_keys=True, default=str)
            host_errors.append(encoded[-1000:])
        elif "no thread with id" in json.dumps(row, default=str).casefold():
            host_errors.append("no thread with id")
    if len(thread_ids) > 1:
        raise LiveEvaluationError(f"{path} contains multiple parent thread identities")
    observations = list(lifecycle.values())
    return {
        "usage": dict(usage),
        "tool_calls": tool_calls,
        "turns": len(completed),
        "tool_names": tool_names,
        "agent_events": agent_events,
        "agent_spawns": sum(item["event"] == "spawn_agent" for item in agent_events),
        "agent_joins": sum(item["event"] == "wait_agent" for item in agent_events),
        "agent_plans": plan_receipts,
        "plan_transition": plan_transition,
        "plan_receipt_count": len(plan_receipts),
        "observed_assignments": [
            {key: item[key] for key in ("assignment_id", "actor_id", "role")}
            for item in observations
        ],
        "agent_lifecycle": observations,
        "parent_thread_id": next(iter(thread_ids), None),
        "event_schema_version": 1,
        "event_types": sorted({str(row.get("type")) for row in rows}),
        "host_errors": host_errors,
        "usage_includes_subagents": (
            usage.get("includes_subagents") is True
            or usage.get("scope") in {"task-tree", "all-agents", "aggregate"}
        ),
        "invalid_json_lines": invalid_lines,
    }


def _execution_semantics(
    observed_plan: dict[str, Any] | None, lifecycle: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Bind host actors to the exact planned wave, role, dependencies and authority."""
    if observed_plan is None:
        return [], [], not lifecycle
    waves = observed_plan.get("waves")
    if not isinstance(waves, list):
        return [], [], False
    planned: list[dict[str, Any]] = []
    prior_unit_ids: set[str] = set()
    valid = True
    for wave_index, wave in enumerate(waves):
        if not isinstance(wave, dict) or not isinstance(wave.get("assignments"), list):
            return [], [], False
        wave_kind = wave.get("kind")
        wave_parallel = wave.get("parallel")
        if not isinstance(wave_kind, str) or not isinstance(wave_parallel, bool):
            valid = False
        wave_unit_ids: set[str] = set()
        for assignment in wave["assignments"]:
            if not isinstance(assignment, dict):
                valid = False
                continue
            assignment_id = assignment.get("assignment_id")
            unit_id = assignment.get("id")
            role = assignment.get("role")
            permissions = assignment.get("permissions")
            ownership = assignment.get("ownership")
            dependencies = assignment.get("dependencies")
            depth = assignment.get("delegation_depth")
            record = {
                "assignment_id": assignment_id,
                "unit_id": unit_id,
                "role": role,
                "wave_index": wave_index,
                "wave_kind": wave_kind,
                "wave_parallel": wave_parallel,
                "dependencies": dependencies,
                "ownership": ownership,
                "permissions": permissions,
                "delegation_depth": depth,
                "may_spawn": assignment.get("may_spawn"),
                "may_verify_parent": assignment.get("may_verify_parent"),
                "must_be_distinct_from": assignment.get("must_be_distinct_from", []),
            }
            planned.append(record)
            if (
                not isinstance(assignment_id, str)
                or not assignment_id
                or not isinstance(unit_id, str)
                or not unit_id
                or not isinstance(role, str)
                or permissions not in {"read-only", "write-owned-paths"}
                or not isinstance(ownership, list)
                or not all(isinstance(path, str) and path for path in ownership)
                or not isinstance(dependencies, list)
                or not all(isinstance(item, str) and item for item in dependencies)
                or not isinstance(depth, int)
                or isinstance(depth, bool)
                or depth < 1
                or assignment.get("may_spawn") is not False
                or assignment.get("may_verify_parent") is not False
                or not set(dependencies).issubset(prior_unit_ids)
                or (permissions == "write-owned-paths" and not ownership)
            ):
                valid = False
            wave_unit_ids.add(unit_id) if isinstance(unit_id, str) else None
        prior_unit_ids.update(wave_unit_ids)

    assignment_ids = [item["assignment_id"] for item in planned]
    unit_ids = [item["unit_id"] for item in planned]
    if len(assignment_ids) != len(set(assignment_ids)) or len(unit_ids) != len(
        set(unit_ids)
    ):
        valid = False
    write_paths: list[tuple[str, str]] = []
    for item in planned:
        if item["permissions"] != "write-owned-paths":
            continue
        for path in item["ownership"]:
            normalized = path.replace("\\", "/").strip("/").casefold()
            if any(
                other_id != item["assignment_id"]
                and (
                    normalized == other
                    or normalized.startswith(other + "/")
                    or other.startswith(normalized + "/")
                )
                for other_id, other in write_paths
            ):
                valid = False
            write_paths.append((item["assignment_id"], normalized))

    lifecycle_by_id = {item.get("assignment_id"): item for item in lifecycle}
    bindings: list[dict[str, Any]] = []
    actor_ids: list[str] = []
    for item in planned:
        observed = lifecycle_by_id.get(item["assignment_id"])
        if not isinstance(observed, dict):
            valid = False
            continue
        binding = {
            "assignment_id": item["assignment_id"],
            "actor_id": observed.get("actor_id"),
            "role_observed": observed.get("role"),
            "parent_id": observed.get("parent_id"),
            "delegation_depth": observed.get("delegation_depth"),
            "task_name": observed.get("task_name"),
            "binding_provenance": observed.get("binding_provenance"),
        }
        bindings.append(binding)
        if (
            (
                binding["role_observed"] is not None
                and binding["role_observed"] != item["role"]
            )
            or not isinstance(binding["actor_id"], str)
            or not binding["actor_id"]
            or not isinstance(binding["parent_id"], str)
            or not binding["parent_id"]
            or binding["delegation_depth"] != item["delegation_depth"]
        ):
            valid = False
        actor_ids.append(binding["actor_id"])
    if len(actor_ids) != len(set(actor_ids)):
        valid = False

    mode = observed_plan.get("mode")
    verifier_items = [item for item in planned if item["role"] == "verifier"]
    write_items = [
        item for item in planned if item["permissions"] == "write-owned-paths"
    ]
    if mode == "parallel-read-only":
        valid = valid and len(planned) >= 2 and not write_items and not verifier_items
    elif mode == "parallel-packets":
        implementation_waves = {
            item["wave_index"] for item in write_items if item["wave_parallel"] is True
        }
        valid = valid and len(write_items) >= 2 and len(implementation_waves) == 1
    elif mode == "staged-verify":
        verifier_only = (
            len(planned) == 1
            and len(verifier_items) == 1
            and verifier_items[0]["wave_kind"] == "verification"
            and verifier_items[0]["wave_index"] == 0
            and verifier_items[0]["dependencies"] == []
            and verifier_items[0]["permissions"] == "read-only"
            and verifier_items[0]["ownership"] == []
        )
        verifier_after_writes = (
            bool(write_items)
            and len(verifier_items) == 1
            and verifier_items[0]["wave_kind"] == "verification"
            and verifier_items[0]["wave_index"]
            > max(item["wave_index"] for item in write_items)
            and set(verifier_items[0]["dependencies"])
            == {item["unit_id"] for item in write_items}
        )
        valid = valid and (verifier_only or verifier_after_writes)
    elif mode == "solo":
        valid = valid and not planned and not lifecycle
    else:
        valid = False
    return planned, bindings, bool(valid)


def _normalized_task_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _bind_rollout_assignments(
    observed_plan: dict[str, Any] | None, lifecycle: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    bound = [dict(item) for item in lifecycle]
    if observed_plan is None:
        return bound
    aliases: dict[str, str] = {}
    for wave in observed_plan.get("waves", []):
        if not isinstance(wave, dict):
            continue
        for assignment in wave.get("assignments", []):
            if not isinstance(assignment, dict):
                continue
            assignment_id = assignment.get("assignment_id")
            for alias in (assignment.get("id"), assignment_id):
                if isinstance(alias, str) and isinstance(assignment_id, str):
                    normalized = _normalized_task_name(alias)
                    if normalized in aliases and aliases[normalized] != assignment_id:
                        aliases[normalized] = ""
                    else:
                        aliases[normalized] = assignment_id
    for item in bound:
        task_name = item.get("task_name")
        if isinstance(task_name, str):
            item["assignment_id"] = aliases.get(_normalized_task_name(task_name))
    return bound


def classify_agent_decision(
    parsed: dict[str, Any], controller_mode: str
) -> dict[str, Any]:
    """Classify an explicit plan or the focused no-agent fast path."""
    observed_plan = parsed["agent_plans"][-1] if parsed["agent_plans"] else None
    suppressed_plan = observed_plan if controller_mode == "forced-solo" else None
    active_plan = None if controller_mode == "forced-solo" else observed_plan
    implicit_solo = (
        active_plan is None
        and parsed["agent_spawns"] == 0
        and not parsed.get("agent_lifecycle", [])
    )
    selected_mode = (
        "solo"
        if controller_mode == "forced-solo"
        else active_plan.get("mode")
        if active_plan
        else ("solo" if implicit_solo else None)
    )
    lifecycle = _bind_rollout_assignments(
        active_plan, parsed.get("agent_lifecycle", [])
    )
    planned, lifecycle_bindings, semantic_binding = _execution_semantics(
        active_plan, lifecycle
    )
    planned_ids = [item.get("assignment_id") for item in planned]
    valid_plan_ids = all(
        isinstance(item, str) and item for item in planned_ids
    ) and len(planned_ids) == len(set(planned_ids))
    lifecycle_with_invalid_ids = [
        item
        for item in lifecycle
        if any(
            phase in item.get("phases", []) for phase in ("spawned", "joined", "result")
        )
        and not (isinstance(item.get("assignment_id"), str) and item["assignment_id"])
    ]

    def phase_assignment_ids(phase: str) -> list[str]:
        return sorted(
            item["assignment_id"]
            for item in lifecycle
            if phase in item.get("phases", [])
            and isinstance(item.get("assignment_id"), str)
            and item["assignment_id"]
        )

    spawned_ids = phase_assignment_ids("spawned")
    joined_ids = phase_assignment_ids("joined")
    result_ids = phase_assignment_ids("result")
    usage_by_assignment = {
        item["assignment_id"]: item.get("usage")
        for item in lifecycle
        if item.get("usage") is not None
        and isinstance(item.get("assignment_id"), str)
        and item["assignment_id"]
    }
    exact_lifecycle = (
        valid_plan_ids
        and not lifecycle_with_invalid_ids
        and sorted(planned_ids) == spawned_ids == joined_ids == result_ids
        and set(usage_by_assignment) == set(planned_ids)
        and semantic_binding
    )
    host_persistent = bool(parsed.get("parent_thread_id")) and not parsed.get(
        "host_errors"
    )
    explicit_plan_complete = active_plan is not None and (
        selected_mode == "solo"
        and not planned
        and not lifecycle
        and host_persistent
        or selected_mode != "solo"
        and bool(planned)
        and exact_lifecycle
        and parsed["usage_includes_subagents"]
        and host_persistent
    )
    complete = (
        parsed["agent_spawns"] == 0
        and not lifecycle
        and selected_mode == "solo"
        and host_persistent
        if controller_mode == "forced-solo"
        else (implicit_solo and host_persistent) or explicit_plan_complete
    )
    telemetry_observation_complete = bool(
        host_persistent
        and selected_mode in AGENT_PLAN_MODES
        and not lifecycle_with_invalid_ids
        and spawned_ids == joined_ids == result_ids
        and set(usage_by_assignment) == set(spawned_ids)
    )
    executed_mode = selected_mode if complete else "solo" if not spawned_ids else None
    outcome = (
        "completed"
        if complete and selected_mode == executed_mode
        else "degraded"
        if selected_mode in AGENT_PLAN_MODES and telemetry_observation_complete
        else "invalid"
    )
    telemetry_status = "complete" if telemetry_observation_complete else "incomplete"
    controller_compliant = complete if telemetry_observation_complete else None
    plan_sha256 = (
        hashlib.sha256(
            json.dumps(active_plan, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if active_plan is not None
        else None
    )
    suppressed_plan_sha256 = (
        hashlib.sha256(
            json.dumps(suppressed_plan, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if suppressed_plan is not None
        else None
    )
    execution_receipt = {
        "schema_version": 3,
        "plan_sha256": plan_sha256,
        "selected_mode": selected_mode,
        "executed_mode": executed_mode,
        "outcome": outcome,
        "parent_thread_id": parsed.get("parent_thread_id"),
        "planned_assignment_ids": planned_ids,
        "planned_assignments": planned,
        "lifecycle_bindings": lifecycle_bindings,
        "semantic_binding": semantic_binding,
        "spawned_assignment_ids": spawned_ids,
        "joined_assignment_ids": joined_ids,
        "result_assignment_ids": result_ids,
        "invalid_lifecycle_assignment_count": len(lifecycle_with_invalid_ids),
        "descendant_usage": usage_by_assignment,
        "descendant_total_tokens": sum(
            item["input_tokens"] + item["output_tokens"]
            for item in usage_by_assignment.values()
        ),
        "host_errors": list(parsed.get("host_errors", [])),
        "telemetry_observation_complete": telemetry_observation_complete,
        "telemetry_status": telemetry_status,
        "controller_compliant": controller_compliant,
        "plan_adherent": (
            controller_compliant if controller_mode == "adaptive" else None
        ),
        "suppressed_policy_mode": (
            suppressed_plan.get("mode") if suppressed_plan is not None else None
        ),
        "suppressed_policy_plan_sha256": suppressed_plan_sha256,
        "override_reason": "forced-solo" if suppressed_plan is not None else None,
        "complete": complete,
    }
    return {
        "observed_plan": observed_plan,
        "selected_mode": selected_mode,
        "executed_mode": executed_mode,
        "actual_mode": executed_mode,
        "outcome": outcome,
        "decision_observation": (
            "forced-solo-override"
            if suppressed_plan is not None
            else "explicit-agent-plan"
            if active_plan is not None
            else "implicit-solo-no-agent-events"
            if implicit_solo
            else "missing"
        ),
        "planned_assignment_count": len(planned),
        "agent_execution_receipt": execution_receipt,
        "telemetry_observation_complete": telemetry_observation_complete,
        "complete": complete,
    }


def normalize_quality_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LiveEvaluationError("quality check must return one JSON object")
    score = value.get("score")
    evidence = value.get("evidence")
    critical = value.get("critical_errors")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not 0 <= score <= 100
    ):
        raise LiveEvaluationError("quality score must be a number from 0 to 100")
    if (
        not isinstance(evidence, list)
        or not evidence
        or not all(isinstance(item, str) and item for item in evidence)
    ):
        raise LiveEvaluationError("quality evidence must be a non-empty string list")
    if not isinstance(critical, list) or not all(
        isinstance(item, str) and item for item in critical
    ):
        raise LiveEvaluationError("quality critical_errors must be a string list")
    return {
        "score": float(score),
        "evidence": list(evidence),
        "critical_errors": list(critical),
    }


def aggregate_results(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def metric_value(result: dict[str, Any], field: str) -> float:
        if field == "cached_input_tokens" and field not in result:
            return max(
                0,
                result["total_tokens"]
                - result["output_tokens"]
                - result["fresh_input_tokens"],
            )
        return result[field]

    by_repetition: dict[int, dict[str, dict[str, Any]]] = {}
    for result in results:
        repetition = result.get("repetition")
        variant = result.get("variant")
        if not isinstance(repetition, int) or variant not in {"baseline", "candidate"}:
            raise LiveEvaluationError(
                "results contain an invalid repetition or variant"
            )
        variants = by_repetition.setdefault(repetition, {})
        if variant in variants:
            raise LiveEvaluationError("results contain a duplicate arm")
        variants[variant] = result
    if not by_repetition or any(
        set(variants) != {"baseline", "candidate"}
        for variants in by_repetition.values()
    ):
        raise LiveEvaluationError("results must contain complete paired arms")

    successful = {
        repetition: variants
        for repetition, variants in by_repetition.items()
        if variants["baseline"].get("success") is True
        and variants["candidate"].get("success") is True
    }
    metrics: dict[str, Any] = {}
    for field in (
        "total_tokens",
        "cached_input_tokens",
        "fresh_input_tokens",
        "output_tokens",
        "tool_calls",
        "elapsed_seconds",
    ):
        if successful:
            baseline = statistics.median(
                metric_value(variants["baseline"], field)
                for variants in successful.values()
            )
            candidate = statistics.median(
                metric_value(variants["candidate"], field)
                for variants in successful.values()
            )
            metrics[field] = {
                "baseline_median": baseline,
                "candidate_median": candidate,
                "delta_percent": round(((candidate - baseline) / baseline) * 100, 3)
                if baseline
                else None,
            }
        else:
            metrics[field] = {
                "baseline_median": None,
                "candidate_median": None,
                "delta_percent": None,
            }
    pair_total_deltas = [
        round(
            (
                variants["candidate"]["total_tokens"]
                - variants["baseline"]["total_tokens"]
            )
            / variants["baseline"]["total_tokens"]
            * 100,
            3,
        )
        for variants in successful.values()
        if variants["baseline"]["total_tokens"]
    ]
    return {
        "pair_count": len(by_repetition),
        "successful_pair_count": len(successful),
        "failed_pair_count": len(by_repetition) - len(successful),
        "all_pairs_successful": all(
            variants[arm]["success"]
            for variants in by_repetition.values()
            for arm in ("baseline", "candidate")
        ),
        "metrics": metrics,
        "pair_total_token_delta_percent": pair_total_deltas,
        "worst_pair_total_token_delta_percent": (
            max(pair_total_deltas) if pair_total_deltas else None
        ),
    }
