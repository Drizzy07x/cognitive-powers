#!/usr/bin/env python3
"""Assess normalized recurring-work evidence against current local skills."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


SKILL_LOCATIONS = (
    "skills",
    ".agents/skills",
    ".codex/skills",
    ".claude/skills",
    ".claude/commands",
)
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
WORD_PATTERN = re.compile(r"[a-z0-9]+")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "into",
    "of",
    "or",
    "project",
    "skill",
    "skills",
    "the",
    "to",
    "use",
    "when",
    "with",
}


class AuditError(ValueError):
    """Raised when an audit packet cannot be assessed honestly."""


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata


def inventory_skills(root: Path) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for location in SKILL_LOCATIONS:
        base = root / location
        if not base.is_dir():
            continue
        for skill_file in sorted(base.glob("*/SKILL.md")):
            metadata = _frontmatter(skill_file)
            name = metadata.get("name", skill_file.parent.name)
            found.setdefault(
                name,
                {
                    "name": name,
                    "description": metadata.get("description", ""),
                    "path": skill_file.relative_to(root).as_posix(),
                },
            )
    return sorted(found.values(), key=lambda item: item["name"])


def _terms(*values: str) -> set[str]:
    return {
        term
        for value in values
        for term in WORD_PATTERN.findall(value.lower().replace("-", " "))
        if len(term) > 2 and term not in STOP_WORDS
    }


def _similarity(candidate: dict[str, Any], skill: dict[str, str]) -> float:
    left = _terms(str(candidate["candidate_name"]), str(candidate["summary"]))
    right = _terms(skill["name"], skill["description"])
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _parse_day(raw: object, field: str) -> date:
    if not isinstance(raw, str):
        raise AuditError(f"{field} must be a YYYY-MM-DD string")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as error:
        raise AuditError(f"{field} must be a valid YYYY-MM-DD date") from error


def _string_list(candidate: dict[str, Any], field: str) -> list[str]:
    value = candidate.get(field)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise AuditError(
            f"{candidate.get('id', '<unknown>')}.{field} must be a string list"
        )
    return [item.strip() for item in value]


def _repository_paths(
    root: Path, candidate: dict[str, Any]
) -> tuple[list[str], list[str]]:
    existing: list[str] = []
    missing: list[str] = []
    for raw in _string_list(candidate, "repository_paths"):
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise AuditError(
                f"{candidate['id']}.repository_paths contains unsafe path {raw!r}"
            )
        normalized = relative.as_posix()
        (existing if (root / relative).exists() else missing).append(normalized)
    return existing, missing


def assess_pattern(
    root: Path,
    candidate: dict[str, Any],
    skills: list[dict[str, str]],
    *,
    as_of: date,
    stale_after_days: int,
) -> dict[str, Any]:
    identifier = candidate.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise AuditError("every pattern requires a non-empty id")
    summary = candidate.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise AuditError(f"{identifier}.summary must be non-empty")
    name = candidate.get("candidate_name")
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise AuditError(f"{identifier}.candidate_name must be lowercase hyphen-case")
    if "closest_skill" not in candidate:
        raise AuditError(
            f"{identifier}.closest_skill must be present, using null when none exists"
        )

    occurrences = candidate.get("occurrences")
    if not isinstance(occurrences, list):
        raise AuditError(f"{identifier}.occurrences must be a list")
    events: dict[str, date] = {}
    sources: set[str] = set()
    for index, occurrence in enumerate(occurrences):
        if not isinstance(occurrence, dict):
            raise AuditError(f"{identifier}.occurrences[{index}] must be an object")
        event_id = occurrence.get("event_id")
        source = occurrence.get("source")
        if not isinstance(event_id, str) or not event_id.strip():
            raise AuditError(
                f"{identifier}.occurrences[{index}].event_id must be non-empty"
            )
        if not isinstance(source, str) or not source.strip():
            raise AuditError(
                f"{identifier}.occurrences[{index}].source must be non-empty"
            )
        observed = _parse_day(
            occurrence.get("observed_at"),
            f"{identifier}.occurrences[{index}].observed_at",
        )
        if observed > as_of:
            raise AuditError(
                f"{identifier} contains future evidence dated {observed.isoformat()}"
            )
        events[event_id.strip()] = max(events.get(event_id.strip(), observed), observed)
        sources.add(source.strip())

    existing_paths, missing_paths = _repository_paths(root, candidate)
    triggers = _string_list(candidate, "triggers")
    workflow_steps = _string_list(candidate, "workflow_steps")
    validation_commands = _string_list(candidate, "validation_commands")
    latest = max(events.values()) if events else None
    recent = bool(latest and (as_of - latest).days <= stale_after_days)

    skill_by_name = {skill["name"]: skill for skill in skills}
    closest = candidate.get("closest_skill")
    if closest is not None and (
        not isinstance(closest, str) or closest not in skill_by_name
    ):
        raise AuditError(
            f"{identifier}.closest_skill does not name an existing local skill"
        )

    similarities = sorted(
        (
            {"name": skill["name"], "score": round(_similarity(candidate, skill), 4)}
            for skill in skills
        ),
        key=lambda item: (-item["score"], item["name"]),
    )
    likely_overlap = (
        similarities[0] if similarities and similarities[0]["score"] >= 0.24 else None
    )

    reasons: list[str] = []
    action: str
    if len(events) < 2:
        action = "reject"
        reasons.append("fewer than two distinct historical events")
    elif not recent and not existing_paths:
        action = "reject"
        reasons.append("evidence is stale and no declared repository path exists")
    elif closest is not None or name in skill_by_name:
        action = "update"
        closest = closest or name
        reasons.append(f"existing skill {closest} is the declared capability home")
    elif likely_overlap is not None:
        action = "review-overlap"
        reasons.append(
            f"possible overlap with {likely_overlap['name']} requires inspection"
        )
    else:
        action = "new"
        reasons.append("repeated current workflow has no detected local skill overlap")

    score = 0
    if action != "reject":
        score = min(len(events), 4) * 2
        score += min(len(existing_paths), 2) * 2
        score += min(len(validation_commands), 2)
        score += int(bool(triggers)) + int(bool(workflow_steps))
        score += int(action == "update")

    return {
        "id": identifier,
        "candidate_name": name,
        "action": action,
        "closest_skill": closest,
        "priority_score": score,
        "distinct_events": len(events),
        "distinct_sources": len(sources),
        "latest_observed_at": latest.isoformat() if latest else None,
        "current_repository_paths": existing_paths,
        "missing_repository_paths": missing_paths,
        "likely_overlap": likely_overlap,
        "reasons": reasons,
    }


def assess(
    root: Path,
    payload: dict[str, Any],
    *,
    as_of: date,
    stale_after_days: int = 365,
) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise AuditError("schema_version must be 1")
    patterns = payload.get("patterns")
    if not isinstance(patterns, list):
        raise AuditError("patterns must be a list")
    identifiers: set[str] = set()
    for pattern in patterns:
        if not isinstance(pattern, dict):
            raise AuditError("every pattern must be an object")
        identifier = pattern.get("id")
        if isinstance(identifier, str):
            if identifier in identifiers:
                raise AuditError("pattern ids must be unique")
            identifiers.add(identifier)
    skills = inventory_skills(root)
    results = [
        assess_pattern(
            root,
            pattern,
            skills,
            as_of=as_of,
            stale_after_days=stale_after_days,
        )
        for pattern in patterns
    ]
    results.sort(key=lambda item: (-item["priority_score"], item["id"]))
    return {
        "schema_version": 1,
        "kind": "capability_audit",
        "as_of": as_of.isoformat(),
        "stale_after_days": stale_after_days,
        "existing_skills": skills,
        "recommendations": results,
        "summary": {
            action: sum(item["action"] == action for item in results)
            for action in ("update", "new", "review-overlap", "reject")
        },
        "quality_improvement_proven": False,
    }


def _format(report: dict[str, Any]) -> str:
    lines = [f"Capability audit as of {report['as_of']}"]
    for item in report["recommendations"]:
        lines.append(
            f"{item['action'].upper()} {item['id']}: "
            f"events={item['distinct_events']} score={item['priority_score']}"
        )
        lines.extend(f"  {reason}" for reason in item["reasons"])
    lines.append("Quality improvement proven: no")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--root", type=Path, required=True)
    inventory.add_argument("--json", action="store_true")
    assess_parser = subparsers.add_parser("assess")
    assess_parser.add_argument("--root", type=Path, required=True)
    assess_parser.add_argument(
        "--evidence",
        required=True,
        help="UTF-8 JSON path, or - to read the audit packet from stdin",
    )
    assess_parser.add_argument("--as-of", default=date.today().isoformat())
    assess_parser.add_argument("--stale-after-days", type=int, default=365)
    assess_parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root.resolve()
    try:
        if not root.is_dir():
            raise AuditError(f"repository root does not exist: {root}")
        if args.command == "inventory":
            result: Any = inventory_skills(root)
            print(
                json.dumps(result, indent=2)
                if args.json
                else "\n".join(item["name"] for item in result)
            )
            return 0
        if args.stale_after_days < 1:
            raise AuditError("stale-after-days must be positive")
        raw_payload = (
            sys.stdin.read()
            if args.evidence == "-"
            else Path(args.evidence).read_text(encoding="utf-8")
        )
        payload = json.loads(raw_payload)
        report = assess(
            root,
            payload,
            as_of=_parse_day(args.as_of, "as-of"),
            stale_after_days=args.stale_after_days,
        )
        print(json.dumps(report, indent=2) if args.json else _format(report))
        return 0
    except (AuditError, json.JSONDecodeError, OSError) as error:
        print(
            json.dumps({"error": str(error)})
            if getattr(args, "json", False)
            else f"ERROR: {error}"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
