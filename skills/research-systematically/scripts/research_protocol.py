#!/usr/bin/env python3
"""Pre-register research and evaluate claims against source-bound evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
MODES = {"confirmatory", "exploratory"}
VERDICTS = {"confirmed", "partially-confirmed", "rejected", "inconclusive"}


class ResearchError(ValueError):
    """Raised when a research packet loses its pre-registration or evidence chain."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def fingerprint(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchError(f"{field} must be non-empty")
    return value.strip()


def _strings(value: object, field: str, *, empty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ResearchError(f"{field} must be a string list")
    result = list(dict.fromkeys(item.strip() for item in value))
    if not empty and not result:
        raise ResearchError(f"{field} must not be empty")
    return result


def preregister(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise ResearchError("schema_version must be 1")
    research_id = _text(payload.get("research_id"), "research_id")
    question = _text(payload.get("question"), "question")
    methods = _strings(payload.get("methods"), "methods")
    stopping_rules = _strings(payload.get("stopping_rules"), "stopping_rules")
    hypotheses_raw = payload.get("hypotheses")
    experiments_raw = payload.get("experiments")
    if not isinstance(hypotheses_raw, list) or not hypotheses_raw:
        raise ResearchError("hypotheses must be a non-empty list")
    if not isinstance(experiments_raw, list) or not experiments_raw:
        raise ResearchError("experiments must be a non-empty list")
    hypotheses = []
    hypothesis_ids: set[str] = set()
    for index, item in enumerate(hypotheses_raw):
        if not isinstance(item, dict):
            raise ResearchError(f"hypotheses[{index}] must be an object")
        normalized = {
            field: _text(item.get(field), f"hypotheses[{index}].{field}")
            for field in ("hypothesis_id", "statement", "prediction", "falsifier")
        }
        if normalized["hypothesis_id"] in hypothesis_ids:
            raise ResearchError("hypothesis ids must be unique")
        hypothesis_ids.add(normalized["hypothesis_id"])
        hypotheses.append(normalized)
    experiments = []
    experiment_ids: set[str] = set()
    for index, item in enumerate(experiments_raw):
        if not isinstance(item, dict):
            raise ResearchError(f"experiments[{index}] must be an object")
        experiment_id = _text(
            item.get("experiment_id"), f"experiments[{index}].experiment_id"
        )
        if experiment_id in experiment_ids:
            raise ResearchError("experiment ids must be unique")
        mode = item.get("mode")
        if mode not in MODES:
            raise ResearchError(f"experiments[{index}].mode is invalid")
        hypothesis_id = _text(
            item.get("hypothesis_id"), f"experiments[{index}].hypothesis_id"
        )
        if hypothesis_id not in hypothesis_ids:
            raise ResearchError(
                f"experiments[{index}] references an unknown hypothesis"
            )
        experiments.append(
            {
                "experiment_id": experiment_id,
                "mode": mode,
                "hypothesis_id": hypothesis_id,
                "procedure": _text(
                    item.get("procedure"), f"experiments[{index}].procedure"
                ),
                "success_condition": _text(
                    item.get("success_condition"),
                    f"experiments[{index}].success_condition",
                ),
            }
        )
        experiment_ids.add(experiment_id)
    registration = {
        "schema_version": 1,
        "kind": "research_preregistration",
        "research_id": research_id,
        "question": question,
        "hypotheses": hypotheses,
        "methods": methods,
        "experiments": experiments,
        "stopping_rules": stopping_rules,
    }
    return {**registration, "preregistration_hash": fingerprint(registration)}


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise ResearchError("schema_version must be 1")
    registration = payload.get("preregistration")
    if not isinstance(registration, dict):
        raise ResearchError("preregistration must be an object")
    supplied_hash = payload.get("preregistration_hash")
    unsigned = dict(registration)
    unsigned.pop("preregistration_hash", None)
    expected_hash = fingerprint(unsigned)
    if supplied_hash != expected_hash:
        raise ResearchError("preregistration hash does not match the frozen plan")

    evidence_raw = payload.get("evidence")
    if not isinstance(evidence_raw, list) or not evidence_raw:
        raise ResearchError("evidence must be a non-empty list")
    evidence: list[dict[str, str]] = []
    evidence_ids: set[str] = set()
    for index, item in enumerate(evidence_raw):
        if not isinstance(item, dict):
            raise ResearchError(f"evidence[{index}] must be an object")
        evidence_id = _text(item.get("evidence_id"), f"evidence[{index}].evidence_id")
        if evidence_id in evidence_ids:
            raise ResearchError("evidence ids must be unique")
        bound = _text(item.get("fingerprint"), f"evidence[{index}].fingerprint")
        if not HASH.fullmatch(bound):
            raise ResearchError(f"evidence[{index}].fingerprint must be sha256")
        evidence.append(
            {
                "evidence_id": evidence_id,
                "source": _text(item.get("source"), f"evidence[{index}].source"),
                "fingerprint": bound,
                "observation": _text(
                    item.get("observation"), f"evidence[{index}].observation"
                ),
            }
        )
        evidence_ids.add(evidence_id)

    planned = {
        item["experiment_id"]: item for item in registration.get("experiments", [])
    }
    experiments_raw = payload.get("experiments")
    if not isinstance(experiments_raw, list):
        raise ResearchError("experiments must be a list")
    experiments = []
    reported: set[str] = set()
    for index, item in enumerate(experiments_raw):
        if not isinstance(item, dict):
            raise ResearchError(f"experiments[{index}] must be an object")
        experiment_id = _text(
            item.get("experiment_id"), f"experiments[{index}].experiment_id"
        )
        if experiment_id in reported:
            raise ResearchError("reported experiment ids must be unique")
        mode = item.get("mode")
        if mode not in MODES:
            raise ResearchError(f"experiments[{index}].mode is invalid")
        if mode == "confirmatory":
            if (
                experiment_id not in planned
                or planned[experiment_id]["mode"] != "confirmatory"
            ):
                raise ResearchError(
                    "confirmatory results must match a pre-registered experiment"
                )
        refs = _strings(item.get("evidence_ids"), f"experiments[{index}].evidence_ids")
        if not set(refs) <= evidence_ids:
            raise ResearchError(f"experiments[{index}] references unknown evidence")
        experiments.append(
            {
                "experiment_id": experiment_id,
                "mode": mode,
                "status": _text(item.get("status"), f"experiments[{index}].status"),
                "result": _text(item.get("result"), f"experiments[{index}].result"),
                "evidence_ids": refs,
                "deviations": _strings(
                    item.get("deviations", []),
                    f"experiments[{index}].deviations",
                    empty=True,
                ),
            }
        )
        reported.add(experiment_id)

    claims_raw = payload.get("claims")
    if not isinstance(claims_raw, list):
        raise ResearchError("claims must be a list")
    claims = []
    for index, item in enumerate(claims_raw):
        if not isinstance(item, dict):
            raise ResearchError(f"claims[{index}] must be an object")
        refs = _strings(item.get("evidence_ids"), f"claims[{index}].evidence_ids")
        if not set(refs) <= evidence_ids:
            raise ResearchError(f"claims[{index}] references unknown evidence")
        claims.append(
            {
                "claim_id": _text(item.get("claim_id"), f"claims[{index}].claim_id"),
                "claim": _text(item.get("claim"), f"claims[{index}].claim"),
                "evidence_ids": refs,
                "status": _text(item.get("status"), f"claims[{index}].status"),
            }
        )

    def records(field: str, required: tuple[str, ...]) -> list[dict[str, Any]]:
        raw = payload.get(field, [])
        if not isinstance(raw, list):
            raise ResearchError(f"{field} must be a list")
        result = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ResearchError(f"{field}[{index}] must be an object")
            normalized = {
                name: _text(item.get(name), f"{field}[{index}].{name}")
                for name in required
            }
            refs = _strings(item.get("evidence_ids"), f"{field}[{index}].evidence_ids")
            if not set(refs) <= evidence_ids:
                raise ResearchError(f"{field}[{index}] references unknown evidence")
            normalized["evidence_ids"] = refs
            result.append(normalized)
        return result

    dead_ends = records("dead_ends", ("dead_end_id", "approach", "reason"))
    pivots = records("pivots", ("pivot_id", "from_approach", "to_approach", "reason"))
    verifier = payload.get("verifier")
    if not isinstance(verifier, dict):
        raise ResearchError("verifier must be an object")
    verdict = verifier.get("verdict")
    if verdict not in VERDICTS:
        raise ResearchError("verifier.verdict is invalid")
    verifier_evidence = _strings(verifier.get("evidence_ids"), "verifier.evidence_ids")
    if not set(verifier_evidence) <= evidence_ids:
        raise ResearchError("verifier references unknown evidence")
    normalized_verifier = {
        "identity": _text(verifier.get("identity"), "verifier.identity"),
        "verdict": verdict,
        "rationale": _text(verifier.get("rationale"), "verifier.rationale"),
        "evidence_ids": verifier_evidence,
    }
    planned_confirmatory = {
        item["experiment_id"]
        for item in registration.get("experiments", [])
        if item.get("mode") == "confirmatory"
    }
    missing_confirmatory = sorted(planned_confirmatory - reported)
    open_experiments = sorted(
        item["experiment_id"]
        for item in experiments
        if item["status"] not in {"completed", "stopped"}
    )
    complete = (
        not missing_confirmatory and not open_experiments and verdict != "inconclusive"
    )
    return {
        "schema_version": 1,
        "kind": "research_evaluation",
        "research_id": registration.get("research_id"),
        "preregistration_hash": supplied_hash,
        "experiments": experiments,
        "claims": claims,
        "evidence": evidence,
        "dead_ends": dead_ends,
        "pivots": pivots,
        "verifier": normalized_verifier,
        "missing_confirmatory_experiments": missing_confirmatory,
        "open_experiments": open_experiments,
        "research_complete": complete,
        "claims_proven_beyond_evidence": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preregister", "evaluate"))
    parser.add_argument("--input", required=True, help="JSON path or - for stdin")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        raw = (
            sys.stdin.read()
            if args.input == "-"
            else Path(args.input).read_text(encoding="utf-8")
        )
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ResearchError("input must be a JSON object")
        result = (
            preregister(payload) if args.command == "preregister" else evaluate(payload)
        )
    except (ResearchError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({"error": str(error)}) if args.json else f"ERROR: {error}")
        return 2
    print(json.dumps(result, indent=2) if args.json else json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
