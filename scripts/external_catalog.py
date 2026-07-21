#!/usr/bin/env python3
"""Validate and query the immutable external capability catalog."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
VALID_KINDS = {"provider", "pattern", "catalog", "vertical", "reference"}
VALID_STATUSES = {"candidate", "approved", "deferred", "rejected", "deprecated"}
VALID_TRANSITIONS = {
    "candidate": {"approved", "deferred", "rejected"},
    "approved": {"deprecated"},
    "deferred": {"candidate", "rejected"},
    "rejected": {"candidate"},
    "deprecated": {"candidate"},
}
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class CatalogError(ValueError):
    """Raised when catalog provenance or lifecycle rules are violated."""


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def catalog_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def load_catalog(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CatalogError("catalog must be a JSON object")
    return value


def validate_catalog(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported schema_version")
    if payload.get("plugin") != "cognitive-powers":
        errors.append("plugin identity must be cognitive-powers")
    if (
        not isinstance(payload.get("meta_version"), int)
        or payload.get("meta_version", 0) < 1
    ):
        errors.append("meta_version must be a positive integer")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        return errors + ["sources must be a non-empty list"]

    identities: set[str] = set()
    source_names: set[str] = set()
    for index, source in enumerate(sources):
        prefix = f"sources[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{prefix} must be an object")
            continue
        name = source.get("name")
        commit = source.get("commit")
        identity = f"{name}@{commit}"
        if not isinstance(name, str) or "/" not in name:
            errors.append(f"{prefix}.name must use owner/repository")
        elif name in source_names:
            errors.append(f"duplicate source name: {name}")
        else:
            source_names.add(name)
        if not isinstance(commit, str) or not COMMIT_PATTERN.fullmatch(commit):
            errors.append(f"{prefix}.commit must be an immutable 40-character SHA")
        elif identity in identities:
            errors.append(f"duplicate source identity: {identity}")
        else:
            identities.add(identity)
        if source.get("kind") not in VALID_KINDS:
            errors.append(f"{prefix}.kind is invalid")
        if source.get("status") not in VALID_STATUSES:
            errors.append(f"{prefix}.status is invalid")
        url = source.get("source")
        if not isinstance(url, str) or url != f"https://github.com/{name}":
            errors.append(f"{prefix}.source must be the canonical GitHub URL")
        license_name = source.get("license")
        if not isinstance(license_name, str) or not license_name.strip():
            errors.append(f"{prefix}.license is required")
        if license_name == "NO-LICENSE-DETECTED" and source.get("status") == "approved":
            errors.append(f"{prefix} cannot be approved without a detected license")
        if not isinstance(source.get("decision"), str) or not source.get("decision"):
            errors.append(f"{prefix}.decision is required")
        capabilities = source.get("capabilities")
        if (
            not isinstance(capabilities, list)
            or not capabilities
            or not all(isinstance(item, str) and item for item in capabilities)
        ):
            errors.append(f"{prefix}.capabilities must be non-empty strings")
        files = source.get("files")
        if not isinstance(files, list):
            errors.append(f"{prefix}.files must be a list")
        elif any(
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
            for item in files
        ):
            errors.append(f"{prefix}.files entries require path and sha256")

    labels = payload.get("labels")
    if not isinstance(labels, dict):
        errors.append("labels must be an object")
    else:
        for label, identity in labels.items():
            if not isinstance(label, str) or not label:
                errors.append("label names must be non-empty strings")
            if identity not in identities:
                errors.append(
                    f"label {label!r} points to unknown identity {identity!r}"
                )
    return errors


def resolve_label(payload: dict[str, Any], label: str) -> dict[str, Any]:
    errors = validate_catalog(payload)
    if errors:
        raise CatalogError("; ".join(errors))
    identity = payload["labels"].get(label)
    if identity is None:
        raise CatalogError(f"unknown label: {label}")
    for source in payload["sources"]:
        if f"{source['name']}@{source['commit']}" == identity:
            return copy.deepcopy(source)
    raise CatalogError(f"label target disappeared: {label}")


def transition_source(
    payload: dict[str, Any],
    name: str,
    status: str,
    *,
    expected_meta_version: int,
) -> dict[str, Any]:
    errors = validate_catalog(payload)
    if errors:
        raise CatalogError("; ".join(errors))
    if payload["meta_version"] != expected_meta_version:
        raise CatalogError("catalog changed since it was read")
    if status not in VALID_STATUSES:
        raise CatalogError(f"invalid status: {status}")
    updated = copy.deepcopy(payload)
    for source in updated["sources"]:
        if source["name"] != name:
            continue
        current = source["status"]
        if status not in VALID_TRANSITIONS.get(current, set()):
            raise CatalogError(f"invalid transition: {current} -> {status}")
        if status == "approved" and source["license"] == "NO-LICENSE-DETECTED":
            raise CatalogError("cannot approve a source without a detected license")
        source["status"] = status
        updated["meta_version"] += 1
        return updated
    raise CatalogError(f"unknown source: {name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "integrations" / "catalog.json",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("label")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = load_catalog(args.catalog)
        errors = validate_catalog(payload)
        if errors:
            raise CatalogError("; ".join(errors))
        result: object = (
            resolve_label(payload, args.label)
            if args.command == "resolve"
            else {
                "valid": True,
                "sources": len(payload["sources"]),
                "meta_version": payload["meta_version"],
                "sha256": catalog_hash(payload),
            }
        )
    except (OSError, json.JSONDecodeError, CatalogError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
