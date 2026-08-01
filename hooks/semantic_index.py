#!/usr/bin/env python3
"""Keep the optional semantic index fresh at the start of a session.

Separate from ``selective_hooks.py`` on purpose: that script records evidence a
completion gate depends on, and index maintenance must never be able to break
it. This one is advisory in full. It refreshes an existing provider index so
semantic navigation is usable, and reports what it did without ever failing the
session.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# SessionStart also fires for compaction and forks, where nothing on disk moved
# and a refresh would only cost time.
REFRESH_SOURCES = {"startup", "resume"}
PROJECT_MARKERS = (".git", ".hg", ".svn")
INDEX_DIRECTORY = "graphify-out"
DEFAULT_TIMEOUT_SECONDS = 120.0
STAMP_TIMEOUT_SECONDS = 15.0
MAX_STDIN_BYTES = 2 * 1024 * 1024


def _read_payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    except OSError:
        return {}
    if not raw or len(raw) > MAX_STDIN_BYTES:
        return {}
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _first(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = payload.get(name)
        if value is not None:
            return value
    return None


def _workspace(payload: dict[str, Any]) -> Path | None:
    value = _first(payload, "cwd", "workingDirectory", "working_directory")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        path = Path(value).expanduser().resolve()
    except OSError:
        return None
    return path if path.is_dir() else None


def _is_project(root: Path) -> bool:
    """Refuse to index a directory that is not a checkout.

    A session opened in a home directory or a mounted share would otherwise
    walk an unbounded tree and leave an index behind in it.
    """
    return any((root / marker).exists() for marker in PROJECT_MARKERS)


def _timeout() -> float:
    raw = os.environ.get("COGNITIVE_POWERS_INDEX_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    # inf is a natural way to ask for no timeout, but subprocess.run raises
    # OverflowError for it, which would break this module's never-raise
    # contract. nan already fails the comparison.
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return value


def _git_output(argv: list[str], root: Path, runner) -> str | None:
    """Return one git query's stdout, or None when it fails for any reason."""
    try:
        completed = runner(
            argv,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=STAMP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError, OverflowError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout or ""


def _index_stamp(root: Path, runner) -> str | None:
    """Return a cheap signature of the worktree, or None when unavailable.

    Rebuilding the graph costs seconds; asking Git what changed costs
    milliseconds. Without this the hook re-extracts an unchanged tree on every
    startup and resume.
    """
    parts = []
    for argv in (["git", "rev-parse", "HEAD"], ["git", "status", "--porcelain"]):
        output = _git_output(argv, root, runner)
        if output is None:
            return None
        parts.append(output)
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def refresh(payload: dict[str, Any], *, runner=subprocess.run) -> dict[str, Any]:
    """Return a structured outcome; never raise."""
    if os.environ.get("COGNITIVE_POWERS_DISABLE_INDEX"):
        return {"status": "disabled", "reason": "disabled by environment"}

    source = _first(payload, "source", "trigger")
    if isinstance(source, str) and source and source not in REFRESH_SOURCES:
        return {"status": "skipped", "reason": f"session source is {source}"}

    root = _workspace(payload)
    if root is None:
        return {"status": "skipped", "reason": "no usable working directory"}
    if not _is_project(root):
        return {"status": "skipped", "reason": "working directory is not a checkout"}

    executable = shutil.which("graphify")
    if not executable:
        # The provider is optional. Its absence is the normal case, not a fault.
        return {"status": "skipped", "reason": "graphify is not installed"}

    index = root / INDEX_DIRECTORY
    if not index.is_dir():
        # Refreshing an existing index is maintenance; creating one installs a
        # provider into a checkout the user may not own, which the navigation
        # reference reserves as their decision.
        return {"status": "skipped", "reason": "no existing index to refresh"}

    stamp = _index_stamp(root, runner)
    stamp_path = index / ".cognitive-powers-stamp"
    if stamp is not None:
        try:
            if stamp_path.read_text(encoding="utf-8").strip() == stamp:
                return {"status": "current", "reason": "worktree unchanged"}
        # Silence is this hook's contract, not an oversight: CLAUDE.md,
        # "Three hooks, two shapes", makes semantic_index advisory in full.
        # An unreadable stamp means the index is refreshed again, which is the
        # harmless direction; reporting it would spend a startup message on a
        # cache miss.
        except (OSError, UnicodeDecodeError):
            pass

    timeout = _timeout()
    try:
        completed = runner(
            [executable, "update", str(root)],
            cwd=str(root),
            capture_output=True,
            text=True,
            # graphify emits UTF-8 whatever the console codepage; decoding it
            # with the ANSI page raises on bytes that page leaves undefined.
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "reason": f"graphify update exceeded {timeout}s",
        }
    except (OSError, ValueError, OverflowError) as error:
        return {"status": "error", "reason": f"graphify update failed: {error}"}

    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        return {
            "status": "error",
            "reason": f"graphify update exited {completed.returncode}",
            "detail": tail[-1] if tail else "",
        }
    if stamp is not None:
        try:
            stamp_path.write_text(stamp, encoding="utf-8")
        # Same contract as above (CLAUDE.md, "Three hooks, two shapes"). The
        # refresh already succeeded, so failing to record the stamp costs one
        # redundant rebuild next time and nothing else.
        except OSError:
            pass
    return {"status": "refreshed", "root": str(root)}


def _startup_message(outcome: dict[str, Any]) -> str | None:
    """Return the startup line this outcome deserves, or None for silence.

    Quiet on the ordinary paths. A refreshed index changes what navigation is
    allowed to trust, so that one is worth a line; a missing optional provider
    is not.
    """
    if outcome["status"] == "refreshed":
        return "Cognitive Powers refreshed the semantic index for this project."
    if outcome["status"] in {"error", "timeout"}:
        return (
            "Cognitive Powers could not refresh the semantic index: "
            f"{outcome['reason']}. Navigation falls back to lexical search."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["session-start"])
    parser.parse_args(argv)

    try:
        outcome = refresh(_read_payload())
    except Exception as error:  # noqa: BLE001 - advisory hook must not fail closed
        outcome = {"status": "error", "reason": str(error)}

    message = _startup_message(outcome)
    if message is not None:
        print(
            json.dumps(
                {"systemMessage": message, "suppressOutput": True},
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
