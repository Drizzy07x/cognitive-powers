#!/usr/bin/env python3
"""Probe optional integrations without installing or initializing them."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Mapping, NamedTuple, Sequence


class AdapterSpec(NamedTuple):
    name: str
    executables: tuple[str, ...] = ()
    version_args: tuple[str, ...] = ("--version",)
    config_env: str | None = None
    capability: str = ""


SPECS = {
    "context-mode": AdapterSpec(
        "context-mode", ("context-mode",), capability="large-output"
    ),
    "graphify": AdapterSpec(
        "graphify", ("graphify",), capability="semantic-navigation"
    ),
    "memu": AdapterSpec(
        "memu",
        ("memu", "memu-cli", "memu-codex"),
        version_args=("--help",),
        capability="memory-retrieval",
    ),
    "ruflo": AdapterSpec("ruflo", ("ruflo",), capability="external-coordination"),
    "nacos": AdapterSpec(
        "nacos", config_env="NACOS_BASE_URL", capability="distributed-registry"
    ),
    "lobehub": AdapterSpec(
        "lobehub",
        ("lh", "lobe", "lobehub"),
        config_env="LOBEHUB_EXPORT_PATH",
        capability="manifest-exchange",
    ),
    "obsidian": AdapterSpec(
        "obsidian",
        ("obsidian",),
        version_args=("version",),
        capability="vertical-knowledge-pack",
    ),
}


class AdapterError(ValueError):
    """Raised for an unknown or malformed optional integration."""


def _redact_config(value: str) -> str:
    if value.startswith(("http://", "https://")):
        return value.split("?", 1)[0]
    return str(Path(value).expanduser())


def probe(
    name: str,
    *,
    execute: bool = False,
    environment: Mapping[str, str] | None = None,
    available_tools: Sequence[str] = (),
    timeout: float = 5.0,
) -> dict[str, object]:
    if name not in SPECS:
        raise AdapterError(f"unknown adapter: {name}")
    spec = SPECS[name]
    env = environment if environment is not None else os.environ
    executable = next(
        (shutil.which(item) for item in spec.executables if shutil.which(item)), None
    )
    configured = bool(spec.config_env and env.get(spec.config_env))
    tool_matches = sorted(
        tool
        for tool in available_tools
        if name == "context-mode"
        and (
            tool.startswith("mcp__context_mode__")
            or tool.startswith("mcp__context-mode__")
            or tool.startswith("context_mode.")
            or tool.startswith("ctx_")
        )
    )
    available = executable is not None or configured or bool(tool_matches)
    result: dict[str, object] = {
        "name": name,
        "capability": spec.capability,
        "available": available,
        "configured": configured,
        "executable": executable,
        "liveValidated": False,
        "installedByProbe": False,
        "fallback": "cognitive-powers-native",
        "availabilitySources": [
            source
            for source, present in (
                ("executable", executable is not None),
                ("configuration", configured),
                ("tool-surface", bool(tool_matches)),
            )
            if present
        ],
    }
    if tool_matches:
        result["toolSurface"] = tool_matches
    if configured and spec.config_env:
        result["configuration"] = _redact_config(str(env[spec.config_env]))
    if not execute or executable is None:
        return result
    completed = subprocess.run(
        [executable, *spec.version_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    result["probeExitCode"] = completed.returncode
    result["versionOutput"] = (completed.stdout or completed.stderr).strip()[:500]
    result["liveValidated"] = completed.returncode == 0
    return result


def probe_all(
    *, execute: bool = False, available_tools: Sequence[str] = ()
) -> dict[str, object]:
    adapters = [
        probe(name, execute=execute, available_tools=available_tools)
        for name in sorted(SPECS)
    ]
    return {
        "adapters": adapters,
        "available": [item["name"] for item in adapters if item["available"]],
        "liveValidated": [item["name"] for item in adapters if item["liveValidated"]],
        "noImplicitInstallation": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", choices=[*sorted(SPECS), "all"])
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run only an already-resolved executable's bounded version probe",
    )
    parser.add_argument(
        "--tool-name",
        action="append",
        default=[],
        help="declare an already-available MCP/app tool without executing it",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = (
            probe_all(execute=args.execute, available_tools=args.tool_name)
            if args.name == "all"
            else probe(
                args.name,
                execute=args.execute,
                available_tools=args.tool_name,
            )
        )
    except (AdapterError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
