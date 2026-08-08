#!/usr/bin/env python3
"""Resolve the clone command the README documents against the remote it names.

`bump_version.py --check` proves the release-version carriers agree with each
other. Agreement is not existence. At 1.8.2 every carrier named `v1.8.2`, that
gate was green, and `git clone --branch v1.8.2` returned nothing at all,
because the tag was never pushed -- internal consistency cannot see a missing
ref, and nothing else was looking. This is the half that needs the network.

It is deliberately unreachable from `scripts/validate_all.py --offline`: the
offline entrypoint stays offline, and this runs in its own job. It is also not
on the per-push path, because the release checklist requires a green branch run
*before* the tag is created, so a per-push form of this gate could never be
satisfied at the one moment a release depends on it. The nightly run is what
distinguishes the legitimate window between the bump commit and the tag push
from the 1.8.2 state, in which that window never closed.

Every way of not knowing is a failure. A git that will not run, a remote that
will not answer, output naming no ref, a README that stopped carrying a clone
command at all -- each exits non-zero. A check that reports success when it
could not look is the same defect one storey up.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REMOTE_TIMEOUT_SECONDS = 60

# Captured from the documented command rather than from a version constant,
# because the claim under test is that this exact command still works.
CLONE_PATTERN = re.compile(
    r"git clone\s+--branch\s+(?P<ref>\S+)\s+--depth\s+\d+\s+(?P<url>https://\S+)"
)
# The prerelease part matches bump_version.VERSION_PATTERN. A README pinned to
# a release candidate documents a tag that exists like any other, and refusing
# the spelling here would report a malformed ref for a command that works.
TAG_PATTERN = re.compile(r"^v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?$")


class ReleaseRefError(RuntimeError):
    """Raised when the documented clone command cannot be shown to resolve."""


def _readme_text(root: Path) -> str:
    try:
        return (root / "README.md").read_text(encoding="utf-8")
    except OSError as error:
        raise ReleaseRefError(f"cannot read README.md: {error}") from error


def documented_clone_targets(root: Path) -> list[tuple[str, str]]:
    """Every `(url, ref)` pair the README tells a reader to clone.

    An empty result raises rather than passing vacuously: a README that stopped
    naming a clone command would otherwise turn this gate green by giving it
    nothing to check.
    """
    targets = {
        (match.group("url").rstrip("/"), match.group("ref"))
        for match in CLONE_PATTERN.finditer(_readme_text(root))
    }
    if not targets:
        raise ReleaseRefError("README.md documents no `git clone --branch` command")
    if len(targets) != 1:
        raise ReleaseRefError(
            "README.md documents divergent clone commands: "
            + ", ".join(f"{ref} at {url}" for url, ref in sorted(targets))
        )
    return sorted(targets)


def _ls_remote(url: str, ref: str, runner) -> str:
    """Return what the remote answered, or raise saying why it did not."""
    try:
        completed = runner(
            ["git", "ls-remote", "--tags", url, f"refs/tags/{ref}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=REMOTE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleaseRefError(f"cannot query {url}: {error}") from error
    if completed.returncode != 0:
        diagnostic = " ".join((completed.stderr or "").split()) or "no diagnostic"
        raise ReleaseRefError(
            f"git ls-remote {url} exited {completed.returncode}: {diagnostic}"
        )
    return completed.stdout or ""


def remote_tag_exists(url: str, ref: str, *, runner=subprocess.run) -> bool:
    """Ask the remote whether it publishes `ref` as a tag.

    `git ls-remote` performs the resolution `git clone --branch` performs,
    without a checkout and without credentials against a public repository.
    """
    names = (f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}")
    return any(
        line.split("\t")[-1].strip() in names
        for line in _ls_remote(url, ref, runner).splitlines()
        if line.strip()
    )


def check(root: Path, *, expect_ref: str | None = None, runner=subprocess.run) -> dict:
    """Prove the documented clone command resolves, or raise saying why not."""
    (url, ref), *_ = documented_clone_targets(root)
    if not TAG_PATTERN.fullmatch(ref):
        raise ReleaseRefError(f"README.md documents a malformed release ref: {ref!r}")
    # On a tag build the ref under release is authoritative: a README naming
    # any other tag documents a command for something that was not built here.
    if expect_ref is not None and ref != expect_ref:
        raise ReleaseRefError(
            f"README.md documents {ref}, but the release under build is {expect_ref}"
        )
    if not remote_tag_exists(url, ref, runner=runner):
        raise ReleaseRefError(
            f"{url} publishes no tag {ref}: the documented clone command fails. "
            "Push the tag, or move the carriers back to a release that exists."
        )
    return {"repository": url, "ref": ref, "resolved": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PLUGIN_ROOT)
    parser.add_argument(
        "--expect-ref",
        help="tag under release; requires the README to document exactly it",
    )
    args = parser.parse_args(argv)
    try:
        payload = check(args.root.resolve(), expect_ref=args.expect_ref)
    except ReleaseRefError as error:
        print(json.dumps({"error": str(error), "resolved": False}, sort_keys=True))
        return 2
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
