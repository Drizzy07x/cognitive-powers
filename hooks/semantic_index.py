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
import json
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
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


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

    existing = (root / INDEX_DIRECTORY).is_dir()
    try:
        completed = runner(
            [executable, "update", str(root)],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_timeout(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "reason": f"graphify update exceeded {_timeout()}s",
        }
    except OSError as error:
        return {"status": "error", "reason": f"graphify update failed: {error}"}

    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        return {
            "status": "error",
            "reason": f"graphify update exited {completed.returncode}",
            "detail": tail[-1] if tail else "",
        }
    return {
        "status": "created" if not existing else "refreshed",
        "root": str(root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["session-start"])
    parser.parse_args(argv)

    try:
        outcome = refresh(_read_payload())
    except Exception as error:  # noqa: BLE001 - advisory hook must not fail closed
        outcome = {"status": "error", "reason": str(error)}

    # Quiet on the ordinary paths. A refreshed index changes what navigation is
    # allowed to trust, so that one is worth a line; a missing optional
    # provider is not.
    if outcome["status"] in {"created", "refreshed"}:
        print(
            json.dumps(
                {
                    "systemMessage": (
                        f"Cognitive Powers {outcome['status']} the semantic index "
                        "for this project."
                    ),
                    "suppressOutput": True,
                },
                ensure_ascii=False,
            )
        )
    elif outcome["status"] in {"error", "timeout"}:
        print(
            json.dumps(
                {
                    "systemMessage": (
                        "Cognitive Powers could not refresh the semantic index: "
                        f"{outcome['reason']}. Navigation falls back to lexical "
                        "search."
                    ),
                    "suppressOutput": True,
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
