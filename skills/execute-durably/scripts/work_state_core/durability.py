"""Storage, identity, locking, and WAL primitives for durable work state."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1
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
    for current, directories, filenames in os.walk(root, followlinks=False):
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
            except (OSError, PermissionError):
                continue
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


@contextlib.contextmanager
def session_lock(session_dir: Path) -> Iterator[None]:
    session_dir.mkdir(parents=True, exist_ok=True)
    lock_path = session_dir / ".state.lock"
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(descriptor, f"{os.getpid()} {utc_now()}\n".encode("utf-8"))
            finally:
                os.close(descriptor)
        except FileExistsError:
            try:
                age = max(0.0, time.time() - lock_path.stat().st_mtime)
            except FileNotFoundError:
                continue
            if age >= LOCK_STALE_SECONDS:
                try:
                    lock_path.unlink()
                    continue
                except (FileNotFoundError, PermissionError):
                    pass
            if time.monotonic() >= deadline:
                raise WorkStateError(f"timed out waiting for state lock: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()


def _state_path(session_dir: Path) -> Path:
    return session_dir / "state.json"


def _read_ledger_events(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "ledger.jsonl"
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise WorkStateError(f"ledger is unreadable: {path}: {error}") from error
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _latest_ledger_snapshot(session_dir: Path) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    for event in _read_ledger_events(session_dir):
        candidate = event.get("_state_snapshot")
        if not isinstance(candidate, dict):
            continue
        if latest is None or int(candidate.get("last_seq", -1)) > int(
            latest.get("last_seq", -1)
        ):
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
    if ledger_snapshot is not None and (
        payload is None
        or int(ledger_snapshot.get("last_seq", -1)) > int(payload.get("last_seq", -1))
    ):
        payload = ledger_snapshot
    if payload is None:
        if state_error is not None:
            raise WorkStateError(f"state is unreadable: {path}: {state_error}")
        raise WorkStateError(f"session does not exist: {session_dir}")
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise WorkStateError(f"unsupported or malformed state: {path}")
    if not isinstance(payload.get("criteria"), list):
        raise WorkStateError(f"state has no criteria list: {path}")
    if "work_packets" in payload and not isinstance(payload["work_packets"], list):
        raise WorkStateError(f"state has malformed work_packets: {path}")
    return payload


def _append_ledger(session_dir: Path, event: dict[str, object]) -> None:
    ledger_path = session_dir / "ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
