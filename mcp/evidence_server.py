#!/usr/bin/env python3
"""Expose the durable evidence store to any MCP host, read-only.

Cognitive Powers already consumes MCP tools; it published none. Its receipts,
ledgers, and session state -- the artifacts a completion claim is supposed to
be checked against -- were reachable only by running ``work_state.py``, so a
host that could not shell out could not read the evidence at all.

Every tool here is an inspection. Nothing in this file writes, deletes, or
advances state: mutation stays on the CLI, where the ownership, lock, and
verifier gates live. A second write path would be a second place for those
gates to be wrong.

For the same reason the read path does not reimplement anything either. Each
tool shells out to the canonical ``work_state.py`` subcommand and returns its
JSON verbatim. Two implementations of one contract diverge, and the one behind
an MCP boundary is the one nobody would notice diverging.

Standard library only, like the rest of the plugin: a server that needed
installing before it could report whether the installation works would be
self-defeating.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WORK_STATE = PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "work_state.py"

SERVER_NAME = "cognitive-powers-evidence"
SERVER_VERSION = "1.0.0"
# Negotiation is "answer with a version this server supports", not "agree with
# whatever was asked". Echoing the request claimed support for every version a
# client could name, including ones whose tool surface this file does not
# implement -- the same shape of unearned assurance the rest of this plugin
# exists to refuse. These four share the initialize/ping/tools surface used here.
SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
SUBPROCESS_TIMEOUT_SECONDS = 60.0

# Only inspection subcommands are reachable. The map is the allowlist: a tool
# name that is not a key here cannot reach a subprocess at all, so no argument
# a caller supplies can select a mutating subcommand.
TOOLS: dict[str, dict[str, Any]] = {
    "inspect_evidence_storage": {
        "description": (
            "Report the size and shape of the Cognitive Powers durable evidence "
            "store: logical and physical bytes, file count, project count, "
            "session count, and the largest directories. Read-only."
        ),
        "subcommand": ["storage-inspect"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "largest": {
                    "type": "integer",
                    "description": "How many of the largest directories to list.",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                }
            },
            "additionalProperties": False,
        },
        "arguments": lambda payload: [
            "--largest",
            str(int(payload.get("largest", 20))),
        ],
    },
    "summarize_durable_session": {
        "description": (
            "Summarize one durable session from its fully authenticated ledger "
            "recovery, separating completed criteria from runnable ones. Fails "
            "closed on an unreadable ledger rather than guessing. Read-only."
        ),
        "subcommand": ["resume-summary"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": "The durable session identifier.",
                }
            },
            "required": ["session"],
            "additionalProperties": False,
        },
        "arguments": lambda payload: ["--session", _require_text(payload, "session")],
    },
    "check_durable_session_schema": {
        "description": (
            "Validate one session's state file, authenticated ledger chain, "
            "checkpoint and delta recovery, and recovery record, without "
            "writing anything. Dry run only; it never migrates. Read-only."
        ),
        "subcommand": ["state-migrate"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": "The durable session identifier.",
                }
            },
            "required": ["session"],
            "additionalProperties": False,
        },
        "arguments": lambda payload: ["--session", _require_text(payload, "session")],
    },
}


class ToolError(ValueError):
    """A tool call that cannot be served as asked."""


def _require_text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{name} must be a non-empty string")
    return value


def _interpreter() -> str:
    # sys.executable is this server's own interpreter, which the host resolved
    # from the plugin's python_executable option. Reusing it keeps the CLI on
    # the same interpreter the host already proved it can start.
    return sys.executable or "python3"


def run_work_state(subcommand: list[str], arguments: list[str]) -> dict[str, Any]:
    if not WORK_STATE.is_file():
        raise ToolError(
            f"work_state.py is missing from this installation: {WORK_STATE}"
        )
    argv = [
        _interpreter(),
        str(WORK_STATE),
        *_root_arguments(),
        *subcommand,
        *arguments,
        "--json",
    ]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ToolError(f"cannot run work_state.py: {error}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ToolError(detail or f"work_state.py exited {completed.returncode}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ToolError(f"work_state.py did not return JSON: {error}") from error


def _root_arguments() -> list[str]:
    arguments: list[str] = []
    workspace = os.environ.get("COGNITIVE_POWERS_WORKSPACE")
    if workspace:
        arguments += ["--root", workspace]
    data_root = os.environ.get("COGNITIVE_POWERS_DATA")
    if data_root:
        arguments += ["--data-root", data_root]
    return arguments


def call_tool(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"unknown tool: {name}")
    try:
        arguments = tool["arguments"](payload)
    except (TypeError, ValueError) as error:
        raise ToolError(str(error)) from error
    return run_work_state(list(tool["subcommand"]), list(arguments))


def _tool_listing() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": tool["description"],
            "inputSchema": tool["inputSchema"],
        }
        for name, tool in sorted(TOOLS.items())
    ]


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    identifier = message.get("id")
    if identifier is None:
        # A notification. initialized is the expected one; anything else is
        # still not something a server may answer.
        return None
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        return _result(
            identifier,
            {
                "protocolVersion": requested
                if requested in SUPPORTED_PROTOCOL_VERSIONS
                else DEFAULT_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "ping":
        return _result(identifier, {})
    if method == "tools/list":
        return _result(identifier, {"tools": _tool_listing()})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        payload = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(payload, dict):
            return _error(identifier, -32602, "name and arguments are required")
        try:
            result = call_tool(name, payload)
        except ToolError as error:
            # A refused inspection is a tool result, not a protocol failure:
            # the caller asked a well-formed question the store cannot answer.
            return _result(
                identifier,
                {
                    "content": [{"type": "text", "text": str(error)}],
                    "isError": True,
                },
            )
        return _result(
            identifier,
            {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                ],
                "structuredContent": result,
                "isError": False,
            },
        )
    return _error(identifier, -32601, f"unsupported method: {method}")


def _result(identifier: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def _error(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {"code": code, "message": message},
    }


def serve(source: Any, sink: Any) -> int:
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        try:
            response = handle(message)
        except Exception as error:  # noqa: BLE001 - a server must not exit on one message
            response = _error(
                message.get("id"), -32603, f"{type(error).__name__}: {error}"
            )
            if message.get("id") is None:
                response = None
        if response is not None:
            sink.write(json.dumps(response, ensure_ascii=False) + "\n")
            sink.flush()
    return 0


def main() -> int:
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
