#!/usr/bin/env python3
"""Create minimal, equivalent CODEX_HOME templates for controller A/B runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

from live_ab_runner import (
    codex_host_identity,
    source_sha256,
    tree_hashes,
    validate_arm_plugins,
)


class HomePreparationError(ValueError):
    """Raised when clean, equivalent experiment homes cannot be produced."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_identity(root: Path) -> dict[str, str]:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if sha.returncode or status.returncode:
        raise HomePreparationError("plugin source must be a Git checkout")
    if status.stdout.strip():
        raise HomePreparationError(
            "plugin source must be clean before preparing A/B homes"
        )
    return {"commit": sha.stdout.strip(), "status": "clean"}


def _minimal_config(model: str, reasoning_effort: str) -> str:
    return (
        f"model = {json.dumps(model)}\n"
        f"model_reasoning_effort = {json.dumps(reasoning_effort)}\n"
        'approval_policy = "never"\n'
        'sandbox_mode = "workspace-write"\n'
        'service_tier = "default"\n\n'
        '[plugins."cognitive-powers@personal"]\n'
        "enabled = true\n\n"
        "[features]\n"
        "hooks = true\n"
        "memories = false\n"
        "multi_agent = true\n"
    )


def _copy_plugin(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            "benchmark-results",
            "*.pyc",
            "*.pyo",
        ),
    )


def _login_status(codex: str, home: Path) -> str:
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(home)
    completed = subprocess.run(
        [codex, "login", "status"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    status = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0 or "Logged in using ChatGPT" not in status:
        raise HomePreparationError(f"ChatGPT authentication unavailable in {home}")
    return "chatgpt"


def prepare_homes(
    *,
    source_home: Path,
    plugin_source: Path,
    output_root: Path,
    model: str,
    reasoning_effort: str,
    codex: str,
) -> dict[str, Any]:
    source_home = source_home.resolve()
    plugin_source = plugin_source.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        raise HomePreparationError(f"output already exists: {output_root}")
    for required in (source_home / "auth.json", source_home / "AGENTS.md"):
        if not required.is_file():
            raise HomePreparationError(
                f"missing required source-home file: {required.name}"
            )
    manifest = json.loads(
        (plugin_source / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise HomePreparationError("plugin version is missing")
    git = _git_identity(plugin_source)
    config = _minimal_config(model, reasoning_effort)
    homes: dict[str, Path] = {}
    try:
        for arm in ("baseline", "candidate"):
            home = output_root / arm
            cache = (
                home / "plugins" / "cache" / "personal" / "cognitive-powers" / version
            )
            cache.parent.mkdir(parents=True)
            shutil.copy2(source_home / "auth.json", home / "auth.json")
            shutil.copy2(source_home / "AGENTS.md", home / "AGENTS.md")
            (home / "config.toml").write_text(config, encoding="utf-8")
            _copy_plugin(plugin_source, cache)
            homes[arm] = home
        auth_methods = {arm: _login_status(codex, home) for arm, home in homes.items()}
        if len(set(auth_methods.values())) != 1:
            raise HomePreparationError(
                "experiment homes use different authentication methods"
            )
        plugin = validate_arm_plugins(codex, homes["baseline"], homes["candidate"])
        try:
            host_identity = codex_host_identity(codex)
        except ValueError as error:
            raise HomePreparationError(str(error)) from error
        baseline_hashes = tree_hashes(homes["baseline"])
        candidate_hashes = tree_hashes(homes["candidate"])
        if baseline_hashes != candidate_hashes:
            raise HomePreparationError("experiment homes are not byte-equivalent")
        receipt = {
            "schema_version": 1,
            "kind": "controller-ab-home-preparation",
            "source_git": git,
            "plugin_version": version,
            "plugin_sha256": plugin["source_sha256"],
            "home_sha256": source_sha256(baseline_hashes),
            "config_sha256": _sha256(homes["baseline"] / "config.toml"),
            "instructions_sha256": _sha256(homes["baseline"] / "AGENTS.md"),
            "authentication": "chatgpt",
            "model": model,
            "reasoning_effort": reasoning_effort,
            "host_identity": host_identity,
            "homes": {arm: str(home) for arm, home in homes.items()},
            "receipt_contains_secrets": False,
        }
        (output_root / "home-preparation.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return receipt
    except Exception:
        if output_root.exists():
            shutil.rmtree(output_root)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-home", type=Path, required=True)
    parser.add_argument("--plugin-source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--codex", default="codex")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = prepare_homes(
            source_home=args.source_home,
            plugin_source=args.plugin_source,
            output_root=args.output_root,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            codex=args.codex,
        )
    except (HomePreparationError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
