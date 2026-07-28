#!/usr/bin/env python3
"""Provider-neutral, fail-closed semantic navigation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

CONFIDENCE = {"EXTRACTED": "high", "INFERRED": "medium", "AMBIGUOUS": "low"}


class SemanticProviderError(RuntimeError):
    pass


def _load(name: str):
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location("cp_" + path.stem, path)
    if not spec or not spec.loader:
        raise SemanticProviderError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _root(value: str | Path) -> Path:
    value = Path(value).expanduser().resolve()
    if not value.is_dir():
        raise SemanticProviderError(f"root is not a directory: {value}")
    return value


def _inside(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SemanticProviderError(f"manifest path escapes project: {rel}") from exc
    return path


def _graph_health(payload: dict[str, Any]) -> dict[str, Any]:
    nodes = payload.get("nodes")
    edges = payload.get("edges") if "edges" in payload else payload.get("links")
    issues: list[str] = []
    identifiers: set[str] = set()
    if not isinstance(nodes, list):
        issues.append("nodes must be a list")
        nodes = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            issues.append(f"nodes[{index}] must be an object")
            continue
        raw_identifier = node.get("id")
        identifier = str(raw_identifier).strip() if raw_identifier is not None else ""
        if not identifier:
            issues.append(f"nodes[{index}] has no non-empty id")
        elif identifier in identifiers:
            issues.append(f"nodes[{index}] duplicates id {identifier}")
        else:
            identifiers.add(identifier)
    if not isinstance(edges, list):
        issues.append("edges or links must be a list")
        edges = []
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            issues.append(f"edges[{index}] must be an object")
            continue
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if not source or not target:
            issues.append(f"edges[{index}] has no source or target")
        elif source not in identifiers or target not in identifiers:
            issues.append(f"edges[{index}] has a missing endpoint")
        elif source == target:
            issues.append(f"edges[{index}] is a self-loop")
    return {
        "status": "ok" if not issues else "invalid",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "issue_count": len(issues),
        "issues": issues[:20],
    }


def _detector_paths(value: Any) -> set[str]:
    values: list[Any] = []
    if isinstance(value, dict):
        for group in value.values():
            if isinstance(group, list):
                values.extend(group)
    elif isinstance(value, list):
        values.extend(value)
    return {str(item) for item in values if isinstance(item, (str, Path))}


def _within_index(root: Path, index: Path, value: str) -> bool:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    try:
        path.resolve().relative_to(index)
    except ValueError:
        return False
    return True


def _display_path(root: Path, value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _manifest_completeness(
    root: Path,
    index: Path,
    records: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """Judge completeness from the manifest when graphify is not importable.

    Graphify also ships as a self-contained executable, which exposes no
    ``graphify`` package for the module detector to import. The manifest still
    records every indexed file, so deletions and unindexed siblings are
    decidable here without the provider.

    Modified files are already rejected upstream by the manifest hash check, so
    this decides the remaining exact question: whether an indexed file still
    exists.

    It deliberately does not guess at files graphify has never indexed.
    Graphify's corpus rules are not derivable from its output -- it skips
    tracked, small files that share a suffix with files it did index -- and
    guessing produced false ``incomplete`` verdicts that disabled the provider
    permanently. That gap is reported in ``new_file_detection`` rather than
    hidden, and the session-start refresh is what actually closes it.
    """
    indexed = 0
    deleted: list[str] = []
    for rel in records:
        try:
            path = _inside(root, str(rel))
        except SemanticProviderError:
            deleted.append(str(rel))
            continue
        if path.is_file():
            indexed += 1
        else:
            deleted.append(str(rel))

    if deleted:
        return (
            {
                "status": "incomplete",
                "detector": "manifest",
                "new_file_detection": "unavailable",
                "corpus_file_count": indexed,
                "pending_file_count": 0,
                "deleted_file_count": len(deleted),
                "walk_error_count": 0,
                "warnings": sorted(deleted)[:20],
            },
            "graphify index is incomplete",
        )
    # Not "complete". Every indexed file was already resolved and hashed
    # upstream, so this detector adds no verdict of its own, and it cannot see
    # a file graphify has never indexed. Saying "complete" would report a graph
    # missing an arbitrary number of files as a fully covering one.
    return (
        {
            "status": "unverified",
            "detector": "manifest",
            "new_file_detection": "unavailable",
            "corpus_file_count": indexed,
            "pending_file_count": 0,
            "deleted_file_count": 0,
            "walk_error_count": 0,
            "warnings": [],
        },
        None,
    )


def _graphify_completeness(
    root: Path,
    index: Path,
    manifest: Path,
    records: dict[str, Any],
    *,
    runner,
    timeout_seconds: float,
) -> tuple[dict[str, Any], str | None]:
    python_marker = index / ".graphify_python"
    try:
        executable_text = python_marker.read_text(encoding="utf-8").strip()
    except OSError:
        return _manifest_completeness(root, index, records)
    executable = Path(executable_text).expanduser()
    if not executable.is_absolute():
        executable = (index / executable).resolve()
    if not executable.is_file():
        return _manifest_completeness(root, index, records)
    script = (
        "from graphify.detect import detect_incremental;"
        "import json,sys;"
        "from pathlib import Path;"
        "print(json.dumps(detect_incremental(Path(sys.argv[1]), sys.argv[2])))"
    )
    argv = [
        str(executable),
        "-X",
        "utf8",
        "-c",
        script,
        str(root),
        str(manifest),
    ]
    try:
        completed = runner(
            argv,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (
            {
                "status": "invalid",
                "pending_file_count": 0,
                "deleted_file_count": 0,
                "walk_error_count": 0,
            },
            f"graphify detector failed closed: {exc}",
        )
    if completed.returncode != 0:
        return (
            {
                "status": "invalid",
                "pending_file_count": 0,
                "deleted_file_count": 0,
                "walk_error_count": 0,
            },
            f"graphify detector failed with exit code {completed.returncode}",
        )
    try:
        detected = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        return (
            {
                "status": "invalid",
                "pending_file_count": 0,
                "deleted_file_count": 0,
                "walk_error_count": 0,
            },
            f"graphify detector returned invalid JSON: {exc}",
        )
    if not isinstance(detected, dict) or "files" not in detected:
        return (
            {
                "status": "invalid",
                "pending_file_count": 0,
                "deleted_file_count": 0,
                "walk_error_count": 0,
            },
            "graphify detector returned an unsupported schema",
        )
    files = {
        value
        for value in _detector_paths(detected.get("files"))
        if not _within_index(root, index, value)
    }
    unchanged = {
        value
        for value in _detector_paths(detected.get("unchanged_files"))
        if not _within_index(root, index, value)
    }
    explicit_new = {
        value
        for value in _detector_paths(detected.get("new_files"))
        if not _within_index(root, index, value)
    }
    pending = (files - unchanged) | explicit_new
    deleted = {
        value
        for value in _detector_paths(detected.get("deleted_files"))
        if not _within_index(root, index, value)
    }
    raw_walk_errors = detected.get("walk_errors", [])
    walk_errors = (
        raw_walk_errors if isinstance(raw_walk_errors, list) else [raw_walk_errors]
    )
    warnings = sorted(
        {
            *(_display_path(root, value) for value in pending),
            *(_display_path(root, value) for value in deleted),
        }
    )[:20]
    complete = not pending and not deleted and not walk_errors
    return (
        {
            "status": "complete" if complete else "incomplete",
            "detector": "graphify-module",
            "corpus_file_count": len(files),
            "pending_file_count": len(pending),
            "deleted_file_count": len(deleted),
            "walk_error_count": len(walk_errors),
            "warnings": warnings,
        },
        None if complete else "graphify index is incomplete",
    )


def _inspect_graphify(
    root: str | Path,
    graphify_dir: str | Path | None = None,
    *,
    runner=subprocess.run,
    timeout_seconds: float = 10.0,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    root = _root(root)
    # Resolve the default too: _within_index compares resolved member paths
    # against this base, so a symlinked graphify-out (a common way to park a
    # large index on another volume) reported every indexed file as outside
    # its own index.
    out = (
        Path(graphify_dir).resolve()
        if graphify_dir
        else (root / "graphify-out").resolve()
    )
    graph, manifest, marker = (
        out / "graph.json",
        out / "manifest.json",
        out / ".graphify_root",
    )
    base = {
        "provider": "graphify",
        "available": graph.exists() or manifest.exists(),
        "usable": False,
        "fresh": False,
        "worktree_bound": False,
        "root": str(root),
        "index": str(out),
        "warnings": [],
        "node_count": 0,
        "edge_count": 0,
        "manifest_file_count": 0,
        "health": {"status": "unknown", "issue_count": 0, "issues": []},
        "completeness": {
            "status": "unknown",
            "pending_file_count": 0,
            "deleted_file_count": 0,
            "walk_error_count": 0,
        },
    }
    if not graph.is_file() or not manifest.is_file():
        base["reason"] = "graphify graph or manifest missing"
        return base, None
    try:
        marker_root = (
            Path(marker.read_text(encoding="utf-8").strip()).resolve()
            if marker.is_file()
            else None
        )
    except OSError as exc:
        base["reason"] = f"invalid graphify root marker: {exc}"
        return base, None
    if marker_root != root:
        base["reason"] = "graphify index is not bound to this worktree"
        return base, None
    base["worktree_bound"] = True
    try:
        records = json.loads(manifest.read_text(encoding="utf-8"))
        payload = json.loads(graph.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        base["reason"] = f"invalid graphify index: {exc}"
        return base, None
    if not isinstance(records, dict) or not isinstance(payload, dict):
        base["reason"] = "unsupported graphify schema"
        return base, None
    base["manifest_file_count"] = len(records)
    health = _graph_health(payload)
    base["health"] = health
    base["node_count"] = health["node_count"]
    base["edge_count"] = health["edge_count"]
    if health["status"] != "ok":
        base["reason"] = "graphify graph health check failed"
        base["warnings"] = health["issues"]
        return base, None
    stale = []
    for rel, item in records.items():
        if not isinstance(item, dict):
            stale.append(str(rel))
            continue
        try:
            path = _inside(root, str(rel))
        except SemanticProviderError:
            stale.append(str(rel))
            continue
        expected = item.get("ast_hash") or item.get("semantic_hash")
        if (
            not path.is_file()
            or not isinstance(expected, str)
            or len(expected) not in (32, 64)
        ):
            stale.append(str(rel))
            continue
        algo = hashlib.sha256 if len(expected) == 64 else hashlib.md5
        if algo(path.read_bytes()).hexdigest().lower() != expected.lower():
            stale.append(str(rel))
    if stale:
        base["reason"] = f"graphify index is stale for {len(stale)} file(s)"
        base["warnings"] = stale[:20]
        return base, None
    completeness, completeness_error = _graphify_completeness(
        root,
        out,
        manifest,
        records,
        runner=runner,
        timeout_seconds=timeout_seconds,
    )
    base["completeness"] = completeness
    if completeness_error is not None:
        base["reason"] = completeness_error
        base["warnings"] = completeness.get("warnings", [])[:20]
        return base, None
    # Only the provider's own detector establishes that the graph covers the
    # tree. Without it every indexed file is known to be current, but files it
    # never indexed are invisible, so callers must not read this as coverage.
    base["completeness_verified"] = completeness.get("status") == "complete"
    base.update(usable=True, fresh=True, reason=None)
    return base, payload


def probe_graphify(
    root: str | Path,
    graphify_dir: str | Path | None = None,
    *,
    runner=subprocess.run,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    status, _payload = _inspect_graphify(
        root,
        graphify_dir,
        runner=runner,
        timeout_seconds=timeout_seconds,
    )
    return status


def _lexical(root: Path, query: str, reason: str | None) -> dict[str, Any]:
    raw = _load("context_lens.py").select_context(
        root, query, max_files=12, max_chars=12000
    )
    candidates = [
        {
            "path": x["path"],
            "label": x["path"],
            "kind": "file",
            "confidence": "low",
            "raw_confidence": None,
            "score": x["score"],
        }
        for x in raw["files"]
    ]
    return _result(
        root, "lexical", "search", False, True, True, candidates, reason, raw
    )


def _result(
    root,
    provider,
    mode,
    semantic,
    fresh,
    bound,
    candidates,
    reason=None,
    raw=None,
    coverage_verified=True,
):
    return {
        "schema_version": 1,
        "provider": provider,
        "mode": mode,
        "root": str(root),
        "semantic": semantic,
        "fresh": fresh,
        "worktree_bound": bound,
        "confidence_model": "normalized-labels-v1",
        "candidates": candidates,
        "proof_status": "navigation_only",
        "requires_behavioral_verification": True,
        # False when freshness was established but coverage was not: every
        # indexed file matches, yet a file the provider never indexed cannot be
        # seen. Absence from these candidates is then not evidence of absence.
        "coverage_verified": coverage_verified,
        "reason": reason,
        "raw": raw,
    }


def _graph_candidates(
    graph: dict[str, Any], query: str, changed: Sequence[str] | None = None
):
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", graph.get("links", []))
    byid = {str(n.get("id")): n for n in nodes if isinstance(n, dict)}
    terms = set(re.findall(r"[\w.-]+", query.lower()))
    selected = {}
    for n in byid.values():
        hay = " ".join(
            str(n.get(k, ""))
            for k in ("label", "name", "source_file", "sourceFile", "filePath", "path")
        ).lower()
        if terms and any(t in hay for t in terms):
            selected[str(n.get("id"))] = n
    if changed:
        needles = {Path(x).as_posix().lower() for x in changed}
        seeds = {
            i
            for i, n in byid.items()
            if any(
                p
                in " ".join(
                    str(n.get(k, ""))
                    for k in ("source_file", "sourceFile", "filePath", "path")
                ).lower()
                for p in needles
            )
        }
        selected.update({i: byid[i] for i in seeds})
        for e in edges if isinstance(edges, list) else []:
            if not isinstance(e, dict):
                continue
            a, b = str(e.get("source")), str(e.get("target"))
            if a in seeds and b in byid:
                selected[b] = byid[b]
            if b in seeds and a in byid:
                selected[a] = byid[a]
    result = []
    for n in selected.values():
        raw = str(n.get("confidence", n.get("provenance", ""))).upper()
        path = next(
            (
                n.get(k)
                for k in ("source_file", "sourceFile", "filePath", "path")
                if n.get(k)
            ),
            None,
        )
        result.append(
            {
                "id": n.get("id"),
                "label": n.get("label", n.get("name")),
                "path": path,
                "kind": n.get("kind", n.get("type")),
                "confidence": CONFIDENCE.get(raw, "unknown"),
                "raw_confidence": raw or None,
            }
        )
    return result


def search(
    root: str | Path,
    query: str,
    *,
    provider="auto",
    codegraph_executable=None,
    graphify_dir=None,
    runner=subprocess.run,
    graphify_runner=subprocess.run,
):
    root = _root(root)
    query = query.strip()
    if not query:
        raise SemanticProviderError("query must not be empty")
    if provider in ("auto", "codegraph"):
        cg = _load("semantic_context.py")
        p = cg.probe_codegraph(root, executable=codegraph_executable, runner=runner)
        if p["usable"]:
            raw = cg.explore(
                root, query, executable=codegraph_executable, runner=runner
            )
            return _result(root, "codegraph", "search", True, True, True, [], raw=raw)
        if provider == "codegraph":
            return _lexical(root, query, str(p["reason"]))
    if provider in ("auto", "graphify"):
        p, graph = _inspect_graphify(root, graphify_dir, runner=graphify_runner)
        if p["usable"]:
            return _result(
                root,
                "graphify",
                "search",
                True,
                True,
                True,
                _graph_candidates(graph or {}, query),
                coverage_verified=bool(p.get("completeness_verified")),
            )
        if provider == "graphify":
            return _lexical(root, query, str(p["reason"]))
    return _lexical(root, query, "no fresh semantic provider was usable")


def affected(root: str | Path, changed_files: Sequence[str], **kwargs):
    root = _root(root)
    changed = [x.strip() for x in changed_files if x.strip()]
    if not changed:
        raise SemanticProviderError("at least one changed file is required")
    provider = kwargs.get("provider", "auto")
    if provider in ("auto", "codegraph"):
        cg = _load("semantic_context.py")
        executable = kwargs.get("codegraph_executable")
        runner = kwargs.get("runner", subprocess.run)
        status = cg.probe_codegraph(root, executable=executable, runner=runner)
        if status["usable"]:
            raw = cg.affected_tests(root, changed, executable=executable, runner=runner)
            candidates = [
                {
                    "path": item if isinstance(item, str) else item.get("path"),
                    "label": item
                    if isinstance(item, str)
                    else item.get("name", item.get("path")),
                    "kind": "test",
                    "confidence": "unknown",
                    "raw_confidence": None,
                }
                for item in raw.get("affectedTests", [])
            ]
            return _result(
                root, "codegraph", "affected", True, True, True, candidates, raw=raw
            )
    p, graph = (
        _inspect_graphify(
            root,
            kwargs.get("graphify_dir"),
            runner=kwargs.get("graphify_runner", subprocess.run),
        )
        if provider in ("auto", "graphify")
        else ({"usable": False, "reason": "not selected"}, None)
    )
    if p["usable"]:
        return _result(
            root,
            "graphify",
            "affected",
            True,
            True,
            True,
            _graph_candidates(graph or {}, " ".join(changed), changed),
            coverage_verified=bool(p.get("completeness_verified")),
        )
    result = search(root, " ".join(Path(x).stem for x in changed), **kwargs)
    result["mode"] = "affected"
    return result


def probe(root: str | Path, **kwargs):
    root = _root(root)
    return {
        "codegraph": _load("semantic_context.py").probe_codegraph(
            root,
            executable=kwargs.get("codegraph_executable"),
            runner=kwargs.get("runner", subprocess.run),
        ),
        "graphify": probe_graphify(
            root,
            kwargs.get("graphify_dir"),
            runner=kwargs.get("graphify_runner", subprocess.run),
        ),
        "lexical": {
            "available": True,
            "usable": True,
            "fresh": True,
            "worktree_bound": True,
        },
    }


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument(
        "--provider",
        choices=("auto", "codegraph", "graphify", "lexical"),
        default="auto",
    )
    ap.add_argument("--codegraph")
    ap.add_argument("--graphify-out")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("probe")
    s = sp.add_parser("search")
    s.add_argument("--query", required=True)
    a = sp.add_parser("affected")
    a.add_argument("--file", action="append", required=True)
    ns = ap.parse_args(argv)
    kw = {
        "provider": ns.provider,
        "codegraph_executable": ns.codegraph,
        "graphify_dir": ns.graphify_out,
    }
    result = (
        probe(ns.root, **kw)
        if ns.cmd == "probe"
        else search(ns.root, ns.query, **kw)
        if ns.cmd == "search"
        else affected(ns.root, ns.file, **kw)
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
