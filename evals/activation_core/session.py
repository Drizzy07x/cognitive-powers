#!/usr/bin/env python3
"""Run one prompt against the real host with this plugin installed.

Everything here was established by probing the shipped CLI rather than read
from documentation, so the reasons are recorded beside the flags:

* ``--plugin-dir`` loads the tree for one session and registers it under the
  source ``cognitive-powers@inline``. The ``pluginConfigs`` key that supplies
  ``python_executable`` is therefore ``cognitive-powers@inline``; without it
  every hook exits before running and all three arms collapse into one.
* ``--plugin-dir`` replaces the installed plugin set, so an operator's own
  installed copy cannot contribute a second catalogue to the session.
* ``CLAUDE_CONFIG_DIR`` is deliberately **not** redirected. Pointing it at an
  empty directory hides the credentials and every run ends at ``Not logged in``.
* There is no ``--max-turns``. Cost is bounded by a restricted tool set, a hard
  dollar ceiling, a wall-clock timeout, and stopping the process once the
  case's verdict can no longer change.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from .arms import ALL_TOGGLES, Arm
from .cases import Case
from .fixtures import materialize
from .transcript import SKILL_TOOL

PLUGIN_CONFIG_KEY = "cognitive-powers@inline"

# The model may read to decide, but it may not shell out, spawn agents, browse,
# or edit. Those are the expensive turns, and none of them changes which
# workflow the first assistant turn selects.
DEFAULT_TOOLS = ("Skill", "Read", "Glob", "Grep", "TodoWrite")

# Inherited state that would otherwise leak into the measurement. CLAUDECODE is
# set inside a Claude Code session and makes a nested run behave as a child;
# the toggles are the arm itself, and an operator who exported one would be
# running a different arm than the one they asked for.
SCRUBBED = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CONFIG_DIR", *ALL_TOGGLES)


# A rate limit is the provider asking for a pause, not the plugin failing to
# activate. The wording is matched narrowly and never against the raw stream:
# a healthy session emits `rate_limit_event` informationally, so a substring
# scan for "rate_limit" matched every run and "please try again" matched the
# model's own prose. Both were in the first version and both fired constantly.
RATE_LIMIT_SIGNS = (
    "rate limit",
    "429",
    "too many requests",
    "overloaded",
)
DEFAULT_ATTEMPTS = 5
DEFAULT_BACKOFF_SECONDS = 8.0
MAX_BACKOFF_SECONDS = 240.0


class Run(NamedTuple):
    """One invocation: what was asked, what came back, and what it cost."""

    case_id: str
    arm: str
    repetition: int
    stream: str
    stderr: str
    exit_code: int
    duration_seconds: float
    stopped_early: bool
    timed_out: bool
    attempts: int = 1
    rate_limited: bool = False


def _terminal_error_text(stream: str) -> str:
    """The failure text a terminal result carries, and nothing else.

    Reading the whole stream was the defect: every session emits
    `rate_limit_event` as ordinary telemetry, so any scan across it reported a
    throttled provider on runs that were completely healthy.
    """
    for line in reversed(stream.splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "result":
            continue
        parts = (
            event.get("result"),
            event.get("subtype"),
            event.get("api_error_status"),
            event.get("terminal_reason"),
        )
        return " ".join(str(part) for part in parts if isinstance(part, str))
    return ""


def looks_rate_limited(run: Run) -> bool:
    """Whether this run failed because the provider throttled it.

    Deliberately conjunctive, and the guards are load-bearing rather than
    defensive. A run the harness stopped on purpose exits non-zero *by our own
    hand*, so treating that exit code as evidence of anything about the provider
    turned every successful early stop into five retries with backoff -- the
    cheapest runs in the corpus became the most expensive, and a matrix that
    should have taken two hours was on track for thirteen.
    """
    if run.stopped_early or run.timed_out:
        return False
    if run.exit_code == 0:
        return False
    haystack = f"{run.stderr}\n{_terminal_error_text(run.stream)}".lower()
    return any(sign in haystack for sign in RATE_LIMIT_SIGNS)


def _settings(path: Path, python_executable: str) -> Path:
    payload = {
        "pluginConfigs": {
            PLUGIN_CONFIG_KEY: {"options": {"python_executable": python_executable}}
        }
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _environment(arm: Arm, data_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in SCRUBBED:
        env.pop(name, None)
    env.update(arm.env)
    # Durable state redirected per run. The default root is shared between
    # hosts on this machine by design, and an eval writing receipts into it
    # would leave the operator's own store carrying evidence of prompts nobody
    # asked for.
    env["COGNITIVE_POWERS_DATA"] = str(data_root)
    return env


def build_argv(
    case: Case,
    *,
    plugin_root: Path,
    settings_path: Path,
    model: str,
    max_cost_usd: float,
    tools: Iterable[str] = DEFAULT_TOOLS,
) -> list[str]:
    return [
        "claude",
        "-p",
        case.prompt,
        "--plugin-dir",
        str(plugin_root),
        "--settings",
        str(settings_path),
        "--setting-sources",
        "",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        "--model",
        model,
        "--permission-mode",
        "plan",
        "--no-session-persistence",
        "--max-budget-usd",
        str(max_cost_usd),
        "--tools",
        ",".join(tools),
    ]


def _terminate_tree(process: subprocess.Popen) -> None:
    """Kill the whole process tree, not just the process that was spawned.

    The host launches child processes, and killing only the parent leaves them
    running with the inherited stdout pipe still open -- so the read loop keeps
    blocking on a session that was already abandoned, and each stopped run
    leaks a descendant. Windows needs an external tree kill for this; POSIX has
    the process group the spawn already asked for.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        killpg = getattr(os, "killpg", None)
        getpgid = getattr(os, "getpgid", None)
        if killpg is not None and getpgid is not None:
            try:
                killpg(getpgid(process.pid), 9)
            except (OSError, ProcessLookupError):
                pass
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()


def _settled(collected: list[str], case: Case, installed: frozenset[str]) -> bool:
    """Whether the verdict can no longer change, so the process can be stopped.

    Only a satisfied should-fire expectation settles a run. Silence never
    does: a workflow the model has not invoked yet is not a workflow it will
    not invoke, and a probe that called the case at the first non-Skill tool
    would report the model's exploration as a refusal. That asymmetry is the
    whole cost model -- positives stop early and cheaply, negatives are paid in
    full, and the corpus has far more of the former.
    """
    if not case.expect:
        return False
    from .transcript import read  # local import keeps this module import-cheap

    reading = read("\n".join(collected), installed)
    if set(reading.fired) & set(case.forbid):
        return True
    return case.satisfied_by(reading.fired)


def run_case(
    case: Case,
    arm: Arm,
    repetition: int,
    *,
    plugin_root: Path,
    workspace_root: Path,
    python_executable: str,
    installed: frozenset[str],
    model: str = "sonnet",
    max_cost_usd: float = 0.75,
    timeout_seconds: float = 300.0,
    claude_executable: str = "claude",
    max_attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    sleep: Any = time.sleep,
) -> Run:
    """Run one repetition, waiting out a throttled provider rather than scoring it.

    A rate limit says nothing about whether the workflow would have activated,
    so treating one as a failed case would let a busy hour look like a
    regression. Retries are bounded: a run still throttled after the last
    attempt comes back marked, and the scorer records it as incomplete -- absent
    from the denominator rather than counted as a miss.
    """
    attempt = 1
    while True:
        run = _attempt(
            case,
            arm,
            repetition,
            plugin_root=plugin_root,
            workspace_root=workspace_root,
            python_executable=python_executable,
            installed=installed,
            model=model,
            max_cost_usd=max_cost_usd,
            timeout_seconds=timeout_seconds,
            claude_executable=claude_executable,
        )
        throttled = looks_rate_limited(run)
        if not throttled or attempt >= max_attempts:
            return run._replace(attempts=attempt, rate_limited=throttled)
        sleep(min(backoff_seconds * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS))
        attempt += 1


def _attempt(
    case: Case,
    arm: Arm,
    repetition: int,
    *,
    plugin_root: Path,
    workspace_root: Path,
    python_executable: str,
    installed: frozenset[str],
    model: str,
    max_cost_usd: float,
    timeout_seconds: float,
    claude_executable: str,
) -> Run:
    """One spawn of the host, with no opinion about retrying."""
    base = workspace_root / f"{case.case_id}-{arm.name}-{repetition}"
    workspace = materialize(case.fixture, base / "ws")
    settings_path = _settings(base / "settings.json", python_executable)
    env = _environment(arm, base / "data")

    argv = build_argv(
        case,
        plugin_root=plugin_root,
        settings_path=settings_path,
        model=model,
        max_cost_usd=max_cost_usd,
    )
    argv[0] = claude_executable

    started = time.monotonic()
    collected: list[str] = []
    stopped_early = False
    timed_out = False

    process = subprocess.Popen(
        argv,
        cwd=workspace,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        # A process group on POSIX so the whole tree can be signalled at once.
        # Windows Python accepts the argument and ignores it, which is why the
        # tree kill there goes through taskkill instead.
        start_new_session=os.name != "nt",
    )
    assert process.stdout is not None
    # The timeout has to come from outside the read loop. Checking the clock
    # after each line only bounds a run that is still producing lines, so a
    # session that stalls with its pipe open would block here forever and take
    # the whole matrix with it.
    expired = threading.Event()

    def _expire() -> None:
        expired.set()
        _terminate_tree(process)

    watchdog = threading.Timer(timeout_seconds, _expire)
    watchdog.daemon = True
    watchdog.start()
    try:
        for line in process.stdout:
            collected.append(line.rstrip("\n"))
            # Cheap pre-filter before the structural check, which re-reads the
            # whole stream. Only a line that mentions the Skill tool can change
            # the verdict, so a session that reads twenty files no longer pays
            # a full re-parse per line. The substring never decides anything --
            # it only decides whether to ask the reader.
            if SKILL_TOOL in line and _settled(collected, case, installed):
                stopped_early = True
                break
    finally:
        watchdog.cancel()
        timed_out = expired.is_set()
        if stopped_early or timed_out:
            _terminate_tree(process)
        stderr = process.stderr.read() if process.stderr is not None else ""
        process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        exit_code = process.wait()

    return Run(
        case_id=case.case_id,
        arm=arm.name,
        repetition=repetition,
        stream="\n".join(collected),
        stderr=stderr,
        exit_code=exit_code,
        duration_seconds=round(time.monotonic() - started, 3),
        stopped_early=stopped_early,
        timed_out=timed_out,
    )


def default_python() -> str:
    """The interpreter the plugin's hooks will be told to use."""
    return os.environ.get("COGNITIVE_POWERS_EVAL_PYTHON") or sys.executable
