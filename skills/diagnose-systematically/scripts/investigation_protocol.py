#!/usr/bin/env python3
"""Route diagnostic effort and normalize independent hypothesis findings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


CONFIDENCE = {"low": 0, "medium": 1, "high": 2}


class InvestigationError(ValueError):
    """Raised when an investigation packet violates the contract."""


def _positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InvestigationError(f"{field} must be a positive integer")
    return value


def _boolean(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise InvestigationError(f"{field} must be boolean")
    return value


def route(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise InvestigationError("schema_version must be 1")
    reproduced = _boolean(payload, "symptom_reproduced")
    components = _positive_int(payload, "affected_components")
    seams = _positive_int(payload, "plausible_failure_seams")
    intermittent = _boolean(payload, "intermittent")
    regression_uncertain = _boolean(payload, "recent_change_uncertain")
    cheap_discriminator = _boolean(payload, "cheap_discriminator_available")

    reasons: list[str] = []
    parallel = False
    if not reproduced:
        reasons.append("reproduce the symptom before splitting causal investigation")
    elif cheap_discriminator:
        reasons.append("run the available cheap discriminating probe before delegating")
    elif components >= 2 and seams >= 2:
        parallel = True
        reasons.append(
            "multiple components and failure seams can be investigated independently"
        )
    if reproduced and not cheap_discriminator and intermittent and regression_uncertain:
        parallel = True
        reasons.append(
            "intermittency and an uncertain regression window create distinct probes"
        )
    if reproduced and not cheap_discriminator and components >= 3:
        parallel = True
        reasons.append("the failure crosses at least three components")

    if not parallel:
        reasons.append("one focused investigation has lower coordination cost")
        lanes = ["local-investigation"]
        mode = "focused"
    else:
        lanes = ["reproduction-scope"]
        if seams >= 2 or components >= 3:
            lanes.append("code-path-failure-seam")
        if regression_uncertain:
            lanes.append("recent-change-regression")
        lanes.append("proof-observability")
        mode = "parallel-read-only"

    return {
        "schema_version": 1,
        "mode": mode,
        "lanes": lanes,
        "reasons": reasons,
        "symptom_reproduced": reproduced,
        "fix_authorized": False,
    }


def _strings(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise InvestigationError(f"{field} must be a string list")
    return sorted(set(item.strip() for item in value))


def synthesize(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise InvestigationError("schema_version must be 1")
    reproduced = _boolean(payload, "symptom_reproduced")
    findings = payload.get("findings")
    if not isinstance(findings, list) or not findings:
        raise InvestigationError("findings must be a non-empty list")

    grouped: dict[str, dict[str, Any]] = {}
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise InvestigationError(f"findings[{index}] must be an object")
        required = (
            "role",
            "hypothesis_key",
            "hypothesis",
            "prediction",
            "falsifier",
            "proof_step",
            "confidence",
        )
        for field in required:
            if not isinstance(finding.get(field), str) or not finding[field].strip():
                raise InvestigationError(f"findings[{index}].{field} must be non-empty")
        confidence = finding["confidence"].strip().lower()
        if confidence not in CONFIDENCE:
            raise InvestigationError(f"findings[{index}].confidence is invalid")
        evidence = _strings(finding.get("evidence"), f"findings[{index}].evidence")
        missing = _strings(
            finding.get("missing_evidence"),
            f"findings[{index}].missing_evidence",
        )
        key = finding["hypothesis_key"].strip()
        current = grouped.get(key)
        if current is None:
            grouped[key] = {
                "hypothesis_key": key,
                "hypothesis": finding["hypothesis"].strip(),
                "predictions": {finding["prediction"].strip()},
                "falsifiers": {finding["falsifier"].strip()},
                "roles": {finding["role"].strip()},
                "evidence": set(evidence),
                "missing_evidence": set(missing),
                "proof_steps": {finding["proof_step"].strip()},
                "confidence": confidence,
            }
            continue
        current["roles"].add(finding["role"].strip())
        current["predictions"].add(finding["prediction"].strip())
        current["falsifiers"].add(finding["falsifier"].strip())
        current["evidence"].update(evidence)
        current["missing_evidence"].update(missing)
        current["proof_steps"].add(finding["proof_step"].strip())
        if CONFIDENCE[confidence] < CONFIDENCE[current["confidence"]]:
            current["confidence"] = confidence

    hypotheses: list[dict[str, Any]] = []
    for item in grouped.values():
        evidence = sorted(item["evidence"])
        confidence = item["confidence"] if evidence else "low"
        hypotheses.append(
            {
                "hypothesis_key": item["hypothesis_key"],
                "hypothesis": item["hypothesis"],
                "predictions": sorted(item["predictions"]),
                "falsifiers": sorted(item["falsifiers"]),
                "roles": sorted(item["roles"]),
                "evidence": evidence,
                "missing_evidence": sorted(item["missing_evidence"]),
                "proof_steps": sorted(item["proof_steps"]),
                "confidence": confidence,
            }
        )
    hypotheses.sort(
        key=lambda item: (
            -CONFIDENCE[item["confidence"]],
            -len(item["evidence"]),
            item["hypothesis_key"],
        )
    )
    return {
        "schema_version": 1,
        "kind": "diagnostic_hypothesis_synthesis",
        "symptom_reproduced": reproduced,
        "leading_hypothesis": hypotheses[0] if hypotheses else None,
        "hypotheses": hypotheses,
        "root_cause_proven": False,
    }


def _read_json(value: str) -> dict[str, Any]:
    raw = sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise InvestigationError("input must be a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("route", "synthesize"))
    parser.add_argument("--input", required=True, help="JSON path or - for stdin")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        payload = _read_json(args.input)
        result = route(payload) if args.command == "route" else synthesize(payload)
    except (InvestigationError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({"error": str(error)}) if args.json else f"ERROR: {error}")
        return 2
    print(json.dumps(result, indent=2) if args.json else json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
