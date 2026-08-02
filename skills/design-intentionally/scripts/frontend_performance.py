#!/usr/bin/env python3
"""Run a bounded, version-bound static audit of React and Next.js source."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable, NamedTuple, Sequence


SOURCE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}
IGNORED_PARTS = {
    ".git",
    ".next",
    ".turbo",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
}
HEAVY_CLIENT_PACKAGES = {
    "chart.js",
    "mapbox-gl",
    "monaco-editor",
    "three",
}
IMPORT_PATTERN = re.compile(r"(?m)^\s*import(?:[\s\S]*?from\s*)?[\"']([^\"']+)[\"']")


class FrontendAuditError(ValueError):
    pass


class Finding(NamedTuple):
    rule: str
    severity: str
    path: str
    line: int
    message: str


def _load_package_json(root: Path) -> dict[str, Any]:
    package_path = root / "package.json"
    if not package_path.is_file():
        raise FrontendAuditError(f"package.json not found: {package_path}")
    try:
        value = json.loads(package_path.read_text(encoding="utf-8"))
    # A manifest saved in UTF-16 or cp1252 raises UnicodeDecodeError, which is a
    # ValueError and not a JSONDecodeError, so it escaped as a traceback instead
    # of the documented error object.
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FrontendAuditError(f"cannot read package.json: {package_path}") from error
    if not isinstance(value, dict):
        raise FrontendAuditError("package.json must contain an object")
    return value


def _dependency_versions(package: dict[str, Any]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for field in ("dependencies", "devDependencies"):
        values = package.get(field, {})
        if not isinstance(values, dict):
            continue
        for name, version in values.items():
            if isinstance(name, str) and isinstance(version, str):
                versions.setdefault(name, version)
    return versions


def iter_source_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in SOURCE_SUFFIXES:
            continue
        if any(
            part.casefold() in IGNORED_PARTS for part in path.relative_to(root).parts
        ):
            continue
        yield path


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _is_root_layout(relative: Path) -> bool:
    parts = tuple(part.casefold() for part in relative.parts)
    return (
        len(parts) >= 2
        and parts[-2] == "app"
        and parts[-1]
        in {
            "layout.js",
            "layout.jsx",
            "layout.ts",
            "layout.tsx",
        }
    )


def _has_use_client_directive(text: str) -> bool:
    remaining = text.lstrip("\ufeff")
    while True:
        remaining = remaining.lstrip()
        if remaining.startswith("//"):
            newline = remaining.find("\n")
            if newline < 0:
                return False
            remaining = remaining[newline + 1 :]
            continue
        if remaining.startswith("/*"):
            end = remaining.find("*/", 2)
            if end < 0:
                return False
            remaining = remaining[end + 2 :]
            continue
        break
    return bool(re.match(r"[\"']use client[\"']\s*;?", remaining))


def _mask_javascript(text: str, *, mask_strings: bool) -> str:
    """Mask comments and optionally strings while preserving offsets and newlines."""
    characters = list(text)
    index = 0
    state = "code"
    quote = ""
    regex_character_class = False
    while index < len(characters):
        current = characters[index]
        following = characters[index + 1] if index + 1 < len(characters) else ""
        if state == "code":
            if current == "/" and following == "/":
                characters[index] = characters[index + 1] = " "
                state = "line-comment"
                index += 2
                continue
            if current == "/" and following == "*":
                characters[index] = characters[index + 1] = " "
                state = "block-comment"
                index += 2
                continue
            if current in {"'", '"', "`"}:
                quote = current
                state = "string"
                if mask_strings:
                    characters[index] = " "
                index += 1
                continue
            if current == "/" and mask_strings:
                prefix = text[:index].rstrip()
                previous = prefix[-1:] if prefix else ""
                previous_word_match = re.search(r"([A-Za-z_$][\w$]*)$", prefix)
                previous_word = (
                    previous_word_match.group(1) if previous_word_match else ""
                )
                if (
                    not prefix
                    or previous in "=(:,![{;?~"
                    or prefix.endswith("=>")
                    or previous_word
                    in {
                        "case",
                        "return",
                        "throw",
                        "yield",
                    }
                ):
                    characters[index] = " "
                    state = "regex"
                    regex_character_class = False
                    index += 1
                    continue
        elif state == "line-comment":
            if current == "\n":
                state = "code"
            else:
                characters[index] = " "
            index += 1
            continue
        elif state == "block-comment":
            if current == "*" and following == "/":
                characters[index] = characters[index + 1] = " "
                state = "code"
                index += 2
                continue
            if current != "\n":
                characters[index] = " "
            index += 1
            continue
        elif state == "string":
            if current == "\\":
                if mask_strings:
                    characters[index] = " "
                    if index + 1 < len(characters) and characters[index + 1] != "\n":
                        characters[index + 1] = " "
                index += 2
                continue
            if current == quote:
                if mask_strings:
                    characters[index] = " "
                state = "code"
                index += 1
                continue
            if mask_strings and current != "\n":
                characters[index] = " "
            index += 1
            continue
        elif state == "regex":
            if current == "\\":
                characters[index] = " "
                if index + 1 < len(characters) and characters[index + 1] != "\n":
                    characters[index + 1] = " "
                index += 2
                continue
            if current == "[":
                regex_character_class = True
            elif current == "]":
                regex_character_class = False
            elif current == "/" and not regex_character_class:
                characters[index] = " "
                state = "code"
                index += 1
                continue
            if current != "\n":
                characters[index] = " "
            index += 1
            continue
        index += 1
    return "".join(characters)


def audit(root: Path) -> dict[str, Any]:
    project_root = root.expanduser().resolve()
    if not project_root.is_dir():
        raise FrontendAuditError(f"project root is not a directory: {project_root}")
    dependencies = _dependency_versions(_load_package_json(project_root))
    frameworks = {
        name: dependencies[name]
        for name in ("next", "react", "react-dom")
        if name in dependencies
    }
    if "react" not in frameworks and "next" not in frameworks:
        raise FrontendAuditError("project does not declare React or Next.js")

    findings: list[Finding] = []
    scanned = 0
    for path in iter_source_files(project_root):
        scanned += 1
        relative = path.relative_to(project_root)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        is_client = _has_use_client_directive(text)
        visible_code = _mask_javascript(text, mask_strings=True)

        if "next" in frameworks and is_client and _is_root_layout(relative):
            findings.append(
                Finding(
                    "next-root-client-boundary",
                    "warning",
                    relative.as_posix(),
                    1,
                    "Root layout is a client component; verify that the client boundary cannot be moved lower.",
                )
            )

        if "next" in frameworks:
            for match in re.finditer(r"<img\b", visible_code, re.IGNORECASE):
                findings.append(
                    Finding(
                        "next-raw-img",
                        "advisory",
                        relative.as_posix(),
                        _line_number(text, match.start()),
                        "Raw img element found; verify whether next/image is appropriate for this asset.",
                    )
                )
            for match in re.finditer(r"<script\b", visible_code, re.IGNORECASE):
                findings.append(
                    Finding(
                        "next-raw-script",
                        "advisory",
                        relative.as_posix(),
                        _line_number(text, match.start()),
                        "Raw script element found; verify loading strategy and whether next/script is appropriate.",
                    )
                )

        if is_client:
            for match in IMPORT_PATTERN.finditer(text):
                keyword = re.search(r"\bimport\b", match.group(0))
                if keyword is None:
                    continue
                keyword_offset = match.start() + keyword.start()
                if visible_code[keyword_offset : keyword_offset + 6] != "import":
                    continue
                package = match.group(1)
                if package in HEAVY_CLIENT_PACKAGES:
                    findings.append(
                        Finding(
                            "heavy-client-import",
                            "advisory",
                            relative.as_posix(),
                            _line_number(text, match.start()),
                            f"Static client import of {package}; measure the bundle before deciding whether to split it.",
                        )
                    )

    ordered = sorted(findings, key=lambda item: (item.path, item.line, item.rule))
    return {
        "type": "frontend_performance_audit",
        "schemaVersion": 1,
        "projectRoot": str(project_root),
        "frameworkVersions": frameworks,
        "scannedFiles": scanned,
        "findings": [item._asdict() for item in ordered],
        "summary": {
            "warning": sum(item.severity == "warning" for item in ordered),
            "advisory": sum(item.severity == "advisory" for item in ordered),
        },
        "measuredRuntimePerformance": False,
        "optimizationProven": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--fail-on-warning", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = audit(args.root)
    except FrontendAuditError as error:
        print(json.dumps({"type": "frontend_performance_audit", "error": str(error)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.fail_on_warning and result["summary"]["warning"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
