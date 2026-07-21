#!/usr/bin/env python3
"""Select delivery-review angles and normalize structured findings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


LEVEL = {"low": 0, "medium": 1, "high": 2}
AXES = {"contract", "quality"}
CATEGORIES = {
    "regression",
    "security",
    "reliability",
    "compatibility",
    "coverage",
    "scope",
}
PASS_VERDICTS = {"confirmed", "failed", "inconclusive"}


class ReviewError(ValueError):
    """Raised when review routing or synthesis violates the contract."""


def _boolean(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ReviewError(f"{field} must be boolean")
    return value


def select_angles(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise ReviewError("schema_version must be 1")
    cross_cutting = _boolean(payload, "cross_cutting")
    release_critical = _boolean(payload, "release_critical")
    delegated = _boolean(payload, "delegated")
    security_requested = _boolean(payload, "security_requested")
    boundaries = payload.get("boundaries")
    if not isinstance(boundaries, list) or not all(
        isinstance(item, str) for item in boundaries
    ):
        raise ReviewError("boundaries must be a string list")
    changed_modules = payload.get("changed_modules")
    if (
        not isinstance(changed_modules, int)
        or isinstance(changed_modules, bool)
        or changed_modules < 1
    ):
        raise ReviewError("changed_modules must be a positive integer")

    security_boundaries = {
        "authentication",
        "authorization",
        "secrets",
        "untrusted-input",
        "permissions",
        "destructive-operation",
    }
    security_relevant = security_requested or bool(
        set(boundaries) & security_boundaries
    )
    broad = cross_cutting or release_critical or delegated or changed_modules > 1
    if not broad and not security_relevant:
        passes = [{"name": "focused", "axes": ["contract", "quality"]}]
    else:
        passes = [
            {"name": "contract-scope", "axes": ["contract"]},
            {"name": "runtime-quality", "axes": ["quality"]},
        ]
        if changed_modules > 1:
            passes.append({"name": "code-contracts", "axes": ["quality"]})
        if delegated:
            passes.append({"name": "durable-evidence", "axes": ["contract", "quality"]})
        if security_relevant:
            passes.append({"name": "security-boundary", "axes": ["quality"]})
    return {
        "schema_version": 1,
        "passes": passes,
        "security_review_selected": security_relevant,
        "fixed_reviewer_count": False,
    }


def _strings(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ReviewError(f"{field} must be a string list")
    return sorted(set(item.strip() for item in value))


def _axis_verdict(
    verdicts: list[dict[str, Any]], axis: str, conflict: bool = False
) -> str:
    relevant = [item for item in verdicts if item["axis"] == axis]
    if not relevant:
        return "unverified"
    values = {item["verdict"] for item in relevant}
    if "failed" in values:
        return "contradicted"
    if conflict:
        return (
            "partially verified"
            if any(item["evidence"] for item in relevant)
            else "unverified"
        )
    if "inconclusive" in values:
        return (
            "partially verified"
            if any(item["evidence"] for item in relevant)
            else "unverified"
        )
    return "verified"


def synthesize(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise ReviewError("schema_version must be 1")
    source_identity = payload.get("source_identity")
    if not isinstance(source_identity, str) or not source_identity.strip():
        raise ReviewError("source_identity must be non-empty")
    findings = payload.get("findings")
    verdicts = payload.get("pass_verdicts")
    if not isinstance(findings, list) or not isinstance(verdicts, list):
        raise ReviewError("findings and pass_verdicts must be lists")

    normalized_verdicts: list[dict[str, Any]] = []
    for index, verdict in enumerate(verdicts):
        if not isinstance(verdict, dict):
            raise ReviewError(f"pass_verdicts[{index}] must be an object")
        axis = verdict.get("axis")
        value = verdict.get("verdict")
        if axis not in AXES or value not in PASS_VERDICTS:
            raise ReviewError(f"pass_verdicts[{index}] has invalid axis or verdict")
        normalized_verdicts.append(
            {
                "pass": str(verdict.get("pass", "")).strip(),
                "axis": axis,
                "verdict": value,
                "evidence": _strings(
                    verdict.get("evidence"), f"pass_verdicts[{index}].evidence"
                ),
                "source_identity": str(verdict.get("source_identity", "")).strip(),
            }
        )
        if normalized_verdicts[-1]["source_identity"] != source_identity.strip():
            raise ReviewError(
                f"pass_verdicts[{index}] reviews a different source identity"
            )

    grouped: dict[str, dict[str, Any]] = {}
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ReviewError(f"findings[{index}] must be an object")
        for field in ("finding_id", "issue_key", "location", "problem", "follow_up"):
            if not isinstance(finding.get(field), str) or not finding[field].strip():
                raise ReviewError(f"findings[{index}].{field} must be non-empty")
        axis = finding.get("axis")
        category = finding.get("category")
        severity = finding.get("severity")
        confidence = finding.get("confidence")
        if axis not in AXES or category not in CATEGORIES:
            raise ReviewError(f"findings[{index}] has invalid axis or category")
        if severity not in LEVEL or confidence not in LEVEL:
            raise ReviewError(f"findings[{index}] has invalid severity or confidence")
        evidence = _strings(finding.get("evidence"), f"findings[{index}].evidence")
        if str(finding.get("source_identity", "")).strip() != source_identity.strip():
            raise ReviewError(f"findings[{index}] reviews a different source identity")
        key = finding["issue_key"].strip()
        current = grouped.get(key)
        if current is None:
            grouped[key] = {
                "issue_key": key,
                "finding_ids": {finding["finding_id"].strip()},
                "axis": axis,
                "categories": {category},
                "severity": severity,
                "confidence": confidence,
                "locations": {finding["location"].strip()},
                "problem": finding["problem"].strip(),
                "problem_variants": {finding["problem"].strip()},
                "evidence": set(evidence),
                "follow_ups": {finding["follow_up"].strip()},
            }
            continue
        if current["axis"] != axis:
            raise ReviewError(f"issue_key {key} cannot cross review axes")
        current["finding_ids"].add(finding["finding_id"].strip())
        current["categories"].add(category)
        current["locations"].add(finding["location"].strip())
        current["evidence"].update(evidence)
        current["follow_ups"].add(finding["follow_up"].strip())
        current["problem_variants"].add(finding["problem"].strip())
        if LEVEL[severity] > LEVEL[current["severity"]]:
            current["severity"] = severity
        if LEVEL[confidence] < LEVEL[current["confidence"]]:
            current["confidence"] = confidence

    merged: list[dict[str, Any]] = []
    for item in grouped.values():
        confidence = item["confidence"] if item["evidence"] else "low"
        merged.append(
            {
                "issue_key": item["issue_key"],
                "finding_ids": sorted(item["finding_ids"]),
                "axis": item["axis"],
                "categories": sorted(item["categories"]),
                "severity": item["severity"],
                "confidence": confidence,
                "locations": sorted(item["locations"]),
                "problem": item["problem"],
                "problem_variants": sorted(item["problem_variants"]),
                "conflict": len(item["problem_variants"]) > 1,
                "evidence": sorted(item["evidence"]),
                "follow_ups": sorted(item["follow_ups"]),
            }
        )
    merged.sort(
        key=lambda item: (
            -LEVEL[item["severity"]],
            -LEVEL[item["confidence"]],
            item["issue_key"],
        )
    )
    contract_conflict = any(
        item["axis"] == "contract" and item["conflict"] for item in merged
    )
    quality_conflict = any(
        item["axis"] == "quality" and item["conflict"] for item in merged
    )
    return {
        "schema_version": 1,
        "kind": "delivery_review_synthesis",
        "source_identity": source_identity.strip(),
        "contract_verdict": _axis_verdict(
            normalized_verdicts, "contract", contract_conflict
        ),
        "quality_verdict": _axis_verdict(
            normalized_verdicts, "quality", quality_conflict
        ),
        "findings": merged,
        "pass_verdicts": normalized_verdicts,
    }


def _read_json(value: str) -> dict[str, Any]:
    raw = sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ReviewError("input must be a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("select", "synthesize"))
    parser.add_argument("--input", required=True, help="JSON path or - for stdin")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        payload = _read_json(args.input)
        result = (
            select_angles(payload) if args.command == "select" else synthesize(payload)
        )
    except (ReviewError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({"error": str(error)}) if args.json else f"ERROR: {error}")
        return 2
    print(json.dumps(result, indent=2) if args.json else json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
