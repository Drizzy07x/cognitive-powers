#!/usr/bin/env python3
"""Exercise verify_installed against a disposable Git tag and isolated Codex home."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "scripts" / "verify_installed.py"
TAG = "v1.6.0"


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_installed_fixture", VERIFIER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load installed-tree verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fixture(source: Path) -> None:
    (source / ".codex-plugin").mkdir(parents=True)
    (source / "skills").mkdir()
    for name in ("execute-durably", "solve-efficiently", "verify-delivery"):
        skill = source / "skills-core" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    internal = source / "skills" / "internal-workflow"
    internal.mkdir()
    (internal / "SKILL.md").write_text(
        "---\nname: internal-workflow\n---\n", encoding="utf-8"
    )
    (source / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "cognitive-powers",
                "version": "1.6.0",
                "skills": "./skills-core/",
            }
        ),
        encoding="utf-8",
    )
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "fixture@example.invalid")
    _git(source, "config", "user.name", "Fixture")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "fixture")
    _git(source, "tag", TAG)


def main() -> int:
    verifier = _load_verifier()
    with tempfile.TemporaryDirectory(prefix="cognitive-powers-verify-") as temporary:
        base = Path(temporary)
        source = base / "source"
        installed = base / "installed"
        isolated_home = base / "home"
        source.mkdir()
        isolated_home.mkdir()
        _write_fixture(source)
        shutil.copytree(source, installed, ignore=shutil.ignore_patterns(".git"))
        commit = _git(source, "rev-parse", "--verify", f"{TAG}^{{commit}}")
        (installed / ".codex-marketplace-install.json").write_text(
            json.dumps(
                {
                    "source_type": "git",
                    "source": "https://github.com/Drizzy07x/cognitive-powers.git",
                    "ref_name": commit,
                    "revision": commit,
                    "sparse_paths": [],
                }
            ),
            encoding="utf-8",
        )

        def fake_codex(argv: list[str]) -> subprocess.CompletedProcess[str]:
            if argv == ["codex", "plugin", "marketplace", "list", "--json"]:
                payload = {
                    "marketplaces": [
                        {
                            "name": "cognitive-powers",
                            "root": str(installed),
                            "marketplaceSource": {
                                "source": "https://github.com/Drizzy07x/cognitive-powers.git"
                            },
                        }
                    ]
                }
            elif argv == ["codex", "plugin", "list", "--json"]:
                payload = {
                    "installed": [
                        {
                            "name": "cognitive-powers",
                            "pluginId": "cognitive-powers@cognitive-powers",
                            "installed": True,
                            "enabled": True,
                            "version": "1.6.0",
                        }
                    ]
                }
            else:
                return subprocess.CompletedProcess(argv, 64, "", "unexpected command")
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

        previous = {
            name: os.environ.get(name) for name in ("CODEX_HOME", "HOME", "USERPROFILE")
        }
        try:
            for name in previous:
                os.environ[name] = str(isolated_home)
            report, code = verifier.verify_installation(
                source, installed, TAG, run=fake_codex
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
        parsed = json.loads(encoded)
        invariants = (
            code == 0,
            parsed.get("matched") is True,
            parsed.get("commit") == commit,
            parsed.get("readOnly") is True,
            parsed.get("content", {}).get("unexpectedExtras") == [],
            parsed.get("surface", {}).get("exposedSkills")
            == ["execute-durably", "solve-efficiently", "verify-delivery"],
            parsed.get("inventory", {}).get("marketplaceCount") == 1,
            parsed.get("inventory", {}).get("installationCount") == 1,
        )
        if not all(invariants):
            print(encoded)
            return 1
        print(
            json.dumps(
                {
                    "matched": True,
                    "tag": TAG,
                    "commit": commit,
                    "isolatedHome": True,
                },
                sort_keys=True,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
