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


def _load(name: str, directory: Path | None = None):
    path = directory / name if directory is not None else Path(__file__).with_name(name)
    # An absent module used to escape as FileNotFoundError from exec_module,
    # which no caller of this fail-closed surface guards for.
    if not path.is_file():
        raise SemanticProviderError(f"cannot load {path}")
    spec = importlib.util.spec_from_file_location("cp_" + path.stem, path)
    if not spec or not spec.loader:
        raise SemanticProviderError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# scripts/skill_routing.py holds the tree's one tokenizer -- folding, English
# and Spanish stopwords, and stemming to a fixpoint -- tuned against a measured
# routing corpus. Matching graph nodes with a second, naive implementation is
# the split CLAUDE.md warns about, and it cost exactly what a split costs: the
# unfiltered "the" and "is" matched by substring inside "displayName" and every
# other identifier containing them, so one prompt selected 3537 of 3801 nodes.
_PLUGIN_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_ROUTING: Any = None


def _routing():
    global _ROUTING
    if _ROUTING is None:
        _ROUTING = _load("skill_routing.py", _PLUGIN_SCRIPTS)
    return _ROUTING


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


def _detector_paths(value: Any) -> tuple[set[str], int]:
    """Return the paths the detector named, and how many it named unreadably.

    The second number is not diagnostics. Silently dropping an entry this
    reader does not recognize removes it from ``pending`` and ``deleted``, and
    that is the direction which reports a stale index as fresh: a changed file
    spelled as an object rather than a string left the pending set and the
    probe answered "complete", with the corpus count quietly one lower. Every
    unrecognized shape resolved that way, so a graphify whose detector schema
    drifted was trusted exactly when it had stopped being legible.
    """
    values: list[Any] = []
    unreadable = 0
    if isinstance(value, dict):
        for group in value.values():
            if isinstance(group, list):
                values.extend(group)
            elif group is not None:
                unreadable += 1
    elif isinstance(value, list):
        values.extend(value)
    elif value is not None:
        unreadable += 1
    paths: set[str] = set()
    for item in values:
        if isinstance(item, (str, Path)):
            paths.add(str(item))
        else:
            unreadable += 1
    return paths, unreadable


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
    # detect_incremental defaults to kind="semantic", the yardstick for
    # `graphify extract`: it re-queues every file `graphify update` touched,
    # because an AST-only pass empties semantic_hash on purpose. The
    # session-start refresh runs exactly that update, so the default question
    # reported every refreshed file as pending forever, this adapter rejected
    # its own maintained index, and navigation fell back to lexical while the
    # hook reported a successful refresh. Ask about the layer the plugin
    # actually maintains. A provider too old to know the parameter answers the
    # only question it has rather than failing the probe closed.
    script = (
        "from graphify.detect import detect_incremental;"
        "import inspect,json,sys;"
        "from pathlib import Path;"
        "kw={'kind':'ast'} if 'kind' in "
        "inspect.signature(detect_incremental).parameters else {};"
        "print(json.dumps(detect_incremental(Path(sys.argv[1]),sys.argv[2],**kw)))"
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
    unreadable = 0

    def outside_index(field: str) -> set[str]:
        nonlocal unreadable
        named, unread = _detector_paths(detected.get(field))
        unreadable += unread
        return {value for value in named if not _within_index(root, index, value)}

    files = outside_index("files")
    unchanged = outside_index("unchanged_files")
    explicit_new = outside_index("new_files")
    pending = (files - unchanged) | explicit_new
    deleted = outside_index("deleted_files")
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
    complete = not pending and not deleted and not walk_errors and not unreadable
    return (
        {
            "status": "complete" if complete else "incomplete",
            "detector": "graphify-module",
            "corpus_file_count": len(files),
            "pending_file_count": len(pending),
            "deleted_file_count": len(deleted),
            "walk_error_count": len(walk_errors),
            "unreadable_entry_count": unreadable,
            "warnings": warnings,
        },
        None
        if complete
        else (
            f"graphify detector output is not fully readable ({unreadable} unrecognized)"
            if unreadable
            else "graphify index is incomplete"
        ),
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
    # UnicodeDecodeError is a ValueError, caught by neither guard. graphify
    # writes both of these files, so their encoding is not this adapter's to
    # assume, and one written as UTF-16 escaped probe_graphify as a traceback
    # instead of demoting the index to unusable with a stated reason.
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
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


# graphify labels a code identifier verbatim, so "displayName" is a single
# word to any tokenizer and unreachable from a query that spells the two words
# apart. Splitting on the boundary is what keeps that reachable now that terms
# have to match whole tokens rather than substrings.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# The keys graphify and networkx exports use for the same two facts. Stated
# once because three separate copies of these tuples had already drifted apart
# in what they included.
_NODE_PATH_KEYS = ("source_file", "sourceFile", "filePath", "path")
_NODE_TEXT_KEYS = ("label", "name", *_NODE_PATH_KEYS)

# Candidates are read by a model that then has to open them. Every node sharing
# any term is not a candidate set, it is a second copy of the graph.
_MAX_TERM_CANDIDATES = 40


def _terms(text: str) -> set[str]:
    tokenize = _routing().tokenize
    tokens = set(tokenize(text))
    split = _CAMEL_BOUNDARY.sub(" ", text)
    if split != text:
        tokens |= set(tokenize(split))
    return tokens


def _node_text(node: dict[str, Any], keys: Sequence[str]) -> str:
    return " ".join(str(node.get(key, "")) for key in keys)


def _ranked_by_terms(byid: dict[str, Any], query: str) -> dict[str, Any]:
    """Return the nodes sharing the most query terms, best first.

    Ranking and the cap are one decision: without them a caller reading the
    top of the list gets whatever order the export happened to use.
    """
    terms = _terms(query)
    if not terms:
        return {}
    scored = [
        (overlap, identifier)
        for identifier, node in byid.items()
        if (overlap := len(terms & _terms(_node_text(node, _NODE_TEXT_KEYS))))
    ]
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return {
        identifier: byid[identifier] for _, identifier in scored[:_MAX_TERM_CANDIDATES]
    }


def _neighbourhood(
    byid: dict[str, Any], edges: Any, changed: Sequence[str]
) -> dict[str, Any]:
    """Nodes whose file is one of ``changed``, plus their direct neighbours."""
    needles = {Path(x).as_posix().lower() for x in changed}
    seeds = {
        identifier
        for identifier, node in byid.items()
        if any(p in _node_text(node, _NODE_PATH_KEYS).lower() for p in needles)
    }
    selected = {identifier: byid[identifier] for identifier in seeds}
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict):
            continue
        a, b = str(edge.get("source")), str(edge.get("target"))
        if a in seeds and b in byid:
            selected[b] = byid[b]
        if b in seeds and a in byid:
            selected[a] = byid[a]
    return selected


def _candidate(node: dict[str, Any]) -> dict[str, Any]:
    raw = str(node.get("confidence", node.get("provenance", ""))).upper()
    return {
        "id": node.get("id"),
        "label": node.get("label", node.get("name")),
        "path": next((node.get(k) for k in _NODE_PATH_KEYS if node.get(k)), None),
        "kind": node.get("kind", node.get("type")),
        "confidence": CONFIDENCE.get(raw, "unknown"),
        "raw_confidence": raw or None,
    }


def _graph_candidates(
    graph: dict[str, Any], query: str, changed: Sequence[str] | None = None
):
    nodes = graph.get("nodes", [])
    byid = {str(n.get("id")): n for n in nodes if isinstance(n, dict)}
    selected = _ranked_by_terms(byid, query)
    if changed:
        # Appended rather than ranked: a seed's file was named by the caller,
        # so it is evidence of a different kind than a term overlap and must
        # not be dropped by the cap above.
        edges = graph.get("edges", graph.get("links", []))
        selected.update(_neighbourhood(byid, edges, changed))
    return [_candidate(node) for node in selected.values()]


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
    # Every refusal this module raises is a domain error it chose to state, and
    # each one reached the caller as a traceback instead: an unreadable root, an
    # empty query, a manifest path escaping the project. semantic_context.py
    # beside it already answers with the error and exit 2, which is what a
    # caller reading this surface's JSON can act on.
    try:
        result = (
            probe(ns.root, **kw)
            if ns.cmd == "probe"
            else search(ns.root, ns.query, **kw)
            if ns.cmd == "search"
            else affected(ns.root, ns.file, **kw)
        )
    except SemanticProviderError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
