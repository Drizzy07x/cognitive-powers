#!/usr/bin/env python3
"""Project-scoped, demand-only memory with auditable native writes."""

from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
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
    path = Path(
        str(
            receipt["snapshot"]
            or (store.parent / ".memory-context-snapshots" / "empty")
        )
    ).with_suffix(".receipt.json")
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    receipt["receipt_path"] = str(path.resolve())
    return receipt


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as h:
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
        records[:] = [x for x in records if x.get("id") != rec["id"]]
        records.append(rec)
        _atomic_json(path, payload)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, project_scope TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL, timestamp TEXT NOT NULL, source_sha256 TEXT NOT NULL, confidence TEXT NOT NULL, expires_at TEXT NOT NULL, supersedes TEXT NOT NULL)"
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
    path = Path(store).resolve()
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
        shutil.copy2(data["snapshot"], path)
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


def _tokens(text):
    return set(re.findall(r"[\w.-]+", str(text).lower()))


def _filter(records, scope, query, limit):
    now = datetime.now(timezone.utc)
    valid = []
    warnings = []
    for raw in records:
        if raw.get("project_scope") != scope:
            continue
        try:
            rec = _normalize(raw, scope)
            if _dt(rec["expires_at"]) <= now:
                continue
        except MemoryContextError as exc:
            warnings.append(str(exc))
            continue
        overlap = len(_tokens(query) & _tokens(rec["content"]))
        if overlap == 0:
            continue
        score = (
            overlap * 10
            + {"high": 3, "medium": 2, "low": 1, "unknown": 0}[rec["confidence"]]
        )
        rec["score"] = score
        valid.append(rec)
    superseded = {x for r in valid for x in r["supersedes"]}
    valid = [r for r in valid if r["id"] not in superseded]
    valid.sort(key=lambda r: (-r["score"], r["id"]))
    return valid[:limit], warnings


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
):
    scope = _scope(project_scope)
    if not demand:
        raise MemoryContextError("retrieval requires explicit demand")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise MemoryContextError("limit must be a positive integer")
    if not _tokens(query):
        raise MemoryContextError("query must not be empty")
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
    results, warnings = _filter(records, scope, query, limit)
    return {
        "schema_version": 1,
        "provider": provider,
        "project_scope": scope,
        "demand_triggered": True,
        "query": query,
        "results": results,
        "warnings": warnings,
        "proof_status": "context_only",
    }
