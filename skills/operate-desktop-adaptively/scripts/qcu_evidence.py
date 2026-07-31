#!/usr/bin/env python3
"""Probe QCU and normalize a raw desktop transcript into durable evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


SCHEMA_VERSION = 1
Runner = Callable[..., subprocess.CompletedProcess[str]]


class QcuEvidenceError(RuntimeError):
    """Raised when QCU evidence is unavailable or inconclusive."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def resolve_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise QcuEvidenceError(f"root is not a directory: {root}")
    return root


def _resolve_executable(explicit: str | None) -> str | None:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        return shutil.which(explicit)
    for name in ("qcu", "quick-computer-use", "quickcomputeruse"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def probe(
    root: str | Path,
    *,
    executable: str | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, object]:
    project_root = resolve_root(root)
    resolved = _resolve_executable(executable)
    if resolved is None:
        return {
            "available": False,
            "usable": False,
            "reason": "QCU executable not found",
            "root": str(project_root),
            "executable": None,
        }
    try:
        completed = runner(
            [resolved, "--help"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise QcuEvidenceError(f"QCU probe failed to start: {error}") from error
    help_text = f"{completed.stdout}\n{completed.stderr}"
    required = ("sidecar-ready", "sidecar-where", "sidecar-do", "finish")
    missing = [name for name in required if name not in help_text]
    usable = completed.returncode == 0 and not missing
    reason = None
    if completed.returncode != 0:
        reason = f"QCU help exited with {completed.returncode}"
    elif missing:
        reason = "QCU fast surface missing: " + ", ".join(missing)
    return {
        "available": True,
        "usable": usable,
        "reason": reason,
        "root": str(project_root),
        "executable": resolved,
        "surface": {name: name not in missing for name in required},
    }


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise QcuEvidenceError(f"{label} must be a non-empty regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    # A transcript emitted in UTF-16 or cp1252 raises UnicodeDecodeError, which is
    # a ValueError and not a JSONDecodeError, so it escaped as a traceback the
    # calling model could not parse instead of the documented error object.
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QcuEvidenceError(f"{label} is not valid JSON: {path}") from error
    if not isinstance(value, dict):
        raise QcuEvidenceError(f"{label} must be a JSON object")
    return value


def _nested_values(value: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for current_key, current in value.items():
            if current_key == key:
                found.append(current)
            found.extend(_nested_values(current, key))
    elif isinstance(value, list):
        for current in value:
            found.extend(_nested_values(current, key))
    return found


def _command_result(command: dict[str, Any]) -> dict[str, Any]:
    result = command.get("result")
    if not isinstance(result, dict):
        raise QcuEvidenceError("every command must contain a parsed JSON result")
    return result


def _validate_transcript(payload: dict[str, Any]) -> dict[str, object]:
    if (
        payload.get("schemaVersion") != 1
        or payload.get("type") != "qcu_desktop_transcript"
        or payload.get("provider") != "quick-computer-use"
    ):
        raise QcuEvidenceError("transcript identity is invalid")
    required_text = ("qcuVersion", "sessionId", "objective", "expectedWindow")
    for field in required_text:
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise QcuEvidenceError(f"transcript field {field} must be non-empty")
    if payload.get("realActions") is not True:
        raise QcuEvidenceError("transcript does not identify real actions")
    commands = payload.get("commands")
    if not isinstance(commands, list) or not commands:
        raise QcuEvidenceError("transcript has no commands")
    names: list[str] = []
    action_count = 0
    foreground_observations = 0
    observation_ids: list[str] = []
    command_evidence: list[tuple[int, set[str], bool]] = []
    stale_frames = 0
    busy_count = 0
    observed_session_ids: set[str] = set()
    for command_index, command in enumerate(commands):
        if not isinstance(command, dict):
            raise QcuEvidenceError("command entry is malformed")
        name = str(command.get("name") or "").strip()
        argv = command.get("argv")
        if (
            not name
            or not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) for item in argv)
        ):
            raise QcuEvidenceError("every command requires a name and string argv")
        if isinstance(command.get("exitCode"), bool) or not isinstance(
            command.get("exitCode"), int
        ):
            raise QcuEvidenceError("every command requires an integer exitCode")
        if command["exitCode"] != 0:
            raise QcuEvidenceError(f"QCU command failed: {name}")
        result = _command_result(command)
        names.append(name)
        statuses = {str(item) for item in _nested_values(result, "status")}
        observed_session_ids.update(
            str(item)
            for item in _nested_values(result, "session_id")
            if isinstance(item, str) and item
        )
        if "busy_no_queue" in statuses:
            busy_count += 1
            raise QcuEvidenceError("transcript contains busy_no_queue input")
        if statuses.intersection({"failed", "rejected", "error", "blocked"}):
            raise QcuEvidenceError(f"QCU command reported failure: {name}")
        stale_frames += sum(
            item is True for item in _nested_values(result, "stale_frame_reused")
        )
        current_observation_ids = {
            str(item)
            for item in _nested_values(result, "observation_id")
            if isinstance(item, str) and item
        }
        observation_ids.extend(current_observation_ids)
        expected = _nested_values(result, "expected")
        foreground_match = False
        for collection in expected:
            if not isinstance(collection, list):
                continue
            if any(
                isinstance(item, dict)
                and item.get("status") == "foreground"
                and str(item.get("target") or "").casefold()
                == payload["expectedWindow"].casefold()
                for item in collection
            ):
                foreground_observations += 1
                foreground_match = True
        command_evidence.append(
            (command_index, current_observation_ids, foreground_match)
        )
        if name == "sidecar-ready":
            sidecar_statuses = {
                str(item) for item in _nested_values(result, "sidecar_status")
            }
            if "success" not in statuses and not any(
                status.startswith("running") for status in sidecar_statuses
            ):
                raise QcuEvidenceError(
                    "sidecar-ready does not report a running sidecar"
                )
        if name == "sidecar-do":
            if "success" not in statuses:
                raise QcuEvidenceError("sidecar-do does not report status success")
            action_count += 1
    if observed_session_ids and observed_session_ids != {payload["sessionId"]}:
        raise QcuEvidenceError(
            "QCU result session IDs do not match the transcript session"
        )
    if stale_frames:
        raise QcuEvidenceError("transcript contains reused stale capture")
    if (
        "sidecar-ready" not in names
        or "sidecar-do" not in names
        or "finish" not in names
    ):
        raise QcuEvidenceError(
            "transcript is missing ready, action, or finish commands"
        )
    if (
        names.index("sidecar-ready") > names.index("sidecar-do")
        or names[-1] != "finish"
    ):
        raise QcuEvidenceError("QCU command order is invalid")
    if foreground_observations < 1:
        raise QcuEvidenceError("expected window was not observed in the foreground")
    finish = commands[-1]
    if "objective_verified" not in finish["argv"] or "objective_verified" not in {
        str(item) for item in _nested_values(_command_result(finish), "reason")
    }:
        raise QcuEvidenceError("finish does not record objective_verified")
    verification = payload.get("finalVerification")
    if not isinstance(verification, dict):
        raise QcuEvidenceError("final verification is missing")
    if (
        verification.get("objectiveSatisfied") is not True
        or verification.get("expectedWindowForeground") is not True
    ):
        raise QcuEvidenceError("final verification does not prove objective and focus")
    evidence = verification.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        raise QcuEvidenceError("final verification evidence is empty")
    final_observation = verification.get("observationId")
    if not isinstance(final_observation, str) or not final_observation.strip():
        raise QcuEvidenceError("final verification has no observation ID")
    last_action_index = max(
        index for index, name in enumerate(names) if name == "sidecar-do"
    )
    final_observation_bound = any(
        index >= last_action_index and final_observation in ids and foreground
        for index, ids, foreground in command_evidence
    )
    if not final_observation_bound:
        raise QcuEvidenceError(
            "final observation is not bound to post-action foreground QCU evidence"
        )
    return {
        "actionCount": action_count,
        "foregroundObservationCount": foreground_observations,
        "observationIds": sorted(set(observation_ids)),
        "staleFrameCount": stale_frames,
        "busyNoQueueCount": busy_count,
    }


def _default_data_root() -> Path:
    configured = os.environ.get("COGNITIVE_POWERS_DATA") or os.environ.get(
        "PLUGIN_DATA"
    )
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".codex" / "cognitive-powers").resolve()


def _project_key(root: Path) -> str:
    # Identical derivation to browser and skyvern evidence: the resolved
    # spelling, casefolded on Windows, so all three partition one workspace
    # under one key.
    canonical = str(root)
    if os.name == "nt":
        canonical = canonical.casefold()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def normalize(
    root: str | Path,
    transcript: str | Path,
    *,
    artifact_dir: str | Path | None = None,
) -> tuple[dict[str, object], int]:
    project_root = resolve_root(root)
    transcript_path = Path(transcript).expanduser().resolve()
    payload = _load_json_object(transcript_path, "QCU transcript")
    summary = _validate_transcript(payload)
    if artifact_dir:
        output_root = Path(artifact_dir).expanduser().resolve()
    else:
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        # Partition by project the way browser and skyvern evidence already
        # do; one shared pool made per-project retention and cleanup
        # impossible to express.
        output_root = _default_data_root() / "qcu" / _project_key(project_root) / run_id
    if _is_within(output_root, project_root):
        raise QcuEvidenceError(
            f"artifact directory must be outside the workspace: {output_root}"
        )
    if output_root.exists() and any(output_root.iterdir()):
        raise QcuEvidenceError(f"artifact directory is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    copied_transcript = output_root / "qcu-transcript.json"
    shutil.copyfile(transcript_path, copied_transcript)
    receipt_path = output_root / "cognitive-qcu-receipt.json"
    receipt: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "type": "qcu_desktop_evidence",
        "provider": "quick-computer-use",
        "capturedAt": utc_now(),
        "root": str(project_root),
        "qcuVersion": payload["qcuVersion"],
        "sessionId": payload["sessionId"],
        "objective": payload["objective"],
        "expectedWindow": payload["expectedWindow"],
        "realActions": True,
        "objectiveSatisfied": True,
        "focusVerified": True,
        "finished": True,
        "finishReason": "objective_verified",
        "verification": payload["finalVerification"],
        "summary": summary,
        "artifactRoot": str(output_root),
        "artifacts": [
            {
                "path": copied_transcript.name,
                "sha256": _sha256_file(copied_transcript),
                "bytes": copied_transcript.stat().st_size,
            }
        ],
    }
    receipt_path.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    receipt["receipt"] = str(receipt_path)
    return receipt, 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--qcu", help="QCU executable path or command")
    parser.add_argument("--json", action="store_true")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    subparsers.add_parser("probe")
    normalize_parser = subparsers.add_parser("normalize")
    normalize_parser.add_argument("--transcript", required=True)
    normalize_parser.add_argument("--artifact-dir")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.subcommand == "probe":
            payload = probe(args.root, executable=args.qcu)
            exit_code = 0
        else:
            payload, exit_code = normalize(
                args.root, args.transcript, artifact_dir=args.artifact_dir
            )
    except QcuEvidenceError as error:
        payload = {"error": str(error)}
        exit_code = 2
    print(
        json.dumps(payload, indent=2, ensure_ascii=False)
        if args.json
        else json.dumps(payload, ensure_ascii=False)
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
