"""Storage, identity, locking, and WAL primitives for durable work state."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl

SCHEMA_VERSION = 1
MIGRATION_POLICY_SCHEMA_VERSION = 1
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_STALE_SECONDS = 30.0
OUTPUT_TAIL_CHARS = 8_000

IGNORED_DIRECTORIES = frozenset(
    {
        ".cognitive-powers",
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "bin",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "obj",
        "blob-report",
        "playwright-report",
        "target",
        "test-results",
        "vendor",
        "venv",
    }
)

IGNORED_SOURCE_SUFFIXES = frozenset(
    {
        ".7z",
        ".avi",
        ".bmp",
        ".class",
        ".dll",
        ".dylib",
        ".exe",
        ".gif",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp3",
        ".mp4",
        ".o",
        ".obj",
        ".pdf",
        ".png",
        ".pyc",
        ".so",
        ".tar",
        ".ttf",
        ".wav",
        ".webp",
        ".woff",
        ".woff2",
        ".zip",
    }
)

IGNORED_SOURCE_FILES = frozenset({".coverage", "coverage.xml"})
VALID_VERDICTS = frozenset({"confirmed", "rejected", "inconclusive"})
RUNNABLE_STATUSES = frozenset(
    {"pending", "failed", "blocked", "rejected", "inconclusive", "stale"}
)


class WorkStateError(RuntimeError):
    """Raised when a durable-state contract would be violated."""


class EvidenceStaleError(WorkStateError):
    """Raised when evidence no longer identifies the reviewed source or receipt."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_identifier(value: str, label: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")[:80]
    if not sanitized:
        raise WorkStateError(
            f"{label} must contain letters, digits, dots, underscores, or hyphens"
        )
    return sanitized


def resolve_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise WorkStateError(f"workspace root is not a directory: {root}")
    return root


def resolve_data_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get("COGNITIVE_POWERS_DATA") or os.environ.get(
        "PLUGIN_DATA"
    )
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".codex" / "cognitive-powers").resolve()


def project_key(root: Path) -> str:
    canonical = str(root)
    if os.name == "nt":
        canonical = canonical.casefold()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def session_directory(root: Path, data_root: Path, session_id: str) -> Path:
    if _is_within(data_root, root):
        raise WorkStateError(
            f"durable data root must be outside the workspace: {data_root}"
        )
    return (
        data_root
        / "projects"
        / project_key(root)
        / "sessions"
        / sanitize_identifier(session_id, "session")
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ignored_source_directory(name: str) -> bool:
    normalized = name.lower()
    return (
        normalized in IGNORED_DIRECTORIES
        or normalized == ".codegraph"
        or normalized.startswith(".codegraph-")
    )


def source_fingerprint(root: Path, data_root: Path) -> dict[str, object]:
    """Hash the source-oriented workspace surface in stable path order."""
    if _is_within(data_root, root):
        raise WorkStateError(
            f"durable data root must be outside the workspace: {data_root}"
        )
    aggregate = hashlib.sha256()
    file_count = 0

    def walk_error(error: OSError) -> None:
        raise WorkStateError(
            f"cannot enumerate workspace source under {root}: {error}"
        ) from error

    for current, directories, filenames in os.walk(
        root, followlinks=False, onerror=walk_error
    ):
        current_path = Path(current)
        directories[:] = sorted(
            directory
            for directory in directories
            if not _ignored_source_directory(directory)
        )
        for filename in sorted(filenames):
            path = current_path / filename
            if (
                filename in IGNORED_SOURCE_FILES
                or path.suffix.lower() in IGNORED_SOURCE_SUFFIXES
                or path.is_symlink()
            ):
                continue
            try:
                relative = path.relative_to(root).as_posix()
                file_digest = _sha256_file(path)
            except OSError as error:
                raise WorkStateError(
                    f"cannot fingerprint source file {path}: {error}"
                ) from error
            aggregate.update(relative.encode("utf-8"))
            aggregate.update(b"\0")
            aggregate.update(file_digest.encode("ascii"))
            aggregate.update(b"\n")
            file_count += 1
    return {"sha256": aggregate.hexdigest(), "files": file_count}


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        open_process.restype = ctypes.c_void_p
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        get_exit_code.restype = ctypes.c_int
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        handle = open_process(0x1000, 0, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            exit_code = ctypes.c_ulong()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == 259
        finally:
            close_handle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        return error.errno != errno.ESRCH
    return True


def _process_identity(pid: int) -> str | None:
    """Return a PID-reuse-resistant process creation identity when available."""

    if pid <= 0:
        return None
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

        open_process = kernel32.OpenProcess
        open_process.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        open_process.restype = ctypes.c_void_p
        get_process_times = kernel32.GetProcessTimes
        get_process_times.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        get_process_times.restype = ctypes.c_int
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        handle = open_process(0x1000, 0, pid)
        if not handle:
            return None
        try:
            creation = FileTime()
            exit_time = FileTime()
            kernel_time = FileTime()
            user_time = FileTime()
            if not get_process_times(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return None
            created = (creation.high << 32) | creation.low
            return f"windows-filetime:{created}"
        finally:
            close_handle(handle)

    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        raw = proc_stat.read_text(encoding="utf-8")
    except OSError:
        raw = ""
    if raw:
        closing = raw.rfind(")")
        fields = raw[closing + 1 :].split() if closing >= 0 else []
        if len(fields) > 19:
            return f"proc-start-ticks:{fields[19]}"
    try:
        completed = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    started = " ".join(completed.stdout.split())
    return f"ps-lstart:{started}" if completed.returncode == 0 and started else None


def _process_matches_identity(pid: int, expected: str | None) -> bool:
    if not _process_is_alive(pid):
        return False
    if expected is None:
        return True
    current = _process_identity(pid)
    if current is None:
        # An inaccessible creation time is not evidence that a live PID was reused.
        # Preserve ownership until the process dies or an actual mismatch is observed.
        return True
    return secrets.compare_digest(current, expected)


def _read_lock_identity(
    lock_path: Path,
) -> tuple[int | None, str | None, str | None, str]:
    raw_bytes = lock_path.read_bytes()
    identity = hashlib.sha256(raw_bytes).hexdigest()
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None, None, None, identity
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        fields = raw.split()
        try:
            pid = int(fields[0])
        except (IndexError, ValueError):
            pid = None
        return pid, None, None, identity
    if not isinstance(payload, dict):
        return None, None, None, identity
    pid = payload.get("pid")
    token = payload.get("token")
    process_identity = payload.get("process_identity")
    return (
        pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        token if isinstance(token, str) and token else None,
        process_identity
        if isinstance(process_identity, str) and process_identity
        else None,
        identity,
    )


def _try_lock_guard(descriptor: int) -> bool:
    os.lseek(descriptor, 0, os.SEEK_SET)
    try:
        if os.name == "nt":
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return False
        raise
    return True


def _unlock_guard(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextlib.contextmanager
def _state_lock_guard(lock_path: Path) -> Iterator[None]:
    """Serialize lock-file generation changes; the OS releases this on crash."""
    guard_path = lock_path.with_name(f"{lock_path.name}.guard")
    descriptor = os.open(guard_path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        while not acquired:
            acquired = _try_lock_guard(descriptor)
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise WorkStateError(
                    f"timed out waiting for state lock guard: {guard_path}"
                )
            time.sleep(0.05)
        yield
    finally:
        if acquired:
            _unlock_guard(descriptor)
        os.close(descriptor)


def _unlink_lock_if_identity_guarded(lock_path: Path, identity: str) -> bool:
    try:
        current = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return False
    if current != identity:
        return False
    try:
        lock_path.unlink()
    except FileNotFoundError:
        return False
    return True


def _unlink_lock_if_identity(lock_path: Path, identity: str) -> bool:
    with _state_lock_guard(lock_path):
        return _unlink_lock_if_identity_guarded(lock_path, identity)


@contextlib.contextmanager
def session_lock(session_dir: Path) -> Iterator[None]:
    session_dir.mkdir(parents=True, exist_ok=True)
    lock_path = session_dir / ".state.lock"
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    descriptor: int | None = None
    token = secrets.token_hex(16)
    while descriptor is None:
        reclaimed = False
        with _state_lock_guard(lock_path):
            try:
                descriptor = os.open(
                    lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                try:
                    payload = {
                        "schema_version": 1,
                        "pid": os.getpid(),
                        "token": token,
                        "process_identity": _process_identity(os.getpid()),
                        "created_at": utc_now(),
                    }
                    lock_bytes = (json.dumps(payload, sort_keys=True) + "\n").encode(
                        "utf-8"
                    )
                    written = 0
                    while written < len(lock_bytes):
                        count = os.write(descriptor, lock_bytes[written:])
                        if count <= 0:
                            raise OSError("lock identity write made no progress")
                        written += count
                    os.fsync(descriptor)
                except OSError:
                    os.close(descriptor)
                    descriptor = None
                    with contextlib.suppress(FileNotFoundError):
                        lock_path.unlink()
                    raise
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
            except FileExistsError:
                try:
                    owner_pid, _, owner_identity, identity = _read_lock_identity(
                        lock_path
                    )
                    age = max(0.0, time.time() - lock_path.stat().st_mtime)
                except (FileNotFoundError, PermissionError):
                    reclaimed = True
                else:
                    owner_is_dead = (
                        owner_pid is not None
                        and not _process_matches_identity(owner_pid, owner_identity)
                    )
                    unidentified_is_stale = (
                        owner_pid is None and age >= LOCK_STALE_SECONDS
                    )
                    if owner_is_dead or unidentified_is_stale:
                        reclaimed = _unlink_lock_if_identity_guarded(
                            lock_path, identity
                        )
        if descriptor is not None:
            break
        if reclaimed:
            continue
        if time.monotonic() >= deadline:
            raise WorkStateError(f"timed out waiting for state lock: {lock_path}")
        time.sleep(0.05)
    try:
        yield
    finally:
        identity: str | None = None
        current_token: str | None = None
        try:
            _, current_token, _, identity = _read_lock_identity(lock_path)
        except (FileNotFoundError, PermissionError):
            pass
        if current_token == token and identity is not None:
            _unlink_lock_if_identity(lock_path, identity)


def _state_path(session_dir: Path) -> Path:
    return session_dir / "state.json"


def state_migration_report(session_dir: Path) -> dict[str, object]:
    """Inspect state schema compatibility without changing session files."""
    path = _state_path(session_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkStateError(f"state is unreadable: {path}: {error}") from error
    if not isinstance(payload, dict):
        raise WorkStateError(f"state is not an object: {path}")
    version = payload.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise WorkStateError(f"state has malformed schema_version: {path}")
    if version != SCHEMA_VERSION:
        raise WorkStateError(
            f"state schema_version {version} is unsupported; "
            f"this checkout supports {SCHEMA_VERSION} and has no migration path"
        )
    _validate_state_payload(payload, path)
    ledger_events = _read_ledger_events(session_dir)
    for event in ledger_events:
        snapshot = event.get("_state_snapshot")
        if snapshot is not None:
            if not isinstance(snapshot, dict):
                raise WorkStateError(
                    f"ledger contains a malformed state snapshot: "
                    f"{session_dir / 'ledger.jsonl'}"
                )
            _validate_state_payload(
                snapshot,
                session_dir / "ledger.jsonl",
                label="ledger state snapshot",
            )
    return {
        "policy_schema_version": MIGRATION_POLICY_SCHEMA_VERSION,
        "mode": "dry-run",
        "status": "current",
        "state_schema_version": version,
        "target_schema_version": SCHEMA_VERSION,
        "migration_required": False,
        "available_migrations": [],
        "backup_required_before_apply": True,
        "backup_created": False,
        "ledger_events": len(ledger_events),
        "ledger_validated": True,
    }


def _read_ledger_events(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "ledger.jsonl"
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise WorkStateError(f"ledger is unreadable: {path}: {error}") from error
    for line_number, line in enumerate(lines, 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise WorkStateError(
                f"ledger line {line_number} is malformed: {path}: {error}"
            ) from error
        if not isinstance(value, dict):
            raise WorkStateError(
                f"ledger line {line_number} is not an event object: {path}"
            )
        events.append(value)
    return events


def _state_sequence(payload: dict[str, Any], label: str) -> int:
    sequence = payload.get("last_seq")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise WorkStateError(f"{label} has malformed last_seq")
    return sequence


def _validate_state_payload(
    payload: dict[str, Any], path: Path, *, label: str = "state"
) -> None:
    version = payload.get("schema_version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != SCHEMA_VERSION
    ):
        raise WorkStateError(f"unsupported or malformed {label}: {path}")
    _state_sequence(payload, label)
    criteria = payload.get("criteria")
    if not isinstance(criteria, list) or not all(
        isinstance(item, dict) for item in criteria
    ):
        raise WorkStateError(f"{label} has malformed criteria: {path}")
    if "work_packets" in payload and (
        not isinstance(payload["work_packets"], list)
        or not all(isinstance(item, dict) for item in payload["work_packets"])
    ):
        raise WorkStateError(f"{label} has malformed work_packets: {path}")


def _latest_ledger_snapshot(session_dir: Path) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    for event in _read_ledger_events(session_dir):
        candidate = event.get("_state_snapshot")
        if not isinstance(candidate, dict):
            continue
        _validate_state_payload(
            candidate,
            session_dir / "ledger.jsonl",
            label="ledger state snapshot",
        )
        if latest is None or _state_sequence(
            candidate, "ledger state snapshot"
        ) > _state_sequence(latest, "ledger state snapshot"):
            latest = candidate
    return latest


def load_state(session_dir: Path) -> dict[str, Any]:
    path = _state_path(session_dir)
    payload: dict[str, Any] | None = None
    state_error: OSError | json.JSONDecodeError | None = None
    if path.is_file():
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict):
                payload = candidate
        except (OSError, json.JSONDecodeError) as error:
            state_error = error
    ledger_snapshot = _latest_ledger_snapshot(session_dir)
    if ledger_snapshot is not None:
        try:
            payload_sequence = (
                _state_sequence(payload, "state") if payload is not None else -1
            )
        except WorkStateError:
            payload_sequence = -1
        if _state_sequence(ledger_snapshot, "ledger state snapshot") > payload_sequence:
            payload = ledger_snapshot
    if payload is None:
        if state_error is not None:
            raise WorkStateError(f"state is unreadable: {path}: {state_error}")
        raise WorkStateError(f"session does not exist: {session_dir}")
    _validate_state_payload(payload, path)
    return payload


def _append_ledger(session_dir: Path, event: dict[str, object]) -> None:
    ledger_path = session_dir / "ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
