#!/usr/bin/env python3
"""Select communication detail and validate evidence-preserving output receipts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


KINDS = {"progress", "handoff", "answer", "diagnosis", "decision", "warning"}
# A host placeholder rather than a model identifier, e.g. "<synthetic>".
HOST_GENERATED_MODEL = re.compile(r"<[^>]*>")
COMPLEXITIES = {"low", "medium", "high"}
CONSEQUENCES = {"reversible", "material", "irreversible"}


class ContractError(ValueError):
    pass


def load_object(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read JSON object: {source}") from error
    if not isinstance(value, dict):
        raise ContractError("JSON input must be an object")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_profile(signals: dict[str, Any]) -> dict[str, Any]:
    kind = signals.get("kind")
    complexity = signals.get("complexity")
    consequence = signals.get("consequence")
    unresolved = signals.get("unresolved", False)
    evidence_count = signals.get("evidence_count", 0)
    if kind not in KINDS:
        raise ContractError(f"unsupported kind: {kind}")
    if complexity not in COMPLEXITIES:
        raise ContractError(f"unsupported complexity: {complexity}")
    if consequence not in CONSEQUENCES:
        raise ContractError(f"unsupported consequence: {consequence}")
    if not isinstance(unresolved, bool):
        raise ContractError("unresolved must be boolean")
    if (
        isinstance(evidence_count, bool)
        or not isinstance(evidence_count, int)
        or evidence_count < 0
    ):
        raise ContractError("evidence_count must be a non-negative integer")

    if consequence == "irreversible" or kind == "warning":
        profile = "explicit"
        reason = "consequential wording requires complete scope and ordering"
    elif (
        complexity == "high"
        or kind in {"diagnosis", "decision"}
        or unresolved
        or evidence_count >= 3
    ):
        profile = "normal"
        reason = "causal context, limitations, or multiple evidence items must remain visible"
    else:
        profile = "compact"
        reason = "routine low-risk communication can be reduced to outcome and decisive evidence"
    return {"profile": profile, "reason": reason, "signals": signals}


def _string_list(case: dict[str, Any], field: str) -> list[str]:
    value = case.get(field, [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ContractError(f"{field} must be a list of non-empty strings")
    return value


def assess_output(case: dict[str, Any], output: str) -> dict[str, Any]:
    if not isinstance(output, str) or not output.strip():
        raise ContractError("output must not be empty")
    required = _string_list(case, "required_facts")
    literals = _string_list(case, "exact_literals")
    filler = _string_list(case, "forbidden_filler")
    max_words = case.get("max_words")
    if isinstance(max_words, bool) or not isinstance(max_words, int) or max_words <= 0:
        raise ContractError("max_words must be a positive integer")

    folded_output = output.casefold()
    missing_facts = [item for item in required if item.casefold() not in folded_output]
    altered_literals = [item for item in literals if item not in output]
    filler_hits = [item for item in filler if item.casefold() in output.casefold()]
    word_count = len(re.findall(r"\S+", output))
    result = {
        "id": case.get("id"),
        "profile": case.get("expected_profile"),
        "wordCount": word_count,
        "maxWords": max_words,
        "missingFacts": missing_facts,
        "alteredLiterals": altered_literals,
        "fillerHits": filler_hits,
        "integrityPassed": not missing_facts and not altered_literals,
        "budgetPassed": word_count <= max_words,
        "fillerPassed": not filler_hits,
    }
    result["passed"] = all(
        result[key] for key in ("integrityPassed", "budgetPassed", "fillerPassed")
    )
    return result


def _usage_int(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"usage.{key} must be a non-negative integer")
    return value


def _provider_usage():
    """Load the shared conversion from the plugin root."""
    path = Path(__file__).resolve().parents[3] / "scripts" / "provider_usage.py"
    spec = importlib.util.spec_from_file_location("cp_provider_usage", path)
    if spec is None or spec.loader is None:
        raise ContractError(f"cannot load the shared usage conversion: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError) as error:
        raise ContractError(
            f"cannot load the shared usage conversion: {path}"
        ) from error
    return module


def normalize_usage(usage: dict[str, Any]) -> tuple[int, int, int, str]:
    """Return ``(total_input, cached, output, schema)`` from either shape.

    Delegates to the single conversion the durable recorder also uses, so the
    two can never read different sets of provider shapes.
    """
    module = _provider_usage()
    try:
        return module.normalize_usage(usage)
    except module.UsageError as error:
        raise ContractError(str(error)) from error


def create_receipt(
    source_path: str | Path,
    *,
    task_id: str,
    variant: str,
    success: bool,
    quality_score: float,
    critical_failure: bool,
) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    payload = load_object(source)
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise ContractError("provider record must contain a usage object")
    input_tokens, cached_tokens, output_tokens, usage_schema = normalize_usage(usage)
    provider = payload.get("provider")
    model = payload.get("model")
    if (
        not isinstance(provider, str)
        or not provider
        or not isinstance(model, str)
        or not model
    ):
        raise ContractError("provider record must identify provider and model")
    if not task_id.strip() or not variant.strip():
        raise ContractError("task_id and variant must not be empty")
    if not isinstance(quality_score, (int, float)) or isinstance(quality_score, bool):
        raise ContractError("quality_score must be numeric")
    if not 0 <= float(quality_score) <= 100:
        raise ContractError("quality_score must be between 0 and 100")
    return {
        "type": "communication_usage_evidence",
        "schemaVersion": 1,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "taskId": task_id,
        "variant": variant,
        "provider": provider,
        "model": model,
        "success": success,
        "qualityScore": float(quality_score),
        "criticalFailure": critical_failure,
        "usage": {
            "inputTokens": input_tokens,
            "cachedInputTokens": cached_tokens,
            "freshInputTokens": input_tokens - cached_tokens,
            "outputTokens": output_tokens,
            "totalTokens": input_tokens + output_tokens,
            # Two providers count a cached prompt differently, so a comparison
            # across schemas is not like for like.
            "sourceSchema": usage_schema,
        },
        "providerRecord": str(source),
        "providerRecordSha256": sha256_file(source),
        "counterfactualEstimated": False,
    }


def usage_from_transcript(
    transcript_path: str | Path, *, session_id: str | None = None
) -> dict[str, Any]:
    """Build a provider record from a Claude Code transcript.

    The host writes several rows per assistant message, one per content block,
    and repeats the identical usage object on each. Summing rows would multiply
    a single message's cost, so usage is taken once per ``message.id``. The
    nested ``iterations`` array restates the same figures and is ignored for
    the same reason.

    This reads the host's on-disk transcript, which is not a published
    interface. A row that does not carry the expected shape is skipped rather
    than guessed at, and the returned record reports how many messages were
    counted so a caller can see when the answer rests on nothing.
    """
    path = Path(transcript_path).expanduser().resolve()
    if not path.is_file():
        raise ContractError(f"transcript is not a file: {path}")

    totals = {
        "input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": 0,
    }
    seen: set[str] = set()
    models: list[str] = []
    versions: list[str] = []
    skipped = 0
    unexpected = 0
    host_generated = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(row, dict) or row.get("type") != "assistant":
                continue
            if session_id and row.get("sessionId") != session_id:
                continue
            message = row.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            identifier = message.get("id")
            if not isinstance(usage, dict) or not isinstance(identifier, str):
                continue
            if identifier in seen:
                continue
            model = message.get("model")
            if isinstance(model, str) and HOST_GENERATED_MODEL.fullmatch(model):
                # The host writes its own assistant rows with a placeholder
                # model such as "<synthetic>". Their tokens belong to no model
                # this record can name, so counting them would misattribute
                # usage and listing them would make every real transcript fail
                # the single-model check.
                host_generated += 1
                continue
            # Every field must be readable. A partially recognised row would
            # silently undercount, which reads as a real efficiency result.
            if any(
                isinstance(usage.get(key), bool)
                or not isinstance(usage.get(key), int)
                or usage[key] < 0
                for key in totals
            ):
                unexpected += 1
                continue
            seen.add(identifier)
            for key in totals:
                totals[key] += usage[key]
            model = message.get("model")
            if isinstance(model, str) and model and model not in models:
                models.append(model)
            version = row.get("version")
            if isinstance(version, str) and version and version not in versions:
                versions.append(version)

    if unexpected:
        raise ContractError(
            f"{unexpected} assistant message(s) carry a usage shape this "
            "producer does not recognise; the host transcript format is not a "
            "published interface and appears to have changed"
        )
    if not seen:
        raise ContractError(f"transcript contains no assistant usage: {path}")
    if len(models) != 1:
        raise ContractError(
            "transcript must cover exactly one model to be a usage record, "
            f"found {models or 'none'}"
        )
    return {
        "provider": "anthropic",
        "model": models[0],
        "usage": dict(totals),
        "source": str(path),
        "messageCount": len(seen),
        "unparsableLines": skipped,
        # Host-written rows carry real tokens this record cannot attribute to a
        # model. Reporting the count keeps the omission visible.
        "hostGeneratedRowsExcluded": host_generated,
        # Pin the host that wrote this shape. When the format later changes,
        # the recorded version says which build the numbers came from instead
        # of leaving a silent discrepancy to explain.
        "hostVersions": sorted(versions),
    }


def compare_receipts(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    if (
        baseline.get("type") != "communication_usage_evidence"
        or candidate.get("type") != "communication_usage_evidence"
    ):
        raise ContractError("both inputs must be communication usage receipts")
    if baseline.get("taskId") != candidate.get("taskId"):
        raise ContractError("receipt task IDs do not match")
    for label, receipt in (("baseline", baseline), ("candidate", candidate)):
        if not isinstance(receipt.get("usage"), dict):
            # Reached before the numeric loop, so an unreadable receipt would
            # otherwise raise AttributeError, which the CLI handler does not
            # catch and which used to surface as a clean error.
            raise ContractError(f"{label} receipt has no usage object")
    # A provider that excludes the cached prefix from input_tokens and one that
    # includes it do not produce comparable totals, so a cross-schema delta
    # would read as an efficiency result that no measurement supports.
    schemas = {
        baseline["usage"].get("sourceSchema", "codex"),
        candidate["usage"].get("sourceSchema", "codex"),
    }
    if len(schemas) > 1:
        raise ContractError(
            "receipts come from different provider usage schemas and count a "
            f"cached prompt differently: {sorted(schemas)}"
        )
    eligible = (
        baseline.get("success") is True
        and candidate.get("success") is True
        and baseline.get("criticalFailure") is False
        and candidate.get("criticalFailure") is False
        and float(candidate.get("qualityScore", -1))
        >= float(baseline.get("qualityScore", 101))
    )
    metrics: dict[str, Any] = {}
    for key in ("inputTokens", "freshInputTokens", "outputTokens", "totalTokens"):
        base_value = baseline["usage"][key]
        candidate_value = candidate["usage"][key]
        metrics[key] = {
            "baseline": base_value,
            "candidate": candidate_value,
            "delta": candidate_value - base_value,
            "reductionPercent": round(
                (base_value - candidate_value) * 100 / base_value, 2
            )
            if base_value
            else None,
        }
    return {
        "taskId": baseline["taskId"],
        "eligibleForEfficiencyClaim": eligible,
        "qualityDelta": round(
            float(candidate["qualityScore"]) - float(baseline["qualityScore"]), 2
        ),
        "metrics": metrics,
        "counterfactualEstimated": False,
    }


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def parse_bool(value: str) -> bool:
    lowered = value.casefold()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    select_parser = subparsers.add_parser("select")
    select_parser.add_argument("--input", required=True)
    assess_parser = subparsers.add_parser("assess")
    assess_parser.add_argument("--case", required=True)
    text_group = assess_parser.add_mutually_exclusive_group(required=True)
    text_group.add_argument("--text")
    text_group.add_argument("--text-file")
    receipt_parser = subparsers.add_parser("receipt")
    receipt_parser.add_argument("--source", required=True)
    receipt_parser.add_argument("--output", required=True)
    receipt_parser.add_argument("--task-id", required=True)
    receipt_parser.add_argument("--variant", required=True)
    receipt_parser.add_argument("--success", required=True, type=parse_bool)
    receipt_parser.add_argument("--quality-score", required=True, type=float)
    receipt_parser.add_argument("--critical-failure", default=False, type=parse_bool)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--baseline", required=True)
    compare_parser.add_argument("--candidate", required=True)
    transcript_parser = subparsers.add_parser("usage-from-transcript")
    transcript_parser.add_argument("--transcript", required=True)
    transcript_parser.add_argument("--session-id")
    transcript_parser.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.command == "select":
            result = select_profile(load_object(args.input))
        elif args.command == "assess":
            output = (
                args.text
                if args.text is not None
                else Path(args.text_file).read_text(encoding="utf-8")
            )
            result = assess_output(load_object(args.case), output)
        elif args.command == "receipt":
            result = create_receipt(
                args.source,
                task_id=args.task_id,
                variant=args.variant,
                success=args.success,
                quality_score=args.quality_score,
                critical_failure=args.critical_failure,
            )
            write_json(args.output, result)
        elif args.command == "usage-from-transcript":
            result = usage_from_transcript(args.transcript, session_id=args.session_id)
            if args.output:
                write_json(args.output, result)
        else:
            result = compare_receipts(
                load_object(args.baseline), load_object(args.candidate)
            )
    except (ContractError, OSError, KeyError, TypeError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
