#!/usr/bin/env python3
"""Validate and render deterministic, version-neutral prompt contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = (
    "outcome",
    "success_criteria",
    "boundaries",
    "permissions",
    "tools",
    "evidence",
    "stop_conditions",
)
LIST_FIELDS = REQUIRED_FIELDS[1:]
OPTIONAL_FIELDS = ("target_model",)


class ContractError(ValueError):
    """Raised when a prompt contract is malformed or incomplete."""


def load_contract(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except OSError as error:
        raise ContractError(f"cannot read contract: {source}") from error
    except json.JSONDecodeError as error:
        raise ContractError(
            f"invalid JSON at line {error.lineno}, column {error.colno}"
        ) from error
    if not isinstance(value, dict):
        raise ContractError("contract must be a JSON object")
    return value


def _clean_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty string")
    return value.strip()


def _clean_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ContractError(f"{field} must be a non-empty list")
    cleaned = [
        _clean_text(item, f"{field}[{index}]") for index, item in enumerate(value)
    ]
    folded = [item.casefold() for item in cleaned]
    if len(folded) != len(set(folded)):
        raise ContractError(f"{field} must not contain duplicates")
    return cleaned


def validate_contract(value: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized contract or raise ContractError."""
    if not isinstance(value, dict):
        raise ContractError("contract must be a JSON object")
    missing = [field for field in REQUIRED_FIELDS if field not in value]
    unknown = sorted(set(value) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if missing:
        raise ContractError(f"missing required fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"unknown fields: {', '.join(unknown)}")

    normalized: dict[str, Any] = {"outcome": _clean_text(value["outcome"], "outcome")}
    for field in LIST_FIELDS:
        normalized[field] = _clean_list(value[field], field)
    if "target_model" in value:
        normalized["target_model"] = _clean_text(value["target_model"], "target_model")
    return normalized


def render_prompt(value: dict[str, Any]) -> str:
    contract = validate_contract(value)
    sections = [("Outcome", [contract["outcome"]])]
    labels = {
        "success_criteria": "Success criteria",
        "boundaries": "Boundaries",
        "permissions": "Permissions",
        "tools": "Tools",
        "evidence": "Required evidence",
        "stop_conditions": "Stop conditions",
    }
    sections.extend((labels[field], contract[field]) for field in LIST_FIELDS)
    if "target_model" in contract:
        sections.append(("Optional target model", [contract["target_model"]]))

    lines: list[str] = []
    for heading, items in sections:
        lines.append(f"## {heading}")
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "render"))
    parser.add_argument("contract")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        if args.command == "validate":
            print(
                json.dumps(
                    validate_contract(contract),
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
            )
        else:
            print(render_prompt(contract), end="")
    except (ContractError, UnicodeDecodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
