#!/usr/bin/env python3
"""Verify an installed Cognitive Powers tree against an immutable Git tag."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence


def _load_frontmatter_reader():
    """Load the reader shared with the sibling release gate.

    Dependency-free and loaded by path, so this script keeps working when it is
    executed standalone from a staged package.
    """
    path = Path(__file__).resolve().with_name("skill_frontmatter.py")
    spec = importlib.util.spec_from_file_location("cp_skill_frontmatter", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the shared frontmatter reader: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FRONTMATTER = _load_frontmatter_reader()

EXIT_IDENTITY = 10
EXIT_CONTENT = 11
EXIT_INVENTORY = 12
EXIT_HOST = 13
ALLOWED_EXTRAS = {".codex-marketplace-install.json"}
EXPECTED_SKILLS = ["execute-durably", "solve-efficiently", "verify-delivery"]
SUPPORTED_HOSTS = ("codex", "claude-code")
EXPECTED_REPOSITORY_SOURCES = {
    "Drizzy07x/cognitive-powers",
    "https://github.com/Drizzy07x/cognitive-powers",
    "https://github.com/Drizzy07x/cognitive-powers.git",
    "git@github.com:Drizzy07x/cognitive-powers",
    "git@github.com:Drizzy07x/cognitive-powers.git",
    "ssh://git@github.com/Drizzy07x/cognitive-powers",
    "ssh://git@github.com/Drizzy07x/cognitive-powers.git",
}
Run = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    # A bare name goes to CreateProcess on Windows, which only ever appends
    # .exe. The Codex CLI is installed by npm, so its entry point there is
    # codex.cmd, and the host check failed with WinError 2 on a host that was
    # installed and working. shutil.which honours PATHEXT; the resulting
    # FileNotFoundError is an OSError, which callers already report as an
    # unexecutable host CLI rather than as a missing installation.
    executable = shutil.which(argv[0])
    if executable is None:
        raise FileNotFoundError(f"{argv[0]} is not on PATH")
    return subprocess.run(
        [executable, *argv[1:]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-C", str(root), *args])


def _identity_failure(tag: str, message: str) -> tuple[dict[str, Any], int]:
    return (
        {
            "schemaVersion": 1,
            "product": "cognitive-powers",
            "tag": tag,
            "matched": False,
            "failureCategory": "identity",
            "findings": [message],
        },
        EXIT_IDENTITY,
    )


def _tag_identity(source_root: Path, tag: str) -> tuple[str, dict[str, str]]:
    resolved = _git(source_root, "rev-parse", "--verify", f"{tag}^{{commit}}")
    if resolved.returncode != 0:
        raise ValueError(f"tag does not resolve to a commit: {tag}")
    commit = resolved.stdout.strip()
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
        raise ValueError("resolved tag commit is not a full SHA-1")
    tree = _git(source_root, "ls-tree", "-r", "-z", tag)
    if tree.returncode != 0:
        raise ValueError(f"cannot enumerate tag tree: {tag}")
    records: dict[str, str] = {}
    for raw in tree.stdout.split("\0"):
        if not raw:
            continue
        metadata, path = raw.split("\t", 1)
        _mode, kind, blob = metadata.split(" ")
        if kind == "blob":
            records[path] = blob
    if not records:
        raise ValueError("tag tree contains no blobs")
    return commit, records


def _installed_files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and ".git" not in path.relative_to(root).parts
    )


def _hash_installed(source_root: Path, relative: str, path: Path) -> str | None:
    completed = _git(source_root, "hash-object", f"--path={relative}", str(path))
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and len(value) == 40 else None


def _json_command(run: Run, argv: list[str]) -> Any:
    try:
        completed = run(argv)
    except OSError as error:
        raise RuntimeError(f"cannot execute host CLI: {error}") from error
    if completed.returncode != 0:
        raise RuntimeError(
            f"host CLI failed ({completed.returncode}): {' '.join(argv)}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"host CLI returned invalid JSON: {' '.join(argv)}"
        ) from error


def _model_invocable_skills(installed_root: Path) -> list[str]:
    """Return installed skills Claude Code may load automatically.

    The reader and the truthy test are shared with ``doctor.py`` on purpose.
    These two scripts are release gates checked against each other, and a
    second parser here diverged from that one: its ``(\\S+)`` capture could not
    read a quoted or spaced value the other accepts, so one would report a
    skill routable while the other called it an error.
    """
    automatic: list[str] = []
    for skill in sorted((installed_root / "skills").glob("*/SKILL.md")):
        fields = _FRONTMATTER.read(skill)
        if not fields:
            continue
        if not _FRONTMATTER.is_truthy(fields.get("disable-model-invocation")):
            automatic.append(skill.parent.name)
    return automatic


def _claude_surface(installed_root: Path, version: str) -> dict[str, Any]:
    """Verify the Claude Code packaging of an installed tree."""
    try:
        manifest = json.loads(
            (installed_root / ".claude-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as error:
        return {
            "matched": False,
            "host": "claude-code",
            "error": str(error),
            "exposedSkills": [],
            "internalWorkflows": [],
        }
    installed_skills = sorted(
        path.parent.name for path in (installed_root / "skills").glob("*/SKILL.md")
    )
    automatic = _model_invocable_skills(installed_root)
    return {
        "matched": (
            manifest.get("name") == "cognitive-powers"
            and manifest.get("version") == version
            and manifest.get("hooks") == "./hooks/hooks.claude.json"
            and "skills" not in manifest
            # Every installed workflow must be routable on this host: the core
            # skills delegate to the specialized ones by name, and Claude Code
            # cannot invoke a skill it was never shown.
            and automatic == installed_skills
            and len(installed_skills) == 15
        ),
        "host": "claude-code",
        "exposedSkills": automatic,
        "internalWorkflows": installed_skills,
    }


def verify_installation(
    source_root: Path,
    installed_root: Path,
    tag: str,
    *,
    run: Run = _run,
    host: str = "codex",
) -> tuple[dict[str, Any], int]:
    if host not in SUPPORTED_HOSTS:
        raise ValueError(f"unsupported host: {host}")
    source_root = source_root.resolve()
    installed_root = installed_root.resolve()
    try:
        commit, expected = _tag_identity(source_root, tag)
    except ValueError as error:
        return _identity_failure(tag, str(error))
    if not installed_root.is_dir():
        return _identity_failure(
            tag, f"installed root is not a directory: {installed_root}"
        )

    installed_files = _installed_files(installed_root)
    expected_paths = sorted(expected)
    missing = sorted(set(expected_paths) - set(installed_files))
    extras = sorted(set(installed_files) - set(expected_paths))
    unexpected_extras = sorted(set(extras) - ALLOWED_EXTRAS)
    mismatched = [
        relative
        for relative in expected_paths
        if relative not in missing
        and _hash_installed(source_root, relative, installed_root / relative)
        != expected[relative]
    ]
    content = {
        "matched": not missing and not mismatched and not unexpected_extras,
        "trackedFileCount": len(expected_paths),
        "missing": missing,
        "mismatched": mismatched,
        "extras": extras,
        "unexpectedExtras": unexpected_extras,
    }

    version = tag.removeprefix("v")

    if host == "claude-code":
        surface = _claude_surface(installed_root, version)
        if not content["matched"]:
            category, code = "content", EXIT_CONTENT
        elif not surface["matched"]:
            category, code = "inventory", EXIT_INVENTORY
        else:
            category, code = None, 0
        report = {
            "schemaVersion": 1,
            "product": "cognitive-powers",
            "host": host,
            "tag": tag,
            "commit": commit,
            "version": version,
            "installedRoot": str(installed_root),
            "matched": code == 0,
            "failureCategory": category,
            "content": content,
            "surface": surface,
            # Content and packaging are verified from the tag. The host's own
            # installation registry is not read, so this is never a complete
            # installed-host verification.
            "hostInventoryVerified": False,
            "inventory": {
                "attempted": False,
                "verified": False,
                "reason": (
                    "installed-host inventory is not implemented for claude-code"
                ),
            },
            "readOnly": True,
        }
        return report, code

    try:
        manifest = json.loads(
            (installed_root / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as error:
        manifest = {}
        surface_error = str(error)
    else:
        surface_error = None
    exposed = sorted(
        path.parent.name for path in (installed_root / "skills-core").glob("*/SKILL.md")
    )
    internal = sorted(
        path.parent.name for path in (installed_root / "skills").glob("*/SKILL.md")
    )
    surface_matched = (
        manifest.get("name") == "cognitive-powers"
        and manifest.get("version") == version
        and manifest.get("skills") == "./skills-core/"
        and exposed == EXPECTED_SKILLS
    )
    surface = {
        "matched": surface_matched,
        "host": host,
        "exposedSkills": exposed,
        "internalWorkflows": internal,
    }
    if surface_error:
        surface["error"] = surface_error

    try:
        marketplaces = _json_command(
            run, ["codex", "plugin", "marketplace", "list", "--json"]
        )
        plugins = _json_command(run, ["codex", "plugin", "list", "--json"])
    except RuntimeError as error:
        report = {
            "schemaVersion": 1,
            "product": "cognitive-powers",
            "tag": tag,
            "commit": commit,
            "matched": False,
            "failureCategory": "host-cli",
            "content": content,
            "surface": surface,
            "findings": [str(error)],
        }
        return report, EXIT_HOST
    configured = (
        [
            item
            for item in marketplaces.get("marketplaces", [])
            if isinstance(item, dict) and item.get("name") == "cognitive-powers"
        ]
        if isinstance(marketplaces, dict)
        else []
    )
    installed = (
        [
            item
            for item in plugins.get("installed", [])
            if isinstance(item, dict)
            and item.get("name") == "cognitive-powers"
            and item.get("installed") is True
        ]
        if isinstance(plugins, dict)
        else []
    )
    marketplace_source = (
        configured[0].get("marketplaceSource", {}).get("source")
        if len(configured) == 1
        and isinstance(configured[0].get("marketplaceSource"), dict)
        else None
    )
    marketplace_root_value = configured[0].get("root") if len(configured) == 1 else None
    try:
        marketplace_root = (
            Path(marketplace_root_value).resolve()
            if isinstance(marketplace_root_value, str) and marketplace_root_value
            else None
        )
    except OSError:
        marketplace_root = None
    marketplace_root_matches = marketplace_root == installed_root
    metadata_path = installed_root / ".codex-marketplace-install.json"
    try:
        install_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        install_metadata = None
    # What has to be proven is that the installed tree is the release commit.
    # Its own checkout answers that directly, while the host metadata is only a
    # record the host chose to leave: Codex does not write it where a
    # marketplace is rooted, so requiring it rejected installations that are in
    # fact at the right commit. The revision is therefore the primary evidence
    # and the metadata is corroboration, which is accepted when absent and
    # refused when present and disagreeing.
    try:
        head = _git(installed_root, "rev-parse", "HEAD")
        installed_revision = head.stdout.strip() if head.returncode == 0 else None
    except OSError:
        installed_revision = None
    revision_pinned = installed_revision == commit
    metadata_present = isinstance(install_metadata, dict)
    metadata_pinned = bool(
        metadata_present
        and install_metadata.get("source_type") == "git"
        and install_metadata.get("source") in EXPECTED_REPOSITORY_SOURCES
        and install_metadata.get("ref_name") == commit
        and install_metadata.get("revision") == commit
        and install_metadata.get("sparse_paths") == []
    )
    source_pinned = (
        marketplace_source in EXPECTED_REPOSITORY_SOURCES
        and (revision_pinned or metadata_pinned)
        and (metadata_pinned or not metadata_present)
    )
    inventory_matched = (
        len(configured) == 1
        and source_pinned
        and marketplace_root_matches
        and len(installed) == 1
        and installed[0].get("pluginId") == "cognitive-powers@cognitive-powers"
        and installed[0].get("enabled") is True
        and installed[0].get("version") == version
    )
    inventory = {
        "matched": inventory_matched,
        "marketplaceCount": len(configured),
        "marketplaceSource": marketplace_source,
        "marketplaceRoot": str(marketplace_root) if marketplace_root else None,
        "marketplaceRootMatchesInstalledRoot": marketplace_root_matches,
        "sourcePinnedToCommit": source_pinned,
        "installedRevision": installed_revision,
        "revisionPinnedToCommit": revision_pinned,
        "installMetadataPresent": metadata_present,
        "installMetadataPinnedToCommit": metadata_pinned,
        "installMetadataRevision": (
            install_metadata.get("revision")
            if isinstance(install_metadata, dict)
            else None
        ),
        "installationCount": len(installed),
        "pluginIds": sorted(str(item.get("pluginId")) for item in installed),
    }

    if not content["matched"]:
        category, code = "content", EXIT_CONTENT
    elif not surface["matched"] or not inventory["matched"]:
        category, code = "inventory", EXIT_INVENTORY
    else:
        category, code = None, 0
    report = {
        "schemaVersion": 1,
        "product": "cognitive-powers",
        "host": host,
        "tag": tag,
        "commit": commit,
        "version": version,
        "installedRoot": str(installed_root),
        "matched": code == 0,
        "failureCategory": category,
        "content": content,
        "surface": surface,
        "hostInventoryVerified": True,
        "inventory": inventory,
        "readOnly": True,
    }
    return report, code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--installed-root", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--host",
        choices=SUPPORTED_HOSTS,
        default="codex",
        help=(
            "host packaging to verify. claude-code verifies tagged content and "
            "packaging only; it never reads the host installation registry."
        ),
    )
    args = parser.parse_args(argv)
    report, code = verify_installation(
        args.source_root, args.installed_root, args.tag, host=args.host
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
