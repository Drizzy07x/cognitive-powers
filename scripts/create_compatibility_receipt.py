#!/usr/bin/env python3
"""Create a canonical CI compatibility receipt bound to a validation receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence


class ReceiptError(ValueError):
    pass


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def create_receipt(
    validation_path: Path,
    installation_path: Path,
    *,
    scenario_evidence_path: Path | None = None,
    os_name: str,
    python: str,
    codex_cli_version: str,
    scenario: str,
    commit: str,
    workflow: str,
    run_id: str,
    run_attempt: int,
) -> dict[str, Any]:
    raw = validation_path.read_bytes()
    validation = json.loads(raw)
    git = validation.get("git") if isinstance(validation, dict) else None
    if (
        not isinstance(validation, dict)
        or validation.get("kind") != "cognitive-powers-validation"
        or validation.get("passed") is not True
        or not isinstance(git, dict)
        or git.get("sha") != commit
        or git.get("dirty") is not False
        or git.get("identityStable") is not True
    ):
        raise ReceiptError(
            "validation receipt is not passing or bound to the CI commit"
        )
    if (
        len(commit) != 40
        or any(character not in "0123456789abcdef" for character in commit)
        or not run_id.isdigit()
        or run_attempt < 1
        or not codex_cli_version.strip()
    ):
        raise ReceiptError("CI identity is malformed")
    installation_raw = installation_path.read_bytes()
    installation = json.loads(installation_raw)
    if (
        not isinstance(installation, dict)
        or installation.get("schemaVersion") != 1
        or installation.get("product") != "cognitive-powers"
        or installation.get("commit") != commit
        or installation.get("tag") != "v1.6.0"
        or installation.get("matched") is not True
        or installation.get("readOnly") is not True
    ):
        raise ReceiptError("installation report is not a passing immutable install")
    scenario_evidence: dict[str, Any] | None = None
    if scenario != "clean-install":
        if scenario_evidence_path is None:
            raise ReceiptError("non-clean scenario requires scenario evidence")
        scenario_raw = scenario_evidence_path.read_bytes()
        scenario_document = json.loads(scenario_raw)
        scenarios = (
            scenario_document.get("scenarios")
            if isinstance(scenario_document, dict)
            else None
        )
        outcome = scenarios.get(scenario) if isinstance(scenarios, dict) else None
        if (
            not isinstance(scenario_document, dict)
            or scenario_document.get("schemaVersion") != 1
            or scenario_document.get("product") != "cognitive-powers"
            or scenario_document.get("candidateCommit") != commit
            or scenario_document.get("candidateTag") != "v1.6.0"
            or not isinstance(outcome, dict)
            or outcome.get("passed") is not True
            or not isinstance(outcome.get("finalTag"), str)
            or not isinstance(outcome.get("finalCommit"), str)
            or len(outcome["finalCommit"]) != 40
            or any(c not in "0123456789abcdef" for c in outcome["finalCommit"])
        ):
            raise ReceiptError("scenario evidence is malformed or not candidate-bound")
        scenario_evidence = {
            "reportSha256": _sha256(scenario_raw),
            "finalTag": outcome["finalTag"],
            "finalCommit": outcome["finalCommit"],
        }
    receipt: dict[str, Any] = {
        "schemaVersion": 2,
        "os": os_name,
        "python": python,
        "codexCli": codex_cli_version.strip().removeprefix("codex-cli "),
        "scenario": scenario,
        "passed": True,
        "identity": {
            "commit": commit,
            "workflow": workflow,
            "runId": run_id,
            "runAttempt": run_attempt,
            "codexCliVersion": codex_cli_version.strip(),
        },
        "installation": {
            "commit": commit,
            "tag": installation["tag"],
            "reportSha256": _sha256(installation_raw),
        },
        "attestation": {
            "kind": "github-actions-validation",
            "validationReceiptSha256": _sha256(raw),
            "verified": False,
        },
    }
    if scenario_evidence is not None:
        receipt["scenarioEvidence"] = scenario_evidence
    canonical = json.dumps(
        receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    receipt["receiptSha256"] = _sha256(canonical)
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--installation", type=Path, required=True)
    parser.add_argument("--scenario-evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--os", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--codex-cli-version", required=True)
    parser.add_argument("--scenario", default="clean-install")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = create_receipt(
            args.validation,
            args.installation,
            scenario_evidence_path=args.scenario_evidence,
            os_name=args.os,
            python=args.python,
            codex_cli_version=args.codex_cli_version,
            scenario=args.scenario,
            commit=args.commit,
            workflow=args.workflow,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
        )
        args.output.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError, ReceiptError) as error:
        print(json.dumps({"error": str(error)}))
        return 2
    print(
        json.dumps(
            {"output": str(args.output), "receiptSha256": receipt["receiptSha256"]}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
