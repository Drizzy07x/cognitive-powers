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
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Callable

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK_SCRIPT = PLUGIN_ROOT / "hooks" / "selective_hooks.py"
INDEX_SCRIPT = PLUGIN_ROOT / "hooks" / "semantic_index.py"
WORK_STATE = PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "work_state.py"
MINIMUM_PYTHON = (3, 11)
SUBPROCESS_TIMEOUT_SECONDS = 120.0
PLUGIN_NAME = "cognitive-powers"
RELEASE_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# Optional providers are resolved the way the skills that use them resolve
# them. Absence is a supported configuration, never a failure.


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


# Every other check here observes the tree it was started from, and neither host
# names that tree in the plugin's own files, so a healthy report said nothing
# about whether the host loads this copy at all. It said so while Claude Code
# held 1.7.4 active with a complete 1.8.1 beside it in the cache: nine checks
# green, three workflows unreachable, and the only visible symptom a short skill
# listing no script beside the host can read. Each host is reconstructed from its
# own resolution rule, because they differ. Claude Code pins a version in
# installed_plugins.json and keeps every version it ever fetched, so a newer
# cached copy is one it fetched and never activated. Codex records no version at
# all -- config.toml carries only `enabled` -- and resolves the highest version
# directory in its cache, so that drift resolves itself there and what remains is
# a root that is not the version Codex would load.


class _RecordProblem(Exception):
    """A host record that cannot support a verdict, carrying the verdict to give."""

    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _claude_config_dir() -> Path:
    # CLAUDE_CONFIG_DIR relocates the whole profile, records included. Reading
    # ~/.claude unconditionally would report "nothing installed" on a host that
    # has an installation, which is the reassuring half of the wrong answer.
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".claude"


def _codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    return Path(override).expanduser() if override else Path.home() / ".codex"


def _release_key(text: str) -> tuple[int, int, int] | None:
    matched = RELEASE_VERSION.match(text)
    return (int(matched[1]), int(matched[2]), int(matched[3])) if matched else None


def _marketplace(plugin_id: str) -> str:
    return plugin_id.partition("@")[2] or plugin_id


def _inside(child: Path, parent: Path) -> bool:
    # normcase because the two paths reach here from different sources -- one
    # built from Path.home(), one from __file__ -- and Windows spells the same
    # directory either way.
    try:
        resolved = Path(os.path.normcase(str(child.resolve())))
        root = Path(os.path.normcase(str(parent.resolve())))
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def _declared_version(manifest: Path) -> str | None:
    try:
        declared = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return declared if isinstance(declared, str) and declared else None


def _cached_versions(cache: Path) -> list[str]:
    """Releases of this plugin the host keeps on disk, oldest first.

    A directory that is not an X.Y.Z release is not a version either host can
    resolve to, so it cannot be the newer copy this check is looking for.
    """
    try:
        entries = [entry.name for entry in cache.iterdir() if entry.is_dir()]
    except OSError:
        return []
    keyed = [(key, name) for name in entries if (key := _release_key(name))]
    return [name for _, name in sorted(keyed)]


def _load_record(
    record: Path, parse: Callable[[str], Any], host: str
) -> dict[str, Any]:
    if not record.is_file():
        raise _RecordProblem(
            "skipped",
            f"no {host} installation record at {record}; a staged tree and CI "
            "have none, and that is a supported configuration",
        )
    try:
        document = parse(record.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        # A record that cannot be read is not one that is absent. Skipping here
        # would answer a corrupt host exactly as it answers an unconfigured one.
        raise _RecordProblem("fail", f"cannot read {record}: {error}") from error
    if not isinstance(document, dict):
        raise _RecordProblem("fail", f"{record} does not hold a mapping")
    return document


def _sole_entry(table: Any, record: Path, host: str) -> tuple[str, Any]:
    """The one recorded installation of this plugin, whatever marketplace named it."""
    if not isinstance(table, dict):
        raise _RecordProblem(
            "skipped", f"{host} records no installed plugins in {record}"
        )
    entries = sorted(
        ((key, value) for key, value in table.items() if _plugin_id(key)),
        key=lambda entry: entry[0],
    )
    if not entries:
        raise _RecordProblem(
            "skipped", f"{host} records no {PLUGIN_NAME} installation in {record}"
        )
    if len(entries) > 1:
        named = ", ".join(key for key, _ in entries)
        raise _RecordProblem(
            "fail",
            f"{record} records {len(entries)} {PLUGIN_NAME} installations "
            f"({named}), so which one the host loads is not resolvable",
        )
    return entries[0]


def _plugin_id(key: str) -> bool:
    return isinstance(key, str) and key.partition("@")[0] == PLUGIN_NAME


def _claude_activated_version(installations: Any, plugin_id: str, record: Path) -> str:
    entries = installations if isinstance(installations, list) else []
    if len(entries) != 1 or not isinstance(entries[0], dict):
        raise _RecordProblem(
            "fail", f"{record} does not record exactly one activation of {plugin_id}"
        )
    version = entries[0].get("version")
    if not isinstance(version, str) or not RELEASE_VERSION.match(version):
        raise _RecordProblem(
            "fail",
            f"{record} names {version!r} as the active version of {plugin_id}, "
            "which is not a release this plugin publishes",
        )
    return version


def _refuse_newer_cached_copy(activated: str, cache: Path) -> None:
    """Claude Code pins a version, so a newer cached copy is one it never loaded."""
    active = _release_key(activated)
    newer = [
        version for version in _cached_versions(cache) if _release_key(version) > active
    ]
    if newer:
        raise _RecordProblem(
            "fail",
            f"Claude Code activated {activated}, but {newer[-1]} is already "
            f"complete at {cache / newer[-1]} and was never activated; anything "
            f"added after {activated} is unreachable until the host loads it",
        )


def _refuse_unloaded_root(declared: str | None, activated: str, host: str) -> None:
    """A root inside the host's own cache has to be the version that host loads."""
    if declared is None:
        raise _RecordProblem(
            "fail",
            f"this root sits in the {host} plugin cache and declares no version, "
            "so nothing here can be matched to what the host loads",
        )
    if declared != activated:
        raise _RecordProblem(
            "fail",
            f"this root declares {declared} but {host} activated {activated}, so "
            "every other check here describes a tree the host does not load",
        )


def check_claude_code_activation(config_dir: Path, plugin_root: Path) -> CheckResult:
    """Report the version Claude Code activated beside the root being checked."""
    record = config_dir / "plugins" / "installed_plugins.json"
    cache_root = config_dir / "plugins" / "cache"
    declared = _declared_version(plugin_root / ".claude-plugin" / "plugin.json")
    context = {
        "record": str(record),
        "checkedRoot": str(plugin_root),
        "checkedVersion": declared,
    }
    try:
        document = _load_record(record, json.loads, "Claude Code")
        plugin_id, installations = _sole_entry(
            document.get("plugins"), record, "Claude Code"
        )
        activated = _claude_activated_version(installations, plugin_id, record)
        _refuse_newer_cached_copy(
            activated, cache_root / _marketplace(plugin_id) / PLUGIN_NAME
        )
        if _inside(plugin_root, cache_root):
            _refuse_unloaded_root(declared, activated, "Claude Code")
    except _RecordProblem as problem:
        return CheckResult(
            "host.claude_code", problem.status, problem.detail, **context
        )
    return CheckResult(
        "host.claude_code",
        "pass",
        f"Claude Code activated {activated} and has nothing newer cached",
        activatedVersion=activated,
        **context,
    )


def check_codex_activation(codex_home: Path, plugin_root: Path) -> CheckResult:
    """Report the version Codex would load beside the root being checked."""
    record = codex_home / "config.toml"
    cache_root = codex_home / "plugins" / "cache"
    declared = _declared_version(plugin_root / ".codex-plugin" / "plugin.json")
    context = {
        "record": str(record),
        "checkedRoot": str(plugin_root),
        "checkedVersion": declared,
    }
    try:
        document = _load_record(record, tomllib.loads, "Codex")
        plugin_id, settings = _sole_entry(document.get("plugins"), record, "Codex")
        if not isinstance(settings, dict) or settings.get("enabled") is not True:
            raise _RecordProblem(
                "fail",
                f"{record} installs {plugin_id} and leaves it disabled, so "
                "nothing it ships is reachable whatever this tree contains",
            )
        cache = cache_root / _marketplace(plugin_id) / PLUGIN_NAME
        cached = _cached_versions(cache)
        if not cached:
            raise _RecordProblem(
                "fail", f"{record} enables {plugin_id} but {cache} holds no version"
            )
        # config.toml carries no version, and the CLI reports the highest version
        # directory here -- observed against codex-cli 0.145.0, which answered
        # 9.9.10 over 9.9.9 with both cached and the marketplace still on 9.9.9.
        activated = cached[-1]
        if _inside(plugin_root, cache_root):
            _refuse_unloaded_root(declared, activated, "Codex")
    except _RecordProblem as problem:
        return CheckResult("host.codex", problem.status, problem.detail, **context)
    return CheckResult(
        "host.codex",
        "pass",
        f"Codex loads {activated}, the newest version it has cached",
        activatedVersion=activated,
        **context,
    )


def check_host_activation() -> list[CheckResult]:
    """Both hosts: an installation that drifts on one of them is still drifting."""
    return [
        check_claude_code_activation(_claude_config_dir(), PLUGIN_ROOT),
        check_codex_activation(_codex_home(), PLUGIN_ROOT),
    ]


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


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _resolve_documentation_cli() -> str | None:
    """Resolve the docs CLI the way use-current-docs actually does.

    Probing a single executable name would report the provider absent on a
    machine where the skill resolves it through another launcher, which turns
    this observation back into a declaration.
    """
    direct = _which("ctx7", "ctx7.cmd")
    if direct:
        return direct
    launcher = _which("npx", "npx.cmd", "pnpm", "pnpm.cmd")
    return f"{launcher} (launcher for ctx7)" if launcher else None


def check_optional_providers() -> list[CheckResult]:
    """Report provider availability without treating absence as a fault."""
    resolvers = {
        "graphify": lambda: _which("graphify", "graphify.exe"),
        "codegraph": lambda: (
            os.environ.get("CODEGRAPH_EXECUTABLE")
            or _which("codegraph", "codegraph.exe")
        ),
        "documentation": _resolve_documentation_cli,
    }
    results = []
    for name, resolve in resolvers.items():
        location = resolve()
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
    # Ahead of everything that runs, because every result below describes this
    # root, and whether the host loads this root is what makes them relevant.
    checks.extend(check_host_activation())
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
        # The root alone does not say which version was checked, and a report
        # that cannot be compared to a host record is the state this check exists
        # to end.
        "declaredVersion": _declared_version(
            PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
        )
        or _declared_version(PLUGIN_ROOT / ".codex-plugin" / "plugin.json"),
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
        version = report.get("declaredVersion") or "unknown version"
        print(f"root: {report.get('pluginRoot')} ({version})\n")
        for check in report.get("checks", []):
            print(f"{check['status']:8} {check['name']}: {check['detail']}")
        print(f"\npassed: {report['passed']}")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
