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

import argparse
import json
import re
from pathlib import Path
from typing import Sequence

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_RELATIVE = Path(".codex-plugin") / "plugin.json"
CHANGELOG_RELATIVE = Path("CHANGELOG.md")
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")
HEADING_PATTERN = re.compile(
    r"^## (\d+\.\d+\.\d+) - \d{4}-\d{2}-\d{2}\s*$", re.MULTILINE
)
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


def release_notes(version: str, root: Path | None = None) -> str:
    """Return the changelog section that describes one release.

    A published release has to say what changed, and the changelog is where
    that is already written. Notes generated from commit subjects restate the
    branch history instead: they name each change without its cause, its blast
    radius, or why it was not caught earlier, which is the part a reader
    deciding whether to upgrade actually needs.
    """
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ReleaseIdentityError(f"malformed release version: {version!r}")
    path = (root or PLUGIN_ROOT) / CHANGELOG_RELATIVE
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ReleaseIdentityError(f"cannot read changelog: {path}") from error
    headings = list(HEADING_PATTERN.finditer(text))
    for index, heading in enumerate(headings):
        if heading.group(1) != version:
            continue
        following = headings[index + 1].start() if index + 1 < len(headings) else None
        body = text[heading.end() : following].strip("\n")
        if not body.strip():
            raise ReleaseIdentityError(f"changelog section is empty: {version}")
        return body + "\n"
    raise ReleaseIdentityError(f"changelog has no section for {version}: {path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write one release's changelog notes")
    parser.add_argument(
        "--version", default=None, help="defaults to the declared plugin version"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        version = args.version or plugin_version()
        notes = release_notes(version)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Newline-pinned for the same reason the release manifest is: nothing
        # downstream should differ by the platform that produced it.
        args.output.write_text(notes, encoding="utf-8", newline="\n")
    except (OSError, ReleaseIdentityError) as error:
        print(json.dumps({"error": str(error)}))
        return 2
    print(json.dumps({"output": str(args.output), "version": version}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
