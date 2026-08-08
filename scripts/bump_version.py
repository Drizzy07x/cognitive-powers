#!/usr/bin/env python3
"""Move every release-version carrier to the version the changelog declares.

A bump touches eight files, and the 1.6.0/1.7.0 era proved what one missed
carrier costs: two versions were described but never tagged, gates pinned the
wrong identity, and the README documented a rollback to a tag that does not
exist. This script makes the changelog the single starting point -- the bump
refuses to run until the new section is written -- and rewrites every carrier
from it, deriving the documented rollback target from the newest actually
published release.

The upgrade-origin constants (the v1.5.2 the lifecycle harness upgrades from)
are deliberately not carriers: they name a scenario origin the compatibility
contract declares, and they change only when that contract does.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
# One spelling of a version, used by every pattern below. The prerelease part
# is a closed set rather than the whole of semver: semver orders prerelease
# identifiers by rules nothing in this repository implements, and a format the
# ordering below cannot rank is one that picks the wrong rollback target in
# silence. Three labels that rank alpha < beta < rc is what the release
# checklist actually uses.
_CORE = r"\d+\.\d+\.\d+"
_PRERELEASE = r"(?:-(?:alpha|beta|rc)\.\d+)?"
_VERSION = _CORE + _PRERELEASE
PRERELEASE_RANK = {"alpha": 0, "beta": 1, "rc": 2}
VERSION_PATTERN = re.compile(rf"^{_VERSION}$")
HEADING_PATTERN = re.compile(
    rf"^## ({_VERSION}) - (\d{{4}}-\d{{2}}-\d{{2}})\s*$", re.MULTILINE
)
# The lookbehind keeps scenario identifiers such as "upgrade-v1.5.2" out of
# reach: they name an origin release on purpose, exactly as the release
# binding tests treat them.
TAG_PATTERN = re.compile(rf"(?<![A-Za-z0-9-])v{_VERSION}")


def version_order(version: str) -> tuple[int, int, int, int, int]:
    """Rank one version against another, prereleases below their own release.

    A prerelease is not a release of its line; 1.10.0-rc.1 precedes 1.10.0 and
    follows every 1.9.x. Comparing the dotted parts as integers is what the
    rollback target used to do, and `int("0-rc")` raises rather than ranking,
    so a prerelease would have failed the bump instead of being placed.
    """
    core, _, prerelease = version.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    if not prerelease:
        return (major, minor, patch, len(PRERELEASE_RANK), 0)
    label, _, number = prerelease.partition(".")
    return (major, minor, patch, PRERELEASE_RANK[label], int(number))


class BumpError(ValueError):
    """Raised when the version carriers cannot be moved coherently."""


def _read(root: Path, relative: str) -> str:
    try:
        return (root / relative).read_text(encoding="utf-8")
    except OSError as error:
        raise BumpError(f"cannot read {relative}: {error}") from error


def _write(root: Path, relative: str, content: str) -> None:
    (root / relative).write_text(content, encoding="utf-8", newline="\n")


def changelog_version(root: Path) -> str:
    match = HEADING_PATTERN.search(_read(root, "CHANGELOG.md"))
    if match is None:
        raise BumpError("CHANGELOG.md has no dated release heading")
    return match.group(1)


def changelog_section_body(root: Path, version: str) -> str:
    text = _read(root, "CHANGELOG.md")
    headings = list(HEADING_PATTERN.finditer(text))
    for index, heading in enumerate(headings):
        if heading.group(1) != version:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        return text[heading.end() : end].strip()
    raise BumpError(f"CHANGELOG.md has no section for {version}")


def published_releases(root: Path) -> list[str]:
    payload = json.loads(_read(root, "docs/releases.json"))
    releases = payload.get("published")
    if not isinstance(releases, list) or not releases:
        raise BumpError("docs/releases.json lists no published releases")
    return [str(item) for item in releases]


def rollback_target(root: Path, version: str) -> str:
    new = version_order(version)
    for tag in published_releases(root):
        if version_order(tag[1:]) < new:
            return tag
    raise BumpError(f"no published release below {version} to roll back to")


def _replace_json_version(root: Path, relative: str, version: str) -> bool:
    text = _read(root, relative)
    updated, count = re.subn(
        rf'("version"\s*:\s*"){_VERSION}(")',
        rf"\g<1>{version}\g<2>",
        text,
        count=1,
    )
    if count != 1:
        raise BumpError(f"{relative} carries no version field")
    changed = updated != text
    if changed:
        _write(root, relative, updated)
    return changed


def _apply(root: Path, version: str) -> list[str]:
    rollback = rollback_target(root, version)
    changed: list[str] = []

    for relative in (
        ".codex-plugin/plugin.json",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
    ):
        if _replace_json_version(root, relative, version):
            changed.append(relative)

    installer = _read(root, "install.ps1")
    updated, count = re.subn(
        rf'(\[string\]\$ReleaseRef = ")v{_VERSION}(")',
        rf"\g<1>v{version}\g<2>",
        installer,
        count=1,
    )
    if count != 1:
        raise BumpError("install.ps1 carries no default release ref")
    if updated != installer:
        _write(root, "install.ps1", updated)
        changed.append("install.ps1")

    # The POSIX installer is a second carrier of the same default. A port that
    # is not bumped installs the previous release while reporting the new one,
    # which is the stale-tag failure the 1.7.1 era produced repeatedly -- and it
    # would be invisible on the platform most contributors bump from.
    posix_installer = _read(root, "install.sh")
    updated, count = re.subn(
        rf'(^release_ref=")v{_VERSION}(")',
        rf"\g<1>v{version}\g<2>",
        posix_installer,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise BumpError("install.sh carries no default release ref")
    if updated != posix_installer:
        _write(root, "install.sh", updated)
        changed.append("install.sh")

    # The rollback command names the newest published release; every other bare
    # release tag in either document names the release being declared. The same
    # literal can play both roles across a bump, so the rollback phrase is
    # protected positionally rather than by value.
    #
    # Only README.md carries that phrase. Running the protection over
    # docs/operations.md as well caught its PowerShell *install* example, which
    # is not a rollback: the 1.8.2 bump moved that line backwards to v1.7.2
    # while its POSIX sibling three lines below moved forwards to v1.8.2, under
    # a sentence promising the two could not diverge. An install example names
    # the release being declared, like every other tag in that file.
    sentinel = "\0cp-rollback-target\0"
    readme = _read(root, "README.md")
    updated = re.sub(rf"-ReleaseRef v{_VERSION}", sentinel, readme)
    updated = TAG_PATTERN.sub(f"v{version}", updated)
    updated = updated.replace(sentinel, f"-ReleaseRef {rollback}")
    if updated != readme:
        _write(root, "README.md", updated)
        changed.append("README.md")

    operations = _read(root, "docs/operations.md")
    updated = TAG_PATTERN.sub(f"v{version}", operations)
    if updated != operations:
        _write(root, "docs/operations.md", updated)
        changed.append("docs/operations.md")
    return changed


def check(root: Path) -> dict[str, object]:
    version = changelog_version(root)
    body = changelog_section_body(root, version)
    if not body:
        raise BumpError(f"CHANGELOG section for {version} is empty")
    problems: list[str] = []
    for relative in (
        ".codex-plugin/plugin.json",
        ".claude-plugin/plugin.json",
    ):
        declared = json.loads(_read(root, relative)).get("version", "")
        if str(declared).split("+", 1)[0] != version:
            problems.append(f"{relative} declares {declared!r}, changelog {version}")
    marketplace = json.loads(_read(root, ".claude-plugin/marketplace.json"))
    entries = [
        str(entry.get("version", "")).split("+", 1)[0]
        for entry in marketplace.get("plugins", [])
    ]
    if entries != [version]:
        problems.append(f"marketplace.json declares {entries}, changelog {version}")
    installer = _read(root, "install.ps1")
    if f'[string]$ReleaseRef = "v{version}"' not in installer:
        problems.append("install.ps1 default release ref is stale")
    posix_installer = _read(root, "install.sh")
    if f'release_ref="v{version}"' not in posix_installer:
        problems.append("install.sh default release ref is stale")
    rollback = rollback_target(root, version)
    readme = _read(root, "README.md")
    if f"-ReleaseRef {rollback}" not in readme:
        problems.append(f"README rollback target is not {rollback}")
    # Every other release tag in the README names the release being declared,
    # including the `git clone --branch` command a stranger actually runs. That
    # command was the one carrier nothing checked, so a hand edit to it passed
    # this gate. The rollback phrase is removed positionally, exactly as the
    # bump protects it, rather than exempted by value.
    stale_readme = sorted(
        {
            tag
            for tag in TAG_PATTERN.findall(
                re.sub(rf"-ReleaseRef v{_VERSION}", "", readme)
            )
            if tag != f"v{version}"
        }
    )
    if stale_readme:
        problems.append(
            f"README.md names {', '.join(stale_readme)}, declared v{version}"
        )
    # The runbook has no rollback command, so every tag in it names the release
    # being declared. Checking it here is what makes the divergence the 1.8.2
    # bump produced fail the gate instead of sitting under a sentence claiming
    # it cannot happen.
    operations = _read(root, "docs/operations.md")
    stale = sorted(
        {tag for tag in TAG_PATTERN.findall(operations) if tag != f"v{version}"}
    )
    if stale:
        problems.append(
            f"docs/operations.md names {', '.join(stale)}, declared v{version}"
        )
    if problems:
        raise BumpError("; ".join(problems))
    return {"version": version, "rollback": rollback, "aligned": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "version",
        nargs="?",
        help="target version; defaults to the newest changelog heading",
    )
    parser.add_argument("--root", type=Path, default=PLUGIN_ROOT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify every carrier matches the changelog without writing",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.check:
            if args.version is not None:
                raise BumpError("--check takes no version argument")
            payload = check(root)
            print(json.dumps(payload, sort_keys=True))
            return 0
        version = args.version
        if version is None:
            raise BumpError("a target version is required unless --check is given")
        if not VERSION_PATTERN.fullmatch(version):
            raise BumpError(f"malformed version: {version!r}")
        declared = changelog_version(root)
        if declared != version:
            raise BumpError(
                f"write the CHANGELOG section first: its newest heading is "
                f"{declared}, not {version}"
            )
        if not changelog_section_body(root, version):
            raise BumpError(f"CHANGELOG section for {version} is empty")
        changed = _apply(root, version)
        check(root)
        print(
            json.dumps(
                {
                    "version": version,
                    "rollback": rollback_target(root, version),
                    "changed": changed,
                },
                sort_keys=True,
            )
        )
        # docs/releases.json is deliberately not a carrier here: the new tag
        # does not exist yet, and the file records only published releases.
        # It gains this release in the post-publication step of the checklist.
        return 0
    except BumpError as error:
        print(json.dumps({"error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
