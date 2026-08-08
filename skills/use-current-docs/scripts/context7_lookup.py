#!/usr/bin/env python3
"""Retrieve bounded, version-aware Context7 documentation with external caching."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 1
DEFAULT_MAX_CHARS = 10_000
DEFAULT_TTL_HOURS = 24.0
IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "__pycache__",
        "bin",
        "build",
        "dist",
        "node_modules",
        "obj",
        "target",
        "vendor",
        "venv",
    }
)
MANIFEST_NAMES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "pyproject.toml",
        "Cargo.toml",
        "Directory.Packages.props",
    }
)


class Context7LookupError(RuntimeError):
    """Raised when current documentation cannot be retrieved or normalized.

    Deliberately not named LookupError: shadowing the builtin made main()'s
    except tuple silently stop covering KeyError and IndexError, which are
    builtin LookupError subclasses.
    """


def _ranking_number(candidate: dict[str, Any], *fields: str) -> float:
    """Read the first ranking score a candidate states, or 0.0 when it states none.

    These come from an external index and ``float()`` took them at their word:
    "high", a list, or a mapping raised ValueError or TypeError out of the
    scoring loop and straight past ``main()``'s except tuple -- the traceback
    the class docstring above already warns this module is prone to.

    A score that cannot be read is not a reason to abandon the lookup. It is a
    candidate with nothing to contribute, so it contributes nothing and the
    others still rank. Falsy values fall through to the next field exactly as
    the ``or`` chain this replaced did, and a non-finite number is refused
    because it would poison the comparison rather than lose one candidate.
    """
    for field in fields:
        value = candidate.get(field)
        if not value or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            number = float(value)
        elif isinstance(value, str):
            try:
                number = float(value)
            except ValueError:
                continue
        else:
            continue
        if number == number and number not in (float("inf"), float("-inf")):
            return number
    return 0.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def normalize_version(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "*":
        return None
    match = re.search(
        r"(?<![A-Za-z0-9])v?(\d+(?:\.\d+){0,3}(?:[-+][0-9A-Za-z.-]+)?)", text
    )
    return match.group(1) if match else None


def _dependency(
    name: str, version: object, source: Path, kind: str
) -> dict[str, str | None]:
    return {
        "name": name,
        "version": normalize_version(version),
        "declared": None if version is None else str(version),
        "source": source.as_posix(),
        "kind": kind,
    }


def _json_dependencies(path: Path) -> list[dict[str, str | None]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    found: list[dict[str, str | None]] = []
    if path.name == "package.json":
        for group in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        ):
            values = payload.get(group, {})
            if isinstance(values, dict):
                found.extend(
                    _dependency(str(name), version, path, group)
                    for name, version in values.items()
                )
    elif path.name == "package-lock.json":
        values = payload.get("dependencies", {})
        if isinstance(values, dict):
            for name, metadata in values.items():
                version = (
                    metadata.get("version") if isinstance(metadata, dict) else metadata
                )
                found.append(_dependency(str(name), version, path, "locked"))
    return found


def _toml_dependencies(path: Path) -> list[dict[str, str | None]]:
    import tomllib

    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    found: list[dict[str, str | None]] = []
    if path.name == "pyproject.toml":
        project = payload.get("project", {})
        if isinstance(project, dict):
            for item in project.get("dependencies", []) or []:
                match = re.match(r"\s*([A-Za-z0-9_.-]+)(.*)", str(item))
                if match:
                    found.append(
                        _dependency(match.group(1), match.group(2), path, "dependency")
                    )
        poetry = payload.get("tool", {}).get("poetry", {}).get("dependencies", {})
        if isinstance(poetry, dict):
            for name, version in poetry.items():
                if normalize_name(str(name)) != "python":
                    declared = (
                        version.get("version") if isinstance(version, dict) else version
                    )
                    found.append(_dependency(str(name), declared, path, "dependency"))
    elif path.name == "Cargo.toml":
        for group in ("dependencies", "dev-dependencies", "build-dependencies"):
            values = payload.get(group, {})
            if isinstance(values, dict):
                for name, version in values.items():
                    declared = (
                        version.get("version") if isinstance(version, dict) else version
                    )
                    found.append(_dependency(str(name), declared, path, group))
    return found


def _xml_dependencies(path: Path) -> list[dict[str, str | None]]:
    root = ET.parse(path).getroot()
    found: list[dict[str, str | None]] = []
    for element in root.iter():
        if (
            element.tag.rsplit("}", 1)[-1] != "PackageReference"
            and element.tag.rsplit("}", 1)[-1] != "PackageVersion"
        ):
            continue
        name = element.attrib.get("Include") or element.attrib.get("Update")
        version = element.attrib.get("Version")
        if version is None:
            for child in element:
                if child.tag.rsplit("}", 1)[-1] == "Version":
                    version = child.text
                    break
        if name:
            found.append(_dependency(name, version, path, "package-reference"))
    return found


def _requirements(path: Path) -> list[dict[str, str | None]]:
    found: list[dict[str, str | None]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.split("#", 1)[0].strip()
        if not value or value.startswith(("-", "http:", "https:", "git+")):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)(.*)", value)
        if match:
            found.append(
                _dependency(match.group(1), match.group(2), path, "requirement")
            )
    return found


def discover_dependencies(root: Path) -> list[dict[str, str | None]]:
    root = root.resolve()
    manifests: list[Path] = []
    for current, directories, filenames in os.walk(root):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory not in IGNORED_DIRECTORIES
        )
        for filename in sorted(filenames):
            if (
                filename in MANIFEST_NAMES
                or filename.endswith((".csproj", ".fsproj"))
                or re.fullmatch(r"requirements(?:-[^.]+)?\.txt", filename)
            ):
                manifests.append(Path(current) / filename)
        if len(manifests) >= 200:
            break
    found: list[dict[str, str | None]] = []
    for path in manifests:
        try:
            relative = path.relative_to(root)
            if path.suffix == ".json":
                entries = _json_dependencies(path)
            elif path.suffix == ".toml":
                entries = _toml_dependencies(path)
            elif path.suffix in {".csproj", ".fsproj", ".props"}:
                entries = _xml_dependencies(path)
            else:
                entries = _requirements(path)
            for entry in entries:
                entry["source"] = relative.as_posix()
                found.append(entry)
        except (OSError, ValueError, json.JSONDecodeError, ET.ParseError):
            continue
    priority = {"locked": 0, "package-reference": 1, "dependency": 2, "requirement": 2}
    unique: dict[str, dict[str, str | None]] = {}
    for item in sorted(
        found,
        key=lambda value: (priority.get(str(value["kind"]), 5), str(value["source"])),
    ):
        unique.setdefault(normalize_name(str(item["name"])), item)
    return sorted(unique.values(), key=lambda value: str(value["name"]).casefold())


def find_dependency(
    dependencies: Iterable[dict[str, str | None]], library: str
) -> dict[str, str | None] | None:
    target = normalize_name(library)
    ranked: list[tuple[int, dict[str, str | None]]] = []
    for item in dependencies:
        name = normalize_name(str(item["name"]))
        score = 100 if name == target else 60 if target in name or name in target else 0
        if score:
            ranked.append((score, item))
    return max(ranked, key=lambda pair: pair[0])[1] if ranked else None


def extract_candidates(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("results", "libraries", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if payload.get("id"):
            return [payload]
    return []


def _version_options(candidate: dict[str, Any]) -> list[str]:
    values = candidate.get("versions", [])
    if isinstance(values, dict):
        values = list(values.keys()) + list(values.values())
    if not isinstance(values, list):
        values = [values]
    return [str(value) for value in values if value]


def select_library_candidate(
    candidates: Sequence[dict[str, Any]], library: str, version: str | None
) -> dict[str, Any]:
    if not candidates:
        raise Context7LookupError(f"Context7 returned no candidates for {library}")
    target_name = normalize_name(library)
    target_version = normalize_version(version)
    ranked: list[tuple[float, dict[str, Any], str | None]] = []
    reputation_scores = {"high": 20, "medium": 10, "low": 2, "unknown": 0}
    for candidate in candidates:
        candidate_id = str(candidate.get("id") or candidate.get("libraryId") or "")
        if not candidate_id.startswith("/"):
            continue
        title = str(
            candidate.get("title")
            or candidate.get("name")
            or candidate_id.rsplit("/", 1)[-1]
        )
        normalized = normalize_name(title)
        name_score = (
            100
            if normalized == target_name
            else 65
            if target_name in normalized or normalized in target_name
            else 0
        )
        reputation = str(
            candidate.get("sourceReputation")
            or candidate.get("reputation")
            or "unknown"
        ).casefold()
        benchmark = _ranking_number(candidate, "benchmarkScore", "score")
        coverage = _ranking_number(candidate, "totalSnippets", "codeSnippets")
        matched_option: str | None = None
        if target_version:
            for option in _version_options(candidate):
                if normalize_version(option) == target_version:
                    matched_option = option
                    break
        version_score = 80 if matched_option else (-30 if target_version else 0)
        score = (
            name_score
            + reputation_scores.get(reputation, 0)
            + min(benchmark, 100) / 10
            + min(coverage, 10_000) / 1_000
            + version_score
        )
        ranked.append((score, candidate, matched_option))
    if not ranked:
        raise Context7LookupError(
            "Context7 candidates did not contain valid library IDs"
        )
    _, selected, option = max(ranked, key=lambda value: value[0])
    base_id = str(selected.get("id") or selected.get("libraryId"))
    selected_id = base_id
    if option:
        selected_id = (
            option
            if option.startswith("/")
            else f"{base_id.rstrip('/')}/{option.lstrip('/')}"
        )
    return {
        "id": selected_id,
        "base_id": base_id,
        "name": str(selected.get("title") or selected.get("name") or library),
        "requested_version": target_version,
        "matched_version": normalize_version(option),
        "version_matched": bool(target_version and option),
        "source_reputation": selected.get("sourceReputation")
        or selected.get("reputation"),
        "benchmark_score": selected.get("benchmarkScore") or selected.get("score"),
        "code_snippets": selected.get("totalSnippets") or selected.get("codeSnippets"),
    }


def _snippet_text(item: dict[str, Any], kind: str) -> dict[str, Any] | None:
    title = item.get("codeTitle") or item.get("title") or item.get("breadcrumb")
    source = item.get("source") or item.get("url") or item.get("pageUrl")
    if kind == "code":
        values = item.get("codeList") or item.get("code") or item.get("content")
        if isinstance(values, list):
            content = "\n\n".join(
                str(value.get("code") if isinstance(value, dict) else value)
                for value in values
            )
        else:
            content = str(values or "")
    else:
        content = str(item.get("content") or item.get("text") or "")
    if not content.strip():
        return None
    return {"kind": kind, "title": title, "source": source, "content": content.strip()}


def extract_snippets(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        source_items = payload
        return [
            value
            for item in source_items
            if isinstance(item, dict)
            for value in [_snippet_text(item, str(item.get("kind") or "info"))]
            if value
        ]
    if not isinstance(payload, dict):
        return []
    snippets: list[dict[str, Any]] = []
    for key, kind in (
        ("codeSnippets", "code"),
        ("infoSnippets", "info"),
        ("snippets", "info"),
    ):
        values = payload.get(key, [])
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict):
                    normalized = _snippet_text(item, kind)
                    if normalized:
                        snippets.append(normalized)
    return snippets


def bound_snippets(
    base: dict[str, Any], snippets: Sequence[dict[str, Any]], max_chars: int
) -> tuple[list[dict[str, Any]], bool]:
    selected: list[dict[str, Any]] = []
    truncated = False
    for snippet in snippets:
        candidate = selected + [snippet]
        if len(canonical_json({**base, "snippets": candidate})) <= max_chars:
            selected = candidate
            continue
        remaining = (
            max_chars - len(canonical_json({**base, "snippets": selected})) - 200
        )
        if remaining > 200:
            shortened = {**snippet, "content": str(snippet["content"])[:remaining]}
            selected.append(shortened)
        truncated = True
        break
    if len(selected) < len(snippets):
        truncated = True
    return selected, truncated


def resolve_data_root() -> Path:
    configured = os.environ.get("COGNITIVE_POWERS_DATA")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".codex" / "cognitive-powers").resolve()
    )


def project_key(root: Path) -> str:
    canonical = (
        str(root.resolve()).casefold() if os.name == "nt" else str(root.resolve())
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def cache_path(root: Path, identity: dict[str, Any]) -> Path:
    key = sha256_json(identity)[:32]
    return resolve_data_root() / "external-context" / project_key(root) / f"{key}.json"


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _parse_expiry(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A hand-edited or foreign cache entry may carry a naive stamp, and
    # comparing that against the aware clock raised TypeError -- an exception
    # outside main()'s except tuple. The writer pins UTC, so naive reads as
    # UTC rather than crashing the lookup.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def find_cli() -> list[str]:
    direct = shutil.which("ctx7") or shutil.which("ctx7.cmd")
    if direct:
        return [direct]
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx:
        return [npx, "--yes", "ctx7@latest"]
    pnpm = shutil.which("pnpm") or shutil.which("pnpm.cmd")
    if pnpm:
        return [pnpm, "dlx", "ctx7@latest"]
    raise Context7LookupError(
        "Context7 MCP was not used and no ctx7, npx, or pnpm CLI is available"
    )


def run_cli(arguments: Sequence[str], timeout: float = 45.0) -> object:
    command = [*find_cli(), *arguments, "--json"]
    environment = os.environ.copy()
    environment["CTX7_TELEMETRY_DISABLED"] = "1"
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
        env=environment,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise Context7LookupError(
            f"Context7 CLI failed with exit code {completed.returncode}: {detail}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise Context7LookupError("Context7 CLI did not return JSON") from error


def lookup(
    root: Path,
    library: str,
    query: str,
    *,
    version: str | None = None,
    library_id: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    ttl_hours: float = DEFAULT_TTL_HOURS,
    refresh: bool = False,
    library_results: object | None = None,
    docs_results: object | None = None,
) -> dict[str, Any]:
    if not library.strip() or not query.strip():
        raise Context7LookupError("library and query must not be empty")
    if max_chars < 1_000:
        raise Context7LookupError("max-chars must be at least 1000")
    dependencies = discover_dependencies(root)
    local = find_dependency(dependencies, library)
    requested_version = normalize_version(version) or (
        str(local["version"]) if local and local.get("version") else None
    )
    identity = {
        "provider": "context7",
        "root": str(root.resolve()),
        "library": library,
        "version": requested_version,
        "library_id": library_id,
        "query": query,
        "max_chars": max_chars,
        "ttl_hours": ttl_hours,
    }
    cached_path = cache_path(root, identity)
    if not refresh and cached_path.is_file():
        cached = _read_json(cached_path)
        if (
            isinstance(cached, dict)
            and (
                _parse_expiry(cached.get("expires_at"))
                or datetime.min.replace(tzinfo=timezone.utc)
            )
            > utc_now()
        ):
            cached["cache"] = {"hit": True, "path": str(cached_path)}
            return cached
    if library_id:
        selected = {
            "id": library_id,
            "base_id": library_id,
            "name": library,
            "requested_version": requested_version,
            "matched_version": normalize_version(library_id),
            "version_matched": bool(
                requested_version and normalize_version(library_id) == requested_version
            ),
            "source_reputation": None,
            "benchmark_score": None,
            "code_snippets": None,
        }
    else:
        raw_candidates = (
            library_results
            if library_results is not None
            else run_cli(["library", library, query])
        )
        selected = select_library_candidate(
            extract_candidates(raw_candidates), library, requested_version
        )
    raw_docs = (
        docs_results
        if docs_results is not None
        else run_cli(["docs", str(selected["id"]), query])
    )
    retrieved = utc_now()
    response_hash = sha256_json(raw_docs)
    base = {
        "schema_version": SCHEMA_VERSION,
        "type": "external_context",
        "provider": "context7",
        "library": library,
        "selected_library": selected,
        "requested_version": requested_version,
        "local_dependency": local,
        "query": query,
        "retrieved_at": iso_utc(retrieved),
        "expires_at": iso_utc(retrieved + timedelta(hours=ttl_hours)),
        "provider_response_sha256": response_hash,
    }
    snippets, truncated = bound_snippets(base, extract_snippets(raw_docs), max_chars)
    payload = {
        **base,
        "snippets": snippets,
        "snippet_count": len(snippets),
        "truncated": truncated,
        "cache": {"hit": False, "path": str(cached_path)},
    }
    while len(canonical_json(payload)) > max_chars and payload["snippets"]:
        excess = len(canonical_json(payload)) - max_chars
        last = payload["snippets"][-1]
        content = str(last["content"])
        if len(content) > excess + 100:
            last["content"] = content[: len(content) - excess - 20]
        else:
            payload["snippets"].pop()
        payload["snippet_count"] = len(payload["snippets"])
        payload["truncated"] = True
    _write_json_atomic(cached_path, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    subparsers = parser.add_subparsers(dest="command", required=True)
    dependencies = subparsers.add_parser(
        "dependencies", help="detect local dependency versions"
    )
    dependencies.add_argument("--json", action="store_true")
    lookup_parser = subparsers.add_parser(
        "lookup", help="retrieve version-aware Context7 documentation"
    )
    lookup_parser.add_argument("--library", required=True)
    lookup_parser.add_argument("--query", required=True)
    lookup_parser.add_argument("--version")
    lookup_parser.add_argument("--library-id")
    lookup_parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    lookup_parser.add_argument(
        "--cache-ttl-hours", type=float, default=DEFAULT_TTL_HOURS
    )
    lookup_parser.add_argument("--refresh", action="store_true")
    lookup_parser.add_argument(
        "--library-results", type=Path, help="normalize saved MCP/CLI library JSON"
    )
    lookup_parser.add_argument(
        "--docs-results", type=Path, help="normalize saved MCP/CLI docs JSON"
    )
    lookup_parser.add_argument("--output", type=Path)
    lookup_parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    try:
        if not root.is_dir():
            raise Context7LookupError(f"root is not a directory: {root}")
        if args.command == "dependencies":
            payload: object = {
                "schema_version": SCHEMA_VERSION,
                "root": str(root),
                "dependencies": discover_dependencies(root),
            }
        else:
            payload = lookup(
                root,
                args.library,
                args.query,
                version=args.version,
                library_id=args.library_id,
                max_chars=args.max_chars,
                ttl_hours=args.cache_ttl_hours,
                refresh=args.refresh,
                library_results=_read_json(args.library_results)
                if args.library_results
                else None,
                docs_results=_read_json(args.docs_results)
                if args.docs_results
                else None,
            )
            if args.output:
                _write_json_atomic(args.output.resolve(), payload)
        print(
            canonical_json(payload)
            if args.json
            else json.dumps(payload, indent=2, ensure_ascii=False)
        )
        return 0
    except (
        Context7LookupError,
        OSError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
