#!/usr/bin/env python3
"""The one release identity every release-facing script derives from.

A version literal in a release gate is invisible to a green suite: the fixtures
are written to agree with the constant, so only the next tag ever disagrees, and
by then the gate either refuses the release outright or names the artifact after
the version before it. Deriving identity here keeps the manifest the single
place a release version is written, and keeps the artifact name bound to the
content it describes.

Dependency-free and loaded by path, so a script keeps working when it is
executed standalone from a staged package.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_RELATIVE = Path(".codex-plugin") / "plugin.json"
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")
ARCHIVE_PREFIX = "cognitive-powers-"


class ReleaseIdentityError(ValueError):
    """Raised when the release version cannot be derived from the manifest."""


def plugin_version(root: Path | None = None) -> str:
    """Return the released version declared by the plugin manifest.

    Build metadata after ``+`` is part of a local identity, not of the release
    the tag names, so it is dropped exactly as the plugin contract test drops it.
    """
    path = (root or PLUGIN_ROOT) / MANIFEST_RELATIVE
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseIdentityError(f"cannot read plugin manifest: {path}") from error
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if not isinstance(version, str):
        raise ReleaseIdentityError(f"plugin manifest declares no version: {path}")
    released = version.split("+", 1)[0]
    if not VERSION_PATTERN.fullmatch(released):
        raise ReleaseIdentityError(
            f"plugin manifest version is malformed: {version!r} in {path}"
        )
    return released


def release_tag(root: Path | None = None) -> str:
    return f"v{plugin_version(root)}"


def archive_name(version: str) -> str:
    """Return the canonical archive filename for one release version."""
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ReleaseIdentityError(f"malformed release version: {version!r}")
    return f"{ARCHIVE_PREFIX}{version}.tar"
