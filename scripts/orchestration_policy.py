#!/usr/bin/env python3
"""Select execution intensity and conservative host-agent plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any


REQUEST_MODES = {"answer", "diagnose", "change", "monitor"}
DURABLE_SIGNALS = (
    ("multi_turn_expected", "the work is expected to span multiple turns"),
    ("compaction_risk", "the work must remain recoverable across compaction"),
    ("resumable_required", "the work explicitly requires resumable state"),
    ("durable_evidence_required", "the outcome requires durable evidence receipts"),
)
PHASES = {"discover", "diagnose", "implement", "verify"}
AUTHORIZATIONS = {"read-only", "change"}
AGENT_ROLES = {"investigator", "researcher", "reviewer", "test-writer", "executor"}
READ_ONLY_ROLES = {"investigator", "researcher", "reviewer"}
PROFILE = "automatic-conservative-balanced"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
AGENT_PLAN_INTERFACE_VERSION = 1
AGENT_PLAN_INPUT_VERSIONS = (1, 2)


class OrchestrationError(ValueError):
    """Raised when task signals violate the orchestration contract."""


def _boolean(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise OrchestrationError(f"{field} must be boolean")
    return value


def _non_negative_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OrchestrationError(f"{field} must be a non-negative integer")
    return value


def _string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise OrchestrationError(f"{field} must be a non-empty string")
    return value.strip()


def _string_list(value: object, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise OrchestrationError(f"{field} must be a string list")
    result = sorted(set(item.strip() for item in value))
    if not allow_empty and not result:
        raise OrchestrationError(f"{field} must not be empty")
    return result


def select_intensity(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the stable v1 execution policy and its observable reasons."""
    if payload.get("schema_version") != 1:
        raise OrchestrationError("schema_version must be 1")
    request_mode = payload.get("request_mode")
    if request_mode not in REQUEST_MODES:
        raise OrchestrationError(
            "request_mode must be answer, diagnose, change, or monitor"
        )

    estimated_steps = _non_negative_int(payload, "estimated_steps")
    affected_files = _non_negative_int(payload, "affected_files")
    unclear_context = _boolean(payload, "unclear_context")
    cross_cutting = _boolean(payload, "cross_cutting")
    signals = {field: _boolean(payload, field) for field, _ in DURABLE_SIGNALS}

    reasons: list[str] = []
    durable_reasons = [reason for field, reason in DURABLE_SIGNALS if signals[field]]
    if durable_reasons:
        intensity = "durable"
        reasons.extend(durable_reasons)
    else:
        if cross_cutting:
            reasons.append("the task crosses implementation boundaries")
        if unclear_context:
            reasons.append("the relevant context is not yet established")
        if affected_files >= 2:
            reasons.append("the task affects multiple files")
        if estimated_steps >= 3:
            reasons.append("the task has at least three expected steps")
        if reasons:
            intensity = "standard"
        else:
            intensity = "focused"
            reasons.append("the task is short, local, and recoverable in one pass")

    process = {
        "progressive_context": intensity in {"standard", "durable"},
        "external_state": intensity == "durable",
        "evidence_receipts": intensity == "durable",
        "memory_retrieval": False,
        "delegation": False,
    }
    abstained = [name for name, enabled in process.items() if not enabled]
    implementation_authorized = request_mode == "change"
    if request_mode == "diagnose":
        reasons.append("diagnosis preserves investigation-only scope")

    return {
        "schema_version": 1,
        "intensity": intensity,
        "request_mode": request_mode,
        "reasons": reasons,
        "process": process,
        "abstained_process": abstained,
        "implementation_authorized": implementation_authorized,
    }


def agent_plan_template(schema_version: int = 2) -> dict[str, Any]:
    """Return the compact, versioned planning-input interface."""
    if schema_version not in AGENT_PLAN_INPUT_VERSIONS:
        raise OrchestrationError("agent-plan template version must be 1 or 2")
    unit = {
        "id": "lane-a",
        "role": "investigator",
        "objective": "Produce one independent evidence-backed finding",
        "context": ["minimum task context"],
        "owned_paths": [],
        "dependencies": [],
        "read_only": True,
        "ready": True,
        "distinct_output": True,
        "expected_output": "Evidence-backed finding",
        "check": ["python", "-m", "unittest", "tests.test_target"],
        "stop_conditions": ["Stop if the assigned boundary is invalid"],
        "red_test_possible": False,
        "depth": 1,
    }
    template: dict[str, Any] = {
        "schema_version": schema_version,
        "request_mode": "diagnose",
        "phase": "diagnose",
        "authorization": "read-only",
        "boundaries_clear": True,
        "cheap_local_step_available": False,
        "symptom_reproduced": True,
        "durable_or_release_critical": False,
        "quality_claim": False,
        "delegated_change": False,
        "packet_plan_valid": False,
        "available_agent_slots": 4,
        "current_depth": 0,
        "completed_unit_ids": [],
        "units": [unit, {**unit, "id": "lane-b"}],
    }
    if schema_version == 2:
        template.update(
            {
                "retry_record": None,
                "verification_check": [
                    "python",
                    "-m",
                    "unittest",
                    "tests.test_target",
                ],
            }
        )
    else:
        template.update(
            {
                "previous_worker_failed": False,
                "failure_classified": False,
                "retry_attempts": 0,
            }
        )
    return {
        "schema_version": AGENT_PLAN_INTERFACE_VERSION,
        "kind": "agent_plan_input_interface",
        "planner_input_schema_version": schema_version,
        "supported_planner_input_schema_versions": list(AGENT_PLAN_INPUT_VERSIONS),
        "allowed_values": {
            "request_mode": sorted(REQUEST_MODES),
            "phase": sorted(PHASES),
            "authorization": sorted(AUTHORIZATIONS),
            "unit_role": sorted(AGENT_ROLES),
        },
        "field_rules": {
            "enum_values_are_exact": True,
            "verifier_units_are_synthetic": (
                "do not put role=verifier in units; provide verification_check and "
                "the planner creates the fresh verifier"
            ),
        },
        "commands": {
            "discover": "orchestration_policy.py --agent-plan-template 2 --json",
            "plan": "orchestration_policy.py --agent-plan - --json",
        },
        "execution_semantics": {
            "selected_mode": "mode chosen by this planner",
            "executed_mode": "mode observed by the host; null in a new plan",
            "outcome": "planned until a host receipt records completed, degraded, failed, or blocked",
            "degradation": "null unless execution differs from selection; record the exact cause in the host receipt",
        },
        "template": template,
    }


def _solo_plan(
    reason: str,
    *,
    valid_input: bool,
    durable: bool = False,
    stop_conditions: list[str] | None = None,
    schema_version: int = 1,
) -> dict[str, Any]:
    plan = {
        "schema_version": schema_version,
        "kind": "agent_plan",
        "profile": PROFILE,
        "valid_input": valid_input,
        "mode": "solo",
        "selected_mode": "solo",
        "executed_mode": None,
        "outcome": "planned",
        "degradation": None,
        "spawn_count": 0,
        "total_planned_agents": 0,
        "max_concurrent_workers": 0,
        "max_depth": 0,
        "reserve_verifier_slot": False,
        "waves": [],
        "reasons": [reason],
        "abstentions": ["delegation", "external_agent_state"],
        "retry_policy": {
            "max_retries_per_assignment": 1,
            "retry_allowed": False,
            "fallback": "main-agent-absorbs-or-reports-blocker",
        },
        "stop_conditions": stop_conditions or [reason],
        "receipt_policy": {
            "emit_json": durable,
            "external_state_required": durable,
            "end_to_end_improvement_proven": False,
        },
    }
    plan["plan_id"] = _content_id("plan", plan)
    return plan


def _content_id(prefix: str, value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()}"


def _normalize_owned_path(value: str, field: str) -> str:
    raw = value.strip()
    if raw.startswith(("\\\\", "//")) or re.match(r"^[A-Za-z]:", raw):
        raise OrchestrationError(f"{field} contains an unsafe path")
    normalized = raw.replace("\\", "/").rstrip("/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {".", ".."} for part in path.parts)
        or normalized in {".", "/"}
    ):
        raise OrchestrationError(f"{field} contains an unsafe path")
    return str(path)


def _paths_overlap(left: str, right: str) -> bool:
    left_parts = tuple(part.casefold() for part in PurePosixPath(left).parts)
    right_parts = tuple(part.casefold() for part in PurePosixPath(right).parts)
    shortest = min(len(left_parts), len(right_parts))
    return left_parts[:shortest] == right_parts[:shortest]


def _normalize_red_observation(
    value: object, index: int, *, schema_version: int, legacy_possible: bool
) -> dict[str, Any] | None:
    if schema_version == 1:
        if not legacy_possible:
            return None
        return {
            "observed": True,
            "command": ["legacy-v1-red-check"],
            "exit_code": 1,
            "evidence": "legacy v1 red_test_possible signal",
            "state_sha256": "0" * 64,
        }
    if value is None:
        return None
    if not isinstance(value, dict):
        raise OrchestrationError(f"units[{index}].red_observation must be an object")
    observed = _boolean(value, "observed")
    command = value.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) and item for item in command)
    ):
        raise OrchestrationError(
            f"units[{index}].red_observation.command must be a non-empty argv"
        )
    exit_code = value.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise OrchestrationError(
            f"units[{index}].red_observation.exit_code must be an integer"
        )
    evidence = _string(value, "evidence")
    state_sha256 = _string(value, "state_sha256").lower()
    if not SHA256_RE.fullmatch(state_sha256):
        raise OrchestrationError(
            f"units[{index}].red_observation.state_sha256 must be sha256"
        )
    if not observed or exit_code == 0:
        raise OrchestrationError(
            f"units[{index}].red_observation must record an observed failing check"
        )
    return {
        "observed": observed,
        "command": command,
        "exit_code": exit_code,
        "evidence": evidence,
        "state_sha256": state_sha256,
    }


def _normalize_unit(
    value: object, index: int, *, schema_version: int
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OrchestrationError(f"units[{index}] must be an object")
    unit_id = _string(value, "id")
    role = _string(value, "role")
    if role not in AGENT_ROLES:
        raise OrchestrationError(f"units[{index}].role is unsupported")
    depth = _non_negative_int(value, "depth")
    if depth not in {1, 2}:
        raise OrchestrationError(f"units[{index}].depth must be 1 or 2")
    read_only = _boolean(value, "read_only")
    if role in READ_ONLY_ROLES and not read_only:
        raise OrchestrationError(f"units[{index}] read-only role cannot write")
    if role in {"executor", "test-writer"} and read_only:
        raise OrchestrationError(f"units[{index}] write role cannot be read-only")
    if depth == 2 and (not read_only or role not in READ_ONLY_ROLES):
        raise OrchestrationError(
            f"units[{index}] depth-2 work must use a read-only role"
        )
    owned_paths = [
        _normalize_owned_path(item, f"units[{index}].owned_paths")
        for item in _string_list(
            value.get("owned_paths"),
            f"units[{index}].owned_paths",
            allow_empty=read_only,
        )
    ]
    check = value.get("check")
    if (
        not isinstance(check, list)
        or not check
        or not all(isinstance(item, str) and item for item in check)
    ):
        raise OrchestrationError(f"units[{index}].check must be a non-empty argv")
    if schema_version == 1:
        legacy_red_possible = _boolean(value, "red_test_possible")
    else:
        raw_legacy_red = value.get("red_test_possible", False)
        if not isinstance(raw_legacy_red, bool):
            raise OrchestrationError(
                f"units[{index}].red_test_possible must be boolean"
            )
        legacy_red_possible = raw_legacy_red
    red_observation = _normalize_red_observation(
        value.get("red_observation"),
        index,
        schema_version=schema_version,
        legacy_possible=legacy_red_possible,
    )
    if (
        role == "test-writer"
        and red_observation is not None
        and schema_version == 2
        and red_observation["command"] != check
    ):
        raise OrchestrationError(
            f"units[{index}].red_observation.command must match the assigned check"
        )
    return {
        "id": unit_id,
        "role": role,
        "objective": _string(value, "objective"),
        "context": _string_list(value.get("context"), f"units[{index}].context"),
        "owned_paths": sorted(set(owned_paths)),
        "dependencies": _string_list(
            value.get("dependencies"),
            f"units[{index}].dependencies",
            allow_empty=True,
        ),
        "read_only": read_only,
        "ready": _boolean(value, "ready"),
        "distinct_output": _boolean(value, "distinct_output"),
        "expected_output": _string(value, "expected_output"),
        "check": check,
        "stop_conditions": _string_list(
            value.get("stop_conditions"), f"units[{index}].stop_conditions"
        ),
        "red_test_possible": legacy_red_possible,
        "red_observation": red_observation,
        "depth": depth,
    }


def _validate_units(
    payload: dict[str, Any], *, schema_version: int
) -> tuple[list[dict[str, Any]], set[str]]:
    raw_units = payload.get("units")
    if not isinstance(raw_units, list):
        raise OrchestrationError("units must be a list")
    units = [
        _normalize_unit(value, index, schema_version=schema_version)
        for index, value in enumerate(raw_units)
    ]
    identifiers = [unit["id"] for unit in units]
    if len(identifiers) != len(set(identifiers)):
        raise OrchestrationError("unit ids must be unique")
    known = set(identifiers)
    for unit in units:
        unknown = set(unit["dependencies"]) - known
        if unknown or unit["id"] in unit["dependencies"]:
            raise OrchestrationError(f"unit {unit['id']} has invalid dependencies")
    dependencies = {unit["id"]: unit["dependencies"] for unit in units}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(unit_id: str) -> None:
        if unit_id in visiting:
            raise OrchestrationError("unit dependencies must be acyclic")
        if unit_id in visited:
            return
        visiting.add(unit_id)
        for dependency in dependencies[unit_id]:
            visit(dependency)
        visiting.remove(unit_id)
        visited.add(unit_id)

    for unit_id in sorted(known):
        visit(unit_id)
    completed = set(
        _string_list(
            payload.get("completed_unit_ids"), "completed_unit_ids", allow_empty=True
        )
    )
    if completed - known:
        raise OrchestrationError("completed_unit_ids contains an unknown unit")
    return sorted(units, key=lambda item: item["id"]), completed


def _assignment(unit: dict[str, Any], *, may_write: bool) -> dict[str, Any]:
    assignment = {
        "id": unit["id"],
        "role": unit["role"],
        "objective": unit["objective"],
        "context": unit["context"],
        "ownership": unit["owned_paths"],
        "permissions": "write-owned-paths" if may_write else "read-only",
        "expected_output": unit["expected_output"],
        "check": unit["check"],
        "dependencies": unit["dependencies"],
        "stop_conditions": unit["stop_conditions"],
        "delegation_depth": unit["depth"],
        "may_spawn": False,
        "may_verify_parent": False,
    }
    assignment["assignment_id"] = _content_id("assignment", assignment)
    return assignment


def _argv(value: object, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise OrchestrationError(f"{field} must be a non-empty argv")
    return list(value)


def _topological_selection(
    candidates: list[dict[str, Any]], completed: set[str], capacity: int
) -> tuple[
    list[dict[str, Any]],
    list[list[dict[str, Any]]],
    list[dict[str, Any]],
]:
    remaining = {unit["id"]: unit for unit in candidates}
    selected: list[dict[str, Any]] = []
    layers: list[list[dict[str, Any]]] = []
    satisfied = set(completed)
    if capacity <= 0:
        return selected, layers, list(remaining.values())
    while remaining:
        layer = [
            unit
            for unit in remaining.values()
            if set(unit["dependencies"]).issubset(satisfied)
        ]
        if not layer:
            break
        layer = sorted(layer, key=lambda item: item["id"])
        layer = layer[:capacity]
        layers.append(layer)
        selected.extend(layer)
        for unit in layer:
            remaining.pop(unit["id"])
            satisfied.add(unit["id"])
    unscheduled = [remaining[unit_id] for unit_id in sorted(remaining)]
    return selected, layers, unscheduled


def _retry_record(
    payload: dict[str, Any],
    *,
    schema_version: int,
    units: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if schema_version == 1:
        previous_worker_failed = _boolean(payload, "previous_worker_failed")
        failure_classified = _boolean(payload, "failure_classified")
        attempts = _non_negative_int(payload, "retry_attempts")
        if failure_classified and not previous_worker_failed:
            raise OrchestrationError(
                "failure_classified requires previous_worker_failed"
            )
        if not previous_worker_failed:
            return None
        if not failure_classified:
            return {"invalid_reason": "classify the worker failure before retrying"}
        failed_id = payload.get("failed_assignment_id")
        if not isinstance(failed_id, str) or not failed_id:
            failed_id = units[0]["id"] if units else ""
        return {
            "failed_unit_id": failed_id,
            "failure_class": "legacy-v1-classified",
            "evidence": "legacy v1 classified failure",
            "attempts": attempts,
        }

    value = payload.get("retry_record")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise OrchestrationError("retry_record must be an object or null")
    assignment_id = _string(value, "failed_assignment_id")
    failure_class = _string(value, "failure_class")
    evidence = _string(value, "evidence")
    attempts = _non_negative_int(value, "attempts")
    matches = [
        unit
        for unit in units
        if _assignment(unit, may_write=not unit["read_only"])["assignment_id"]
        == assignment_id
    ]
    if len(matches) != 1:
        raise OrchestrationError(
            "retry_record does not identify exactly one assignment"
        )
    return {
        "failed_unit_id": matches[0]["id"],
        "failed_assignment_id": assignment_id,
        "failure_class": failure_class,
        "evidence": evidence,
        "attempts": attempts,
    }


def _select_agent_plan(payload: dict[str, Any]) -> dict[str, Any]:
    schema_version = payload.get("schema_version")
    if schema_version not in {1, 2}:
        raise OrchestrationError("schema_version must be 1 or 2")
    request_mode = payload.get("request_mode")
    if request_mode not in REQUEST_MODES:
        raise OrchestrationError("request_mode is unsupported")
    phase = payload.get("phase")
    if phase not in PHASES:
        raise OrchestrationError("phase is unsupported")
    authorization = payload.get("authorization")
    if authorization not in AUTHORIZATIONS:
        raise OrchestrationError("authorization is unsupported")

    boundaries_clear = _boolean(payload, "boundaries_clear")
    cheap_local_step = _boolean(payload, "cheap_local_step_available")
    symptom_reproduced = _boolean(payload, "symptom_reproduced")
    durable = _boolean(payload, "durable_or_release_critical")
    quality_claim = _boolean(payload, "quality_claim")
    delegated_change = _boolean(payload, "delegated_change")
    packet_plan_valid = _boolean(payload, "packet_plan_valid")
    available_slots = _non_negative_int(payload, "available_agent_slots")
    current_depth = _non_negative_int(payload, "current_depth")
    units, completed = _validate_units(payload, schema_version=schema_version)
    retry_record = _retry_record(payload, schema_version=schema_version, units=units)

    if current_depth > 0:
        return _solo_plan(
            "workers cannot spawn descendants; the main agent owns all delegation",
            valid_input=True,
            durable=durable,
            schema_version=schema_version,
        )
    if not boundaries_clear:
        return _solo_plan(
            "work boundaries are not yet clear",
            valid_input=True,
            durable=durable,
            schema_version=schema_version,
        )
    if cheap_local_step:
        return _solo_plan(
            "a cheaper local discriminating step should run before delegation",
            valid_input=True,
            durable=durable,
            schema_version=schema_version,
        )
    if phase == "diagnose" and not symptom_reproduced:
        return _solo_plan(
            "reproduce the symptom before splitting causal investigation",
            valid_input=True,
            durable=durable,
            schema_version=schema_version,
        )
    if retry_record and "invalid_reason" in retry_record:
        return _solo_plan(
            retry_record["invalid_reason"],
            valid_input=True,
            durable=durable,
            schema_version=schema_version,
        )
    if retry_record and retry_record["attempts"] >= 1:
        return _solo_plan(
            "the single worker retry is exhausted",
            valid_input=True,
            durable=durable,
            stop_conditions=[
                "main agent absorbs the assignment or reports a genuine blocker"
            ],
            schema_version=schema_version,
        )

    candidates = [
        unit
        for unit in units
        if unit["id"] not in completed and unit["ready"] and unit["distinct_output"]
    ]
    if phase in {"discover", "diagnose"}:
        candidates = [unit for unit in candidates if unit["read_only"]]
    elif phase == "implement":
        candidates = [unit for unit in candidates if not unit["read_only"]]
    else:
        candidates = []

    verifier_required = durable or quality_claim or delegated_change
    if phase == "verify":
        verifier_required = True
    if retry_record and schema_version == 2:
        verifier_required = True
    if available_slots < 2:
        return _solo_plan(
            "the host has no worker slot beyond the main agent",
            valid_input=True,
            durable=durable,
            schema_version=schema_version,
        )
    if retry_record and schema_version == 2:
        candidates = [
            unit for unit in candidates if unit["id"] == retry_record["failed_unit_id"]
        ]
        if not candidates:
            return _solo_plan(
                "the retry assignment is not eligible in the current phase",
                valid_input=True,
                durable=durable,
                schema_version=schema_version,
            )
    worker_capacity = min(max(available_slots - 1, 0), 3, len(candidates))
    selected, layers, unscheduled = _topological_selection(
        candidates, completed, worker_capacity
    )
    if unscheduled:
        blocked_ids = ", ".join(unit["id"] for unit in unscheduled)
        return _solo_plan(
            f"eligible units cannot be fully scheduled because dependencies are "
            f"unsatisfied: {blocked_ids}",
            valid_input=True,
            durable=durable,
            schema_version=schema_version,
        )

    single_worker_staged_verify = (
        phase != "verify"
        and len(selected) == 1
        and verifier_required
        and schema_version == 2
        and not retry_record
    )
    if (
        phase != "verify"
        and len(selected) < 2
        and not retry_record
        and not single_worker_staged_verify
    ):
        return _solo_plan(
            "fewer than two executable workers do not justify coordination",
            valid_input=True,
            durable=durable,
            schema_version=schema_version,
        )

    writes = bool(selected) and any(not unit["read_only"] for unit in selected)
    if writes:
        if request_mode != "change" or authorization != "change":
            return _solo_plan(
                "parallel writes lack explicit change authorization",
                valid_input=True,
                durable=durable,
                schema_version=schema_version,
            )
        if not packet_plan_valid and not single_worker_staged_verify:
            return _solo_plan(
                "parallel writes require a validated packet plan",
                valid_input=True,
                durable=durable,
                schema_version=schema_version,
            )
        for layer in layers:
            for index, left in enumerate(layer):
                for right in layer[index + 1 :]:
                    if any(
                        _paths_overlap(left_path, right_path)
                        for left_path in left["owned_paths"]
                        for right_path in right["owned_paths"]
                    ):
                        return _solo_plan(
                            "parallel write ownership overlaps",
                            valid_input=True,
                            durable=durable,
                            schema_version=schema_version,
                        )
        test_writers = [unit for unit in selected if unit["role"] == "test-writer"]
        executors = [unit for unit in selected if unit["role"] == "executor"]
        if any(unit["red_observation"] is None for unit in test_writers):
            return _solo_plan(
                "test-writer delegation requires an observed structured RED",
                valid_input=True,
                durable=durable,
                schema_version=schema_version,
            )
        for test_writer in test_writers:
            for executor in executors:
                if test_writer["id"] in executor["dependencies"] and any(
                    _paths_overlap(test_path, source_path)
                    for test_path in test_writer["owned_paths"]
                    for source_path in executor["owned_paths"]
                ):
                    return _solo_plan(
                        "test-writer and dependent executor require separate ownership",
                        valid_input=True,
                        durable=durable,
                        schema_version=schema_version,
                    )
    else:
        test_writers = []
        executors = []

    waves: list[dict[str, Any]] = []
    for layer in layers:
        if writes:
            kind = (
                "test-first"
                if all(unit["role"] == "test-writer" for unit in layer)
                else "implementation"
            )
        else:
            kind = "read-only-investigation"
        waves.append(
            {
                "kind": kind,
                "parallel": len(layer) > 1,
                "assignments": [_assignment(unit, may_write=writes) for unit in layer],
            }
        )

    if writes:
        verifier_required = True
    if verifier_required:
        if schema_version == 2:
            verification_check = _argv(
                payload.get("verification_check"), "verification_check"
            )
        else:
            verification_check = _argv(
                payload.get(
                    "verification_check",
                    selected[0]["check"] if selected else ["python", "-m", "unittest"],
                ),
                "verification_check",
            )
        if verification_check == ["use", "declared", "verification", "target"]:
            raise OrchestrationError("verification_check cannot be a marker command")
        used_ids = {unit["id"] for unit in units}
        verifier_id = "fresh-verifier"
        suffix = 2
        while verifier_id in used_ids:
            verifier_id = f"fresh-verifier-{suffix}"
            suffix += 1
        verifier = {
            "id": verifier_id,
            "role": "verifier",
            "objective": "Independently verify the integrated result",
            "context": ["objective", "integrated diff", "criteria", "receipts"],
            "ownership": [],
            "permissions": "read-only",
            "expected_output": "confirmed, rejected, or inconclusive with evidence",
            "check": verification_check,
            "dependencies": [unit["id"] for unit in selected],
            "stop_conditions": [
                "do not modify the workspace",
                "do not self-verify",
            ],
            "delegation_depth": 1,
            "may_spawn": False,
            "may_verify_parent": False,
            "must_be_distinct_from": [unit["id"] for unit in selected],
        }
        verifier["assignment_id"] = _content_id("assignment", verifier)
        waves.append(
            {
                "kind": "verification",
                "parallel": False,
                "assignments": [verifier],
            }
        )

    if not selected and not verifier_required:
        return _solo_plan(
            "no eligible agent assignment remains",
            valid_input=True,
            durable=durable,
            schema_version=schema_version,
        )
    if not selected:
        mode = "staged-verify"
    elif retry_record and schema_version == 2:
        mode = "staged-verify"
    elif single_worker_staged_verify:
        mode = "staged-verify"
    elif writes and len(layers) > 1:
        mode = "staged-verify"
    elif writes:
        mode = "parallel-packets"
    elif verifier_required:
        mode = "staged-verify"
    else:
        mode = "parallel-read-only"

    retry_allowed = retry_record is not None and retry_record["attempts"] == 0
    total_agents = len(selected) + (1 if verifier_required else 0)
    max_concurrent_workers = max(
        (len(wave["assignments"]) for wave in waves if wave["kind"] != "verification"),
        default=0,
    )
    effective_depth = max(
        [unit["depth"] for unit in selected] + ([1] if verifier_required else [])
    )
    plan = {
        "schema_version": schema_version,
        "kind": "agent_plan",
        "profile": PROFILE,
        "valid_input": True,
        "mode": mode,
        "selected_mode": mode,
        "executed_mode": None,
        "outcome": "planned",
        "degradation": None,
        "spawn_count": len(selected),
        "total_planned_agents": total_agents,
        "max_concurrent_workers": max_concurrent_workers,
        "max_depth": effective_depth,
        "reserve_verifier_slot": verifier_required,
        "waves": waves,
        "reasons": [
            f"{len(selected)} executable workers justify bounded coordination",
            "the main agent retains synthesis, integration, and final claims",
        ],
        "abstentions": [
            "fixed-size swarm",
            "worker-created descendants",
            "self-verification",
            "undeclared writes",
        ],
        "retry_policy": {
            "max_retries_per_assignment": 1,
            "retry_allowed": retry_allowed,
            "target_assignment_id": (
                retry_record.get("failed_assignment_id") if retry_record else None
            ),
            "fallback": "main-agent-absorbs-or-reports-blocker",
        },
        "stop_conditions": [
            "interrupt duplicate or out-of-scope work",
            "re-evaluate after every wave",
            "stop when coordination no longer has positive value",
        ],
        "receipt_policy": {
            "emit_json": durable,
            "external_state_required": durable,
            "end_to_end_improvement_proven": False,
        },
    }
    assignment_ids = [
        assignment["assignment_id"]
        for wave in waves
        for assignment in wave["assignments"]
    ]
    if len(assignment_ids) != len(set(assignment_ids)):
        raise OrchestrationError("assignment ids must be globally unique")
    plan["plan_id"] = _content_id("plan", plan)
    return plan


def select_agent_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Return an observable agent plan; semantic input errors fail closed to solo."""
    if not isinstance(payload, dict):
        return _solo_plan("input must be an object", valid_input=False)
    durable = payload.get("durable_or_release_critical") is True
    try:
        return _select_agent_plan(payload)
    except OrchestrationError as error:
        schema_version = 2 if payload.get("schema_version") == 2 else 1
        return _solo_plan(
            str(error),
            valid_input=False,
            durable=durable,
            schema_version=schema_version,
        )


def _validate_content_id(value: dict[str, Any], field: str, prefix: str) -> None:
    claimed = value.get(field)
    if not isinstance(claimed, str):
        raise OrchestrationError(f"{field} is required")
    unhashed = {key: item for key, item in value.items() if key != field}
    if claimed != _content_id(prefix, unhashed):
        raise OrchestrationError(f"{field} does not match content")


def _commands(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise OrchestrationError("commands must be a list")
    for index, command in enumerate(value):
        if not isinstance(command, dict):
            raise OrchestrationError(f"commands[{index}] must be an object")
        _argv(command.get("argv"), f"commands[{index}].argv")
        exit_code = command.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise OrchestrationError(f"commands[{index}].exit_code is invalid")
    return value


def _validate_worker_result_body(
    result: dict[str, Any],
    *,
    plan_id: str,
    assignment: dict[str, Any],
    assignments: list[dict[str, Any]],
) -> dict[str, Any]:
    assignment_id = assignment["assignment_id"]
    if result.get("plan_id") != plan_id:
        raise OrchestrationError("result plan_id does not match plan")
    if result.get("assignment_id") != assignment_id:
        raise OrchestrationError("result assignment_id does not match assignment")
    for field in ("role", "permissions", "delegation_depth", "dependencies"):
        if result.get(field) != assignment.get(field):
            raise OrchestrationError(f"result {field} does not match assignment")
    ownership_bound = "ownership" in result
    if ownership_bound and result["ownership"] != assignment.get("ownership", []):
        raise OrchestrationError("result ownership does not match assignment")

    role = assignment["role"]
    status = result.get("status")
    allowed = (
        {"confirmed", "rejected", "inconclusive"}
        if role == "verifier"
        else {"completed", "no-op", "failed", "blocked"}
    )
    if status not in allowed:
        raise OrchestrationError("status is incompatible with assignment role")
    changed_paths = [
        _normalize_owned_path(item, "changed_paths")
        for item in _string_list(
            result.get("changed_paths"), "changed_paths", allow_empty=True
        )
    ]
    read_only = assignment["permissions"] == "read-only"
    if read_only and changed_paths:
        raise OrchestrationError("read-only assignments cannot report changed paths")
    ownership = assignment.get("ownership", [])
    if not read_only and any(
        not any(
            _paths_overlap(changed, owned)
            and len(PurePosixPath(changed).parts) >= len(PurePosixPath(owned).parts)
            for owned in ownership
        )
        for changed in changed_paths
    ):
        raise OrchestrationError("changed_paths exceeds assignment ownership")
    if status == "completed" and not read_only and not changed_paths:
        raise OrchestrationError(
            "completed write assignments require changed paths or explicit no-op"
        )

    no_op_reason: str | None = None
    if status == "no-op":
        if read_only:
            raise OrchestrationError("only write assignments may report no-op")
        if changed_paths:
            raise OrchestrationError("no-op cannot report changed paths")
        no_op_reason = _string(result, "no_op_reason")
    elif "no_op_reason" in result:
        raise OrchestrationError("no_op_reason is only valid for no-op results")

    commands = _commands(result.get("commands"))
    blockers = _string_list(result.get("blockers"), "blockers", allow_empty=True)
    risks = _string_list(result.get("risks"), "risks", allow_empty=True)
    required_commands = [
        command for command in commands if command["argv"] == assignment["check"]
    ]
    if status in {"completed", "no-op", "failed", "confirmed", "rejected"} and not (
        required_commands
    ):
        raise OrchestrationError("result does not include the exact assignment check")
    if status in {"completed", "no-op", "confirmed"} and (
        blockers
        or any(command["exit_code"] != 0 for command in commands)
        or not any(command["exit_code"] == 0 for command in required_commands)
    ):
        raise OrchestrationError(
            f"{status} requires a zero required check, zero exits, and no blockers"
        )
    if status == "failed" and not any(
        command["exit_code"] != 0 for command in commands
    ):
        raise OrchestrationError("failed requires an observed non-zero exit")
    if status in {"blocked", "inconclusive"} and not blockers:
        raise OrchestrationError(f"{status} requires at least one blocker")

    actor_id = _string(result, "actor_id")
    verified: list[str] | None = None
    if role == "verifier":
        verified = _string_list(
            result.get("verified_assignment_ids"), "verified_assignment_ids"
        )
        worker_ids = {
            item["assignment_id"] for item in assignments if item["role"] != "verifier"
        }
        if len(verified) != len(set(verified)) or set(verified) != worker_ids:
            raise OrchestrationError(
                "verifier must cover every planned worker assignment exactly once"
            )
    elif "verified_assignment_ids" in result:
        raise OrchestrationError("only verifiers may report verified assignments")

    return {
        "assignment_id": assignment_id,
        "actor_id": actor_id,
        "role": role,
        "status": status,
        "changed_paths": changed_paths,
        "commands": commands,
        "blockers": blockers,
        "risks": risks,
        "ownership_bound": ownership_bound,
        "required_check_observed": bool(required_commands),
        "no_op_reason": no_op_reason,
        "verified_assignment_ids": verified,
    }


def _durable_context_blockers(
    payload: dict[str, Any],
    *,
    plan_id: str,
    assignment: dict[str, Any],
    assignments: list[dict[str, Any]],
    normalized: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    context = payload.get("execution_context")
    if context is None:
        return [
            "execution_context is required for durable eligibility",
            "source binding is absent",
            "actor binding is absent",
        ]
    if not isinstance(context, dict):
        raise OrchestrationError("execution_context must be an object")
    if context.get("schema_version") is None:
        blockers.append("execution_context schema_version is absent")
    elif context.get("schema_version") != 1:
        raise OrchestrationError("execution_context schema_version must be 1")

    source_sha256 = context.get("source_sha256")
    if source_sha256 is None:
        blockers.append("source binding is absent")
    elif not isinstance(source_sha256, str) or not SHA256_RE.fullmatch(source_sha256):
        raise OrchestrationError("execution_context source_sha256 must be sha256")

    bindings = context.get("actor_bindings")
    assignment_ids = {item["assignment_id"] for item in assignments}
    if bindings is None:
        blockers.append("actor bindings are absent")
        bindings = {}
    elif not isinstance(bindings, dict):
        raise OrchestrationError("execution_context actor_bindings must be an object")
    else:
        for bound_assignment_id, actor_id in bindings.items():
            if bound_assignment_id not in assignment_ids:
                raise OrchestrationError(
                    "execution_context references an unknown assignment"
                )
            if not isinstance(actor_id, str) or not actor_id:
                raise OrchestrationError("execution_context actor_id is invalid")

    current_actor = bindings.get(assignment["assignment_id"])
    if current_actor is None:
        blockers.append("current assignment actor binding is absent")
    elif current_actor != normalized["actor_id"]:
        raise OrchestrationError("result actor_id does not match execution context")
    if not normalized["ownership_bound"]:
        blockers.append("result ownership binding is absent")
    if not normalized["required_check_observed"]:
        blockers.append("required assignment check evidence is absent")

    if assignment["role"] != "verifier":
        return blockers

    worker_assignments = [item for item in assignments if item["role"] != "verifier"]
    worker_ids = {item["assignment_id"] for item in worker_assignments}
    worker_actor_ids: set[str] = set()
    for worker_id in sorted(worker_ids):
        worker_actor = bindings.get(worker_id)
        if worker_actor is None:
            blockers.append(f"worker actor binding is absent: {worker_id}")
        else:
            worker_actor_ids.add(worker_actor)
    if current_actor is not None and current_actor in worker_actor_ids:
        raise OrchestrationError(
            "verifier actor must be distinct from every observed worker actor"
        )

    raw_worker_results = context.get("worker_results")
    if raw_worker_results is None:
        blockers.append("prior worker result bundle is absent")
        return blockers
    if not isinstance(raw_worker_results, list):
        raise OrchestrationError("execution_context worker_results must be a list")
    results_by_id: dict[str, dict[str, Any]] = {}
    for worker_result in raw_worker_results:
        if not isinstance(worker_result, dict):
            raise OrchestrationError("execution_context worker result is invalid")
        worker_id = worker_result.get("assignment_id")
        if worker_id not in worker_ids:
            raise OrchestrationError(
                "execution_context worker result references an invalid assignment"
            )
        if worker_id in results_by_id:
            raise OrchestrationError(
                "execution_context worker results must be one-to-one"
            )
        results_by_id[worker_id] = worker_result
    missing_results = sorted(worker_ids - set(results_by_id))
    if missing_results:
        blockers.append(
            "prior worker results are absent: " + ", ".join(missing_results)
        )
        return blockers

    assignments_by_id = {item["assignment_id"]: item for item in worker_assignments}
    for worker_id in sorted(worker_ids):
        worker_result = _validate_worker_result_body(
            results_by_id[worker_id],
            plan_id=plan_id,
            assignment=assignments_by_id[worker_id],
            assignments=assignments,
        )
        if not worker_result["ownership_bound"]:
            blockers.append(f"worker ownership binding is absent: {worker_id}")
        if not worker_result["required_check_observed"]:
            blockers.append(f"worker required check is absent: {worker_id}")
        bound_actor = bindings.get(worker_id)
        if bound_actor is not None and bound_actor != worker_result["actor_id"]:
            raise OrchestrationError(
                "worker result actor_id does not match execution context"
            )
        if normalized["status"] == "confirmed" and worker_result["status"] not in {
            "completed",
            "no-op",
        }:
            raise OrchestrationError(
                "confirmed verifier requires completed or no-op worker results"
            )
    return blockers


def _validate_worker_result_v2(payload: dict[str, Any]) -> dict[str, Any]:
    plan = payload.get("plan")
    result = payload.get("result")
    if not isinstance(plan, dict) or plan.get("schema_version") != 2:
        raise OrchestrationError("plan must be a v2 agent_plan object")
    if not isinstance(result, dict):
        raise OrchestrationError("result must be an object")
    _validate_content_id(plan, "plan_id", "plan")
    raw_waves = plan.get("waves")
    if not isinstance(raw_waves, list):
        raise OrchestrationError("plan waves are invalid")
    assignments: list[dict[str, Any]] = []
    for wave in raw_waves:
        if not isinstance(wave, dict) or not isinstance(wave.get("assignments"), list):
            raise OrchestrationError("plan waves are invalid")
        for assignment in wave["assignments"]:
            if not isinstance(assignment, dict):
                raise OrchestrationError("plan assignment is invalid")
            _validate_content_id(assignment, "assignment_id", "assignment")
            assignments.append(assignment)
    assignment_id = payload.get("assignment_id")
    matches = [
        assignment
        for assignment in assignments
        if assignment["assignment_id"] == assignment_id
    ]
    if len(matches) != 1:
        raise OrchestrationError(
            "assignment_id does not identify exactly one plan assignment"
        )
    assignment = matches[0]
    normalized = _validate_worker_result_body(
        result,
        plan_id=plan["plan_id"],
        assignment=assignment,
        assignments=assignments,
    )
    durable_blockers = _durable_context_blockers(
        payload,
        plan_id=plan["plan_id"],
        assignment=assignment,
        assignments=assignments,
        normalized=normalized,
    )

    validation = {
        "schema_version": 2,
        "kind": "worker_result_validation",
        "valid": True,
        "durable_claim_eligible": not durable_blockers,
        "durable_claim_blockers": durable_blockers,
        "plan_id": plan["plan_id"],
        "assignment_id": assignment_id,
        "actor_id": normalized["actor_id"],
        "role": normalized["role"],
        "status": normalized["status"],
        "changed_paths": normalized["changed_paths"],
        "commands": normalized["commands"],
        "blockers": normalized["blockers"],
        "risks": normalized["risks"],
    }
    if normalized["no_op_reason"] is not None:
        validation["no_op_reason"] = normalized["no_op_reason"]
    if normalized["verified_assignment_ids"] is not None:
        validation["verified_assignment_ids"] = normalized["verified_assignment_ids"]
    return validation


def validate_worker_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the minimum response contract required from a worker."""
    if not isinstance(payload, dict):
        return {
            "schema_version": 1,
            "kind": "worker_result_validation",
            "valid": False,
            "error": "input must be an object",
        }
    try:
        if payload.get("schema_version") == 2:
            return _validate_worker_result_v2(payload)
        if payload.get("schema_version") != 1:
            raise OrchestrationError("schema_version must be 1 or 2")
        status = payload.get("status")
        if status not in {"completed", "failed", "blocked"}:
            raise OrchestrationError("status is unsupported")
        changed_paths = _string_list(
            payload.get("changed_paths"), "changed_paths", allow_empty=True
        )
        commands = _commands(payload.get("commands"))
        blockers = _string_list(payload.get("blockers"), "blockers", allow_empty=True)
        risks = _string_list(payload.get("risks"), "risks", allow_empty=True)
        return {
            "schema_version": 1,
            "kind": "worker_result_validation",
            "valid": True,
            "durable_claim_eligible": False,
            "status": status,
            "changed_paths": changed_paths,
            "commands": commands,
            "blockers": blockers,
            "risks": risks,
        }
    except (OrchestrationError, TypeError) as error:
        return {
            "schema_version": 2 if payload.get("schema_version") == 2 else 1,
            "kind": "worker_result_validation",
            "valid": False,
            "durable_claim_eligible": False,
            "error": str(error),
        }


def evaluate_cases(cases_path: Path) -> dict[str, Any]:
    data = json.loads(cases_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("cases"), list):
        raise OrchestrationError("case file must use schema_version 1 with cases")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, case in enumerate(data["cases"]):
        if not isinstance(case, dict):
            raise OrchestrationError(f"cases[{index}] must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise OrchestrationError(f"cases[{index}].id must be unique and non-empty")
        seen.add(case_id)
        actual = select_intensity(case.get("input", {}))
        checks = {
            "intensity": actual["intensity"] == case.get("expected_intensity"),
            "implementation_authorized": actual["implementation_authorized"]
            == case.get("expected_implementation_authorized"),
        }
        expected_abstentions = case.get("expected_abstentions", [])
        if not isinstance(expected_abstentions, list):
            raise OrchestrationError(
                f"cases[{index}].expected_abstentions must be a list"
            )
        checks["abstentions"] = all(
            item in actual["abstained_process"] for item in expected_abstentions
        )
        results.append(
            {
                "id": case_id,
                "passed": all(checks.values()),
                "checks": checks,
                "actual": actual,
            }
        )
    return {
        "schema_version": 1,
        "suite": "cognitive-powers-orchestration-policy",
        "passed": bool(results) and all(item["passed"] for item in results),
        "case_count": len(results),
        "cases": results,
        "end_to_end_improvement_proven": False,
    }


def evaluate_agent_cases(cases_path: Path) -> dict[str, Any]:
    data = json.loads(cases_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("cases"), list):
        raise OrchestrationError("agent case file must use schema_version 1 with cases")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, case in enumerate(data["cases"]):
        if not isinstance(case, dict):
            raise OrchestrationError(f"cases[{index}] must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise OrchestrationError(f"cases[{index}].id must be unique and non-empty")
        seen.add(case_id)
        actual = select_agent_plan(case.get("input", {}))
        checks = {
            "mode": actual["mode"] == case.get("expected_mode"),
            "selected_mode": actual["selected_mode"]
            == case.get("expected_selected_mode", case.get("expected_mode")),
            "executed_mode": actual["executed_mode"]
            == case.get("expected_executed_mode"),
            "outcome": actual["outcome"] == case.get("expected_outcome", "planned"),
            "degradation": actual["degradation"] == case.get("expected_degradation"),
            "spawn_count": actual["spawn_count"] == case.get("expected_spawn_count"),
            "valid_input": actual["valid_input"]
            is case.get("expected_valid_input", True),
        }
        if "expected_wave_kinds" in case:
            checks["wave_kinds"] = [wave["kind"] for wave in actual["waves"]] == case[
                "expected_wave_kinds"
            ]
        if "expected_plan_schema" in case:
            checks["plan_schema"] = (
                actual["schema_version"] == case["expected_plan_schema"]
            )
        if case.get("expect_content_ids"):
            checks["content_ids"] = isinstance(actual.get("plan_id"), str) and all(
                isinstance(assignment.get("assignment_id"), str)
                for wave in actual["waves"]
                for assignment in wave["assignments"]
            )
        results.append(
            {
                "id": case_id,
                "passed": all(checks.values()),
                "checks": checks,
                "actual": actual,
            }
        )
    return {
        "schema_version": 1,
        "suite": "cognitive-powers-agent-planner",
        "passed": bool(results) and all(item["passed"] for item in results),
        "case_count": len(results),
        "cases": results,
        "end_to_end_improvement_proven": False,
    }


def _read_object(value: str) -> dict[str, Any]:
    raw = sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise OrchestrationError("input must be a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="v1 task-signal JSON path or - for stdin")
    source.add_argument("--cases", type=Path, help="v1 intensity case fixture")
    source.add_argument("--agent-plan", help="agent-planning JSON path or - for stdin")
    source.add_argument(
        "--agent-plan-template",
        nargs="?",
        const=2,
        type=int,
        choices=AGENT_PLAN_INPUT_VERSIONS,
        metavar="{1,2}",
        help="emit the versioned compact planning-input interface (default: 2)",
    )
    source.add_argument("--agent-cases", type=Path, help="agent-planning case fixture")
    source.add_argument(
        "--worker-result", help="worker-result JSON path or - for stdin"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if args.input is not None:
            result = select_intensity(_read_object(args.input))
        elif args.cases is not None:
            result = evaluate_cases(args.cases)
        elif args.agent_plan is not None:
            result = select_agent_plan(_read_object(args.agent_plan))
        elif args.agent_plan_template is not None:
            result = agent_plan_template(args.agent_plan_template)
        elif args.agent_cases is not None:
            result = evaluate_agent_cases(args.agent_cases)
        else:
            result = validate_worker_result(_read_object(args.worker_result))
    except (OrchestrationError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({"error": str(error)}) if args.json else f"ERROR: {error}")
        return 2
    print(json.dumps(result, indent=2) if args.json else json.dumps(result))
    if result.get("passed") is False or result.get("valid") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
