"""Storage, identity, locking, and WAL primitives for durable work state."""

from __future__ import annotations

import contextlib
import copy
import ctypes
import errno
import hashlib
import importlib.util
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import time
import unicodedata
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
LEDGER_CHECKPOINT_INTERVAL = 32
LEDGER_MAX_EVENTS = 128


def _load_storage_policy():
    plugin_root = Path(__file__).resolve().parents[4]
    policy_path = plugin_root / "scripts" / "storage_policy.py"
    identity = hashlib.sha256(str(policy_path).encode("utf-8")).hexdigest()[:16]
    module_name = f"_cognitive_storage_policy_{identity}"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, policy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load shared storage policy from {policy_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_STORAGE_POLICY = _load_storage_policy()
IGNORED_DIRECTORIES = frozenset(_STORAGE_POLICY.EXCLUDED_DIRECTORY_NAMES)

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


# Windows resolves these base names to devices rather than to paths, whatever
# extension follows and whatever directory they sit in. A session named "NUL"
# therefore passed every identifier rule, appeared to create its directory, and
# then failed inside the lock with a bare FileNotFoundError from os.open --
# the traceback this module exists to replace with a named refusal. Rejected on
# every platform rather than under os.name == "nt": a store written on Linux is
# copied to Windows, and a session that only becomes unreadable after the copy
# is worse than one that was never created.
_RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{digit}" for digit in "0123456789"}
    | {f"LPT{digit}" for digit in "0123456789"}
)


def sanitize_identifier(value: str, label: str) -> str:
    """Reduce an actor or session name to one stable identity.

    Compose first. Without it the substitution below reads the two Unicode
    spellings of one name differently: composed ``é`` is a single unmapped
    codepoint and disappears entirely, while decomposed ``é`` keeps its ``e``
    and loses only the combining mark. ``agent-café`` then yields
    ``agent-caf`` or ``agent-cafe`` depending on how it was typed.

    That is not cosmetic. Executor and verifier identities are compared to
    refuse self-verification, so one actor could present each form and confirm
    its own work.
    """
    composed = unicodedata.normalize("NFC", value)
    # Truncate before the final strip: doing it the other way round lets the
    # cut reintroduce a trailing separator the strip had just removed.
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", composed.strip())[:80].strip("-.")
    if not sanitized:
        raise WorkStateError(
            f"{label} must contain letters, digits, dots, underscores, or hyphens"
        )
    if sanitized.partition(".")[0].upper() in _RESERVED_DEVICE_NAMES:
        raise WorkStateError(f"{label} must not name a reserved device: {sanitized}")
    return sanitized


def resolve_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise WorkStateError(f"workspace root is not a directory: {root}")
    return root


def resolve_data_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get("COGNITIVE_POWERS_DATA")
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


def canonical_session_name(value: str) -> str:
    """Return the caller's session name as stored, before identifier folding."""
    return unicodedata.normalize("NFC", value).strip()


def _assert_session_name(directory: Path, requested: str) -> None:
    """Refuse a name that merely collides with the stored session.

    ``sanitize_identifier`` is deliberately lossy, so distinct names share one
    directory: ``release/alpha``, ``release alpha`` and ``release:alpha`` all
    reduce to ``release-alpha``, as do any two names sharing an 80-character
    prefix. Without this check the caller silently reads, and then mutates,
    another session's durable state.

    Sessions written before this field existed carry no stored name; those are
    accepted unchanged rather than being made unreadable.

    A caller may address a session either by the name it was created with or by
    the folded identifier, which is what ``init`` prints and what the hook reads
    back out of ``state.json``. Only a third, genuinely different name is
    refused.

    An unreadable ``state.json`` is not this function's business: ``load_state``
    exists to rebuild one from the ledger, and raising here would break the
    commands that recovery depends on.
    """
    try:
        stored = json.loads((directory / "state.json").read_text(encoding="utf-8")).get(
            "session_name"
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return
    if not isinstance(stored, str):
        return
    canonical = canonical_session_name(requested)
    if canonical in {stored, sanitize_identifier(stored, "session")}:
        return
    raise WorkStateError(
        f"session {canonical!r} collides with the stored session {stored!r}: "
        "both reduce to the same identifier, so this request would act on "
        "the wrong session"
    )


def _legacy_identifier(value: str) -> str:
    """Reproduce the identifier this module produced before names were composed.

    Composing first changed where a decomposed name lands: ``sprint-café``
    typed decomposed used to fold to ``sprint-cafe`` and now folds to
    ``sprint-caf``. Without this, an existing session silently becomes
    unreachable and a fresh ``init`` orphans it at the new path.
    """
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")[:80]


def session_directory(root: Path, data_root: Path, session_id: str) -> Path:
    # The project key is a digest of the root, so the caller's spelling decides
    # where durable state lives. macOS resolves "/var" onto "/private/var" and
    # Windows expands 8.3 names, which silently splits one workspace into two
    # stores and makes an existing session unreachable. Canonicalizing here
    # keeps every entry point -- CLI, hook, or direct call -- on one location,
    # and stops a symlinked data root from escaping the containment check below.
    root = root.expanduser().resolve()
    data_root = data_root.expanduser().resolve()
    if _is_within(data_root, root):
        raise WorkStateError(
            f"durable data root must be outside the workspace: {data_root}"
        )
    sessions = data_root / "projects" / project_key(root) / "sessions"
    directory = sessions / sanitize_identifier(session_id, "session")
    if not (directory / "state.json").is_file():
        legacy = _legacy_identifier(session_id)
        if legacy and (sessions / legacy / "state.json").is_file():
            directory = sessions / legacy
    _assert_session_name(directory, session_id)
    return directory


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ignored_source_directory(name: str) -> bool:
    normalized = name.casefold()
    return (
        normalized in IGNORED_DIRECTORIES
        or normalized == ".cognitive-powers"
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
            relative = path.relative_to(root)
            if (
                _STORAGE_POLICY.is_excluded_relative(relative)
                or filename in IGNORED_SOURCE_FILES
                or path.suffix.lower() in IGNORED_SOURCE_SUFFIXES
                or path.is_symlink()
            ):
                continue
            try:
                relative_text = relative.as_posix()
                file_digest = _sha256_file(path)
            except OSError as error:
                raise WorkStateError(
                    f"cannot fingerprint source file {path}: {error}"
                ) from error
            aggregate.update(relative_text.encode("utf-8"))
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
        # On Windows, replacing a file a concurrent reader (the Stop hook
        # reads state.json without the session lock) holds open raises
        # PermissionError. The read window is milliseconds; retry briefly
        # rather than turning an observability race into a traceback.
        if os.name == "nt":
            for delay in (0.01, 0.05, 0.25):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    time.sleep(delay)
            else:
                os.replace(temporary, path)
        else:
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
                except FileNotFoundError:
                    reclaimed = True
                except PermissionError:
                    # An unreadable lock is not a reclaimed one: reporting it
                    # as reclaimed skipped the deadline check and the sleep
                    # below, spinning at full speed with no timeout.
                    pass
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
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
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
    _recover_state_from_events(session_dir, ledger_events)
    if (session_dir / "recovery.json").exists():
        _read_recovery_checkpoint(session_dir)
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
        content = path.read_text(encoding="utf-8")
    # An interrupted append can leave bytes that are not valid UTF-8, exactly
    # the corruption load_state documents for state.json. UnicodeDecodeError is
    # a ValueError rather than an OSError, so it escaped this handler and every
    # command answered a torn ledger with a traceback instead of failing closed.
    except (OSError, UnicodeDecodeError) as error:
        raise WorkStateError(f"ledger is unreadable: {path}: {error}") from error
    # One physical line is one record. read_text has already folded every
    # platform newline to "\n"; splitlines() additionally breaks on U+2028,
    # U+2029, and U+0085, which json.dumps(ensure_ascii=False) leaves raw
    # inside a record, so one event containing one of those separators became
    # two malformed lines and the session was unreadable from init onward.
    # Only the final terminator is dropped: an interior blank line is still
    # corruption and must keep failing as such.
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    parsed: list[dict[str, Any]] = []
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
        snapshot = value.get("_state_snapshot")
        if snapshot is not None:
            if not isinstance(snapshot, dict):
                raise WorkStateError(f"ledger state snapshot is malformed: {path}")
            _validate_state_payload(snapshot, path, label="ledger state snapshot")
        parsed.append(value)

    # Parse and validate the semantic envelope first so diagnostics remain useful,
    # but never return an event until the complete authenticated chain verifies.
    key_path = session_dir / ".ledger.key"
    try:
        key = bytes.fromhex(key_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError) as error:
        raise WorkStateError(
            f"ledger authentication key is unavailable: {key_path}"
        ) from error
    if len(key) != 32:
        raise WorkStateError(f"ledger authentication key is malformed: {key_path}")
    previous = "0" * 64
    for line_number, value in enumerate(parsed, 1):
        supplied = value.get("_ledger_auth")
        unsigned = {key: item for key, item in value.items() if key != "_ledger_auth"}
        canonical = json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        expected = hmac.new(
            key, previous.encode("ascii") + b"\0" + canonical, hashlib.sha256
        ).hexdigest()
        if (
            not isinstance(supplied, dict)
            or supplied.get("previous") != previous
            or not isinstance(supplied.get("digest"), str)
            or not hmac.compare_digest(supplied["digest"], expected)
        ):
            raise WorkStateError(
                f"ledger line {line_number} fails authenticated hash chain: {path}"
            )
        previous = expected
        events.append(unsigned)
    return events


def _state_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _state_delta(
    before: Any, after: Any, path: tuple[str | int, ...] = ()
) -> list[dict[str, Any]]:
    """Return deterministic JSON operations without copying unchanged state."""
    if type(before) is not type(after):
        return [{"op": "set", "path": list(path), "value": copy.deepcopy(after)}]
    if isinstance(before, dict):
        operations: list[dict[str, Any]] = []
        for key in sorted(before.keys() - after.keys()):
            operations.append({"op": "delete", "path": [*path, key]})
        for key in sorted(after):
            if key not in before:
                operations.append(
                    {
                        "op": "set",
                        "path": [*path, key],
                        "value": copy.deepcopy(after[key]),
                    }
                )
            else:
                operations.extend(_state_delta(before[key], after[key], (*path, key)))
        return operations
    if isinstance(before, list):
        if len(before) != len(after):
            return [{"op": "set", "path": list(path), "value": copy.deepcopy(after)}]
        operations = []
        for index, (left, right) in enumerate(zip(before, after)):
            operations.extend(_state_delta(left, right, (*path, index)))
        return operations
    if before != after:
        return [{"op": "set", "path": list(path), "value": copy.deepcopy(after)}]
    return []


def _apply_state_delta(
    before: dict[str, Any], operations: list[dict[str, Any]]
) -> dict[str, Any]:
    payload: Any = copy.deepcopy(before)
    for index, operation in enumerate(operations, 1):
        if not isinstance(operation, dict):
            raise WorkStateError(f"ledger state delta operation {index} is malformed")
        action = operation.get("op")
        path = operation.get("path")
        if action not in {"set", "delete"} or not isinstance(path, list):
            raise WorkStateError(f"ledger state delta operation {index} is malformed")
        if not path:
            if action != "set" or "value" not in operation:
                raise WorkStateError(
                    f"ledger state delta operation {index} cannot delete root"
                )
            payload = copy.deepcopy(operation["value"])
            continue
        parent = payload
        for component in path[:-1]:
            if isinstance(parent, dict) and isinstance(component, str):
                if component not in parent:
                    raise WorkStateError(
                        f"ledger state delta operation {index} traverses a missing key"
                    )
                parent = parent[component]
            elif (
                isinstance(parent, list)
                and isinstance(component, int)
                and not isinstance(component, bool)
                and 0 <= component < len(parent)
            ):
                parent = parent[component]
            else:
                raise WorkStateError(
                    f"ledger state delta operation {index} has an invalid path"
                )
        leaf = path[-1]
        if isinstance(parent, dict) and isinstance(leaf, str):
            if action == "delete":
                if leaf not in parent:
                    raise WorkStateError(
                        f"ledger state delta operation {index} deletes a missing key"
                    )
                del parent[leaf]
            elif "value" in operation:
                parent[leaf] = copy.deepcopy(operation["value"])
            else:
                raise WorkStateError(
                    f"ledger state delta operation {index} has no value"
                )
        elif (
            isinstance(parent, list)
            and isinstance(leaf, int)
            and not isinstance(leaf, bool)
            and 0 <= leaf < len(parent)
            and action == "set"
            and "value" in operation
        ):
            parent[leaf] = copy.deepcopy(operation["value"])
        else:
            raise WorkStateError(
                f"ledger state delta operation {index} has an invalid target"
            )
    if not isinstance(payload, dict):
        raise WorkStateError("ledger state delta did not produce a state object")
    return payload


def _ledger_key(session_dir: Path) -> bytes:
    path = session_dir / ".ledger.key"
    if path.exists():
        try:
            key = bytes.fromhex(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError) as error:
            raise WorkStateError(
                f"ledger authentication key is malformed: {path}"
            ) from error
        if len(key) != 32:
            raise WorkStateError(f"ledger authentication key is malformed: {path}")
        return key
    key = secrets.token_bytes(32)
    _atomic_write_text(path, key.hex() + "\n")
    return key


def _encode_ledger_events(session_dir: Path, events: list[dict[str, Any]]) -> str:
    key = _ledger_key(session_dir)
    previous = "0" * 64
    lines: list[str] = []
    for event in events:
        unsigned = {
            name: value for name, value in event.items() if name != "_ledger_auth"
        }
        canonical = json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        digest = hmac.new(
            key, previous.encode("ascii") + b"\0" + canonical, hashlib.sha256
        ).hexdigest()
        signed = {
            **unsigned,
            "_ledger_auth": {
                "algorithm": "hmac-sha256",
                "previous": previous,
                "digest": digest,
            },
        }
        lines.append(json.dumps(signed, ensure_ascii=False) + "\n")
        previous = digest
    return "".join(lines)


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


def _recover_state_from_events(
    session_dir: Path, events: list[dict[str, Any]]
) -> dict[str, Any] | None:
    recovered: dict[str, Any] | None = None
    ledger_path = session_dir / "ledger.jsonl"
    for line_number, event in enumerate(events, 1):
        sequence = event.get("seq")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise WorkStateError(
                f"ledger line {line_number} has malformed sequence: {ledger_path}"
            )
        if event.get("_historical_only") is True:
            if any(
                name in event
                for name in (
                    "_base_seq",
                    "_state_checkpoint",
                    "_state_delta",
                    "_state_sha256",
                    "_state_snapshot",
                )
            ):
                raise WorkStateError(
                    f"ledger line {line_number} has malformed historical payload: "
                    f"{ledger_path}"
                )
            continue
        checkpoint = event.get("_state_checkpoint", event.get("_state_snapshot"))
        if checkpoint is not None:
            if not isinstance(checkpoint, dict):
                raise WorkStateError(
                    f"ledger state snapshot is malformed: {ledger_path}"
                )
            candidate = copy.deepcopy(checkpoint)
            _validate_state_payload(
                candidate, ledger_path, label="ledger state snapshot"
            )
            if _state_sequence(candidate, "ledger state snapshot") != sequence:
                raise WorkStateError(
                    f"ledger state snapshot sequence mismatch: {ledger_path}"
                )
            recovered = candidate
        elif "_state_delta" in event:
            operations = event.get("_state_delta")
            base_sequence = event.get("_base_seq")
            if not isinstance(operations, list) or recovered is None:
                raise WorkStateError(
                    f"ledger line {line_number} has an unusable state delta: "
                    f"{ledger_path}"
                )
            if (
                base_sequence != _state_sequence(recovered, "ledger recovered state")
                or sequence != base_sequence + 1
            ):
                raise WorkStateError(
                    f"ledger line {line_number} state delta sequence mismatch: "
                    f"{ledger_path}"
                )
            recovered = _apply_state_delta(recovered, operations)
            _validate_state_payload(
                recovered, ledger_path, label="ledger recovered state"
            )
            if _state_sequence(recovered, "ledger recovered state") != sequence:
                raise WorkStateError(
                    f"ledger line {line_number} recovered sequence mismatch: "
                    f"{ledger_path}"
                )
        else:
            # Pre-checkpoint legacy events can still be inspected. Once a
            # recoverable chain starts, every later transition must carry state.
            if recovered is not None:
                raise WorkStateError(
                    f"ledger line {line_number} has no recovery payload: {ledger_path}"
                )
            continue
        expected_hash = event.get("_state_sha256")
        if expected_hash is not None and (
            not isinstance(expected_hash, str)
            or not secrets.compare_digest(expected_hash, _state_digest(recovered))
        ):
            raise WorkStateError(
                f"ledger line {line_number} state hash mismatch: {ledger_path}"
            )
    return recovered


def _latest_ledger_snapshot(session_dir: Path) -> dict[str, Any] | None:
    return _recover_state_from_events(session_dir, _read_ledger_events(session_dir))


def _recovery_path(session_dir: Path) -> Path:
    return session_dir / "recovery.json"


def _atomic_write_recovery(session_dir: Path, state: dict[str, Any]) -> None:
    payload = {
        "schema_version": 1,
        "last_seq": _state_sequence(state, "state"),
        "state_sha256": _state_digest(state),
        "state": state,
    }
    _atomic_write_json(_recovery_path(session_dir), payload)


def _read_recovery_checkpoint(session_dir: Path) -> dict[str, Any] | None:
    path = _recovery_path(session_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkStateError(
            f"recovery checkpoint is unreadable: {path}: {error}"
        ) from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise WorkStateError(f"recovery checkpoint is malformed: {path}")
    state = payload.get("state")
    if not isinstance(state, dict):
        raise WorkStateError(f"recovery checkpoint has no state: {path}")
    _validate_state_payload(state, path, label="recovery state")
    if (
        payload.get("last_seq") != _state_sequence(state, "recovery state")
        or not isinstance(payload.get("state_sha256"), str)
        or not secrets.compare_digest(payload["state_sha256"], _state_digest(state))
    ):
        raise WorkStateError(f"recovery checkpoint hash or sequence mismatch: {path}")
    return state


def load_state(session_dir: Path) -> dict[str, Any]:
    path = _state_path(session_dir)
    candidates: list[tuple[str, dict[str, Any]]] = []
    state_error: OSError | ValueError | None = None
    if path.is_file():
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict):
                _validate_state_payload(candidate, path)
                candidates.append(("state", candidate))
        # A truncated write can leave bytes that are not valid UTF-8, which
        # raises before the JSON parser sees them. That is the corruption the
        # ledger and recovery candidates below exist to survive, so it must
        # not escape as a traceback.
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            state_error = error
    ledger_state = _latest_ledger_snapshot(session_dir)
    if ledger_state is not None:
        candidates.append(("ledger", ledger_state))
    recovery_state = _read_recovery_checkpoint(session_dir)
    if recovery_state is not None:
        candidates.append(("recovery", recovery_state))
    if not candidates:
        if state_error is not None:
            raise WorkStateError(f"state is unreadable: {path}: {state_error}")
        raise WorkStateError(f"session does not exist: {session_dir}")
    latest_sequence = max(
        _state_sequence(payload, label) for label, payload in candidates
    )
    latest = [
        (label, payload)
        for label, payload in candidates
        if _state_sequence(payload, label) == latest_sequence
    ]
    for label, payload in latest:
        if label == "state":
            # state.json remains the supported current-state surface. Recovery
            # sources only supersede it when they contain a newer flushed
            # transition, preserving legacy lock/run recovery semantics that
            # intentionally update the current snapshot in place.
            return copy.deepcopy(payload)
    expected = _state_digest(latest[0][1])
    conflicts = [
        label for label, payload in latest[1:] if _state_digest(payload) != expected
    ]
    if conflicts:
        raise WorkStateError(
            "durable state sources conflict at sequence "
            f"{latest_sequence}: {latest[0][0]}, {', '.join(conflicts)}"
        )
    return copy.deepcopy(latest[0][1])


def _append_ledger(session_dir: Path, event: dict[str, object]) -> None:
    ledger_path = session_dir / "ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_ledger_events(session_dir) if ledger_path.exists() else []
    if existing:
        _recover_state_from_events(session_dir, existing)
    _atomic_write_text(
        ledger_path, _encode_ledger_events(session_dir, [*existing, event])
    )


def _compact_ledger_unlocked(
    session_dir: Path, state: dict[str, Any]
) -> dict[str, object]:
    events = _read_ledger_events(session_dir)
    if not events:
        raise WorkStateError("cannot compact a session without ledger events")
    sequence = _state_sequence(state, "state")
    checkpoint = {
        "seq": sequence,
        "at": utc_now(),
        "event": "compaction_checkpoint",
        "_state_checkpoint": copy.deepcopy(state),
        "_state_sha256": _state_digest(state),
    }
    recovered = _recover_state_from_events(session_dir, [checkpoint])
    if recovered is None or _state_digest(recovered) != _state_digest(state):
        raise WorkStateError("compaction recovery verification failed")
    content = _encode_ledger_events(session_dir, [checkpoint])
    _atomic_write_text(session_dir / "ledger.jsonl", content)
    verified = _recover_state_from_events(session_dir, _read_ledger_events(session_dir))
    if verified is None or _state_digest(verified) != _state_digest(state):
        raise WorkStateError("compacted ledger failed post-write recovery verification")
    return {
        "events_before": len(events),
        "events_after": 1,
        "last_seq": sequence,
        "recovery_verified": True,
    }
