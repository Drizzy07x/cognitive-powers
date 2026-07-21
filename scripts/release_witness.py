#!/usr/bin/env python3
"""Create and verify a release witness bound to plugin files and real receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


IGNORED_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "benchmark-results",
}


class WitnessError(ValueError):
    """Raised when a witness would claim unsupported release evidence."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_release_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        yield path


def _receipt(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise WitnessError(f"validation receipt must be an object: {path}")
    if not isinstance(value.get("name"), str) or not value["name"]:
        raise WitnessError(f"validation receipt requires name: {path}")
    if not isinstance(value.get("passed"), bool):
        raise WitnessError(f"validation receipt requires boolean passed: {path}")
    if not isinstance(value.get("command"), list) or not value["command"]:
        raise WitnessError(f"validation receipt requires immutable argv: {path}")
    return value


def create_witness(root: Path, receipt_paths: Sequence[Path]) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipts = [_receipt(path.resolve()) for path in receipt_paths]
    files = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in iter_release_files(root)
    ]
    aggregate = hashlib.sha256()
    for item in files:
        aggregate.update(item["path"].encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(item["sha256"].encode("ascii"))
        aggregate.update(b"\n")
    return {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "plugin": manifest["name"],
        "version": manifest["version"],
        "files": files,
        "sourceSha256": aggregate.hexdigest(),
        "validations": receipts,
        "releaseReady": bool(receipts) and all(item["passed"] for item in receipts),
        "liveIntegrationsValidated": False,
    }


def verify_witness(root: Path, witness: dict[str, Any]) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    files = witness.get("files")
    if not isinstance(files, list) or not files:
        return ["witness has no files"]
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            errors.append("malformed file record")
            continue
        path = (root / item["path"]).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            errors.append(f"file escapes plugin root: {item['path']}")
            continue
        if not path.is_file():
            errors.append(f"missing file: {item['path']}")
        elif sha256_file(path) != item.get("sha256"):
            errors.append(f"changed file: {item['path']}")
    if witness.get("releaseReady") and not witness.get("validations"):
        errors.append("releaseReady cannot be true without validations")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--receipt", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        output = args.output.resolve()
        try:
            output.relative_to(root)
        except ValueError:
            pass
        else:
            raise WitnessError("release witness output must be outside the plugin root")
        payload = create_witness(root, args.receipt)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary, output)
    except (OSError, json.JSONDecodeError, KeyError, WitnessError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {"output": str(output), "releaseReady": payload["releaseReady"]},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
