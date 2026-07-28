#!/usr/bin/env python3
"""Generate compatibility status exclusively from immutable CI receipts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
from typing import Any, Sequence


def _load_release_identity():
    path = Path(__file__).resolve().with_name("release_identity.py")
    spec = importlib.util.spec_from_file_location("cp_release_identity", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the shared release identity: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RELEASE = _load_release_identity()


class CompatibilityError(ValueError):
    pass


def _digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _valid_receipt_candidate(receipt: dict[str, Any], expected_tag: str) -> bool:
    """Validate canonical content without trusting a self-asserted attestation."""
    if receipt.get("schemaVersion") != 2 or not _digest(receipt.get("receiptSha256")):
        return False
    unsigned = {key: value for key, value in receipt.items() if key != "receiptSha256"}
    canonical = json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != receipt["receiptSha256"]:
        return False
    identity = receipt.get("identity")
    installation = receipt.get("installation")
    attestation = receipt.get("attestation")
    return bool(
        isinstance(identity, dict)
        and isinstance(identity.get("commit"), str)
        and len(identity["commit"]) == 40
        and all(character in "0123456789abcdef" for character in identity["commit"])
        and isinstance(identity.get("workflow"), str)
        and identity["workflow"]
        and isinstance(identity.get("runId"), str)
        and identity["runId"].isdigit()
        and isinstance(identity.get("runAttempt"), int)
        and not isinstance(identity.get("runAttempt"), bool)
        and identity["runAttempt"] >= 1
        and isinstance(installation, dict)
        and installation.get("commit") == identity["commit"]
        and installation.get("tag") == expected_tag
        and _digest(installation.get("reportSha256"))
        and isinstance(attestation, dict)
        and attestation.get("kind") == "github-actions-validation"
        and attestation.get("verified") is False
        and _digest(attestation.get("validationReceiptSha256"))
    )


def build_matrix(
    contract: dict[str, Any],
    receipts: list[dict[str, Any]],
    *,
    verified_receipt_digests: set[str] | None = None,
    expected_tag: str | None = None,
) -> dict[str, Any]:
    if contract.get("schemaVersion") != 1:
        raise CompatibilityError("unsupported contract schema")
    if expected_tag is None:
        expected_tag = _RELEASE.release_tag()
    axes = contract.get("axes")
    scenarios = contract.get("scenarios")
    if (
        not isinstance(axes, dict)
        or sorted(axes) != ["codexCli", "os", "python"]
        or not isinstance(scenarios, list)
    ):
        raise CompatibilityError("compatibility axes or scenarios are malformed")
    for name, values in axes.items():
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(value, str) and value for value in values)
        ):
            raise CompatibilityError(f"axis {name} is malformed")
    if not scenarios or not all(
        isinstance(value, str) and value for value in scenarios
    ):
        raise CompatibilityError("scenarios are malformed")

    by_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    allowed = {name: set(values) for name, values in axes.items()}
    trusted = verified_receipt_digests or set()
    for receipt in receipts:
        if (
            not isinstance(receipt, dict)
            or not _valid_receipt_candidate(receipt, expected_tag)
            or receipt.get("receiptSha256") not in trusted
        ):
            continue
        passed = receipt.get("passed")
        if not isinstance(passed, bool):
            raise CompatibilityError("receipt passed flag is invalid")
        key = (
            receipt.get("os"),
            receipt.get("python"),
            receipt.get("codexCli"),
            receipt.get("scenario"),
        )
        if (
            key[0] not in allowed["os"]
            or key[1] not in allowed["python"]
            or key[2] not in allowed["codexCli"]
            or key[3] not in scenarios
        ):
            raise CompatibilityError("receipt identifies an undeclared combination")
        by_key.setdefault(key, []).append(receipt)
    for key, items in by_key.items():
        if len({item["passed"] for item in items}) > 1:
            raise CompatibilityError(f"conflicting receipts for {key}")

    rows: list[dict[str, Any]] = []
    for os_name, python, codex, scenario in itertools.product(
        axes["os"], axes["python"], axes["codexCli"], scenarios
    ):
        evidence = sorted(
            item["receiptSha256"]
            for item in by_key.get((os_name, python, codex, scenario), [])
        )
        if not evidence:
            status = "unknown"
        elif all(item["passed"] for item in by_key[(os_name, python, codex, scenario)]):
            status = "compatible"
        else:
            status = "incompatible"
        rows.append(
            {
                "os": os_name,
                "python": python,
                "codexCli": codex,
                "scenario": scenario,
                "status": status,
                "evidence": evidence,
            }
        )
    summary = {
        name: sum(row["status"] == name for row in rows)
        for name in ("compatible", "incompatible", "unknown")
    }
    return {
        "schemaVersion": 1,
        "generatedFrom": "ci-receipts",
        "axes": axes,
        "scenarios": scenarios,
        "rows": rows,
        "summary": summary,
    }


def markdown(matrix: dict[str, Any]) -> str:
    lines = [
        "# Generated compatibility matrix",
        "",
        "> Generated from CI receipts. Missing evidence is **unknown**, never compatible.",
        "",
        "| OS | Python | Codex CLI | Scenario | Status | Evidence |",
        "|---|---|---|---|---|---|",
    ]
    for row in matrix["rows"]:
        lines.append(
            f"| {row['os']} | {row['python']} | {row['codexCli']} | {row['scenario']} | {row['status']} | {', '.join(row['evidence']) or 'none'} |"
        )
    return "\n".join(lines) + "\n"


def outputs_match(
    contract: dict[str, Any],
    receipts: list[dict[str, Any]],
    current_matrix: dict[str, Any],
    current_markdown: str,
    *,
    expected_tag: str | None = None,
) -> bool:
    """Return whether committed outputs exactly match receipt-derived truth."""
    expected = build_matrix(contract, receipts, expected_tag=expected_tag)
    return current_matrix == expected and current_markdown == markdown(expected)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, nargs="*", default=[])
    parser.add_argument("--verified-receipt-sha256", action="append", default=[])
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
        receipts = [
            json.loads(path.read_text(encoding="utf-8")) for path in args.receipts
        ]
        matrix = build_matrix(
            contract,
            receipts,
            verified_receipt_digests=set(args.verified_receipt_sha256),
        )
        if args.check:
            current_matrix = json.loads(args.json_output.read_text(encoding="utf-8"))
            current_markdown = args.markdown_output.read_text(encoding="utf-8")
            if not outputs_match(contract, receipts, current_matrix, current_markdown):
                raise CompatibilityError(
                    "committed compatibility outputs do not match CI receipts"
                )
        else:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(
                json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            args.markdown_output.write_text(markdown(matrix), encoding="utf-8")
    except (OSError, json.JSONDecodeError, CompatibilityError) as error:
        print(json.dumps({"error": str(error)}))
        return 2
    print(json.dumps(matrix["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
