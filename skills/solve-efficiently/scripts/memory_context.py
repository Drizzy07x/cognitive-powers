#!/usr/bin/env python3
"""Project-scoped, demand-only memory with auditable native writes."""

from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONF = {
    "high": "high",
    "medium": "medium",
    "low": "low",
    "unknown": "unknown",
    "extracted": "high",
    "inferred": "medium",
    "ambiguous": "low",
}


class MemoryContextError(RuntimeError):
    pass


def _scope(value):
    value = str(value).strip()
    if not value:
        raise MemoryContextError("project_scope is mandatory")
    return value


def _sha(data: bytes):
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path):
    return _sha(path.read_bytes()) if path.exists() else None


def _dt(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result
    except ValueError as exc:
        raise MemoryContextError(f"timezone-aware timestamp required: {value}") from exc


def _normalize(record, scope):
    rec = dict(record)
    rec["project_scope"] = _scope(rec.get("project_scope"))
    if rec["project_scope"] != scope:
        raise MemoryContextError("record project_scope does not match requested scope")
    for key in (
        "id",
        "content",
        "source",
        "timestamp",
        "source_sha256",
        "confidence",
        "expires_at",
        "supersedes",
    ):
        if key not in rec:
            raise MemoryContextError(f"record missing required field: {key}")
    _dt(rec["timestamp"])
    _dt(rec["expires_at"])
    rec["confidence"] = CONF.get(str(rec["confidence"]).lower(), "unknown")
    if not isinstance(rec["supersedes"], list):
        raise MemoryContextError("supersedes must be a list")
    if not isinstance(rec["id"], str) or not rec["id"].strip():
        raise MemoryContextError("record id must be a non-empty string")
    rec["id"] = rec["id"].strip()
    if not isinstance(rec["content"], str):
        raise MemoryContextError("record content must be a string")
    if not all(isinstance(item, str) and item.strip() for item in rec["supersedes"]):
        raise MemoryContextError("supersedes must contain non-empty string ids")
    rec["supersedes"] = [item.strip() for item in rec["supersedes"]]
    if not isinstance(rec["source_sha256"], str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", rec["source_sha256"]
    ):
        raise MemoryContextError("source_sha256 must be SHA-256")
    source = Path(str(rec["source"])).expanduser()
    if source.is_file() and _file_sha(source) != rec["source_sha256"].lower():
        raise MemoryContextError("source hash mismatch")
    return rec


def _snapshot(store: Path, kind: str):
    folder = store.parent / ".memory-context-snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    data = folder / f"{store.name}.{token}.snapshot"
    existed = store.exists()
    if existed:
        if kind == "sqlite":
            with (
                closing(sqlite3.connect(store)) as src,
                closing(sqlite3.connect(data)) as dst,
            ):
                src.backup(dst)
        else:
            shutil.copy2(store, data)
    return {
        "schema_version": 1,
        "store": str(store.resolve()),
        "kind": kind,
        "before_exists": existed,
        "before_sha256": _file_sha(store),
        "snapshot": str(data.resolve()) if existed else None,
    }


def _finish(receipt, store):
    receipt["after_sha256"] = _file_sha(store)
    if receipt["snapshot"]:
        base = Path(str(receipt["snapshot"]))
    else:
        # First write to a store that did not exist yet: there is no snapshot
        # to name the receipt after, and a fixed "empty" name made every such
        # write overwrite the previous one's undo record. Mint the same
        # timestamped identity a snapshot would have carried.
        token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        base = store.parent / ".memory-context-snapshots" / f"{store.name}.{token}"
        base.parent.mkdir(parents=True, exist_ok=True)
    path = base.with_suffix(".receipt.json")
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8", newline="\n")
    receipt["receipt_path"] = str(path.resolve())
    return receipt


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as h:
            json.dump(payload, h, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_native(
    store: str | Path, record: dict[str, Any], *, project_scope: str, provider="json"
):
    scope = _scope(project_scope)
    rec = _normalize(record, scope)
    path = Path(store).expanduser().resolve()
    kind = (
        "sqlite"
        if provider == "sqlite" or path.suffix in (".db", ".sqlite", ".sqlite3")
        else "json"
    )
    receipt = _snapshot(path, kind)
    if kind == "json":
        payload = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {"schema_version": 1, "records": []}
        )
        records = payload.setdefault("records", [])
        records[:] = [
            item
            for item in records
            if not (
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item["id"].strip() == rec["id"]
            )
        ]
        records.append(rec)
        _atomic_json(path, payload)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, project_scope TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL, timestamp TEXT NOT NULL, source_sha256 TEXT NOT NULL, confidence TEXT NOT NULL, expires_at TEXT NOT NULL, supersedes TEXT NOT NULL)"
            )
            legacy_ids = [
                row[0]
                for row in db.execute("SELECT id FROM memories").fetchall()
                if isinstance(row[0], str) and row[0].strip() == rec["id"]
            ]
            db.executemany(
                "DELETE FROM memories WHERE id = ?",
                ((record_id,) for record_id in legacy_ids),
            )
            db.execute(
                "INSERT OR REPLACE INTO memories VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    rec["id"],
                    scope,
                    rec["content"],
                    rec["source"],
                    rec["timestamp"],
                    rec["source_sha256"],
                    rec["confidence"],
                    rec["expires_at"],
                    json.dumps(rec["supersedes"]),
                ),
            )
            db.commit()
    return _finish(receipt, path)


def undo_native(store: str | Path, receipt: str | Path | dict):
    # Expand exactly as write_native did, or a tilde-spelled store never
    # matches the receipt it produced and can never be undone.
    path = Path(store).expanduser().resolve()
    data = (
        json.loads(Path(receipt).read_text(encoding="utf-8"))
        if not isinstance(receipt, dict)
        else receipt
    )
    if str(path) != data.get("store"):
        raise MemoryContextError("receipt belongs to another store")
    if _file_sha(path) != data.get("after_sha256"):
        raise MemoryContextError("store changed after snapshot; undo refused")
    if data["before_exists"]:
        # The receipt already records what the snapshot must restore to, and
        # nothing checked it. A snapshot corrupted, truncated, or replaced on
        # disk was copied straight over the live store and reported as a
        # successful undo -- the one operation whose whole purpose is to put
        # back exactly what was there. Snapshots are ordinary files under
        # .memory-context-snapshots that nothing prunes, so they stay exposed
        # for as long as the user leaves them. A missing one used to escape as
        # a bare FileNotFoundError from copy2 rather than as a refusal.
        snapshot = Path(str(data["snapshot"]))
        if not snapshot.is_file() or _file_sha(snapshot) != data.get("before_sha256"):
            raise MemoryContextError(
                f"snapshot does not match the receipt it belongs to: {snapshot}"
            )
        shutil.copy2(snapshot, path)
    elif path.exists():
        path.unlink()
    return {"undone": True, "store": str(path), "restored_sha256": _file_sha(path)}


def _read_native(path: Path):
    if path.suffix in (".db", ".sqlite", ".sqlite3"):
        if not path.exists():
            return []
        with closing(sqlite3.connect(path)) as db:
            rows = db.execute(
                "SELECT id,project_scope,content,source,timestamp,source_sha256,confidence,expires_at,supersedes FROM memories"
            ).fetchall()
        return [
            dict(
                zip(
                    (
                        "id",
                        "project_scope",
                        "content",
                        "source",
                        "timestamp",
                        "source_sha256",
                        "confidence",
                        "expires_at",
                        "supersedes",
                    ),
                    r[:-1] + (json.loads(r[-1]),),
                )
            )
            for r in rows
        ]
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("records", [])


# scripts/skill_routing.py holds the tree's one tokenizer -- folding, English
# and Spanish stopwords, and stemming to a fixpoint. Scoring memories with a
# second, unfiltered one is the split CLAUDE.md warns about, and it cost what a
# split costs: every record containing "the" matched every query containing
# "the". Measured on a seven-record store, "how does the durable ledger verify
# a receipt" matched all seven, and three of the five it returned shared
# nothing with the query but that one word -- while inflating the two real
# matches by counting it for them too.
_ROUTING_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "skill_routing.py"
_ROUTING: Any = None


def _routing():
    global _ROUTING
    if _ROUTING is None:
        if not _ROUTING_SCRIPT.is_file():
            raise MemoryContextError(f"cannot load {_ROUTING_SCRIPT}")
        spec = importlib.util.spec_from_file_location(
            "cp_memory_skill_routing", _ROUTING_SCRIPT
        )
        if spec is None or spec.loader is None:
            raise MemoryContextError(f"cannot load {_ROUTING_SCRIPT}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _ROUTING = module
    return _ROUTING


def _tokens(text):
    return set(_routing().tokenize(str(text)))


def _filter(records, scope, query, limit):
    now = datetime.now(timezone.utc)
    normalized = []
    warnings = []
    metrics = {
        "scanned_records": len(records),
        "scope_matched_records": 0,
        "expired_records": 0,
        "malformed_records": 0,
        "query_matched_records": 0,
        "superseded_records": 0,
        "duplicate_id_records": 0,
    }
    seen_ids = set()
    for raw in records:
        if not isinstance(raw, dict):
            warnings.append("memory record must be an object")
            metrics["malformed_records"] += 1
            continue
        if raw.get("project_scope") != scope:
            continue
        metrics["scope_matched_records"] += 1
        try:
            rec = _normalize(raw, scope)
            if _dt(rec["expires_at"]) <= now:
                metrics["expired_records"] += 1
                continue
        except MemoryContextError as exc:
            warnings.append(str(exc))
            metrics["malformed_records"] += 1
            continue
        if rec["id"] in seen_ids:
            metrics["duplicate_id_records"] += 1
            warnings.append(f"duplicate memory record id ignored: {rec['id']}")
            continue
        seen_ids.add(rec["id"])
        normalized.append(rec)
    superseded = {item_id for rec in normalized for item_id in rec["supersedes"]}
    metrics["superseded_records"] = sum(rec["id"] in superseded for rec in normalized)
    valid = []
    query_tokens = _tokens(query)
    for rec in normalized:
        if rec["id"] in superseded:
            continue
        overlap = len(query_tokens & _tokens(rec["content"]))
        if overlap == 0:
            continue
        metrics["query_matched_records"] += 1
        rec["score"] = (
            overlap * 10
            + {
                "high": 3,
                "medium": 2,
                "low": 1,
                "unknown": 0,
            }[rec["confidence"]]
        )
        valid.append(rec)
    valid.sort(key=lambda r: (-r["score"], r["id"]))
    selected = valid[:limit]
    metrics["selected_records"] = len(selected)
    metrics["selected_chars"] = sum(len(r["content"]) for r in selected)
    metrics["estimated_selected_tokens"] = (metrics["selected_chars"] + 3) // 4
    return selected, warnings, metrics


def mark_retrieval_usage(payload, *, consumed_ids=(), useful_ids=()):
    """Attach observed downstream use without inferring model quality."""

    selected = {record["id"] for record in payload.get("results", [])}
    consumed = set(consumed_ids)
    useful = set(useful_ids)
    unknown = (consumed | useful) - selected
    if unknown:
        raise MemoryContextError(
            f"cannot classify unselected memory records: {sorted(unknown)}"
        )
    if not useful.issubset(consumed):
        raise MemoryContextError("useful memory records must also be consumed")
    decisions = [
        {
            "id": record["id"],
            "consumed": record["id"] in consumed,
            "usefulness": "useful" if record["id"] in useful else "unknown",
            "content_chars": len(record["content"]),
        }
        for record in payload.get("results", [])
    ]
    payload["usage_decisions"] = decisions
    payload["usage_metrics"].update(
        {
            "consumed_records": len(consumed),
            "useful_records": len(useful),
            "selected_unconsumed_records": len(selected - consumed),
            "consumed_chars": sum(
                item["content_chars"] for item in decisions if item["consumed"]
            ),
            "useful_chars": sum(
                item["content_chars"]
                for item in decisions
                if item["usefulness"] == "useful"
            ),
        }
    )
    return payload


def retrieve(
    store: str | Path | None,
    query: str,
    *,
    project_scope: str,
    demand=False,
    provider="native",
    limit=10,
    memu_executable=None,
    runner=subprocess.run,
    include_usage=False,
):
    scope = _scope(project_scope)
    if not demand:
        raise MemoryContextError("retrieval requires explicit demand")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise MemoryContextError("limit must be a positive integer")
    # "searchable", not merely "non-empty": scoring now drops function words,
    # so a query made only of them shares nothing with any record and would
    # return an empty result that reads as a store with nothing in it.
    if not _tokens(query):
        raise MemoryContextError("query must contain at least one searchable term")
    if provider == "memu":
        exe = str(memu_executable) if memu_executable else shutil.which("memu")
        if not exe:
            raise MemoryContextError("memu CLI is not already installed")
        try:
            cp = runner(
                [exe, "retrieve", f"project_scope:{scope} {query}"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise MemoryContextError("memu retrieval timed out") from exc
        if cp.returncode:
            raise MemoryContextError(cp.stderr.strip() or "memu retrieve failed")
        try:
            payload = json.loads(cp.stdout)
        except json.JSONDecodeError as exc:
            raise MemoryContextError("memu returned invalid JSON") from exc
        records = []
        for key in ("segments", "files", "resources"):
            records.extend(
                payload.get(key, []) if isinstance(payload.get(key), list) else []
            )
    else:
        if store is None:
            raise MemoryContextError("native store is required")
        records = _read_native(Path(store).expanduser().resolve())
    results, warnings, usage_metrics = _filter(records, scope, query, limit)
    payload = {
        "schema_version": 1,
        "provider": provider,
        "project_scope": scope,
        "demand_triggered": True,
        "query": query,
        "results": results,
        "warnings": warnings,
        "proof_status": "context_only",
    }
    if not include_usage:
        return payload
    usage_metrics.update(
        {
            "consumed_records": 0,
            "useful_records": 0,
            "selected_unconsumed_records": len(results),
            "consumed_chars": 0,
            "useful_chars": 0,
        }
    )
    payload.update(
        {
            "schema_version": 2,
            "usage_decisions": [
                {
                    "id": record["id"],
                    "consumed": False,
                    "usefulness": "unknown",
                    "content_chars": len(record["content"]),
                }
                for record in results
            ],
            "usage_metrics": usage_metrics,
        }
    )
    return payload
