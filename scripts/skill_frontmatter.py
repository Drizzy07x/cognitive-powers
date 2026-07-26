#!/usr/bin/env python3
"""One reader for skill frontmatter, shared by the release gates.

``doctor.py`` and ``verify_installed.py`` are checked against each other: the
first reports which skills the model may load, the second asserts the installed
surface matches. Two parsers here diverged once already, one refusing a quoted
or spaced value the other accepted, which let them disagree about whether a
skill is routable.

Deliberately dependency-free so either script can load it by path without
dragging in the rest of the package.
"""

from __future__ import annotations

import re
from pathlib import Path

TRUTHY = frozenset({"true", "yes", "on", "1"})
_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$")


def read(path: Path) -> dict[str, str]:
    """Return the leading YAML frontmatter as flat scalar keys."""
    fields: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return fields
    if not lines or lines[0].strip() != "---":
        return fields
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = _FIELD.match(line)
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


def is_truthy(value: str | None) -> bool:
    """Match every boolean spelling Claude Code accepts in frontmatter."""
    if value is None:
        return False
    return value.strip().strip("\"'").lower() in TRUTHY
