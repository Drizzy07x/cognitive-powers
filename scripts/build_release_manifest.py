#!/usr/bin/env python3
"""Build a reproducible release archive and manifest from one exact Git tag."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Sequence


class ManifestError(ValueError):
    pass


def _run(root: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=text, check=False
        )
    except OSError as error:
        raise ManifestError(f"cannot execute git: {error}") from error


def _checked(root: Path, *args: str) -> str:
    result = _run(root, *args)
    if result.returncode != 0:
        raise ManifestError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path, tag: str, archive_path: Path) -> dict[str, Any]:
    root = root.resolve()
    archive_path = archive_path.resolve()
    commit = _checked(root, "rev-parse", "--verify", f"{tag}^{{commit}}").strip()
    tags = sorted(
        line
        for line in _checked(root, "tag", "--points-at", commit).splitlines()
        if line
    )
    if tags != [tag]:
        raise ManifestError(f"release commit must have exactly tag {tag}; found {tags}")
    raw_manifest = _checked(root, "show", f"{tag}:.codex-plugin/plugin.json")
    try:
        plugin = json.loads(raw_manifest)
    except json.JSONDecodeError as error:
        raise ManifestError("tag plugin manifest is invalid JSON") from error
    version = plugin.get("version")
    if tag != f"v{version}" or plugin.get("name") != "cognitive-powers":
        raise ManifestError("tag and plugin identity do not match")

    records: list[dict[str, Any]] = []
    tree_result = _run(root, "ls-tree", "-r", "-z", tag, text=False)
    if tree_result.returncode != 0:
        raise ManifestError("git ls-tree failed")
    for record in tree_result.stdout.split(b"\0"):
        if not record:
            continue
        metadata_raw, path_raw = record.split(b"\t", 1)
        metadata = metadata_raw.decode("ascii")
        path = path_raw.decode("utf-8", errors="surrogateescape")
        mode, kind, object_id = metadata.split(" ")
        if kind != "blob":
            continue
        shown = _run(root, "show", f"{tag}:{path}", text=False)
        if shown.returncode != 0:
            raise ManifestError(f"cannot read tagged blob: {path}")
        records.append(
            {
                "path": path,
                "gitMode": mode,
                "gitBlob": object_id,
                "sha256": hashlib.sha256(shown.stdout).hexdigest(),
                "bytes": len(shown.stdout),
            }
        )
    records.sort(key=lambda item: item["path"])

    skills = sorted(
        Path(item["path"]).parent.name
        for item in records
        if item["path"].startswith("skills-core/")
        and item["path"].endswith("/SKILL.md")
    )
    if skills != ["execute-durably", "solve-efficiently", "verify-delivery"]:
        raise ManifestError("tag must expose exactly the three supported skills")
    hooks = json.loads(_checked(root, "show", f"{tag}:hooks/hooks.json")).get(
        "hooks", {}
    )
    if not isinstance(hooks, dict):
        raise ManifestError("tag hooks manifest is invalid")

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        root,
        "archive",
        "--format=tar",
        f"--prefix=cognitive-powers-{version}/",
        "-o",
        str(archive_path),
        tag,
    )
    if result.returncode != 0:
        raise ManifestError(result.stderr.strip() or "git archive failed")
    archive_digest = _sha256(archive_path)
    aggregate = hashlib.sha256()
    for item in records:
        aggregate.update(
            item["path"].encode("utf-8")
            + b"\0"
            + item["sha256"].encode("ascii")
            + b"\n"
        )
    return {
        "schemaVersion": 1,
        "product": "cognitive-powers",
        "version": version,
        "tag": tag,
        "commit": commit,
        "archive": {
            "format": "tar",
            "sha256": archive_digest,
            "bytes": archive_path.stat().st_size,
        },
        "files": records,
        "filesSha256": aggregate.hexdigest(),
        "ci": {
            "os": ["macos-latest", "ubuntu-latest", "windows-latest"],
            "python": ["3.11", "3.13"],
        },
        "publicSurface": {
            "skills": skills,
            "hooks": sorted(str(name) for name in hooks),
            "tools": [],
        },
        "reproducible": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(args.root, args.tag, args.archive)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, ManifestError) as error:
        print(json.dumps({"error": str(error)}))
        return 2
    print(
        json.dumps(
            {
                "output": str(args.output),
                "archiveSha256": manifest["archive"]["sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
