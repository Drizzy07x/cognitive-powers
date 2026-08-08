#!/usr/bin/env python3
"""Rank compact, query-relevant context from a source tree."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "bin",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "obj",
        "blob-report",
        "playwright-report",
        "target",
        "test-results",
        "vendor",
        "venv",
    }
)

IGNORED_SUFFIXES = frozenset(
    {
        ".7z",
        ".avi",
        ".bmp",
        ".class",
        ".dll",
        ".dylib",
        ".exe",
        ".gif",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp3",
        ".mp4",
        ".o",
        ".obj",
        ".pdf",
        ".png",
        ".pyc",
        ".so",
        ".tar",
        ".ttf",
        ".wav",
        ".webp",
        ".woff",
        ".woff2",
        ".zip",
    }
)

WORD_RE = re.compile(r"[A-Za-z0-9_+#.-]+")

CODE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".css",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".php",
        ".ps1",
        ".py",
        ".rb",
        ".rs",
        ".sh",
        ".sql",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
    }
)

BOUNDARY_NAMES = frozenset(
    {
        "__init__.py",
        "Cargo.toml",
        "CMakeLists.txt",
        "go.mod",
        "index.js",
        "index.ts",
        "Makefile",
        "package.json",
        "pom.xml",
        "pyproject.toml",
        "tsconfig.json",
    }
)

CONFIG_NAMES = frozenset(
    {
        ".editorconfig",
        ".eslintrc",
        ".prettierrc",
        "biome.json",
        "eslint.config.js",
        "eslint.config.mjs",
        "jest.config.js",
        "pytest.ini",
        "ruff.toml",
        "vitest.config.ts",
    }
)


@dataclass(frozen=True)
class TextFile:
    path: str
    text: str


def _stopwords() -> frozenset[str]:
    """The routing module's judgement about which words carry no evidence.

    Only the list is shared, not the tokenizer: this scorer searches paths and
    raw source, where "work_state.py" is one useful term and a stem like
    "verifi" would not be. What it has no business deciding for itself is which
    words mean nothing, and deciding that separately cost what a split costs.
    Measured on this repository, "how does the durable ledger verify a receipt"
    returned exactly one file -- CHANGELOG.md -- which matched all eight terms
    including "how", "does", "the" and "a", took the full-coverage bonus for
    them, and buried every file that implements the thing asked about.
    """
    global _STOPWORDS
    if _STOPWORDS is None:
        script = Path(__file__).resolve().parents[3] / "scripts" / "skill_routing.py"
        if not script.is_file():
            raise RuntimeError(f"cannot load {script}")
        spec = importlib.util.spec_from_file_location("cl_skill_routing", script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load {script}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _STOPWORDS = module.STOPWORDS
    return _STOPWORDS


_STOPWORDS: frozenset[str] | None = None


def normalize_terms(query: str) -> list[str]:
    """Return stable, de-duplicated search terms."""
    stopwords = _stopwords()
    terms: list[str] = []
    seen: set[str] = set()
    for match in WORD_RE.findall(query.lower()):
        term = match.strip("._-+")
        if term and term not in seen and term not in stopwords:
            terms.append(term)
            seen.add(term)
    if not terms:
        raise ValueError("query must contain at least one searchable term")
    return terms


def _looks_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    if not data:
        return False
    sample = data[:4096]
    suspicious = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return suspicious / len(sample) > 0.10


def _ignored_directory(name: str) -> bool:
    normalized = name.lower()
    return (
        normalized in IGNORED_DIRECTORIES
        or normalized == ".codegraph"
        or normalized.startswith(".codegraph-")
    )


def scan_text_files(root: Path, max_file_bytes: int) -> tuple[list[TextFile], int]:
    """Read bounded text files while pruning generated and dependency trees."""
    files: list[TextFile] = []
    skipped = 0
    for current, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(
            directory for directory in directories if not _ignored_directory(directory)
        )
        current_path = Path(current)
        for filename in sorted(filenames):
            path = current_path / filename
            if path.suffix.lower() in IGNORED_SUFFIXES or path.is_symlink():
                skipped += 1
                continue
            try:
                size = path.stat().st_size
                if size > max_file_bytes:
                    skipped += 1
                    continue
                data = path.read_bytes()
            except (OSError, PermissionError):
                skipped += 1
                continue
            if _looks_binary(data):
                skipped += 1
                continue
            text = data.decode("utf-8-sig", errors="replace")
            files.append(TextFile(path.as_posix(), text))
    return files, skipped


def _score(path: str, text: str, terms: Sequence[str]) -> tuple[int, list[str]]:
    path_lower = path.lower()
    name_lower = Path(path).name.lower()
    content_lower = text.lower()
    matched: list[str] = []
    score = 0
    for term in terms:
        path_hit = term in path_lower
        content_count = content_lower.count(term)
        if path_hit or content_count:
            matched.append(term)
        if path_hit:
            score += 18
            if term in name_lower:
                score += 10
        score += min(content_count, 8) * 2
    if matched:
        score += round(20 * len(matched) / len(terms))
    if len(matched) == len(terms):
        score += 15
    return score, matched


def _excerpts(
    text: str,
    terms: Sequence[str],
    context_lines: int,
    max_excerpts: int,
) -> list[dict[str, object]]:
    lines = text.splitlines()
    lowered = [line.lower() for line in lines]
    matching_lines = [
        index
        for index, line in enumerate(lowered)
        if any(term in line for term in terms)
    ]
    ranges: list[tuple[int, int]] = []
    for index in matching_lines:
        start = max(0, index - context_lines)
        end = min(len(lines), index + context_lines + 1)
        if ranges and start <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))
        if len(ranges) >= max_excerpts:
            break
    return [
        {
            "start_line": start + 1,
            "end_line": end,
            "text": "\n".join(lines[start:end]),
        }
        for start, end in ranges
    ]


def _trim_excerpts(
    excerpts: list[dict[str, object]], budget: int
) -> tuple[list[dict[str, object]], int]:
    trimmed: list[dict[str, object]] = []
    used = 0
    for excerpt in excerpts:
        if used >= budget:
            break
        text = str(excerpt["text"])
        remaining = budget - used
        if len(text) > remaining:
            if remaining < 8:
                break
            text = text[: remaining - 1].rstrip() + "…"
        item = dict(excerpt)
        item["text"] = text
        trimmed.append(item)
        used += len(text)
    return trimmed, used


def select_context(
    root: Path,
    query: str,
    *,
    max_files: int = 12,
    max_chars: int = 12_000,
    max_file_bytes: int = 1_000_000,
    context_lines: int = 1,
    max_excerpts: int = 3,
) -> dict[str, object]:
    """Return ranked excerpts whose payload respects max_chars."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"root is not a directory: {root}")
    if max_files < 1 or max_chars < 1 or max_file_bytes < 1:
        raise ValueError("max-files, max-chars, and max-file-bytes must be positive")

    terms = normalize_terms(query)
    files, skipped = scan_text_files(root, max_file_bytes)
    corpus_chars = sum(len(item.text) for item in files)
    ranked: list[dict[str, object]] = []
    for item in files:
        relative = Path(item.path).relative_to(root).as_posix()
        score, matched = _score(relative, item.text, terms)
        if score == 0:
            continue
        ranked.append(
            {
                "path": relative,
                "score": score,
                "matched_terms": matched,
                "source_chars": len(item.text),
                "excerpts": _excerpts(item.text, terms, context_lines, max_excerpts),
            }
        )
    ranked.sort(key=lambda item: (-int(item["score"]), str(item["path"])))

    selected: list[dict[str, object]] = []
    payload_chars = 0
    for candidate in ranked[:max_files]:
        path_cost = len(str(candidate["path"]))
        remaining = max_chars - payload_chars - path_cost
        if remaining < 0:
            break
        excerpts, excerpt_chars = _trim_excerpts(list(candidate["excerpts"]), remaining)
        if not excerpts and candidate["excerpts"] and selected:
            break
        chosen = dict(candidate)
        chosen["excerpts"] = excerpts
        selected.append(chosen)
        payload_chars += path_cost + excerpt_chars

    # Signed, not floored at zero. A payload larger than the text it was
    # selected from is what happens on a tree smaller than max_chars -- the
    # excerpt markers cost more than they save -- and that is exactly the case
    # someone runs when deciding whether the lens is worth using. Clamping it
    # reported "0.0%" for a payload that had grown by a third, which is the one
    # direction a self-measurement must never round in.
    reduction = 0.0 if corpus_chars == 0 else 1 - payload_chars / corpus_chars
    return {
        "schema_version": 1,
        "query": query,
        "terms": terms,
        "root": str(root),
        "scanned_files": len(files),
        "skipped_files": skipped,
        "corpus_chars": corpus_chars,
        "payload_chars": payload_chars,
        "char_reduction_pct": round(reduction * 100, 2),
        "files": selected,
    }


def _directory_keys(relative: Path, max_depth: int) -> list[str]:
    keys = ["."]
    parent_parts = relative.parent.parts
    for depth in range(1, min(len(parent_parts), max_depth) + 1):
        keys.append(Path(*parent_parts[:depth]).as_posix())
    return keys


def _directory_score(stats: dict[str, object]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    files = int(stats["files"])
    child_dirs = len(stats["child_dirs"])
    code_files = int(stats["code_files"])
    code_ratio = 0.0 if files == 0 else code_files / files

    if files >= 20:
        score += 3
        reasons.append("20+ files")
    elif files >= 8:
        score += 1
        reasons.append("8+ files")
    if child_dirs >= 5:
        score += 2
        reasons.append("5+ child directories")
    elif child_dirs >= 2:
        score += 1
        reasons.append("multiple child directories")
    if code_files >= 3 and code_ratio >= 0.70:
        score += 2
        reasons.append("high code ratio")
    if stats["boundary_files"]:
        score += 2
        reasons.append("module boundary")
    if stats["config_files"]:
        score += 1
        reasons.append("local configuration")
    if int(stats["large_files"]) >= 3:
        score += 1
        reasons.append("large-file concentration")
    if code_files >= 3 and int(stats["test_files"]) > 0:
        score += 1
        reasons.append("code and tests")
    if stats["existing_agents"]:
        score += 4
        reasons.append("existing AGENTS.md")
    return score, reasons


def build_project_map(
    root: Path,
    *,
    max_depth: int = 3,
    max_locations: int = 10,
    max_file_bytes: int = 1_000_000,
) -> dict[str, object]:
    """Return a compact hierarchy and AGENTS.md placement candidates."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"root is not a directory: {root}")
    if max_depth < 0 or max_locations < 1 or max_file_bytes < 1:
        raise ValueError(
            "max-depth must be non-negative; other limits must be positive"
        )

    files, skipped = scan_text_files(root, max_file_bytes)
    stats_by_directory: dict[str, dict[str, object]] = {}

    def stats_for(key: str) -> dict[str, object]:
        return stats_by_directory.setdefault(
            key,
            {
                "files": 0,
                "code_files": 0,
                "test_files": 0,
                "large_files": 0,
                "lines": 0,
                "child_dirs": set(),
                "extensions": Counter(),
                "boundary_files": [],
                "config_files": [],
                "existing_agents": False,
            },
        )

    # The root knowledge base is useful even when the repository is empty.
    stats_for(".")

    for item in files:
        relative = Path(item.path).relative_to(root)
        parent_key = "." if relative.parent == Path(".") else relative.parent.as_posix()
        suffix = relative.suffix.lower()
        line_count = len(item.text.splitlines())
        for key in _directory_keys(relative, max_depth):
            stats = stats_for(key)
            stats["files"] = int(stats["files"]) + 1
            stats["lines"] = int(stats["lines"]) + line_count
            if suffix in CODE_SUFFIXES:
                stats["code_files"] = int(stats["code_files"]) + 1
                stats["extensions"][suffix] += 1
            if "test" in relative.name.lower() or "tests" in relative.parts:
                stats["test_files"] = int(stats["test_files"]) + 1
            if line_count > 500:
                stats["large_files"] = int(stats["large_files"]) + 1

            key_parts = () if key == "." else Path(key).parts
            remaining = relative.parent.parts[len(key_parts) :]
            if remaining:
                stats["child_dirs"].add(remaining[0])

        parent_stats = stats_for(parent_key)
        if relative.name in BOUNDARY_NAMES or relative.suffix.lower() in {
            ".csproj",
            ".sln",
        }:
            parent_stats["boundary_files"].append(relative.name)
        if relative.name in CONFIG_NAMES:
            parent_stats["config_files"].append(relative.name)
        if relative.name == "AGENTS.md":
            parent_stats["existing_agents"] = True

    candidates: list[dict[str, object]] = []
    for path, stats in stats_by_directory.items():
        score, reasons = _directory_score(stats)
        candidates.append(
            {
                "path": path,
                "score": score,
                "recommended": path == "."
                or bool(stats["existing_agents"])
                or score >= 5,
                "reasons": ["root knowledge base"] if path == "." else reasons,
                "files": stats["files"],
                "code_files": stats["code_files"],
                "test_files": stats["test_files"],
                "lines": stats["lines"],
                "child_directories": len(stats["child_dirs"]),
                "languages": [
                    suffix.lstrip(".")
                    for suffix, _ in stats["extensions"].most_common(5)
                ],
                "boundary_files": sorted(set(stats["boundary_files"])),
                "config_files": sorted(set(stats["config_files"])),
                "existing_agents": stats["existing_agents"],
            }
        )
    candidates.sort(
        key=lambda item: (
            0 if item["path"] == "." else 1,
            -int(item["score"]),
            str(item["path"]),
        )
    )
    recommended = [item["path"] for item in candidates if item["recommended"]]
    if "." not in recommended:
        recommended.insert(0, ".")
    recommended = recommended[:max_locations]
    reported_paths = set(recommended)
    for item in candidates:
        if len(reported_paths) >= max_locations * 3:
            break
        if int(item["score"]) > 0:
            reported_paths.add(str(item["path"]))

    return {
        "schema_version": 1,
        "mode": "project-map",
        "root": str(root),
        "scanned_files": len(files),
        "skipped_files": skipped,
        "max_depth": max_depth,
        "recommended_locations": recommended,
        "existing_agents": [
            item["path"] for item in candidates if item["existing_agents"]
        ],
        "candidates": [item for item in candidates if item["path"] in reported_paths],
    }


def format_context(result: dict[str, object]) -> str:
    lines = [
        f"Query: {result['query']}",
        f"Scanned: {result['scanned_files']} text files; selected: {len(result['files'])}",
        (
            f"Payload: {result['payload_chars']} chars; "
            f"reduction vs scanned text: {result['char_reduction_pct']}%"
        ),
    ]
    for item in result["files"]:
        lines.append(
            f"\n[{item['score']}] {item['path']} ({', '.join(item['matched_terms'])})"
        )
        for excerpt in item["excerpts"]:
            lines.append(f"  L{excerpt['start_line']}-{excerpt['end_line']}")
            lines.extend(f"    {line}" for line in str(excerpt["text"]).splitlines())
    return "\n".join(lines)


def format_project_map(result: dict[str, object]) -> str:
    lines = [
        f"Project map: {result['root']}",
        f"Scanned: {result['scanned_files']} text files",
        "Recommended AGENTS.md locations:",
    ]
    candidate_by_path = {item["path"]: item for item in result["candidates"]}
    for path in result["recommended_locations"]:
        candidate = candidate_by_path[path]
        reasons = ", ".join(candidate["reasons"]) or "distinct project scope"
        lines.append(f"  [{candidate['score']}] {path} - {reasons}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="source tree to scan")
    parser.add_argument("--query", help="terms describing the target behavior")
    parser.add_argument(
        "--project-map",
        action="store_true",
        help="score a compact hierarchy for AGENTS.md placement",
    )
    parser.add_argument("--max-files", type=int, default=12)
    parser.add_argument("--max-chars", type=int, default=12_000)
    parser.add_argument("--max-file-bytes", type=int, default=1_000_000)
    parser.add_argument("--context-lines", type=int, default=1)
    parser.add_argument("--max-excerpts", type=int, default=3)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--max-locations", type=int, default=10)
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.project_map:
            result = build_project_map(
                Path(args.root),
                max_depth=args.max_depth,
                max_locations=args.max_locations,
                max_file_bytes=args.max_file_bytes,
            )
        else:
            if args.query is None:
                raise ValueError("--query is required unless --project-map is used")
            result = select_context(
                Path(args.root),
                args.query,
                max_files=args.max_files,
                max_chars=args.max_chars,
                max_file_bytes=args.max_file_bytes,
                context_lines=args.context_lines,
                max_excerpts=args.max_excerpts,
            )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        formatter = format_project_map if args.project_map else format_context
        print(formatter(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
