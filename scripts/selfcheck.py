#!/usr/bin/env python3
"""Observe whether an installed Cognitive Powers actually runs on this host.

``doctor.py`` inspects packaging on disk. That is necessary and not sufficient:
a manifest can be perfectly well formed while the interpreter never resolves,
the hooks never fire, or two components disagree about where evidence lives.
Every check here runs something and reports what happened, so a passing result
means observed behaviour rather than a well-formed declaration.

Checks are read-only with respect to the target repository. Anything that needs
to write does so in a temporary directory that is removed afterwards.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK_SCRIPT = PLUGIN_ROOT / "hooks" / "selective_hooks.py"
INDEX_SCRIPT = PLUGIN_ROOT / "hooks" / "semantic_index.py"
WORK_STATE = PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "work_state.py"
MINIMUM_PYTHON = (3, 11)
SUBPROCESS_TIMEOUT_SECONDS = 120.0

# Optional providers. Absence is a supported configuration, never a failure.
OPTIONAL_PROVIDERS = ("graphify", "codegraph", "npx")


class CheckResult(dict):
    """A single observation: name, status, and what was actually seen."""

    def __init__(self, name: str, status: str, detail: str, **extra: Any) -> None:
        super().__init__(name=name, status=status, detail=detail, **extra)


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        # The children emit UTF-8 whatever the console codepage. Decoding with
        # the ANSI page raises on bytes that page leaves undefined, and a
        # diagnostic must not fail on the output it is reading.
        encoding="utf-8",
        errors="replace",
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
        **kwargs,
    )


def check_interpreter() -> CheckResult:
    version = tuple(sys.version_info[:2])
    if version < MINIMUM_PYTHON:
        return CheckResult(
            "interpreter",
            "fail",
            f"Python {version[0]}.{version[1]} is below the required "
            f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]}",
            executable=sys.executable,
        )
    return CheckResult(
        "interpreter",
        "pass",
        f"running Python {version[0]}.{version[1]}",
        executable=sys.executable,
    )


def check_hook_scripts_execute() -> list[CheckResult]:
    """Run the bundled hooks against synthetic host payloads.

    This is the check that distinguishes a shipped hook from a working one. It
    exercises the two events the manifest declares, in the same exec form the
    host uses, and asserts the observable effects: a ledger entry for an edit,
    and a warning shaped for the running host at stop.
    """
    results: list[CheckResult] = []
    if not HOOK_SCRIPT.is_file():
        return [CheckResult("hooks.present", "fail", f"missing {HOOK_SCRIPT}")]

    with tempfile.TemporaryDirectory() as repo_raw:
        with tempfile.TemporaryDirectory() as data_raw:
            repo = Path(repo_raw).resolve()
            data = Path(data_raw).resolve()
            target = repo / "module.py"
            target.write_text("value = 1\n", encoding="utf-8")
            environment = dict(os.environ)
            environment["COGNITIVE_POWERS_DATA"] = str(data)
            # Claude Code exports this to hook processes only, and selfcheck is
            # an ordinary tool call, so reading it from our own environment
            # would leave the Claude-shaped assertion below permanently
            # unevaluated. Set it deliberately to exercise that path.
            environment["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)

            post = _run(
                [sys.executable, str(HOOK_SCRIPT), "post-tool-use"],
                input=json.dumps(
                    {
                        "session_id": "cognitive-powers-selfcheck",
                        "cwd": str(repo),
                        "hook_event_name": "PostToolUse",
                        "tool_name": "Write",
                        "tool_input": {"file_path": str(target)},
                    }
                ),
                env=environment,
            )
            ledgers = list((data / "hooks" / "events").glob("*.jsonl"))
            if post.returncode != 0:
                results.append(
                    CheckResult(
                        "hooks.post_tool_use",
                        "fail",
                        f"exited {post.returncode}: {post.stderr.strip()[:200]}",
                    )
                )
            elif not ledgers:
                results.append(
                    CheckResult(
                        "hooks.post_tool_use",
                        "fail",
                        "the hook ran but recorded no edit event",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        "hooks.post_tool_use",
                        "pass",
                        "an edit was recorded in the session ledger",
                    )
                )

            stop = _run(
                [sys.executable, str(HOOK_SCRIPT), "stop"],
                input=json.dumps(
                    {
                        "session_id": "cognitive-powers-selfcheck",
                        "cwd": str(repo),
                        "hook_event_name": "Stop",
                    }
                ),
                env=environment,
            )
            if stop.returncode != 0:
                results.append(
                    CheckResult(
                        "hooks.stop",
                        "fail",
                        f"exited {stop.returncode}: {stop.stderr.strip()[:200]}",
                    )
                )
            else:
                try:
                    payload = json.loads(stop.stdout or "{}")
                except json.JSONDecodeError:
                    payload = {}
                if "systemMessage" not in payload:
                    results.append(
                        CheckResult(
                            "hooks.stop",
                            "fail",
                            "an uncovered edit produced no warning",
                        )
                    )
                elif "hookSpecificOutput" not in payload:
                    results.append(
                        CheckResult(
                            "hooks.stop",
                            "fail",
                            "the warning reaches the user but not the agent",
                        )
                    )
                else:
                    results.append(
                        CheckResult(
                            "hooks.stop",
                            "pass",
                            "an uncovered edit produced a warning and exit 0",
                        )
                    )
    return results


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_one_data_root() -> CheckResult:
    """The Stop gate only accepts a receipt under the root the hook resolved.

    The two run in different processes, so a disagreement rejects work that is
    genuinely complete.
    """
    try:
        hook = _load("selfcheck_hook", HOOK_SCRIPT)
        durability = _load(
            "selfcheck_durability",
            WORK_STATE.parent / "work_state_core" / "durability.py",
        )
    except (RuntimeError, OSError, ImportError, SyntaxError) as error:
        return CheckResult("evidence.shared_root", "fail", str(error))

    roots = hook._roots()
    if roots is None:
        return CheckResult(
            "evidence.shared_root",
            "fail",
            "the hook refuses its resolved data root; it may sit inside the plugin",
        )
    hook_root = roots[1]
    writer_root = durability.resolve_data_root(None)
    if hook_root != writer_root:
        return CheckResult(
            "evidence.shared_root",
            "fail",
            f"the hook uses {hook_root} but the receipt writer uses {writer_root}",
        )
    return CheckResult(
        "evidence.shared_root", "pass", f"both resolve {hook_root}", root=str(hook_root)
    )


def check_durable_round_trip() -> CheckResult:
    """Initialise a throwaway session and record one real command receipt."""
    if not WORK_STATE.is_file():
        return CheckResult("evidence.round_trip", "fail", f"missing {WORK_STATE}")
    with tempfile.TemporaryDirectory() as repo_raw:
        with tempfile.TemporaryDirectory() as data_raw:
            repo = Path(repo_raw).resolve()
            data = Path(data_raw).resolve()
            (repo / "module.py").write_text("value = 1\n", encoding="utf-8")
            environment = dict(os.environ)
            environment["COGNITIVE_POWERS_DATA"] = str(data)
            common = [sys.executable, str(WORK_STATE), "--root", str(repo)]
            init = _run(
                common
                + [
                    "init",
                    "--session",
                    "selfcheck",
                    "--objective",
                    "verify the installation responds",
                    "--criterion",
                    "c1 the toolchain records a receipt",
                ],
                env=environment,
            )
            if init.returncode != 0:
                return CheckResult(
                    "evidence.round_trip",
                    "fail",
                    f"init exited {init.returncode}: {init.stderr.strip()[:200]}",
                )
            recorded = _run(
                common
                + [
                    "run",
                    "--session",
                    "selfcheck",
                    "--criterion",
                    "c1",
                    "--executor",
                    "selfcheck",
                    "--json",
                    "--",
                    sys.executable,
                    "-c",
                    "print('ok')",
                ],
                env=environment,
            )
            if recorded.returncode != 0:
                return CheckResult(
                    "evidence.round_trip",
                    "fail",
                    f"run exited {recorded.returncode}: "
                    f"{recorded.stderr.strip()[:200]}",
                )
            receipts = list(data.glob("projects/*/sessions/*/evidence/**/*.json"))
            if not receipts:
                return CheckResult(
                    "evidence.round_trip",
                    "fail",
                    "the command succeeded but no receipt was stored",
                )
    return CheckResult(
        "evidence.round_trip", "pass", "a durable command receipt was written and found"
    )


def check_optional_providers() -> list[CheckResult]:
    """Report provider availability without treating absence as a fault."""
    results = []
    for name in OPTIONAL_PROVIDERS:
        location = shutil.which(name)
        results.append(
            CheckResult(
                f"provider.{name}",
                "pass" if location else "skipped",
                f"found at {location}" if location else "not installed (optional)",
            )
        )
    return results


def check_index_hook() -> CheckResult:
    """The session-start refresh must stay advisory: exit 0 whatever happens."""
    if not INDEX_SCRIPT.is_file():
        return CheckResult("hooks.session_start", "fail", f"missing {INDEX_SCRIPT}")
    # Drive the real startup path against a checkout with no index. A
    # "compact" source would short-circuit before any of the refresh logic ran,
    # so the check would pass without exercising what it claims to.
    with tempfile.TemporaryDirectory() as repo_raw:
        repo = Path(repo_raw).resolve()
        (repo / ".git").mkdir()
        completed = _run(
            [sys.executable, str(INDEX_SCRIPT), "session-start"],
            input=json.dumps({"cwd": str(repo), "source": "startup"}),
        )
        created = (repo / "graphify-out").exists()
    if completed.returncode != 0:
        return CheckResult(
            "hooks.session_start",
            "fail",
            f"an advisory hook exited {completed.returncode}",
        )
    if created:
        return CheckResult(
            "hooks.session_start",
            "fail",
            "the refresh hook created an index in a checkout that had none",
        )
    return CheckResult(
        "hooks.session_start", "pass", "the refresh hook is advisory and exits 0"
    )


def run_checks() -> dict[str, Any]:
    checks: list[CheckResult] = [check_interpreter()]
    checks.extend(check_hook_scripts_execute())
    checks.append(check_index_hook())
    checks.append(check_one_data_root())
    checks.append(check_durable_round_trip())
    checks.extend(check_optional_providers())

    failed = [check for check in checks if check["status"] == "fail"]
    return {
        "schema_version": 1,
        "kind": "cognitive_powers_selfcheck",
        "pluginRoot": str(PLUGIN_ROOT),
        "observed": True,
        "passed": not failed,
        "failedCount": len(failed),
        "checks": checks,
        # The model must supply what no script can see from here.
        "hostObservationsRequired": [
            "which cognitive-powers skills appear in the assistant's own skill listing",
            "whether the plugin agent roles are registered and invocable",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run_checks()
    except Exception as error:  # noqa: BLE001 - a diagnostic must still report
        report = {
            "schema_version": 1,
            "kind": "cognitive_powers_selfcheck",
            "observed": True,
            "passed": False,
            "error": f"{type(error).__name__}: {error}",
            "checks": [],
        }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for check in report.get("checks", []):
            print(f"{check['status']:8} {check['name']}: {check['detail']}")
        print(f"\npassed: {report['passed']}")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
