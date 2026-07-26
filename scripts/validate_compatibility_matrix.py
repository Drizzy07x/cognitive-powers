#!/usr/bin/env python3
"""Fail-closed validation of a release compatibility matrix."""

from __future__ import annotations

import argparse
import json
import re
from itertools import product
from pathlib import Path


def validate(matrix_path: Path, contract_path: Path) -> None:
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if set(matrix) != {
        "schemaVersion",
        "generatedFrom",
        "axes",
        "scenarios",
        "rows",
        "summary",
    }:
        raise ValueError("matrix schema mismatch")
    if matrix["schemaVersion"] != 1 or matrix["generatedFrom"] != "ci-receipts":
        raise ValueError("matrix provenance mismatch")
    axes = contract["axes"]
    scenarios = contract["scenarios"]
    if matrix["axes"] != axes or matrix["scenarios"] != scenarios:
        raise ValueError("matrix contract mismatch")
    expected = set(product(axes["os"], axes["python"], axes["codexCli"], scenarios))
    rows = matrix["rows"]
    keys = []
    for row in rows:
        if set(row) != {"os", "python", "codexCli", "scenario", "status", "evidence"}:
            raise ValueError("matrix row schema mismatch")
        keys.append((row["os"], row["python"], row["codexCli"], row["scenario"]))
        if row["status"] != "compatible":
            raise ValueError("matrix contains a non-compatible row")
        if (
            not isinstance(row["evidence"], list)
            or not row["evidence"]
            or not all(
                isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                for value in row["evidence"]
            )
        ):
            raise ValueError("matrix row lacks receipt evidence")
    if len(keys) != len(expected) or set(keys) != expected:
        raise ValueError("matrix must contain the exact unique contract product")
    derived = {
        name: sum(row["status"] == name for row in rows)
        for name in ("compatible", "incompatible", "unknown")
    }
    if matrix["summary"] != derived or derived != {
        "compatible": len(expected),
        "incompatible": 0,
        "unknown": 0,
    }:
        raise ValueError("matrix summary mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    args = parser.parse_args()
    try:
        validate(args.matrix, args.contract)
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"valid": False, "error": str(error)}))
        return 1
    print(json.dumps({"valid": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
