#!/usr/bin/env python3
"""Validate Cognitive Powers skill structure and local references."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Sequence


NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_PATTERN = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
MAX_SKILL_LINES = 500
PLACEHOLDER_MARKER = "[" + "TODO:"


def _frontmatter(text: str, relative: Path) -> tuple[dict[str, str], list[str]]:
    match = FRONTMATTER_PATTERN.match(text)
    if match is None:
        return {}, [f"{relative.as_posix()}: missing YAML frontmatter"]
    values: dict[str, str] = {}
    errors: list[str] = []
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            errors.append(f"{relative.as_posix()}: malformed frontmatter line: {line}")
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values, errors


def _local_link_target(skill_file: Path, raw_target: str) -> Path | None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return None
    path_text = target.split("#", 1)[0]
    if not path_text:
        return None
    return (skill_file.parent / path_text).resolve()


def _skill_roots(root: Path) -> list[Path]:
    roots = [root / "skills"]
    manifest_path = root / ".codex-plugin" / "plugin.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        declared = manifest.get("skills")
        if isinstance(declared, str):
            exposed = (root / declared).resolve()
            if exposed != roots[0].resolve() and root in exposed.parents:
                roots.append(exposed)
    return roots


def validate(plugin_root: Path) -> list[str]:
    root = plugin_root.resolve()
    errors: list[str] = []
    skill_files = sorted(
        skill_file
        for skills_root in _skill_roots(root)
        for skill_file in skills_root.glob("*/SKILL.md")
    )
    if not skill_files:
        return ["skills: no SKILL.md files found"]

    for skill_file in skill_files:
        relative = skill_file.relative_to(root)
        text = skill_file.read_text(encoding="utf-8")
        metadata, metadata_errors = _frontmatter(text, relative)
        errors.extend(metadata_errors)
        folder_name = skill_file.parent.name
        name = metadata.get("name", "")
        description = metadata.get("description", "")
        if name != folder_name:
            errors.append(
                f"{relative.as_posix()}: name {name!r} must match folder {folder_name!r}"
            )
        if not NAME_PATTERN.fullmatch(name):
            errors.append(f"{relative.as_posix()}: invalid skill name {name!r}")
        if not description or "TODO" in description:
            errors.append(
                f"{relative.as_posix()}: description is missing or unfinished"
            )
        if len(text.splitlines()) > MAX_SKILL_LINES:
            errors.append(f"{relative.as_posix()}: exceeds {MAX_SKILL_LINES} lines")

        for markdown in sorted(skill_file.parent.rglob("*.md")):
            markdown_text = markdown.read_text(encoding="utf-8")
            markdown_relative = markdown.relative_to(root).as_posix()
            if PLACEHOLDER_MARKER in markdown_text:
                errors.append(f"{markdown_relative}: contains scaffold placeholder")
            for raw_target in LINK_PATTERN.findall(markdown_text):
                target = _local_link_target(markdown, raw_target)
                if target is not None and not target.exists():
                    errors.append(
                        f"{markdown_relative}: broken local link {raw_target!r}"
                    )

        agent_file = skill_file.parent / "agents" / "openai.yaml"
        if not agent_file.is_file():
            errors.append(f"{relative.as_posix()}: missing agents/openai.yaml")
            continue
        agent_text = agent_file.read_text(encoding="utf-8")
        short_match = re.search(
            r'^\s*short_description:\s*["\'](.*)["\']\s*$',
            agent_text,
            re.MULTILINE,
        )
        prompt_match = re.search(
            r'^\s*default_prompt:\s*["\'](.*)["\']\s*$',
            agent_text,
            re.MULTILINE,
        )
        short_description = short_match.group(1) if short_match else ""
        if not 25 <= len(short_description) <= 64:
            errors.append(
                f"{agent_file.relative_to(root).as_posix()}: short_description must be 25-64 characters"
            )
        if prompt_match is None or f"${name}" not in prompt_match.group(1):
            errors.append(
                f"{agent_file.relative_to(root).as_posix()}: default_prompt must mention ${name}"
            )
    return errors


def quality_warnings(plugin_root: Path) -> list[str]:
    """Return semantic-maintenance warnings without pretending to grade behavior."""
    root = plugin_root.resolve()
    warnings: list[str] = []
    for skill_file in sorted(
        skill_file
        for skills_root in _skill_roots(root)
        for skill_file in skills_root.glob("*/SKILL.md")
    ):
        relative = skill_file.relative_to(root).as_posix()
        text = skill_file.read_text(encoding="utf-8")
        metadata, _ = _frontmatter(text, skill_file.relative_to(root))
        # Hosts show description and when_to_use as one listing entry, so the
        # trigger is decidable wherever it appears across the pair.
        listing = " ".join(
            part
            for part in (
                metadata.get("description", ""),
                metadata.get("when_to_use", ""),
            )
            if part
        )
        if "Use when" not in listing and "Use for" not in listing:
            warnings.append(
                f"{relative}: description has no decidable Use when/Use for trigger"
            )
        headings = re.findall(r"(?m)^##\s+", text)
        if len(headings) < 2:
            warnings.append(
                f"{relative}: workflow needs at least two explicit sections"
            )
        lowered = text.casefold()
        if not any(
            term in lowered
            for term in ("do not", "never", "only when", "without", "stop")
        ):
            warnings.append(f"{relative}: no explicit limit or stop boundary found")
        if not any(
            term in lowered
            for term in ("verify", "evidence", "acceptance", "completion", "test")
        ):
            warnings.append(f"{relative}: no verification or acceptance language found")
        if len(text.splitlines()) > 180 and not any(
            (skill_file.parent / "references").glob("*.md")
        ):
            warnings.append(
                f"{relative}: long skill has no progressive-disclosure references"
            )
    return warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="plugin root",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict-quality",
        action="store_true",
        help="treat semantic skill-quality warnings as validation failures",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate(args.root)
    warnings = quality_warnings(args.root)
    if args.strict_quality:
        errors.extend(warnings)
    payload = {"valid": not errors, "errors": errors, "warnings": warnings}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif errors:
        print("Skill validation failed:")
        for error in errors:
            print(f"- {error}")
    else:
        print("Skill validation passed")
        for warning in warnings:
            print(f"warning: {warning}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
