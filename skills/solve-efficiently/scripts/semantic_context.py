#!/usr/bin/env python3
"""Use a fresh CodeGraph index when available, otherwise fall back explicitly."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence


SCHEMA_VERSION = 1
DEFAULT_MAX_CHARS = 12_000
DEFAULT_TIMEOUT_SECONDS = 30
TEST_PATH_PATTERN = re.compile(r"(^|/)(tests?|spec|e2e)/|\.(?:test|spec)\.", re.I)
TEST_FILTERS_BY_SUFFIX = {
    ".py": "**/test_*.py",
    ".go": "**/*_test.go",
    ".rs": "tests/*.rs",
    ".rb": "**/*_spec.rb",
    ".java": "**/*Test.java",
    ".kt": "**/*Test.kt",
    ".cs": "**/*Tests.cs",
}
Runner = Callable[..., subprocess.CompletedProcess[str]]


class SemanticContextError(RuntimeError):
    """Raised when the adapter cannot produce a valid normalized result."""


def _load_context_lens():
    script = Path(__file__).with_name("context_lens.py")
    spec = importlib.util.spec_from_file_location("cognitive_context_lens", script)
    if spec is None or spec.loader is None:
        raise SemanticContextError(f"cannot load Context Lens: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise SemanticContextError(f"root is not a directory: {root}")
    return root


def _resolve_executable(explicit: str | None) -> str | None:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        return shutil.which(explicit)
    return shutil.which("codegraph")


def _run(
    executable: str,
    arguments: Sequence[str],
    root: Path,
    timeout_seconds: int,
    runner: Runner,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            [executable, *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SemanticContextError(
            f"CodeGraph command failed to start: {error}"
        ) from error


def _counter(
    container: dict[str, Any], field: str, warnings: list[str], label: str
) -> int:
    """Read a counter the provider stated, warning when it stated an unreadable one.

    This gate decides whether the index may be believed, and it used to skip
    any counter that was not already an ``int``: ``{"modified": "2"}`` -- the
    provider saying two files changed -- filtered itself out of the sum and the
    probe reported a clean, usable index. Every unrecognized shape resolved the
    same way, so a CodeGraph whose status schema drifted would be trusted
    precisely when it could no longer be understood.

    A warning rather than a raise, because ``usable`` is already defined as
    "initialized and nothing to warn about"; that is where fail-closed lives on
    this surface. An absent counter still defaults to zero in silence, so an
    older CLI that never emits one is not accused of anything.
    """
    value = container.get(field, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        warnings.append(f"{label} is not a whole number: {value!r}")
        return 0
    return value


def probe_codegraph(
    root: str | Path,
    *,
    executable: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
) -> dict[str, object]:
    project_root = _resolve_root(root)
    resolved = _resolve_executable(executable)
    if resolved is None:
        return {
            "available": False,
            "usable": False,
            "reason": "codegraph executable not found",
            "executable": None,
            "status": None,
            "warnings": [],
        }
    completed = _run(
        resolved,
        ["status", str(project_root), "--json"],
        project_root,
        timeout_seconds,
        runner,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return {
            "available": True,
            "usable": False,
            "reason": f"codegraph status failed: {detail or completed.returncode}",
            "executable": resolved,
            "status": None,
            "warnings": [],
        }
    try:
        status = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "available": True,
            "usable": False,
            "reason": "codegraph status did not return JSON",
            "executable": resolved,
            "status": None,
            "warnings": [],
        }
    if not isinstance(status, dict):
        raise SemanticContextError("codegraph status JSON must be an object")

    warnings: list[str] = []
    if not status.get("initialized"):
        warnings.append("project is not indexed")
    pending = status.get("pendingChanges")
    pending_total = 0
    if isinstance(pending, dict):
        pending_total = sum(
            _counter(pending, key, warnings, f"pendingChanges.{key}")
            for key in ("added", "modified", "removed")
        )
    elif pending is not None:
        warnings.append("pendingChanges is not an object")
    if pending_total:
        warnings.append(f"index has {pending_total} pending source changes")
    index = status.get("index")
    if index is not None and not isinstance(index, dict):
        warnings.append("index is not an object")
    index_state = index.get("state") if isinstance(index, dict) else None
    pending_refs = (
        _counter(index, "pendingRefs", warnings, "index.pendingRefs")
        if isinstance(index, dict)
        else 0
    )
    reindex_recommended = (
        bool(index.get("reindexRecommended")) if isinstance(index, dict) else False
    )
    if index_state not in {None, "complete"}:
        warnings.append(f"index state is {index_state}")
    if pending_refs:
        warnings.append(f"index has {pending_refs} unresolved references")
    if reindex_recommended:
        warnings.append("index rebuild is recommended")
    if status.get("worktreeMismatch"):
        warnings.append("index belongs to a different worktree")
    usable = bool(status.get("initialized")) and not warnings
    return {
        "available": True,
        "usable": usable,
        "reason": None if usable else "; ".join(warnings),
        "executable": resolved,
        "status": status,
        "warnings": warnings,
    }


def _bounded_payload(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars < 1:
        raise SemanticContextError("max-chars must be positive")
    if len(text) <= max_chars:
        return text, False
    marker = "\n...[truncated by Cognitive Powers]"
    available = max(0, max_chars - len(marker))
    return text[:available] + marker, True


def _fallback_context(
    root: Path,
    query: str,
    *,
    reason: str,
    max_files: int,
    max_chars: int,
) -> dict[str, object]:
    lens = _load_context_lens()
    result = lens.select_context(
        root,
        query,
        max_files=max_files,
        max_chars=max_chars,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": "context-lens",
        "semantic": False,
        "fallback_reason": reason,
        **result,
    }


def explore(
    root: str | Path,
    query: str,
    *,
    executable: str | None = None,
    max_files: int = 12,
    max_chars: int = DEFAULT_MAX_CHARS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
) -> dict[str, object]:
    project_root = _resolve_root(root)
    clean_query = query.strip()
    if not clean_query:
        raise SemanticContextError("query must not be empty")
    probe = probe_codegraph(
        project_root,
        executable=executable,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )
    if not probe["usable"]:
        return _fallback_context(
            project_root,
            clean_query,
            reason=str(probe["reason"]),
            max_files=max_files,
            max_chars=max_chars,
        )
    command = [
        "explore",
        clean_query,
        "--path",
        str(project_root),
        "--max-files",
        str(max_files),
    ]
    completed = _run(
        str(probe["executable"]),
        command,
        project_root,
        timeout_seconds,
        runner,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return _fallback_context(
            project_root,
            clean_query,
            reason=f"codegraph explore failed: {detail or completed.returncode}",
            max_files=max_files,
            max_chars=max_chars,
        )
    payload, truncated = _bounded_payload(completed.stdout, max_chars)
    status = probe["status"] if isinstance(probe["status"], dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": "codegraph",
        "semantic": True,
        "mode": "explore",
        "root": str(project_root),
        "query": clean_query,
        "version": status.get("version"),
        "index_status": status.get("index"),
        "payload": payload,
        "payload_chars": len(payload),
        "truncated": truncated,
        "response_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "command": command,
    }


def impact(
    root: str | Path,
    symbol: str,
    *,
    executable: str | None = None,
    depth: int = 2,
    max_chars: int = DEFAULT_MAX_CHARS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
) -> dict[str, object]:
    project_root = _resolve_root(root)
    clean_symbol = symbol.strip()
    if not clean_symbol:
        raise SemanticContextError("symbol must not be empty")
    if not 1 <= depth <= 10:
        raise SemanticContextError("depth must be between 1 and 10")
    probe = probe_codegraph(
        project_root,
        executable=executable,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )
    if not probe["usable"]:
        fallback = _fallback_context(
            project_root,
            f"{clean_symbol} callers callees imports usages",
            reason=str(probe["reason"]),
            max_files=12,
            max_chars=max_chars,
        )
        fallback["mode"] = "impact-fallback"
        fallback["symbol"] = clean_symbol
        fallback["complete"] = False
        return fallback
    command = [
        "impact",
        clean_symbol,
        "--path",
        str(project_root),
        "--depth",
        str(depth),
        "--json",
    ]
    completed = _run(
        str(probe["executable"]), command, project_root, timeout_seconds, runner
    )
    if completed.returncode != 0:
        raise SemanticContextError(
            (completed.stderr or completed.stdout).strip() or "impact failed"
        )
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SemanticContextError("codegraph impact did not return JSON") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("affected"), list):
        raise SemanticContextError("codegraph impact returned an unsupported schema")
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": "codegraph",
        "semantic": True,
        "complete": True,
        "mode": "impact",
        "root": str(project_root),
        "version": probe["status"].get("version"),
        **raw,
        "response_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "command": command,
    }


def affected_tests(
    root: str | Path,
    files: Sequence[str],
    *,
    executable: str | None = None,
    depth: int = 5,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
    test_filter: str | None = None,
) -> dict[str, object]:
    project_root = _resolve_root(root)
    changed = [value.strip() for value in files if value.strip()]
    if not changed:
        raise SemanticContextError("at least one changed file is required")
    probe = probe_codegraph(
        project_root,
        executable=executable,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )
    if not probe["usable"]:
        query = " ".join(Path(value).stem for value in changed)
        fallback = _fallback_context(
            project_root,
            query,
            reason=str(probe["reason"]),
            max_files=20,
            max_chars=8_000,
        )
        candidates = [
            item["path"]
            for item in fallback.get("files", [])
            if isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and TEST_PATH_PATTERN.search(item["path"])
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "provider": "context-lens",
            "semantic": False,
            "complete": False,
            "mode": "affected-fallback",
            "root": str(project_root),
            "changedFiles": changed,
            "affectedTests": candidates,
            "fallback_reason": probe["reason"],
            "context": fallback,
        }
    suffixes = {Path(value).suffix.lower() for value in changed}
    inferred_filter = (
        TEST_FILTERS_BY_SUFFIX.get(next(iter(suffixes))) if len(suffixes) == 1 else None
    )
    applied_filter = test_filter or inferred_filter
    command = [
        "affected",
        *changed,
        "--path",
        str(project_root),
        "--depth",
        str(depth),
    ]
    if applied_filter:
        command.extend(["--filter", applied_filter])
    command.append("--json")
    completed = _run(
        str(probe["executable"]), command, project_root, timeout_seconds, runner
    )
    if completed.returncode != 0:
        raise SemanticContextError(
            (completed.stderr or completed.stdout).strip() or "affected failed"
        )
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SemanticContextError("codegraph affected did not return JSON") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("affectedTests"), list):
        raise SemanticContextError("codegraph affected returned an unsupported schema")
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": "codegraph",
        "semantic": True,
        "complete": True,
        "mode": "affected",
        "root": str(project_root),
        "version": probe["status"].get("version"),
        "testFilter": applied_filter,
        **raw,
        "response_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "command": command,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--codegraph", help="CodeGraph executable path or command")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--json", action="store_true")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    subparsers.add_parser("probe")

    explore_parser = subparsers.add_parser("explore")
    explore_parser.add_argument("--query", required=True)
    explore_parser.add_argument("--max-files", type=int, default=12)
    explore_parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)

    impact_parser = subparsers.add_parser("impact")
    impact_parser.add_argument("--symbol", required=True)
    impact_parser.add_argument("--depth", type=int, default=2)
    impact_parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)

    affected_parser = subparsers.add_parser("affected")
    affected_parser.add_argument("--file", action="append", required=True)
    affected_parser.add_argument("--depth", type=int, default=5)
    affected_parser.add_argument("--test-filter")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.subcommand == "probe":
            result = probe_codegraph(
                args.root, executable=args.codegraph, timeout_seconds=args.timeout
            )
        elif args.subcommand == "explore":
            result = explore(
                args.root,
                args.query,
                executable=args.codegraph,
                max_files=args.max_files,
                max_chars=args.max_chars,
                timeout_seconds=args.timeout,
            )
        elif args.subcommand == "impact":
            result = impact(
                args.root,
                args.symbol,
                executable=args.codegraph,
                depth=args.depth,
                max_chars=args.max_chars,
                timeout_seconds=args.timeout,
            )
        else:
            result = affected_tests(
                args.root,
                args.file,
                executable=args.codegraph,
                depth=args.depth,
                timeout_seconds=args.timeout,
                test_filter=args.test_filter,
            )
    except SemanticContextError as error:
        print(
            json.dumps({"error": str(error)}, ensure_ascii=False)
            if args.json
            else f"error: {error}"
        )
        return 2
    print(
        json.dumps(result, indent=2, ensure_ascii=False)
        if args.json
        else json.dumps(result, ensure_ascii=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
