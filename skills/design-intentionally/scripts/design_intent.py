#!/usr/bin/env python3
"""Normalize a design brief into bounded, hashable interface intent."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PAGE_KINDS = {
    "marketing": (7, 6, 4),
    "product": (5, 4, 7),
    "dashboard": (4, 3, 9),
    "portfolio": (8, 6, 3),
    "commerce": (6, 4, 6),
    "editorial": (6, 3, 4),
    "public-service": (3, 2, 6),
}
MODES = {"greenfield", "preserve", "overhaul"}


class IntentError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_output_path(
    output: Path, project_root: Path, allow_workspace_output: bool
) -> None:
    if is_within(output, project_root) and not allow_workspace_output:
        raise IntentError(
            "design intent output must stay outside the project unless --allow-workspace-output is explicit"
        )


def load_object(path: str | Path) -> tuple[Path, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IntentError(f"cannot read JSON object: {source}") from error
    if not isinstance(value, dict):
        raise IntentError("design brief must be a JSON object")
    return source, value


def _strings(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise IntentError(f"{field} must be a list of non-empty strings")
    return [item.strip() for item in value]


def _references(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise IntentError("references must be a list")
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise IntentError("each reference must be an object")
        kind, reference_value, note = (
            item.get("kind"),
            item.get("value"),
            item.get("note"),
        )
        if not all(
            isinstance(part, str) and part.strip()
            for part in (kind, reference_value, note)
        ):
            raise IntentError("each reference requires non-empty kind, value, and note")
        normalized.append(
            {
                "kind": kind.strip(),
                "value": reference_value.strip(),
                "note": note.strip(),
            }
        )
    return normalized


def _clamp(value: int) -> int:
    return max(1, min(10, value))


def infer_dials(
    page_kind: str | None, direction: list[str], overrides: Any
) -> dict[str, int]:
    variance, motion, density = PAGE_KINDS.get(page_kind or "", (5, 4, 5))
    signal = " ".join(direction).casefold()
    if any(
        word in signal for word in ("experimental", "playful", "expressive", "kinetic")
    ):
        variance, motion, density = variance + 2, motion + 2, density - 1
    if any(word in signal for word in ("minimal", "calm", "restrained", "quiet")):
        variance, motion, density = variance - 1, motion - 2, density - 1
    if any(word in signal for word in ("premium", "luxury", "cinematic")):
        variance, motion, density = variance + 1, motion + 1, density - 1
    if any(word in signal for word in ("enterprise", "technical", "operational")):
        variance, motion, density = variance - 1, motion - 1, density + 1
    if any(word in signal for word in ("accessible", "trust", "regulated")):
        variance, motion = variance - 2, motion - 2
    values = {
        "variance": _clamp(variance),
        "motion": _clamp(motion),
        "density": _clamp(density),
    }
    if overrides is None:
        return values
    if not isinstance(overrides, dict):
        raise IntentError("dials must be an object")
    for key in values:
        if key not in overrides:
            continue
        value = overrides[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 10
        ):
            raise IntentError(f"dials.{key} must be an integer from 1 to 10")
        values[key] = value
    return values


def normalize_brief(brief: dict[str, Any], source: Path) -> dict[str, Any]:
    project_value = brief.get("project_root")
    if not isinstance(project_value, str) or not project_value.strip():
        raise IntentError("project_root must be a non-empty path")
    project_root = Path(project_value).expanduser().resolve()
    if not project_root.is_dir():
        raise IntentError(f"project_root is not a directory: {project_root}")
    page_kind = brief.get("page_kind")
    if page_kind is not None and page_kind not in PAGE_KINDS:
        raise IntentError(f"unsupported page_kind: {page_kind}")
    mode = brief.get("mode", "greenfield")
    if mode not in MODES:
        raise IntentError(f"unsupported mode: {mode}")
    audience = brief.get("audience")
    if audience is not None and (not isinstance(audience, str) or not audience.strip()):
        raise IntentError("audience must be a non-empty string")
    direction = _strings(brief.get("direction"), "direction")
    preserve = _strings(brief.get("preserve"), "preserve")
    unresolved: list[str] = []
    if page_kind is None:
        unresolved.append("page_kind")
    if not audience:
        unresolved.append("audience")
    if not direction:
        unresolved.append("direction")
    if mode in {"preserve", "overhaul"} and not preserve:
        unresolved.append("preserve")
    intent: dict[str, Any] = {
        "type": "design_intent",
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "projectRoot": str(project_root),
        "pageKind": page_kind,
        "mode": mode,
        "audience": audience.strip() if isinstance(audience, str) else None,
        "direction": direction,
        "avoid": _strings(brief.get("avoid"), "avoid"),
        "brandConstraints": _strings(
            brief.get("brand_constraints"), "brand_constraints"
        ),
        "contentConstraints": _strings(
            brief.get("content_constraints"), "content_constraints"
        ),
        "preserve": preserve,
        "references": _references(brief.get("references")),
        "dials": infer_dials(page_kind, direction, brief.get("dials")),
        "existingSystem": brief.get("existing_system")
        if isinstance(brief.get("existing_system"), str)
        and brief["existing_system"].strip()
        else None,
        "foundationDecision": "reuse-existing"
        if isinstance(brief.get("existing_system"), str)
        and brief["existing_system"].strip()
        else "select-after-dependency-audit",
        "unresolvedChoices": unresolved,
        "readyToImplement": not unresolved,
        "sourceBrief": str(source),
        "sourceBriefSha256": sha256_file(source),
    }
    intent["intentSha256"] = sha256_bytes(canonical_json(intent).encode("utf-8"))
    return intent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--brief", required=True)
    create_parser.add_argument("--output")
    create_parser.add_argument("--allow-workspace-output", action="store_true")
    args = parser.parse_args()
    try:
        source, brief = load_object(args.brief)
        intent = normalize_brief(brief, source)
        if args.output:
            output = Path(args.output).expanduser().resolve()
            validate_output_path(
                output, Path(intent["projectRoot"]), args.allow_workspace_output
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(intent, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(intent, ensure_ascii=False, indent=2))
        return 0 if intent["readyToImplement"] else 1
    except (IntentError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
