#!/usr/bin/env python3
"""Fail-closed aggregation of per-cell reproducible release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path


def _load_release_identity():
    path = Path(__file__).resolve().with_name("release_identity.py")
    spec = importlib.util.spec_from_file_location("cp_release_identity", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the shared release identity: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RELEASE = _load_release_identity()


class AggregationError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aggregate(source: Path, output: Path, *, expected_cells: int) -> dict:
    archives = sorted(source.rglob("release-one.tar"))
    if len(archives) != expected_cells:
        raise AggregationError(
            f"expected {expected_cells} release archives, found {len(archives)}"
        )
    records = []
    archive_digests: set[str] = set()
    manifest_bytes: set[bytes] = set()
    for archive in archives:
        manifest = archive.with_name("release-one.json")
        checksum = archive.with_name("release-one.sha256")
        if not manifest.is_file() or not checksum.is_file():
            raise AggregationError(f"incomplete release artifact set: {archive.parent}")
        digest = _sha256(archive)
        expected_line = f"{digest}  release-one.tar"
        if checksum.read_text(encoding="ascii").strip() != expected_line:
            raise AggregationError(f"invalid checksum file: {checksum}")
        manifest_raw = manifest.read_bytes()
        try:
            manifest_value = json.loads(manifest_raw)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise AggregationError(f"invalid manifest: {manifest}") from error
        if manifest_value.get("archive", {}).get("sha256") != digest:
            raise AggregationError(f"manifest/archive mismatch: {manifest}")
        archive_digests.add(digest)
        manifest_bytes.add(manifest_raw)
        records.append(
            {
                "artifactDirectory": str(archive.parent.relative_to(source)),
                "archiveSha256": digest,
                "manifestSha256": hashlib.sha256(manifest_raw).hexdigest(),
            }
        )
    if len(archive_digests) != 1 or len(manifest_bytes) != 1:
        raise AggregationError(
            "release artifacts are not byte-identical across CI cells"
        )

    # Name the artifact after the manifest that was just proven byte-identical
    # across every cell and matched to the archive digest. A literal here would
    # let the release ship under the previous version's name, which is the one
    # identity claim a checksum cannot catch.
    canonical_manifest_bytes = next(iter(manifest_bytes))
    canonical_manifest_value = json.loads(canonical_manifest_bytes)
    if (
        not isinstance(canonical_manifest_value, dict)
        or canonical_manifest_value.get("product") != "cognitive-powers"
    ):
        raise AggregationError("release manifest does not identify cognitive-powers")
    version = canonical_manifest_value.get("version")
    try:
        archive_filename = _RELEASE.archive_name(version)
    except _RELEASE.ReleaseIdentityError as error:
        raise AggregationError(str(error)) from error

    output.mkdir(parents=True, exist_ok=True)
    canonical_archive = output / archive_filename
    canonical_manifest = output / "release-manifest.json"
    canonical_checksum = output / f"{archive_filename}.sha256"
    shutil.copyfile(archives[0], canonical_archive)
    canonical_manifest.write_bytes(canonical_manifest_bytes)
    digest = next(iter(archive_digests))
    canonical_checksum.write_text(
        f"{digest}  {canonical_archive.name}\n", encoding="ascii", newline="\n"
    )
    report = {
        "schemaVersion": 1,
        "product": "cognitive-powers",
        "version": version,
        "archive": archive_filename,
        "expectedCells": expected_cells,
        "verifiedCells": len(records),
        "archiveSha256": digest,
        "byteIdentical": True,
        "artifacts": records,
    }
    (output / "reproducibility-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-cells", type=int, required=True)
    args = parser.parse_args()
    try:
        report = aggregate(
            args.source.resolve(),
            args.output.resolve(),
            expected_cells=args.expected_cells,
        )
    except (OSError, AggregationError) as error:
        print(json.dumps({"error": str(error)}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
