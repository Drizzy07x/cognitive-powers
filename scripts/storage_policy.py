#!/usr/bin/env python3
"""Shared, fail-closed policy for source trees, fixtures, and package copies."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Sequence


POLICY_VERSION = 1

# Component names are matched case-insensitively at every depth. Keep source
# benchmark definitions ("benchmarks") distinct from generated benchmark output.
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        ".nox",
        ".cache",
        ".venv",
        "venv",
        "node_modules",
        ".next",
        "build",
        "dist",
        "coverage",
        "target",
        "vendor",
        "benchmark-results",
        "benchmark-output",
        "benchmark-outputs",
        "benchmark_outputs",
        "blob-report",
        "playwright-report",
        "test-results",
        "homes",
        "runs",
        "storage",
    }
)
EXCLUDED_FILE_NAMES = frozenset(
    {
        ".codex-marketplace-install.json",
        ".coverage",
        "coverage.xml",
    }
)
EXCLUDED_FILE_SUFFIXES = frozenset({".pyc", ".pyo"})

# These are the excluded trees whose presence in fixture-copy mode can conceal
# a material amount of state. Caches and VCS metadata remain excluded, but do
# not trigger the dependency/generated-tree guard.
GUARDED_EXCLUDED_TREE_NAMES = frozenset(
    {
        ".venv",
        "venv",
        "node_modules",
        ".next",
        "build",
        "dist",
        "coverage",
        "target",
        "vendor",
        "benchmark-results",
        "benchmark-output",
        "benchmark-outputs",
        "benchmark_outputs",
        "blob-report",
        "playwright-report",
        "test-results",
        "homes",
        "runs",
        "storage",
    }
)

DEFAULT_COPY_MAX_FILES = 20_000
DEFAULT_COPY_MAX_BYTES = 256 * 1024 * 1024
DEFAULT_LARGE_TREE_FILE_LIMIT = 1_000
DEFAULT_LARGE_TREE_BYTE_LIMIT = 100 * 1024 * 1024


class StoragePolicyError(ValueError):
    """Raised when enumeration or copying cannot satisfy the shared policy."""


@dataclass(frozen=True)
class TreeMeasurement:
    """A deterministic preflight measurement of files selected by the policy."""

    file_count: int
    total_bytes: int

    def as_dict(self) -> dict[str, int]:
        return {"fileCount": self.file_count, "totalBytes": self.total_bytes}


@dataclass(frozen=True)
class _CopyEntry:
    source: Path
    relative: Path
    size: int


def _normalized_name(value: str) -> str:
    return value.casefold()


def is_excluded_relative(relative: Path | PurePosixPath | str) -> bool:
    """Return whether a relative path is excluded by the shared policy."""
    path = PurePosixPath(str(relative).replace("\\", "/"))
    if any(
        _normalized_name(part) in EXCLUDED_DIRECTORY_NAMES for part in path.parts[:-1]
    ):
        return True
    if path.parts and _normalized_name(path.parts[-1]) in EXCLUDED_DIRECTORY_NAMES:
        return True
    if not path.parts:
        return False
    name = _normalized_name(path.name)
    return (
        name in EXCLUDED_FILE_NAMES
        or PurePosixPath(name).suffix in EXCLUDED_FILE_SUFFIXES
    )


def _require_directory(root: Path) -> Path:
    root = root.resolve()
    if not root.is_dir():
        raise StoragePolicyError(f"tree root is not a directory: {root}")
    return root


def _iter_directory_files(root: Path, directory: Path) -> Iterator[Path]:
    try:
        entries = sorted(
            directory.iterdir(), key=lambda item: (item.name.casefold(), item.name)
        )
    except OSError as error:
        raise StoragePolicyError(
            f"cannot enumerate tree directory {directory}: {error}"
        ) from error
    for path in entries:
        try:
            relative = path.relative_to(root)
            if path.is_symlink():
                continue
            if path.is_dir():
                if _normalized_name(path.name) in EXCLUDED_DIRECTORY_NAMES:
                    continue
                yield from _iter_directory_files(root, path)
                continue
            if not path.is_file() or is_excluded_relative(relative):
                continue
        except OSError as error:
            raise StoragePolicyError(
                f"cannot inspect tree path {path}: {error}"
            ) from error
        yield path


def iter_tree_files(root: Path) -> Iterator[Path]:
    """Yield included regular files in deterministic relative-path order."""
    root = _require_directory(root)
    yield from _iter_directory_files(root, root)


def _manifest_relative(value: str | os.PathLike[str]) -> Path:
    raw = os.fspath(value)
    normalized = raw.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or pure.is_absolute()
        or pure == PurePosixPath(".")
        or ".." in pure.parts
        or (pure.parts and pure.parts[0].endswith(":"))
    ):
        raise StoragePolicyError(f"unsafe fixture manifest entry: {raw}")
    return Path(*pure.parts)


def enumerate_manifest_files(
    root: Path, manifest: Sequence[str | os.PathLike[str]]
) -> tuple[Path, ...]:
    """Resolve an explicit file/directory manifest under ``root``."""
    root = _require_directory(root)
    if not manifest:
        raise StoragePolicyError("fixture manifest must not be empty")
    selected: dict[str, Path] = {}
    for value in manifest:
        relative = _manifest_relative(value)
        if is_excluded_relative(relative):
            raise StoragePolicyError(
                f"fixture manifest entry is excluded by policy: {relative.as_posix()}"
            )
        target = root / relative
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as error:
            raise StoragePolicyError(
                f"fixture manifest entry is missing or escapes the root: "
                f"{relative.as_posix()}"
            ) from error
        if target.is_symlink():
            raise StoragePolicyError(
                f"fixture manifest entry cannot be a symlink: {relative.as_posix()}"
            )
        if target.is_file():
            paths: Iterable[Path] = (target,)
        elif target.is_dir():
            paths = _iter_directory_files(root, target)
        else:
            raise StoragePolicyError(
                f"fixture manifest entry is not a regular file or directory: "
                f"{relative.as_posix()}"
            )
        for path in paths:
            item_relative = path.relative_to(root).as_posix()
            selected[item_relative] = path
    return tuple(selected[key] for key in sorted(selected))


def git_tracked_files(root: Path) -> tuple[Path, ...]:
    """Return policy-included files tracked by Git, failing closed."""
    root = _require_directory(root)
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise StoragePolicyError(
            f"cannot enumerate tracked Git files: {error}"
        ) from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise StoragePolicyError(
            f"cannot enumerate tracked Git files under {root}: {detail}"
        )
    selected: dict[str, Path] = {}
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            relative_text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise StoragePolicyError("tracked Git path is not valid UTF-8") from error
        relative = _manifest_relative(relative_text)
        if is_excluded_relative(relative):
            continue
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise StoragePolicyError(
                f"tracked Git file is unavailable or unsupported: {relative.as_posix()}"
            )
        selected[relative.as_posix()] = path
    return tuple(selected[key] for key in sorted(selected))


def _selected_files(
    root: Path,
    *,
    manifest: Sequence[str | os.PathLike[str]] | None,
    tracked_only: bool,
) -> tuple[Path, ...]:
    if manifest is not None and tracked_only:
        raise StoragePolicyError(
            "select either a fixture manifest or tracked Git files, not both"
        )
    if manifest is not None:
        return enumerate_manifest_files(root, manifest)
    if tracked_only:
        return git_tracked_files(root)
    return tuple(iter_tree_files(root))


def measure_files(files: Iterable[Path]) -> TreeMeasurement:
    """Measure regular files and fail rather than silently omitting read errors."""
    file_count = 0
    total_bytes = 0
    for path in files:
        try:
            stat = path.stat()
        except OSError as error:
            raise StoragePolicyError(
                f"cannot measure tree file {path}: {error}"
            ) from error
        if path.is_symlink() or not path.is_file():
            raise StoragePolicyError(f"tree file changed during measurement: {path}")
        file_count += 1
        total_bytes += stat.st_size
    return TreeMeasurement(file_count=file_count, total_bytes=total_bytes)


def measure_tree(
    root: Path,
    *,
    manifest: Sequence[str | os.PathLike[str]] | None = None,
    tracked_only: bool = False,
) -> TreeMeasurement:
    """Measure the files selected by one bounded-copy strategy."""
    root = _require_directory(root)
    return measure_files(
        _selected_files(root, manifest=manifest, tracked_only=tracked_only)
    )


def enforce_budget(
    measurement: TreeMeasurement,
    *,
    max_files: int,
    max_bytes: int,
    label: str = "copy",
) -> None:
    """Reject a projected copy before any destination data is materialized."""
    if max_files < 0 or max_bytes < 0:
        raise StoragePolicyError(f"{label} budgets must be non-negative")
    if measurement.file_count > max_files:
        raise StoragePolicyError(
            f"projected file count {measurement.file_count} exceeds {label} "
            f"budget max_files={max_files}; stopped before copying"
        )
    if measurement.total_bytes > max_bytes:
        raise StoragePolicyError(
            f"projected bytes {measurement.total_bytes} exceeds {label} "
            f"budget max_bytes={max_bytes}; stopped before copying"
        )


def _raw_tree_measurement(
    root: Path,
    *,
    file_limit: int,
    byte_limit: int,
) -> TreeMeasurement:
    count = 0
    total_bytes = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda item: (item.name.casefold(), item.name),
                reverse=True,
            )
        except OSError as error:
            raise StoragePolicyError(
                f"cannot inspect excluded tree {directory}: {error}"
            ) from error
        for path in entries:
            if path.is_symlink():
                continue
            try:
                if path.is_dir():
                    pending.append(path)
                elif path.is_file():
                    count += 1
                    total_bytes += path.stat().st_size
            except OSError as error:
                raise StoragePolicyError(
                    f"cannot inspect excluded tree path {path}: {error}"
                ) from error
            if count > file_limit or total_bytes > byte_limit:
                return TreeMeasurement(count, total_bytes)
    return TreeMeasurement(count, total_bytes)


def _scan_roots(
    root: Path, manifest: Sequence[str | os.PathLike[str]] | None
) -> tuple[Path, ...]:
    # A manifest bounds what is copied, but it must not hide a bulky excluded
    # dependency tree elsewhere in the fixture. The override is the only
    # supported way to acknowledge that diagnostic condition explicitly.
    del manifest
    return (root,)


def reject_large_excluded_trees(
    root: Path,
    *,
    manifest: Sequence[str | os.PathLike[str]] | None = None,
    allow_override: bool = False,
    file_limit: int = DEFAULT_LARGE_TREE_FILE_LIMIT,
    byte_limit: int = DEFAULT_LARGE_TREE_BYTE_LIMIT,
) -> None:
    """Reject bulky dependency/generated trees hidden by fixture exclusions."""
    if allow_override:
        return
    if file_limit < 0 or byte_limit < 0:
        raise StoragePolicyError("large excluded-tree limits must be non-negative")
    root = _require_directory(root)
    pending = list(_scan_roots(root, manifest))
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(
                directory.iterdir(), key=lambda item: (item.name.casefold(), item.name)
            )
        except OSError as error:
            raise StoragePolicyError(
                f"cannot inspect fixture directory {directory}: {error}"
            ) from error
        for path in entries:
            if path.is_symlink() or not path.is_dir():
                continue
            name = _normalized_name(path.name)
            if name in GUARDED_EXCLUDED_TREE_NAMES:
                measured = _raw_tree_measurement(
                    path, file_limit=file_limit, byte_limit=byte_limit
                )
                if (
                    measured.file_count > file_limit
                    or measured.total_bytes > byte_limit
                ):
                    relative = path.relative_to(root).as_posix()
                    raise StoragePolicyError(
                        "fixture contains excluded large tree "
                        f"{relative} (observed files={measured.file_count}, "
                        f"bytes={measured.total_bytes}; limits files={file_limit}, "
                        f"bytes={byte_limit}); use the explicit override "
                        "allow_large_excluded_trees=True only for diagnosis"
                    )
                continue
            if name not in EXCLUDED_DIRECTORY_NAMES:
                pending.append(path)


def bounded_copy_tree(
    source: Path,
    destination: Path,
    *,
    manifest: Sequence[str | os.PathLike[str]] | None = None,
    tracked_only: bool = False,
    max_files: int = DEFAULT_COPY_MAX_FILES,
    max_bytes: int = DEFAULT_COPY_MAX_BYTES,
    fixture_mode: bool = False,
    allow_large_excluded_trees: bool = False,
    large_tree_file_limit: int = DEFAULT_LARGE_TREE_FILE_LIMIT,
    large_tree_byte_limit: int = DEFAULT_LARGE_TREE_BYTE_LIMIT,
) -> TreeMeasurement:
    """Preflight and copy only the deterministic, policy-included file set."""
    source = _require_directory(source)
    destination = destination.resolve()
    if destination.exists():
        raise StoragePolicyError(f"copy destination already exists: {destination}")
    if fixture_mode:
        reject_large_excluded_trees(
            source,
            manifest=manifest,
            allow_override=allow_large_excluded_trees,
            file_limit=large_tree_file_limit,
            byte_limit=large_tree_byte_limit,
        )
    files = _selected_files(source, manifest=manifest, tracked_only=tracked_only)
    entries: list[_CopyEntry] = []
    for path in files:
        try:
            size = path.stat().st_size
        except OSError as error:
            raise StoragePolicyError(
                f"cannot preflight copy file {path}: {error}"
            ) from error
        entries.append(_CopyEntry(path, path.relative_to(source), size))
    measurement = TreeMeasurement(
        file_count=len(entries),
        total_bytes=sum(entry.size for entry in entries),
    )
    enforce_budget(
        measurement,
        max_files=max_files,
        max_bytes=max_bytes,
        label="copy",
    )

    try:
        for entry in entries:
            target = destination / entry.relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry.source, target)
            if (
                entry.source.stat().st_size != entry.size
                or target.stat().st_size != entry.size
            ):
                raise StoragePolicyError(
                    f"copy source changed after preflight: {entry.relative.as_posix()}"
                )
    except Exception:
        if destination.exists():
            shutil.rmtree(destination)
        raise
    return measurement


def source_identity(root: Path) -> dict[str, int | str]:
    """Hash the shared source-oriented file surface deterministically."""
    root = _require_directory(root)
    aggregate = hashlib.sha256()
    count = 0
    for path in iter_tree_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise StoragePolicyError(
                f"cannot fingerprint source file {relative}: {error}"
            ) from error
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(file_hash.encode("ascii"))
        aggregate.update(b"\n")
        count += 1
    return {"sha256": aggregate.hexdigest(), "fileCount": count}
