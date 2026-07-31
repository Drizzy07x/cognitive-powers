"""Content-addressed artifact storage, inventory, and collection."""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Sequence

from .durability import (
    WorkStateError,
    _process_matches_identity,
    _sha256_file,
    load_state,
    session_lock,
)


def _cas_object_path(data_root: Path, digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise WorkStateError(f"invalid content-addressed digest: {digest!r}")
    return data_root / "objects" / "sha256" / digest[:2] / digest


def _copy_artifact_to_cas(
    data_root: Path, source: Path, destination: Path
) -> dict[str, object]:
    """Materialize one immutable hash-addressed object and reuse its allocation."""
    digest = _sha256_file(source)
    size = source.stat().st_size
    object_path = _cas_object_path(data_root, digest)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    deduplicated = object_path.exists()
    if deduplicated:
        if (
            not object_path.is_file()
            or object_path.is_symlink()
            or object_path.stat().st_size != size
            or _sha256_file(object_path) != digest
        ):
            raise WorkStateError(
                f"content-addressed object is corrupt or colliding: {object_path}"
            )
    else:
        temporary = object_path.with_name(
            f".{object_path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        )
        try:
            shutil.copyfile(source, temporary)
            if temporary.stat().st_size != size or _sha256_file(temporary) != digest:
                raise WorkStateError(f"artifact changed while storing in CAS: {source}")
            try:
                os.replace(temporary, object_path)
            except OSError:
                if object_path.exists() and _sha256_file(object_path) == digest:
                    temporary.unlink()
                    deduplicated = True
                else:
                    raise
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    link_temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    hardlinked = True
    try:
        try:
            os.link(object_path, link_temporary)
        except OSError:
            hardlinked = False
            shutil.copyfile(object_path, link_temporary)
        os.replace(link_temporary, destination)
    finally:
        with contextlib.suppress(FileNotFoundError):
            link_temporary.unlink()
    if destination.stat().st_size != size or _sha256_file(destination) != digest:
        raise WorkStateError(f"CAS materialization failed verification: {destination}")
    return {
        "sha256": digest,
        "bytes": size,
        "object": str(object_path),
        "deduplicated": deduplicated,
        "hardlinked": hardlinked,
    }


def _iter_storage_files(data_root: Path):
    if not data_root.exists():
        return

    def walk_error(error: OSError) -> None:
        raise WorkStateError(
            f"cannot enumerate durable storage under {data_root}: {error}"
        ) from error

    for current, directories, filenames in os.walk(
        data_root, followlinks=False, onerror=walk_error
    ):
        current_path = Path(current)
        retained = []
        for directory in sorted(directories):
            path = current_path / directory
            if path.is_symlink():
                raise WorkStateError(
                    f"durable storage directory cannot be a symlink: {path}"
                )
            retained.append(directory)
        directories[:] = retained
        for filename in sorted(filenames):
            path = current_path / filename
            if path.is_symlink():
                raise WorkStateError(
                    f"durable storage file cannot be a symlink: {path}"
                )
            if path.is_file():
                yield path


def inspect_storage(data_root: Path, *, largest: int = 10) -> dict[str, object]:
    if largest < 0:
        raise WorkStateError("largest directory count must be non-negative")
    data_root = data_root.expanduser().resolve()
    directory_totals: dict[Path, list[int]] = {}
    physical_files: dict[tuple[object, ...], int] = {}
    file_count = 0
    logical_bytes = 0
    for path in _iter_storage_files(data_root) or ():
        try:
            stat = path.stat()
            relative = path.relative_to(data_root)
        except OSError as error:
            raise WorkStateError(
                f"cannot inspect durable file {path}: {error}"
            ) from error
        file_count += 1
        logical_bytes += stat.st_size
        identity: tuple[object, ...]
        if stat.st_ino:
            identity = ("inode", stat.st_dev, stat.st_ino)
        else:
            identity = ("path", str(path.resolve()))
        physical_files.setdefault(identity, stat.st_size)
        parent = relative.parent
        while True:
            totals = directory_totals.setdefault(parent, [0, 0])
            totals[0] += stat.st_size
            totals[1] += 1
            if parent == Path("."):
                break
            parent = parent.parent
    projects_root = data_root / "projects"
    projects = (
        sorted(
            path
            for path in projects_root.iterdir()
            if path.is_dir() and not path.is_symlink()
        )
        if projects_root.is_dir()
        else []
    )
    sessions = [
        session
        for project in projects
        for session in (
            sorted((project / "sessions").iterdir())
            if (project / "sessions").is_dir()
            else []
        )
        if session.is_dir() and not session.is_symlink()
    ]
    ranked = sorted(
        (
            {
                "path": relative.as_posix(),
                "bytes": totals[0],
                "file_count": totals[1],
            }
            for relative, totals in directory_totals.items()
            if relative != Path(".")
        ),
        key=lambda item: (-int(item["bytes"]), str(item["path"])),
    )[:largest]
    return {
        "schema_version": 1,
        "data_root": str(data_root),
        "file_count": file_count,
        "bytes": logical_bytes,
        "logical_bytes": logical_bytes,
        "physical_bytes": sum(physical_files.values()),
        "projects": len(projects),
        "sessions": len(sessions),
        "largest_directories": ranked,
    }


def _session_lock_status(session_dir: Path) -> str | None:
    lock_path = session_dir / ".state.lock"
    if not lock_path.exists():
        return None
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = payload.get("pid") if isinstance(payload, dict) else None
        identity = (
            payload.get("process_identity") if isinstance(payload, dict) else None
        )
    except (OSError, json.JSONDecodeError):
        return "unreadable-lock"
    if not isinstance(pid, int) or isinstance(pid, bool):
        return "unreadable-lock"
    if _process_matches_identity(
        pid, identity if isinstance(identity, str) and identity else None
    ):
        return "live-lock"
    return None


def _digests_reachable_from(evidence: Path) -> set[str] | None:
    """Hash every artifact under one session's evidence tree.

    An unreadable receipt cannot be parsed, but the objects it could possibly
    name are still bounded: every stored artifact is materialized inside the
    session beside that receipt, so hashing what is there enumerates the
    candidates without reading the file that failed.

    Hashing rather than inode identity on purpose -- ``_copy_artifact_to_cas``
    falls back to a real copy when the filesystem refuses a hard link, and that
    fallback breaks ``(st_dev, st_ino)`` equality while preserving content.

    ``None`` means the tree could not be walked, so nothing can be bounded.
    """
    digests: set[str] = set()
    try:
        candidates = sorted(evidence.rglob("*"))
    except OSError:
        return None
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            digests.add(_sha256_file(path))
        except OSError:
            return None
    return digests


def _collect_cas_references(
    session_directories: Sequence[Path],
) -> tuple[set[str], list[str], set[str] | None]:
    """Report referenced digests, unreadable evidence, and what it could name.

    The third value bounds the damage of the second. It used to be absent, and
    the caller answered an unreadable file by protecting every unreferenced
    object in the whole store, for every project, permanently -- garbage
    collection never repairs evidence, so the same file blocked every later run
    too. ``None`` means a session's evidence could not be enumerated at all, in
    which case there is nothing to bound and the caller must fall back to
    protecting everything.
    """
    references: set[str] = set()
    unscannable: list[str] = []
    uncertain: set[str] = set()
    unbounded = False

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    key.endswith("cas_sha256")
                    and isinstance(item, str)
                    and re.fullmatch(r"[0-9a-f]{64}", item)
                ):
                    references.add(item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for session_dir in session_directories:
        evidence = session_dir / "evidence"
        if not evidence.is_dir():
            continue
        try:
            candidates = sorted(evidence.rglob("*.json"))
        except OSError:
            unscannable.append(str(evidence))
            unbounded = True
            continue
        session_unread = False
        for path in candidates:
            if path.is_symlink() or not path.is_file():
                continue
            try:
                visit(json.loads(path.read_text(encoding="utf-8")))
            # Recorded artifacts are copied verbatim, so evidence holds JSON
            # this scan never wrote: a cp1252 artifact raises UnicodeDecodeError,
            # which is a ValueError and escaped here after the session deletions
            # had already happened. Dropping such a file silently would also
            # drop the digests it names and collect objects a retained session
            # still depends on, so the caller is told what stayed unread.
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                unscannable.append(str(path))
                session_unread = True
        if session_unread:
            reachable = _digests_reachable_from(evidence)
            if reachable is None:
                unbounded = True
            else:
                uncertain |= reachable
    return references, unscannable, (None if unbounded else uncertain)


def _storage_session_directories(data_root: Path) -> list[tuple[Path, Path]]:
    projects_root = data_root / "projects"
    if not projects_root.is_dir():
        return []
    result: list[tuple[Path, Path]] = []
    for project in sorted(projects_root.iterdir()):
        sessions = project / "sessions"
        if project.is_symlink() or not sessions.is_dir():
            continue
        for session in sorted(sessions.iterdir()):
            if session.is_dir() and not session.is_symlink():
                result.append((project, session))
    return result


def garbage_collect_storage(
    data_root: Path,
    *,
    older_than_days: float = 30,
    keep_last: int = 5,
    apply: bool = False,
) -> dict[str, object]:
    if older_than_days < 0:
        raise WorkStateError("older-than-days must be non-negative")
    if keep_last < 0:
        raise WorkStateError("keep-last must be non-negative")
    data_root = data_root.expanduser().resolve()
    cutoff = time.time() - (older_than_days * 86400)
    sessions = _storage_session_directories(data_root)
    inspected: dict[Path, dict[str, object]] = {}
    completed_by_project: dict[Path, list[Path]] = {}
    for project, session_dir in sessions:
        lock_status = _session_lock_status(session_dir)
        try:
            state = load_state(session_dir)
            status = state.get("status")
        except WorkStateError as error:
            inspected[session_dir] = {
                "decision": "protect",
                "reason": "unreadable-state",
                "detail": str(error),
            }
            continue
        if lock_status is not None:
            inspected[session_dir] = {
                "decision": "protect",
                "reason": lock_status,
            }
            continue
        if status != "complete":
            inspected[session_dir] = {
                "decision": "protect",
                "reason": "active-session",
            }
            continue
        completed_by_project.setdefault(project, []).append(session_dir)
    for completed in completed_by_project.values():
        ordered = sorted(
            completed,
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        kept = set(ordered[:keep_last])
        for session_dir in ordered:
            modified = session_dir.stat().st_mtime
            if session_dir in kept:
                decision, reason = "keep", "keep-last"
            elif modified > cutoff:
                decision, reason = "keep", "younger-than-age"
            else:
                decision, reason = "delete", "age-and-keep-policy"
            inspected[session_dir] = {
                "decision": decision,
                "reason": reason,
                "mtime": modified,
            }
    session_decisions = [{"path": str(path), **inspected[path]} for _, path in sessions]
    deleted_sessions: list[str] = []
    failed_sessions: list[dict[str, object]] = []
    if apply:
        for item in session_decisions:
            if item["decision"] != "delete":
                continue
            session_dir = Path(str(item["path"]))
            tombstone = session_dir.with_name(
                f".gc-{session_dir.name}-{secrets.token_hex(8)}"
            )
            try:
                with session_lock(session_dir):
                    state = load_state(session_dir)
                    if state.get("status") != "complete":
                        raise WorkStateError(
                            f"session became active during collection: {session_dir}"
                        )
                    os.replace(session_dir, tombstone)
                shutil.rmtree(tombstone)
            except Exception as error:
                if tombstone.exists() and not session_dir.exists():
                    with contextlib.suppress(OSError):
                        os.replace(tombstone, session_dir)
                # One session that cannot be collected -- a lock that times out,
                # a concurrent reopen, or a rename Windows refuses while any
                # descendant is open -- used to abort the whole run and discard
                # the return value, so the sessions already deleted above were
                # gone with nothing left to say which ones they were.
                failed_sessions.append({"path": str(session_dir), "error": str(error)})
                continue
            deleted_sessions.append(str(session_dir))
    if apply:
        # A session whose collection failed is still on disk, so its evidence
        # must keep protecting the objects it references.
        collected = {Path(path) for path in deleted_sessions}
    else:
        collected = {
            Path(str(item["path"]))
            for item in session_decisions
            if item["decision"] == "delete"
        }
    retained_sessions = [
        path for _, path in sessions if path not in collected and path.exists()
    ]
    references, unscannable_evidence, uncertain_digests = _collect_cas_references(
        retained_sessions
    )
    object_root = data_root / "objects" / "sha256"
    object_decisions: list[dict[str, object]] = []
    if object_root.is_dir():
        for path in sorted(object_root.glob("*/*")):
            if not path.is_file() or path.is_symlink():
                continue
            digest = path.name
            try:
                stat = path.stat()
            except OSError as error:
                raise WorkStateError(
                    f"cannot inspect CAS object {path}: {error}"
                ) from error
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                decision, reason = "protect", "malformed-object-name"
            elif digest in references:
                decision, reason = "protect", "referenced"
            # Protect what the unreadable receipts could have named, not the
            # whole store. The bare `if unscannable_evidence` this replaces let
            # one legacy file in one session pin every unreferenced object in
            # every project, and pin it forever, because collection never
            # repairs evidence and every later run re-read the same file.
            elif uncertain_digests is None or digest in uncertain_digests:
                decision, reason = "protect", "unscannable-evidence"
            elif stat.st_mtime > cutoff:
                decision, reason = "keep", "younger-than-age"
            else:
                decision, reason = "delete", "unreferenced-and-old"
            object_decisions.append(
                {
                    "sha256": digest,
                    "path": str(path),
                    "bytes": stat.st_size,
                    "decision": decision,
                    "reason": reason,
                }
            )
    deleted_objects: list[str] = []
    if apply:
        for item in object_decisions:
            if item["decision"] != "delete":
                continue
            path = Path(str(item["path"]))
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            deleted_objects.append(str(path))
        if object_root.is_dir():
            for directory in sorted(object_root.iterdir()):
                with contextlib.suppress(OSError):
                    directory.rmdir()
    return {
        "schema_version": 1,
        "data_root": str(data_root),
        "mode": "apply" if apply else "dry-run",
        "applied": apply,
        "older_than_days": older_than_days,
        "keep_last": keep_last,
        "session_decisions": session_decisions,
        "object_decisions": object_decisions,
        "deleted_sessions": deleted_sessions,
        "deleted_objects": deleted_objects,
        "failed_sessions": failed_sessions,
        "unscannable_evidence": unscannable_evidence,
        "failed": bool(failed_sessions),
    }
