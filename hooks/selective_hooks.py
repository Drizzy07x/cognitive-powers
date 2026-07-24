#!/usr/bin/env python3
"""Small, fail-open Codex hooks for edit provenance and validation reminders."""

from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = "cognitive-powers.edit-event.v1"
RECEIPT_SCHEMA = "cognitive-powers.validation-receipt.v1"
MAX_STDIN_BYTES = 2 * 1024 * 1024
MAX_HASH_BYTES = 32 * 1024 * 1024
LOCK_TIMEOUT_SECONDS = 5.0
SUPPORTED_TOOLS = {
    "apply_patch",
    "edit",
    "write",
    "bash",
    "shell_command",
    "exec_command",
    "local_shell",
}
PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _roots(data_override: str | None = None) -> tuple[Path, Path] | None:
    plugin_root = (
        Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
        .expanduser()
        .resolve()
    )
    configured = (
        data_override
        or os.environ.get("COGNITIVE_POWERS_DATA")
        or os.environ.get("PLUGIN_DATA")
    )
    data_root = (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".codex" / "cognitive-powers").resolve()
    )
    if data_root == plugin_root or _inside(data_root, plugin_root):
        return None
    return plugin_root, data_root


def _first(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = payload.get(name)
        if value is not None:
            return value
    return None


def _session_id(payload: dict[str, Any]) -> str | None:
    value = _first(payload, "sessionId", "session_id", "threadId", "thread_id")
    return value if isinstance(value, str) and value.strip() else None


def _session_key(session_id: str) -> str:
    return _sha256_bytes(session_id.encode("utf-8"))[:32]


def _ledger_path(data_root: Path, session_id: str) -> Path:
    return data_root / "hooks" / "events" / f"{_session_key(session_id)}.jsonl"


def _receipt_path(data_root: Path, session_id: str) -> Path:
    return data_root / "hooks" / "validation" / f"{_session_key(session_id)}.json"


@contextlib.contextmanager
def _ledger_lock(ledger: Path) -> Iterator[None]:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lock = ledger.with_suffix(ledger.suffix + ".lock")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    with lock.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)

        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"timed out waiting for hook ledger lock: {lock}"
                        ) from error
                    time.sleep(0.02)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as error:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"timed out waiting for hook ledger lock: {lock}"
                        ) from error
                    time.sleep(0.02)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_payload() -> dict[str, Any] | None:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if not raw or len(raw) > MAX_STDIN_BYTES:
        return None
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    value = _first(payload, "toolInput", "tool_input", "input")
    return value if isinstance(value, dict) else {}


def _candidate_paths(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("file_path", "filePath", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    if tool_name.lower() == "apply_patch":
        for key in ("patch", "content", "input"):
            value = tool_input.get(key)
            if isinstance(value, str):
                candidates.extend(PATCH_PATH.findall(value))
    return list(dict.fromkeys(candidates))


def _file_records(cwd: Path, candidates: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for candidate in candidates:
        supplied = Path(candidate).expanduser()
        resolved = (
            (cwd / supplied).resolve()
            if not supplied.is_absolute()
            else supplied.resolve()
        )
        if not _inside(resolved, cwd):
            continue
        record: dict[str, Any] = {"path": resolved.relative_to(cwd).as_posix()}
        if resolved.is_file() and not resolved.is_symlink():
            size = resolved.stat().st_size
            record["size"] = size
            if size <= MAX_HASH_BYTES:
                record["sha256"] = _sha256_file(resolved)
        else:
            record["exists"] = False
        records.append(record)
    return records


def _read_ledger(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not path.is_file():
        return [], None
    events: list[dict[str, Any]] = []
    previous: str | None = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return [], "ledger cannot be read"
    for index, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return events, f"ledger line {index} is not JSON"
        if not isinstance(event, dict) or event.get("schema") != SCHEMA:
            return events, f"ledger line {index} has an invalid schema"
        claimed = event.get("eventHash")
        unsigned = dict(event)
        unsigned.pop("eventHash", None)
        if claimed != _sha256_bytes(_canonical(unsigned)):
            return events, f"ledger line {index} hash changed"
        if event.get("previousEventHash") != previous:
            return events, f"ledger line {index} breaks the hash chain"
        previous = claimed
        events.append(event)
    return events, None


def post_tool_use(payload: dict[str, Any]) -> None:
    roots = _roots()
    session_id = _session_id(payload)
    if roots is None or session_id is None:
        return
    _, data_root = roots
    tool_name = _first(payload, "toolName", "tool_name", "tool")
    if not isinstance(tool_name, str) or tool_name.lower() not in SUPPORTED_TOOLS:
        return
    cwd_value = _first(payload, "cwd", "workingDirectory", "working_directory")
    if not isinstance(cwd_value, str) or not cwd_value.strip():
        return
    cwd = Path(cwd_value).expanduser().resolve()
    if not cwd.is_dir() or _inside(data_root, cwd):
        return
    tool_input = _tool_input(payload)
    files = _file_records(cwd, _candidate_paths(tool_name, tool_input))
    ledger = _ledger_path(data_root, session_id)
    with _ledger_lock(ledger):
        events, error = _read_ledger(ledger)
        if error:
            return
        event: dict[str, Any] = {
            "schema": SCHEMA,
            "sessionId": session_id,
            "turnId": _first(payload, "turnId", "turn_id"),
            "event": "PostToolUse",
            "tool": tool_name,
            "cwd": str(cwd),
            "files": files,
            "recordedAt": datetime.now(timezone.utc).isoformat(),
            "previousEventHash": events[-1]["eventHash"] if events else None,
        }
        event["eventHash"] = _sha256_bytes(_canonical(event))
        with ledger.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _receipt_is_current(
    receipt_path: Path,
    session_id: str,
    last_hash: str,
    plugin_root: Path,
    data_root: Path,
    event_cwd: Path,
) -> bool:
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        evidence = Path(receipt["evidencePath"]).resolve()
        evidence_payload, executor = _validated_durable_evidence(
            evidence,
            str(receipt.get("validator", "")),
            plugin_root,
            data_root,
            event_cwd,
        )
        return (
            receipt.get("schema") == RECEIPT_SCHEMA
            and receipt.get("sessionId") == session_id
            and receipt.get("validatedEventHash") == last_hash
            and evidence.is_file()
            and not evidence.is_symlink()
            and receipt.get("evidenceSha256") == _sha256_file(evidence)
            and receipt.get("evidenceType") == evidence_payload["type"]
            and receipt.get("executor") == executor
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _validated_command_evidence(
    evidence: Path, validator: str
) -> tuple[dict[str, Any], str]:
    try:
        value = json.loads(evidence.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("evidence must be a readable JSON receipt") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("evidence must use schema_version 1")
    evidence_type = value.get("type")
    if evidence_type not in {"command", "test_cycle"}:
        raise ValueError("evidence type must be command or test_cycle")
    executor = value.get("executor")
    if not isinstance(executor, str) or not executor.strip():
        raise ValueError("evidence must identify its executor")
    if not validator.strip() or validator.strip() == executor.strip():
        raise ValueError("validator must be non-empty and different from the executor")
    command = value.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(argument, str) and argument for argument in command)
    ):
        raise ValueError("evidence command must be a non-empty argv array")
    if value.get("exit_code") != 0:
        raise ValueError("evidence command did not exit successfully")
    if evidence_type == "test_cycle" and value.get("command_started") is not True:
        raise ValueError(
            "test-cycle evidence does not prove that the green command started"
        )
    for field in ("stdout_sha256", "stderr_sha256"):
        digest = value.get(field)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"evidence {field} must be a SHA-256 digest")
    fingerprint = value.get("source_fingerprint")
    if (
        not isinstance(fingerprint, dict)
        or not isinstance(fingerprint.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", fingerprint["sha256"])
    ):
        raise ValueError("evidence must include a source fingerprint")
    return value, executor.strip()


def _load_work_state(plugin_root: Path):
    path = plugin_root / "skills" / "execute-durably" / "scripts" / "work_state.py"
    spec = importlib.util.spec_from_file_location("cognitive_hook_work_state", path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load durable work-state validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError) as error:
        raise ValueError("cannot load durable work-state validator") from error
    return module


def _validated_durable_evidence(
    evidence: Path,
    validator: str,
    plugin_root: Path,
    data_root: Path,
    event_cwd: Path,
) -> tuple[dict[str, Any], str]:
    value, executor = _validated_command_evidence(evidence, validator)
    evidence_root = next(
        (parent for parent in evidence.parents if parent.name == "evidence"), None
    )
    if (
        not _inside(evidence, data_root)
        or evidence_root is None
        or not _inside(evidence_root, data_root)
    ):
        raise ValueError(
            "evidence must be a durable command receipt under the data root"
        )
    session_dir = evidence_root.parent
    state_path = session_dir / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("evidence session state is unreadable") from error
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError("evidence session state is malformed")
    workspace_value = state.get("workspace_root")
    if not isinstance(workspace_value, str):
        raise ValueError("evidence session has no workspace root")
    workspace = Path(workspace_value).resolve()
    if not workspace.is_dir() or not (
        event_cwd == workspace or _inside(event_cwd, workspace)
    ):
        raise ValueError("evidence belongs to a different workspace")
    work_state = _load_work_state(plugin_root)
    expected_session = work_state.session_directory(
        workspace, data_root, str(state.get("session_id", ""))
    )
    if expected_session != session_dir.resolve():
        raise ValueError("evidence is outside its declared durable session")
    if state.get("project_key") != work_state.project_key(workspace):
        raise ValueError("evidence session project identity changed")
    current_fingerprint = work_state.source_fingerprint(workspace, data_root)
    if value["source_fingerprint"].get("sha256") != current_fingerprint["sha256"]:
        raise ValueError("evidence source fingerprint is stale or fabricated")
    criterion_id = value.get("criterion_id")
    criteria = state.get("criteria")
    if not isinstance(criterion_id, str) or not isinstance(criteria, list):
        raise ValueError("evidence has no durable criterion")
    criterion = next(
        (
            item
            for item in criteria
            if isinstance(item, dict) and item.get("id") == criterion_id
        ),
        None,
    )
    if not isinstance(criterion, dict) or criterion.get("status") != "verified":
        raise ValueError("evidence criterion is not independently verified")
    receipt_value = criterion.get("receipt")
    if (
        not isinstance(receipt_value, str)
        or (session_dir / receipt_value).resolve() != evidence
    ):
        raise ValueError("evidence is not the criterion's active receipt")
    verification = criterion.get("verification")
    if (
        not isinstance(verification, dict)
        or verification.get("verdict") != "confirmed"
        or verification.get("verifier") != validator.strip()
        or verification.get("receipt_sha256") != _sha256_file(evidence)
    ):
        raise ValueError("evidence lacks matching independent verification")
    if criterion.get("executor") != executor:
        raise ValueError("evidence executor does not match durable state")
    return value, executor


def stop(payload: dict[str, Any]) -> None:
    roots = _roots()
    session_id = _session_id(payload)
    if roots is None or session_id is None:
        return
    plugin_root, data_root = roots
    ledger = _ledger_path(data_root, session_id)
    with _ledger_lock(ledger):
        events, error = _read_ledger(ledger)
    if not events and error is None:
        return
    current = (
        error is None
        and bool(events)
        and _receipt_is_current(
            _receipt_path(data_root, session_id),
            session_id,
            events[-1]["eventHash"],
            plugin_root,
            data_root,
            Path(events[-1]["cwd"]).resolve(),
        )
    )
    if current:
        return
    detail = f" ({error})" if error else ""
    message = (
        f"Cognitive Powers session {session_id!r} recorded file-changing tool use "
        f"under {str(data_root)!r}, "
        "but no current, hash-bound validation receipt covers the latest "
        f"edit{detail}."
    )
    print(json.dumps({"systemMessage": message}, ensure_ascii=False))


def record_validation(
    session_id: str,
    evidence_value: str,
    validator: str,
    data_override: str | None = None,
) -> int:
    roots = _roots(data_override)
    if roots is None:
        print("PLUGIN_DATA must resolve outside PLUGIN_ROOT", file=sys.stderr)
        return 2
    plugin_root, data_root = roots
    ledger = _ledger_path(data_root, session_id)
    with _ledger_lock(ledger):
        events, error = _read_ledger(ledger)
        if error or not events:
            print(error or "no edit events exist for this session", file=sys.stderr)
            return 2
        evidence_input = Path(evidence_value).expanduser()
        if evidence_input.is_symlink():
            print("evidence cannot be a symlink", file=sys.stderr)
            return 2
        evidence = evidence_input.resolve()
        if not evidence.is_file() or evidence.stat().st_size == 0:
            print("evidence must be a non-empty regular file", file=sys.stderr)
            return 2
        try:
            evidence_payload, executor = _validated_durable_evidence(
                evidence,
                validator,
                plugin_root,
                data_root,
                Path(events[-1]["cwd"]).resolve(),
            )
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "sessionId": session_id,
            "validatedEventHash": events[-1]["eventHash"],
            "evidencePath": str(evidence),
            "evidenceSha256": _sha256_file(evidence),
            "evidenceType": evidence_payload["type"],
            "executor": executor,
            "validator": validator.strip(),
            "recordedAt": datetime.now(timezone.utc).isoformat(),
        }
        target = _receipt_path(data_root, session_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
        print(
            json.dumps(
                {"receipt": str(target), "validatedEventHash": events[-1]["eventHash"]}
            )
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("post-tool-use")
    subparsers.add_parser("stop")
    receipt = subparsers.add_parser("record-validation")
    receipt.add_argument("--session-id", required=True)
    receipt.add_argument("--evidence", required=True)
    receipt.add_argument("--validator", required=True)
    receipt.add_argument("--data-root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "record-validation":
        return record_validation(
            args.session_id, args.evidence, args.validator, args.data_root
        )
    try:
        payload = _read_payload()
        if payload is not None:
            post_tool_use(payload) if args.command == "post-tool-use" else stop(payload)
    except Exception:
        # Observability must never turn an incomplete payload or I/O race into a tool block.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
