#!/usr/bin/env python3
"""Compile a strict Markdown work-packet plan into plan-packets JSON."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
PACKET_HEADING = re.compile(
    r"^##\s+(?:Packet\s*(?::\s*|\s+))?(?:`([^`]+)`|([^`]+?))\s*$",
    re.IGNORECASE,
)
SECTION_HEADING = re.compile(r"^###\s+(.+?)\s*$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")
SECTION_ALIASES = {
    "objective": "objective",
    "owned paths": "owned_paths",
    "owned_paths": "owned_paths",
    "dependencies": "dependencies",
    "depends on": "dependencies",
    "depends_on": "dependencies",
    "invariants": "invariants",
    "criteria": "invariants",
    "acceptance criteria": "invariants",
    "checks": "checks",
    "integration notes": "integration_notes",
    "integration_notes": "integration_notes",
}
LIST_SECTIONS = {
    "owned_paths",
    "dependencies",
    "invariants",
    "checks",
    "integration_notes",
}
REQUIRED_SECTIONS = {"objective", *LIST_SECTIONS}


class PlanCompilerError(ValueError):
    """Raised when Markdown cannot safely become a work-packet plan."""


def _stable_identifier(value: str, label: str) -> str:
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > 80
        or not IDENTIFIER.fullmatch(candidate)
        or candidate.startswith("-")
        or candidate.endswith("-")
    ):
        raise PlanCompilerError(
            f"{label} must be a stable identifier containing at most 80 letters, "
            "digits, dots, underscores, or hyphens"
        )
    return candidate


def _normalize_owned_path(value: str, packet_id: str) -> str:
    raw = value.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise PlanCompilerError(
            f"packet {packet_id} owned path must be workspace-relative: {value!r}"
        )
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise PlanCompilerError(
            f"packet {packet_id} owned path must not traverse the workspace: {value!r}"
        )
    return path.as_posix()


def _paths_overlap(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts
    if os.name == "nt":
        left_parts = tuple(part.casefold() for part in left_parts)
        right_parts = tuple(part.casefold() for part in right_parts)
    shortest = min(len(left_parts), len(right_parts))
    return left_parts[:shortest] == right_parts[:shortest]


def _unquote_markdown(value: str) -> str:
    result = value.strip()
    if len(result) >= 2 and result.startswith("`") and result.endswith("`"):
        result = result[1:-1].strip()
    return result


def _parse_list(
    lines: list[tuple[int, str]], section: str, packet_id: str
) -> list[str]:
    values: list[str] = []
    for line_number, line in lines:
        match = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if not match:
            raise PlanCompilerError(
                f"line {line_number}: packet {packet_id} section {section} "
                "must contain Markdown bullets"
            )
        value = _unquote_markdown(match.group(1))
        if not value:
            raise PlanCompilerError(
                f"line {line_number}: packet {packet_id} section {section} has an empty item"
            )
        values.append(value)
    if not values:
        raise PlanCompilerError(
            f"packet {packet_id} section {section} must not be empty"
        )
    if len(values) != len(set(values)):
        raise PlanCompilerError(
            f"packet {packet_id} section {section} has duplicate items"
        )
    return values


def _build_packet(
    packet_id: str,
    sections: dict[str, list[tuple[int, str]]],
) -> dict[str, Any]:
    missing = sorted(REQUIRED_SECTIONS - sections.keys())
    if missing:
        raise PlanCompilerError(
            f"packet {packet_id} is missing required sections: {', '.join(missing)}"
        )

    objective_lines = sections["objective"]
    if any(re.match(r"^\s*[-*]\s+", line) for _, line in objective_lines):
        raise PlanCompilerError(
            f"packet {packet_id} objective must be prose, not a list"
        )
    objective = " ".join(line.strip() for _, line in objective_lines).strip()
    if not objective:
        raise PlanCompilerError(f"packet {packet_id} objective must not be empty")

    owned_paths = [
        _normalize_owned_path(value, packet_id)
        for value in _parse_list(sections["owned_paths"], "owned_paths", packet_id)
    ]
    if len(owned_paths) != len(set(owned_paths)):
        raise PlanCompilerError(f"packet {packet_id} owned_paths has duplicate items")
    for index, left in enumerate(owned_paths):
        for right in owned_paths[index + 1 :]:
            if _paths_overlap(left, right):
                raise PlanCompilerError(
                    f"packet {packet_id} has overlapping owned paths: {left} and {right}"
                )

    dependencies = _parse_list(sections["dependencies"], "dependencies", packet_id)
    if len(dependencies) == 1 and dependencies[0].casefold() in {
        "none",
        "ninguna",
        "ninguno",
    }:
        dependencies = []
    elif any(
        value.casefold() in {"none", "ninguna", "ninguno"} for value in dependencies
    ):
        raise PlanCompilerError(
            f"packet {packet_id} dependencies cannot mix 'none' with packet ids"
        )
    dependencies = [
        _stable_identifier(value, f"packet {packet_id} dependency")
        for value in dependencies
    ]

    invariants = _parse_list(sections["invariants"], "invariants", packet_id)
    integration_notes = _parse_list(
        sections["integration_notes"], "integration_notes", packet_id
    )

    raw_checks = _parse_list(sections["checks"], "checks", packet_id)
    checks: list[list[str]] = []
    for check_index, raw_check in enumerate(raw_checks, 1):
        try:
            argv = json.loads(raw_check)
        except json.JSONDecodeError as error:
            raise PlanCompilerError(
                f"packet {packet_id} check {check_index} must be a JSON argv array: {error.msg}"
            ) from error
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(argument, str) and argument for argument in argv)
        ):
            raise PlanCompilerError(
                f"packet {packet_id} check {check_index} must be a non-empty JSON argv array "
                "of non-empty strings"
            )
        checks.append(argv)

    return {
        "id": packet_id,
        "objective": objective,
        "owned_paths": owned_paths,
        "dependencies": dependencies,
        "invariants": invariants,
        "checks": checks,
        "integration_notes": integration_notes,
    }


def _validate_graph(packets: list[dict[str, Any]]) -> None:
    by_id: dict[str, dict[str, Any]] = {}
    for packet in packets:
        packet_id = packet["id"]
        if packet_id in by_id:
            raise PlanCompilerError(f"duplicate packet id: {packet_id}")
        by_id[packet_id] = packet

    for packet in packets:
        for dependency in packet["dependencies"]:
            if dependency not in by_id:
                raise PlanCompilerError(
                    f"packet {packet['id']} has unknown dependency {dependency}"
                )
            if dependency == packet["id"]:
                raise PlanCompilerError(
                    f"packet {packet['id']} cannot depend on itself"
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(packet_id: str) -> None:
        if packet_id in visiting:
            raise PlanCompilerError(
                f"packet plan contains a dependency cycle at {packet_id}"
            )
        if packet_id in visited:
            return
        visiting.add(packet_id)
        for dependency in by_id[packet_id]["dependencies"]:
            visit(dependency)
        visiting.remove(packet_id)
        visited.add(packet_id)

    for packet_id in by_id:
        visit(packet_id)

    for left_index, left in enumerate(packets):
        for right in packets[left_index + 1 :]:
            for left_path in left["owned_paths"]:
                for right_path in right["owned_paths"]:
                    if _paths_overlap(left_path, right_path):
                        raise PlanCompilerError(
                            f"packets {left['id']} and {right['id']} overlap ownership: "
                            f"{left_path} and {right_path}"
                        )


def compile_markdown(markdown: str) -> dict[str, Any]:
    """Compile strict packet Markdown and return a validated schema-version-1 plan."""
    packets: list[dict[str, Any]] = []
    current_id: str | None = None
    current_section: str | None = None
    sections: dict[str, list[tuple[int, str]]] = {}

    def finish_packet() -> None:
        nonlocal current_id, current_section, sections
        if current_id is not None:
            packets.append(_build_packet(current_id, sections))
        current_id = None
        current_section = None
        sections = {}

    for line_number, raw_line in enumerate(markdown.splitlines(), 1):
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("# ") and current_id is None and not packets:
            continue
        packet_match = PACKET_HEADING.match(line)
        if packet_match:
            finish_packet()
            current_id = _stable_identifier(
                packet_match.group(1) or packet_match.group(2),
                f"line {line_number} packet id",
            )
            continue
        section_match = SECTION_HEADING.match(line)
        if section_match:
            if current_id is None:
                raise PlanCompilerError(
                    f"line {line_number}: section appears before the first packet"
                )
            section_name = re.sub(
                r"\s+", " ", section_match.group(1).strip().casefold()
            )
            canonical = SECTION_ALIASES.get(section_name)
            if canonical is None:
                raise PlanCompilerError(
                    f"line {line_number}: unknown packet section {section_match.group(1)!r}"
                )
            if canonical in sections:
                raise PlanCompilerError(
                    f"line {line_number}: packet {current_id} repeats section {canonical}"
                )
            sections[canonical] = []
            current_section = canonical
            continue
        if current_id is None:
            raise PlanCompilerError(
                f"line {line_number}: content appears before the first packet heading"
            )
        if current_section is None:
            raise PlanCompilerError(
                f"line {line_number}: packet {current_id} content appears before a section"
            )
        sections[current_section].append((line_number, line))

    finish_packet()
    if not packets:
        raise PlanCompilerError("plan must contain at least one packet")
    _validate_graph(packets)
    return {"schema_version": SCHEMA_VERSION, "packets": packets}


def _read_markdown(value: str) -> str:
    return sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8")


def _atomic_write(path: Path, content: str) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, target)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("compile",), default="compile")
    parser.add_argument("--input", required=True, help="Markdown path or - for stdin")
    parser.add_argument("--output", help="JSON output path; stdout when omitted or -")
    parser.add_argument("--json", action="store_true", help="render errors as JSON")
    args = parser.parse_args()
    try:
        payload = compile_markdown(_read_markdown(args.input))
        rendered = json.dumps(payload, indent=2) + "\n"
        if args.output and args.output != "-":
            _atomic_write(Path(args.output), rendered)
        else:
            sys.stdout.write(rendered)
    except (PlanCompilerError, OSError, UnicodeError) as error:
        message = json.dumps({"error": str(error)}) if args.json else f"ERROR: {error}"
        print(message)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
