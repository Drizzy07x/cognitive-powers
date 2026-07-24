#!/usr/bin/env python3
"""Print a compact, portable summary of a validation receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


class ReceiptReportError(ValueError):
    """Raised when the expected validation receipt cannot be interpreted."""


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ReceiptReportError(f"{label} must be an object")
    return value


def _bool(mapping: Mapping[str, Any], key: str, label: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ReceiptReportError(f"{label}.{key} must be boolean")
    return value


def _text(mapping: Mapping[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ReceiptReportError(f"{label}.{key} must be a non-empty string")
    return value


def _string_list(mapping: Mapping[str, Any], key: str, label: str) -> list[str]:
    value = mapping.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ReceiptReportError(f"{label}.{key} must be a string array")
    return list(value)


def _failed_commands(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    commands = payload.get("commands")
    if not isinstance(commands, list):
        raise ReceiptReportError("receipt.commands must be an array")
    failed: list[dict[str, Any]] = []
    for index, raw in enumerate(commands):
        label = f"receipt.commands[{index}]"
        command = _mapping(raw, label)
        passed = _bool(command, "passed", label)
        if passed:
            continue
        exit_code = command.get("exitCode")
        if exit_code is not None and (
            not isinstance(exit_code, int) or isinstance(exit_code, bool)
        ):
            raise ReceiptReportError(f"{label}.exitCode must be an integer or null")
        failed.append(
            {
                "name": _text(command, "name", label),
                "argv": _string_list(command, "command", label),
                "exitCode": exit_code,
                "stdoutTail": _text_tail(command, "stdoutTail", label),
                "stderrTail": _text_tail(command, "stderrTail", label),
            }
        )
    return failed


def _text_tail(mapping: Mapping[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise ReceiptReportError(f"{label}.{key} must be a string")
    return value


def build_validation_summary(path: Path) -> dict[str, Any]:
    path = path.resolve()
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ReceiptReportError(
            f"validation receipt cannot be read: {path}"
        ) from error
    try:
        payload = _mapping(json.loads(raw), "receipt")
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ReceiptReportError(
            f"validation receipt is invalid JSON: {path}"
        ) from error

    if payload.get("kind") != "cognitive-powers-validation":
        raise ReceiptReportError("receipt.kind is not cognitive-powers-validation")
    if payload.get("schemaVersion") != 1:
        raise ReceiptReportError("receipt.schemaVersion must be 1")

    offline = _mapping(payload.get("offline"), "receipt.offline")
    git = _mapping(payload.get("git"), "receipt.git")
    source = _mapping(payload.get("source"), "receipt.source")
    git_stable = _bool(git, "identityStable", "receipt.git")
    source_stable = _bool(source, "identityStable", "receipt.source")
    return {
        "kind": "cognitive-powers-validation-summary",
        "receipt": str(path),
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "validation_passed": _bool(payload, "passed", "receipt"),
        "passed": _bool(payload, "passed", "receipt"),
        "offline": {
            "complete": _bool(offline, "complete", "receipt.offline"),
            "passed": _bool(offline, "passed", "receipt.offline"),
        },
        "git": {
            "initialSha": _text(git, "initialSha", "receipt.git"),
            "finalSha": _text(git, "sha", "receipt.git"),
            "dirty": _bool(git, "dirty", "receipt.git"),
            "status": _string_list(git, "status", "receipt.git"),
            "identityStable": git_stable,
        },
        "source": {
            "initialSha256": _text(source, "initialSha256", "receipt.source"),
            "finalSha256": _text(source, "sha256", "receipt.source"),
            "identityStable": source_stable,
        },
        "identityStable": git_stable and source_stable,
        "failedCommands": _failed_commands(payload),
    }


def build_publication_summary(
    validation: Mapping[str, Any], publication_outcome: str
) -> dict[str, Any]:
    receipt_uploaded = publication_outcome == "success"
    validation_passed = validation.get("validation_passed") is True
    return {
        "kind": "cognitive-powers-validation-publication-summary",
        "validation_passed": validation_passed,
        "receipt_sha256": validation["receipt_sha256"],
        "artifact_publication_outcome": publication_outcome,
        "receipt_uploaded": receipt_uploaded,
        "code_check_represents": "validation",
        "release_evidence_preserved": receipt_uploaded,
        "release_preparation_blocked": not (validation_passed and receipt_uploaded),
        "release_ready_claimed": False,
        "release_ready_requirement": (
            "a preserved validation receipt and an independent release witness"
        ),
    }


def _write_github_outputs(summary: Mapping[str, Any]) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    if not destination:
        return
    try:
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(
                f"validation_passed={str(summary['validation_passed']).lower()}\n"
            )
            stream.write(f"receipt_sha256={summary['receipt_sha256']}\n")
    except OSError as error:
        print(f"warning: cannot write GITHUB_OUTPUT: {error}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--publication-outcome",
        choices=("success", "failure", "cancelled", "skipped"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validation = build_validation_summary(args.receipt)
    except (OSError, ReceiptReportError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    _write_github_outputs(validation)
    output = (
        build_publication_summary(validation, args.publication_outcome)
        if args.publication_outcome
        else validation
    )
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
