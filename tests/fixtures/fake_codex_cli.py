#!/usr/bin/env python3
"""Stateful offline fake for Codex plugin/marketplace installer tests.

Roots are reported with forward slashes. On POSIX that is what str() already
returns, so nothing changes there; on Windows it is the one spelling both
installers can consume, because PowerShell accepts either separator while a
backslash inside a bash string is an escape rather than a path component. The
alternative was a second fake that differed only in that detail, which is how
two fixtures drift into testing two different contracts.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fail(state: dict, key: str) -> bool:
    failures = state.get("failures", {})
    remaining = int(failures.get(key, 0))
    if remaining:
        failures[key] = remaining - 1
        state["failures"] = failures
        return True
    return False


def main(argv: list[str]) -> int:
    state_path = Path(os.environ["FAKE_CODEX_STATE"])
    state = load(state_path)
    state.setdefault("log", []).append(argv)
    key = " ".join(argv[:4])
    if fail(state, key):
        save(state_path, state)
        return 41

    if argv[:3] == ["plugin", "marketplace", "list"]:
        print(json.dumps({"marketplaces": state.get("marketplaces", [])}))
    elif argv[:2] == ["plugin", "list"]:
        print(json.dumps({"installed": state.get("installed", [])}))
    elif argv[:3] == ["plugin", "marketplace", "remove"]:
        name = argv[3]
        state["marketplaces"] = [
            m for m in state.get("marketplaces", []) if m["name"] != name
        ]
    elif argv[:3] == ["plugin", "marketplace", "add"]:
        source = argv[3]
        if source == "Drizzy07x/cognitive-powers":
            revision = argv[argv.index("--ref") + 1]
            root_key = (
                "previous_root"
                if revision == state.get("previous_commit")
                else "release_root"
            )
            root = Path(state[root_key]).resolve().as_posix()
            source_value = source + "@" + revision
        else:
            root = Path(source).resolve().as_posix()
            source_value = source
        state.setdefault("marketplaces", []).append(
            {
                "name": "cognitive-powers",
                "root": root,
                "marketplaceSource": {"source": source_value},
            }
        )
    elif argv[:2] == ["plugin", "remove"]:
        plugin_id = argv[2]
        state["installed"] = [
            p for p in state.get("installed", []) if p["pluginId"] != plugin_id
        ]
    elif argv[:2] == ["plugin", "add"]:
        plugin_id = argv[2]
        if plugin_id.endswith("@cognitive-powers"):
            current = next(
                m for m in state["marketplaces"] if m["name"] == "cognitive-powers"
            )
            manifest = Path(current["root"]) / ".codex-plugin" / "plugin.json"
            version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        else:
            version = state.get("personal_version", "1.5.2")
        state["installed"] = [
            p for p in state.get("installed", []) if p["pluginId"] != plugin_id
        ]
        state.setdefault("installed", []).append(
            {
                "name": "cognitive-powers",
                "pluginId": plugin_id,
                "installed": True,
                "enabled": True,
                "version": version,
            }
        )
    else:
        save(state_path, state)
        return 64
    save(state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
