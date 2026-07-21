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


def probe_graphify(
    root: str | Path, graphify_dir: str | Path | None = None
) -> dict[str, Any]:
    root = _root(root)
    out = Path(graphify_dir).resolve() if graphify_dir else root / "graphify-out"
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
    }
    if not graph.is_file() or not manifest.is_file():
        base["reason"] = "graphify graph or manifest missing"
        return base
    try:
        marker_root = (
            Path(marker.read_text(encoding="utf-8").strip()).resolve()
            if marker.is_file()
            else None
        )
    except OSError as exc:
        base["reason"] = f"invalid graphify root marker: {exc}"
        return base
    if marker_root != root:
        base["reason"] = "graphify index is not bound to this worktree"
        return base
    base["worktree_bound"] = True
    try:
        records = json.loads(manifest.read_text(encoding="utf-8"))
        payload = json.loads(graph.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        base["reason"] = f"invalid graphify index: {exc}"
        return base
    if not isinstance(records, dict) or not isinstance(payload, dict):
        base["reason"] = "unsupported graphify schema"
        return base
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
        return base
    base.update(usable=True, fresh=True, reason=None, graph=payload)
    return base


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
    root, provider, mode, semantic, fresh, bound, candidates, reason=None, raw=None
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
        p = probe_graphify(root, graphify_dir)
        if p["usable"]:
            return _result(
                root,
                "graphify",
                "search",
                True,
                True,
                True,
                _graph_candidates(p["graph"], query),
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
    p = (
        probe_graphify(root, kwargs.get("graphify_dir"))
        if provider in ("auto", "graphify")
        else {"usable": False, "reason": "not selected"}
    )
    if p["usable"]:
        return _result(
            root,
            "graphify",
            "affected",
            True,
            True,
            True,
            _graph_candidates(p["graph"], " ".join(changed), changed),
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
        "graphify": probe_graphify(root, kwargs.get("graphify_dir")),
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
