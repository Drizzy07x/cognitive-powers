#!/usr/bin/env python3
"""Small, fail-open hooks for edit provenance and validation reminders.

One script serves both hosts. Codex and Claude Code send different tool names
and payload spellings, so every reader accepts either; only the ``Stop`` output
is host-shaped, because the two hosts route a warning to different audiences.
"""

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
from typing import Any, Callable, Iterator, NamedTuple


SCHEMA = "cognitive-powers.edit-event.v1"
RECEIPT_SCHEMA = "cognitive-powers.validation-receipt.v1"
MAX_STDIN_BYTES = 2 * 1024 * 1024
PREFIX_SCAN_BYTES = 64 * 1024
MAX_HASH_BYTES = 32 * 1024 * 1024
LOCK_TIMEOUT_SECONDS = 5.0
# The shell spellings are defence for a host that ignores matchers, not a claim
# that shell use is recorded: neither manifest routes one here, and
# run_extension_benchmarks.py asserts `Bash` stays out of the PostToolUse
# matcher. Calling post_tool_use directly with a shell tool name does append an
# event and arm the Stop gate, which has twice been read as the shipped
# behaviour -- see the completion gate section of docs/operations.md.
SUPPORTED_TOOLS = {
    "apply_patch",
    "edit",
    "multiedit",
    "notebookedit",
    "write",
    "bash",
    "shell_command",
    "exec_command",
    "local_shell",
}
PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE)


class _StopVerdict(NamedTuple):
    """What the Stop gate concluded, and the state its warning has to describe.

    ``event_cwd`` travels with the verdict rather than being re-derived by the
    warning, because the ledger is read under its lock and the directory the
    latest edit was recorded under is the one fact that decides whether the
    printed remediation is followable at all on this host.
    """

    warn: bool
    error: str | None
    event_cwd: Path | None


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


def _default_data_root() -> Path:
    """Resolve the shared fallback data root.

    This must stay byte-identical to ``resolve_data_root`` in
    ``work_state_core/durability.py``. The hook only observes edits; the durable
    receipts it validates are written by ``work_state.py`` in a separate
    process. If the two resolve different roots, every receipt lands outside the
    root this hook checks and the ``Stop`` gate rejects work that is in fact
    complete.
    """
    return (Path.home() / ".codex" / "cognitive-powers").resolve()


def _plugin_root() -> Path:
    return (
        Path(
            os.environ.get("PLUGIN_ROOT")
            or os.environ.get("CLAUDE_PLUGIN_ROOT")
            or Path(__file__).resolve().parents[1]
        )
        .expanduser()
        .resolve()
    )


def _roots(data_override: str | None = None) -> tuple[Path, Path] | None:
    plugin_root = _plugin_root()
    # Host-injected data variables are deliberately absent: Claude Code exports
    # CLAUDE_PLUGIN_DATA and Codex exports PLUGIN_DATA to hook processes only,
    # so work_state.py -- launched as an ordinary tool call -- never sees
    # either. Honouring one here would point the hook at a root no receipt
    # writer can reach, splitting evidence storage on that host.
    # COGNITIVE_POWERS_DATA is the plugin's own variable: every process sees it.
    configured = data_override or os.environ.get("COGNITIVE_POWERS_DATA")
    data_root = (
        Path(configured).expanduser().resolve() if configured else _default_data_root()
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


def _try_lock_windows(handle: Any, lock: Path, deadline: float) -> bool:
    import msvcrt

    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError as error:
        if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            raise
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"timed out waiting for hook ledger lock: {lock}"
            ) from error
        return False


def _try_lock_posix(handle: Any, lock: Path, deadline: float) -> bool:
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError as error:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"timed out waiting for hook ledger lock: {lock}"
            ) from error
        return False


def _acquire_lock_windows(
    handle: Any, lock: Path, deadline: float
) -> Callable[[], None]:
    import msvcrt

    while not _try_lock_windows(handle, lock, deadline):
        time.sleep(0.02)

    def release() -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    return release


def _acquire_lock_posix(handle: Any, lock: Path, deadline: float) -> Callable[[], None]:
    import fcntl

    while not _try_lock_posix(handle, lock, deadline):
        time.sleep(0.02)

    def release() -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    return release


def _acquire_ledger_lock(
    handle: Any, lock: Path, deadline: float
) -> Callable[[], None]:
    """Take the exclusive lock and hand back the release that matches it.

    Acquire and release travel together rather than being decided by a second
    ``os.name`` test after the yield: the unlock has to reach the same API as
    the lock it undoes, and pairing them here is what stops a later edit from
    releasing through the other platform's call and leaving the ledger
    serialised against nothing.
    """
    if os.name == "nt":
        return _acquire_lock_windows(handle, lock, deadline)
    return _acquire_lock_posix(handle, lock, deadline)


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
        release = _acquire_ledger_lock(handle, lock, deadline)
        try:
            yield
        finally:
            release()


class OversizedPayload:
    """An edit whose payload exceeded the cap, carrying the readable prefix."""

    __slots__ = ("prefix",)

    def __init__(self, prefix: bytes) -> None:
        self.prefix = prefix


def _read_payload() -> dict[str, Any] | OversizedPayload | None:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if not raw:
        return None
    if len(raw) > MAX_STDIN_BYTES:
        return OversizedPayload(raw[:PREFIX_SCAN_BYTES])
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _scan_prefix(prefix: bytes, *names: str) -> str | None:
    """Recover one string field from an unparsable payload prefix.

    Only reached when the payload was too large to parse. A regular expression
    over the head of the stream is not a JSON parser, and it is not used as one:
    a miss returns None and the field is recorded as unknown rather than guessed.
    """
    for name in names:
        match = re.search(
            rb'"'
            + re.escape(name.encode("ascii"))
            + rb'"\s*:\s*"((?:[^"\\]|\\.){0,512})"',
            prefix,
        )
        if match is None:
            continue
        try:
            value = json.loads(b'"' + match.group(1) + b'"')
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, str) and value.strip():
            return value
    return None


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    value = _first(payload, "toolInput", "tool_input", "input")
    return value if isinstance(value, dict) else {}


def _candidate_paths(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    # NotebookEdit is matched by both hosts but spells its target notebook_path.
    # Reading only the three generic keys recorded every notebook edit with an
    # empty file list, so the ledger proved a write happened and nothing about
    # what was written.
    for key in ("file_path", "filePath", "path", "notebook_path", "notebookPath"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    if tool_name.lower() == "apply_patch":
        for key in ("patch", "content", "input"):
            value = tool_input.get(key)
            if isinstance(value, str):
                candidates.extend(PATCH_PATH.findall(value))
    return list(dict.fromkeys(candidates))


def _recorded_path(cwd: Path, candidate: str, data_root: Path) -> Path | None:
    """Resolve one edited path, or None when it is not this session's work.

    The evidence store is not user work. It lives under the working directory
    whenever the session was opened at an ancestor of the home directory, and
    recording its own writes would feed the ledger back into itself.
    """
    supplied = Path(candidate).expanduser()
    resolved = (
        (cwd / supplied).resolve() if not supplied.is_absolute() else supplied.resolve()
    )
    if not _inside(resolved, cwd) or _inside(resolved, data_root):
        return None
    return resolved


def _file_record(resolved: Path, cwd: Path) -> dict[str, Any]:
    record: dict[str, Any] = {"path": resolved.relative_to(cwd).as_posix()}
    if resolved.is_file() and not resolved.is_symlink():
        size = resolved.stat().st_size
        record["size"] = size
        if size <= MAX_HASH_BYTES:
            record["sha256"] = _sha256_file(resolved)
    else:
        record["exists"] = False
    return record


def _file_records(
    cwd: Path, candidates: list[str], data_root: Path
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for candidate in candidates:
        resolved = _recorded_path(cwd, candidate, data_root)
        if resolved is not None:
            records.append(_file_record(resolved, cwd))
    return records


def _ledger_lines(content: str) -> list[str]:
    """Split the ledger into records without splitting inside one.

    One physical line is one record: splitlines() also breaks on U+2028,
    U+2029, and U+0085, which json.dumps(ensure_ascii=False) leaves raw, so
    one edited path carrying one of those separators poisoned the ledger,
    dropped every later event, and left the stop gate warning forever.
    """
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _ledger_record(
    line: str, index: int, previous: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    """Verify one ledger line against the chain, naming what failed."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None, f"ledger line {index} is not JSON"
    if not isinstance(event, dict) or event.get("schema") != SCHEMA:
        return None, f"ledger line {index} has an invalid schema"
    claimed = event.get("eventHash")
    unsigned = dict(event)
    unsigned.pop("eventHash", None)
    if claimed != _sha256_bytes(_canonical(unsigned)):
        return None, f"ledger line {index} hash changed"
    if event.get("previousEventHash") != previous:
        return None, f"ledger line {index} breaks the hash chain"
    return event, None


def _read_ledger(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not path.is_file():
        return [], None
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], "ledger cannot be read"
    events: list[dict[str, Any]] = []
    previous: str | None = None
    for index, line in enumerate(_ledger_lines(content), start=1):
        event, failure = _ledger_record(line, index, previous)
        if event is None:
            return events, failure
        previous = event["eventHash"]
        events.append(event)
    return events, None


def _append_event(data_root: Path, session_id: str, fields: dict[str, Any]) -> None:
    ledger = _ledger_path(data_root, session_id)
    with _ledger_lock(ledger):
        events, error = _read_ledger(ledger)
        if error:
            return
        event: dict[str, Any] = {
            "schema": SCHEMA,
            "sessionId": session_id,
            "event": "PostToolUse",
            **fields,
            "recordedAt": datetime.now(timezone.utc).isoformat(),
            "previousEventHash": events[-1]["eventHash"] if events else None,
        }
        event["eventHash"] = _sha256_bytes(_canonical(event))
        with ledger.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def record_oversized_payload(prefix: bytes) -> None:
    """Record that an edit happened whose payload could not be parsed.

    The stop gate reads an empty ledger as "nothing was edited", so dropping an
    oversized payload did not merely lose the file hashes -- it switched the
    completion gate off for the whole session. Claude Code inlines written
    content in the payload, so writing one large generated file was enough.
    A degraded event keeps the gate firing; it deliberately carries no file
    identity, because none was parsed.
    """
    roots = _roots()
    session_id = _scan_prefix(
        prefix, "sessionId", "session_id", "threadId", "thread_id"
    )
    if roots is None or session_id is None:
        return
    _, data_root = roots
    _append_event(
        data_root,
        session_id,
        {
            "turnId": _scan_prefix(prefix, "turnId", "turn_id"),
            "tool": _scan_prefix(prefix, "toolName", "tool_name", "tool") or "unknown",
            "cwd": _scan_prefix(prefix, "cwd", "workingDirectory", "working_directory")
            or "",
            "files": [],
            "payloadTruncated": True,
        },
    )


def _event_cwd(payload: dict[str, Any], data_root: Path) -> Path | None:
    """Resolve the working directory, or None when this edit must not record.

    Refuse only a session that is working *inside* the evidence store. The
    test used to be the reverse -- data root under cwd -- which silently
    disabled provenance for every session opened at an ancestor of the data
    root: a drive root, the home directory, or ``C:\\Users``. The stop gate
    reads this ledger, so on those hosts the whole completion gate was inert
    while every packaging check still reported healthy. Excluding the store per
    file keeps the original protection without that blast radius.
    """
    value = _first(payload, "cwd", "workingDirectory", "working_directory")
    if not isinstance(value, str) or not value.strip():
        return None
    cwd = Path(value).expanduser().resolve()
    if not cwd.is_dir() or cwd == data_root or _inside(cwd, data_root):
        return None
    return cwd


def post_tool_use(payload: dict[str, Any]) -> None:
    roots = _roots()
    session_id = _session_id(payload)
    if roots is None or session_id is None:
        return
    _, data_root = roots
    tool_name = _first(payload, "toolName", "tool_name", "tool")
    if not isinstance(tool_name, str) or tool_name.lower() not in SUPPORTED_TOOLS:
        return
    cwd = _event_cwd(payload, data_root)
    if cwd is None:
        return
    candidates = _candidate_paths(tool_name, _tool_input(payload))
    fields = {
        "turnId": _first(payload, "turnId", "turn_id"),
        "tool": tool_name,
        "cwd": str(cwd),
        "files": _file_records(cwd, candidates, data_root),
    }
    _append_event(data_root, session_id, fields)


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
    except (OSError, KeyError, TypeError, ValueError, RuntimeError):
        # RuntimeError covers WorkStateError: the durable-evidence check calls
        # into work_state, whose domain error is a RuntimeError, and letting it
        # escape here made the whole stop gate vanish through main's blanket
        # handler instead of refusing this one receipt.
        return False


def _validated_evidence_identity(value: object, validator: str) -> str:
    """Check who produced the receipt, and that someone else is signing it."""
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("evidence must use schema_version 1")
    if value.get("type") not in {"command", "test_cycle"}:
        raise ValueError("evidence type must be command or test_cycle")
    executor = value.get("executor")
    if not isinstance(executor, str) or not executor.strip():
        raise ValueError("evidence must identify its executor")
    if not validator.strip() or validator.strip() == executor.strip():
        raise ValueError("validator must be non-empty and different from the executor")
    return executor.strip()


def _validated_evidence_command(value: dict[str, Any]) -> None:
    """Check that a real command ran and reported success."""
    command = value.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(argument, str) and argument for argument in command)
    ):
        raise ValueError("evidence command must be a non-empty argv array")
    if value.get("exit_code") != 0:
        raise ValueError("evidence command did not exit successfully")
    if value.get("type") == "test_cycle" and value.get("command_started") is not True:
        raise ValueError(
            "test-cycle evidence does not prove that the green command started"
        )


def _validated_evidence_digests(value: dict[str, Any]) -> None:
    """Check that the receipt is bound to output and to a source revision."""
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


def _validated_command_evidence(
    evidence: Path, validator: str
) -> tuple[dict[str, Any], str]:
    """Refuse anything that is not a signed, successful command receipt.

    The three checks stay in this order because the messages are the contract:
    a receipt missing its executor must say so before anything reports what its
    command did, or the refusal names the wrong defect.
    """
    try:
        value = json.loads(evidence.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("evidence must be a readable JSON receipt") from error
    executor = _validated_evidence_identity(value, validator)
    _validated_evidence_command(value)
    _validated_evidence_digests(value)
    return value, executor


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


class _DurableRoots(NamedTuple):
    """The four paths a durable receipt's identity is checked against."""

    workspace: Path
    session_dir: Path
    plugin_root: Path
    data_root: Path


def _durable_session_dir(evidence: Path, data_root: Path) -> Path:
    """Locate the durable session a receipt claims to belong to."""
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
    return evidence_root.parent


def _shares_one_tree(event_cwd: Path, workspace: Path) -> bool:
    """Whether the durable session and the recorded edit sit in one tree.

    Containment used to be demanded in one direction -- the workspace had to
    contain ``cwd`` -- which is not a stricter rule but an unsatisfiable one
    wherever the session cwd is an ancestor of the data root: every workspace
    containing that cwd also contains the evidence store, which ``init``
    refuses to root a session at, and every workspace that avoids the store
    failed this check instead.

    The descendant direction concedes nothing. A receipt binds to the latest
    event's hash and never to a file, so an enclosing workspace was never
    evidence that the receipt was about the edit; both directions prove only
    that the session and the edit are not unrelated trees.
    """
    return (
        event_cwd == workspace
        or _inside(event_cwd, workspace)
        or _inside(workspace, event_cwd)
    )


def _durable_workspace(state: dict[str, Any], event_cwd: Path) -> Path:
    """Place the recorded edit against the workspace the session declared."""
    workspace_value = state.get("workspace_root")
    if not isinstance(workspace_value, str):
        raise ValueError("evidence session has no workspace root")
    workspace = Path(workspace_value).resolve()
    if not workspace.is_dir() or not _shares_one_tree(event_cwd, workspace):
        # Naming both paths is the point. The bare refusal read as a mistyped
        # --session-id or a stale receipt -- operator error -- rather than as a
        # relationship between two directories the operator can go and inspect.
        raise ValueError(
            "evidence belongs to a different workspace: the durable session is "
            f"rooted at {workspace}, which neither contains nor lies inside "
            f"{event_cwd}, the working directory the edit was recorded under"
        )
    return workspace


def _durable_session_state(
    session_dir: Path, event_cwd: Path
) -> tuple[dict[str, Any], Path]:
    """Read the session state and relate the edit to its workspace."""
    state_path = session_dir / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("evidence session state is unreadable") from error
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError("evidence session state is malformed")
    return state, _durable_workspace(state, event_cwd)


def _durable_state_identity(
    state: dict[str, Any], value: dict[str, Any], roots: _DurableRoots
) -> None:
    """Bind the receipt to this session, this project, and this revision."""
    work_state = _load_work_state(roots.plugin_root)
    expected_session = work_state.session_directory(
        roots.workspace, roots.data_root, str(state.get("session_id", ""))
    )
    if expected_session != roots.session_dir.resolve():
        raise ValueError("evidence is outside its declared durable session")
    if state.get("project_key") != work_state.project_key(roots.workspace):
        raise ValueError("evidence session project identity changed")
    current_fingerprint = work_state.source_fingerprint(
        roots.workspace, roots.data_root
    )
    if value["source_fingerprint"].get("sha256") != current_fingerprint["sha256"]:
        raise ValueError("evidence source fingerprint is stale or fabricated")


def _durable_criterion_entry(
    state: dict[str, Any], criterion_id: object
) -> dict[str, Any]:
    """Find the independently verified criterion the receipt claims to close."""
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
    return criterion


def _durable_criterion_receipt(
    criterion: dict[str, Any], evidence: Path, session_dir: Path
) -> None:
    """Check that this file is the criterion's active receipt, not a past one."""
    receipt_value = criterion.get("receipt")
    if (
        not isinstance(receipt_value, str)
        or (session_dir / receipt_value).resolve() != evidence
    ):
        raise ValueError("evidence is not the criterion's active receipt")


def _durable_criterion_verification(
    criterion: dict[str, Any], evidence: Path, validator: str
) -> None:
    """Check that a different party confirmed this exact receipt."""
    verification = criterion.get("verification")
    if (
        not isinstance(verification, dict)
        or verification.get("verdict") != "confirmed"
        or verification.get("verifier") != validator.strip()
        or verification.get("receipt_sha256") != _sha256_file(evidence)
    ):
        raise ValueError("evidence lacks matching independent verification")


def _validated_durable_evidence(
    evidence: Path,
    validator: str,
    plugin_root: Path,
    data_root: Path,
    event_cwd: Path,
) -> tuple[dict[str, Any], str]:
    """Refuse a receipt any durable check cannot account for, in refusal order."""
    value, executor = _validated_command_evidence(evidence, validator)
    session_dir = _durable_session_dir(evidence, data_root)
    state, workspace = _durable_session_state(session_dir, event_cwd)
    roots = _DurableRoots(workspace, session_dir, plugin_root, data_root)
    _durable_state_identity(state, value, roots)
    criterion = _durable_criterion_entry(state, value.get("criterion_id"))
    _durable_criterion_receipt(criterion, evidence, session_dir)
    _durable_criterion_verification(criterion, evidence, validator)
    if criterion.get("executor") != executor:
        raise ValueError("evidence executor does not match durable state")
    return value, executor


def _stop_remediation(message: str) -> str:
    """Spell the fix out for the agent, which cannot read systemMessage.

    Claude Code shows systemMessage to the user only; the agent never sees it.
    Without this the warning names a gap the one party able to close it cannot
    read. additionalContext reaches the agent and, unlike a block decision,
    leaves the hook fail-open.
    """
    return (
        f"{message} Resolve it by producing a real command receipt with "
        "work_state.py run or run-green, confirming that criterion "
        "through work_state.py verify with a different verifier, then "
        "recording it with selective_hooks.py record-validation. Do not "
        "claim the criterion is complete until that receipt exists."
    )


def _enclosing_root_note(data_root: Path, event_cwd: Path | None) -> str:
    """Say where the durable session may be rooted when cwd encloses the store.

    ``work_state.py`` refuses a workspace that contains its own evidence store,
    so where the session cwd is an ancestor of the data root -- a drive root,
    the home directory -- the remediation below cannot be rooted at cwd. Left
    unsaid, an operator follows the three steps, is refused, and reads the
    refusal as a mistake of their own rather than as a constraint.
    """
    if event_cwd is None or not (
        data_root == event_cwd or _inside(data_root, event_cwd)
    ):
        return ""
    return (
        f" The data root {str(data_root)!r} sits inside this session's working "
        f"directory {str(event_cwd)!r}, so work_state.py will refuse to root the "
        "durable session there: root it at the subdirectory the edits landed in "
        "-- one the host does not write to continuously, or the source "
        "fingerprint goes stale between run and verify -- or set "
        "COGNITIVE_POWERS_DATA to a directory outside this working directory."
    )


def _stop_warning(
    session_id: str, data_root: Path, verdict: _StopVerdict
) -> dict[str, Any]:
    """Shape the warning for a session whose latest edit no receipt covers."""
    detail = f" ({verdict.error})" if verdict.error else ""
    message = (
        f"Cognitive Powers session {session_id!r} recorded an edit-tool call "
        f"(ledger under {str(data_root)!r}), "
        "but no current, hash-bound validation receipt covers the latest "
        f"edit{detail}."
    ) + _enclosing_root_note(data_root, verdict.event_cwd)
    output: dict[str, Any] = {"systemMessage": message}
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        output["hookSpecificOutput"] = {
            "hookEventName": "Stop",
            "additionalContext": _stop_remediation(message),
        }
    return output


def _ledger_state(
    data_root: Path, session_id: str
) -> tuple[list[dict[str, Any]], str | None] | None:
    """Read the ledger under its lock, or None when there is none to read.

    Taking the lock first created a one-byte .lock beside a ledger that may
    never exist. Every turn-ending of every read-only session left one behind
    and nothing prunes them. A missing ledger has nothing to serialise against.
    """
    ledger = _ledger_path(data_root, session_id)
    if not ledger.is_file():
        return None
    with _ledger_lock(ledger):
        return _read_ledger(ledger)


def _latest_event_verdict(
    roots: tuple[Path, Path], session_id: str, event: dict[str, Any]
) -> _StopVerdict:
    """Decide the verdict for a ledger that ends in a trustworthy event."""
    plugin_root, data_root = roots
    recorded_cwd = event["cwd"]
    event_cwd = Path(recorded_cwd).resolve()
    covered = _receipt_is_current(
        _receipt_path(data_root, session_id),
        session_id,
        event["eventHash"],
        plugin_root,
        data_root,
        event_cwd,
    )
    # An oversized payload records an empty cwd, which resolves to whatever
    # directory this process happens to be in -- not the session's, and so not
    # a directory the warning may reason about.
    return _StopVerdict(not covered, None, event_cwd if recorded_cwd else None)


def _stop_verdict(plugin_root: Path, data_root: Path, session_id: str) -> _StopVerdict:
    """Whether the latest edit needs a warning, and what broke if one did."""
    state = _ledger_state(data_root, session_id)
    if state is None:
        return _StopVerdict(False, None, None)
    events, error = state
    # An unreadable ledger warns on its own: no trustworthy latest event exists.
    if error is not None:
        return _StopVerdict(True, error, None)
    if not events:
        return _StopVerdict(False, None, None)
    return _latest_event_verdict((plugin_root, data_root), session_id, events[-1])


def stop(payload: dict[str, Any]) -> None:
    roots = _roots()
    session_id = _session_id(payload)
    if roots is None or session_id is None:
        return
    plugin_root, data_root = roots
    verdict = _stop_verdict(plugin_root, data_root, session_id)
    if verdict.warn:
        print(
            json.dumps(
                _stop_warning(session_id, data_root, verdict), ensure_ascii=False
            )
        )


def _validated_evidence_file(evidence_value: str) -> tuple[Path | None, str | None]:
    """Resolve the named receipt, or say why the path cannot be one."""
    evidence_input = Path(evidence_value).expanduser()
    if evidence_input.is_symlink():
        return None, "evidence cannot be a symlink"
    evidence = evidence_input.resolve()
    if not evidence.is_file() or evidence.stat().st_size == 0:
        return None, "evidence must be a non-empty regular file"
    return evidence, None


def _write_receipt(target: Path, receipt: dict[str, Any]) -> None:
    """Put the receipt in place in one step, so no reader sees half of one."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


def record_validation(
    session_id: str,
    evidence_value: str,
    validator: str,
    data_override: str | None = None,
) -> int:
    roots = _roots(data_override)
    if roots is None:
        print("COGNITIVE_POWERS_DATA must resolve outside PLUGIN_ROOT", file=sys.stderr)
        return 2
    plugin_root, data_root = roots
    ledger = _ledger_path(data_root, session_id)
    with _ledger_lock(ledger):
        events, error = _read_ledger(ledger)
        if error or not events:
            print(error or "no edit events exist for this session", file=sys.stderr)
            return 2
        evidence, refusal = _validated_evidence_file(evidence_value)
        if evidence is None:
            print(refusal, file=sys.stderr)
            return 2
        try:
            evidence_payload, executor = _validated_durable_evidence(
                evidence,
                validator,
                plugin_root,
                data_root,
                Path(events[-1]["cwd"]).resolve(),
            )
        except (ValueError, RuntimeError) as error:
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
        _write_receipt(target, receipt)
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


def _run_record_validation(args: argparse.Namespace) -> int:
    """Report a refusal rather than a traceback.

    The stop gate names this command as the remediation path, so its failures
    must stay readable refusals. It reports errors and exits nonzero on
    purpose, unlike the two observability events, which must never block the
    host.
    """
    try:
        return record_validation(
            args.session_id, args.evidence, args.validator, args.data_root
        )
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2


def _run_event(command: str) -> None:
    """Record one observability event from the payload on stdin."""
    payload = _read_payload()
    if isinstance(payload, OversizedPayload):
        # Only the edit event can be degraded and still mean something. A Stop
        # payload is small, so an oversized one is not a real case.
        if command == "post-tool-use":
            record_oversized_payload(payload.prefix)
    elif payload is not None:
        post_tool_use(payload) if command == "post-tool-use" else stop(payload)


def main(argv: list[str] | None = None) -> int:
    # Hook output is UTF-8 JSON for the host, not console text: on a legacy
    # Windows codepage an ensure_ascii=False payload crashed the print itself.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "record-validation":
        return _run_record_validation(args)
    try:
        _run_event(args.command)
    except Exception:
        # Observability must never turn an incomplete payload or I/O race into a tool block.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
