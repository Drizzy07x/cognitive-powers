"""Every skill CLI documents a JSON error shape; a bad file must not traceback.

These entry points are invoked by the model, so a Python traceback is both an
undocumented result and one a caller cannot parse. A file that is not valid
UTF-8 raises ``UnicodeDecodeError``, which is a ``ValueError`` and not a
``json.JSONDecodeError``, so handlers that list only the latter let it escape.

The gate is derived from the shipped tree rather than hand-listed. The previous
hand-maintained list had drifted to 11 of the 24 CLIs, and four of the entry
points it no longer named did traceback: a list that cannot notice a new or
renamed script is not a gate. Every ``skills/*/scripts/*.py`` must appear in one
of the three tables below or ``test_every_shipped_skill_cli_is_classified``
fails.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]

CORRUPT_BYTES = b'{"kind": "progress", "note": "\xff\xfe bad"}'

# Substituted per invocation with the non-UTF-8 file and the directory holding it.
INPUT_FILE = "<input-file>"
INPUT_ROOT = "<input-root>"

# Entry points given a caller-supplied path to a file they are asked to parse.
# An unreadable input must reach the documented error shape, so it must neither
# traceback nor report success.
FILE_INPUTS: dict[str, list[list[str]]] = {
    "skills/audit-capabilities/scripts/capability_audit.py": [
        ["assess", "--input", INPUT_FILE],
    ],
    "skills/audit-capabilities/scripts/capability_lifecycle.py": [
        ["--input", INPUT_FILE],
    ],
    "skills/communicate-efficiently/scripts/communication_contract.py": [
        ["select", "--input", INPUT_FILE],
    ],
    "skills/design-intentionally/scripts/design_evidence.py": [
        [
            "create",
            "--workspace-root",
            INPUT_ROOT,
            "--intent",
            INPUT_FILE,
            "--browser-receipt",
            INPUT_FILE,
            "--review",
            INPUT_FILE,
            "--artifact-dir",
            INPUT_ROOT,
        ],
    ],
    "skills/design-intentionally/scripts/design_intent.py": [
        ["create", "--input", INPUT_FILE],
    ],
    "skills/diagnose-systematically/scripts/investigation_protocol.py": [
        ["--input", INPUT_FILE, "route"],
    ],
    "skills/engineer-prompts/scripts/prompt_contract.py": [
        ["validate", INPUT_FILE],
    ],
    "skills/execute-durably/scripts/coordination_report.py": [
        ["--state", INPUT_FILE],
    ],
    "skills/execute-durably/scripts/plan_compiler.py": [
        ["--input", INPUT_FILE],
    ],
    "skills/execute-durably/scripts/work_state.py": [
        ["plan-packets", "--session", "cli-error-shape", "--plan", INPUT_FILE],
    ],
    "skills/explore-web-adaptively/scripts/skyvern_evidence.py": [
        ["ingest", "--response", INPUT_FILE],
        ["handoff", "--receipt", INPUT_FILE],
    ],
    "skills/operate-desktop-adaptively/scripts/qcu_evidence.py": [
        ["normalize", "--transcript", INPUT_FILE],
    ],
    "skills/research-systematically/scripts/research_protocol.py": [
        ["--input", INPUT_FILE, "preregister"],
    ],
    "skills/solve-efficiently/scripts/orchestration_policy.py": [
        ["--input", INPUT_FILE],
    ],
    "skills/verify-delivery/scripts/knowledge_closeout.py": [
        ["--input", INPUT_FILE],
    ],
    "skills/verify-delivery/scripts/review_protocol.py": [
        ["--input", INPUT_FILE, "select"],
    ],
}

# CLIs whose only input is a workspace root. The corrupt bytes are planted under
# the name each one reads from that root. Skipping a file it was never told to
# parse is a defensible answer here, so only the traceback is gated.
ROOT_INPUTS: dict[str, tuple[str, list[str]]] = {
    "skills/design-intentionally/scripts/frontend_performance.py": (
        "package.json",
        ["--root", INPUT_ROOT],
    ),
    "skills/solve-efficiently/scripts/context_lens.py": (
        "app.js",
        [INPUT_ROOT, "--query", "retry"],
    ),
    "skills/solve-efficiently/scripts/semantic_context.py": (
        "app.js",
        ["--root", INPUT_ROOT, "explore", "--query", "retry"],
    ),
    "skills/solve-efficiently/scripts/semantic_provider.py": (
        "app.js",
        ["--root", INPUT_ROOT, "search", "--query", "retry"],
    ),
    "skills/use-current-docs/scripts/context7_lookup.py": (
        "package.json",
        ["--root", INPUT_ROOT, "dependencies"],
    ),
    "skills/verify-web-behavior/scripts/browser_evidence.py": (
        "playwright.config.ts",
        ["--root", INPUT_ROOT, "probe"],
    ),
}

# Importable modules with no entry point of their own; nothing runs them as a
# process, so they publish no error shape to gate.
NO_ENTRY_POINT: dict[str, str] = {
    "skills/solve-efficiently/scripts/context_pipeline.py": "library module",
    "skills/solve-efficiently/scripts/memory_context.py": "library module",
}


def _shipped_skill_clis() -> set[str]:
    return {
        path.relative_to(PLUGIN_ROOT).as_posix()
        for path in PLUGIN_ROOT.glob("skills/*/scripts/*.py")
    }


def _run(
    script: str, template: list[str], corrupt: Path
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(PLUGIN_ROOT / script)]
    for part in template:
        if part == INPUT_FILE:
            argv.append(str(corrupt))
        elif part == INPUT_ROOT:
            argv.append(str(corrupt.parent))
        else:
            argv.append(part)
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(PLUGIN_ROOT),
        check=False,
    )


class SkillCliErrorShapeTests(unittest.TestCase):
    def test_every_shipped_skill_cli_is_classified(self) -> None:
        classified = set(FILE_INPUTS) | set(ROOT_INPUTS) | set(NO_ENTRY_POINT)
        shipped = _shipped_skill_clis()
        self.assertEqual(
            sorted(shipped - classified),
            [],
            "a shipped skill CLI is outside the traceback gate",
        )
        self.assertEqual(
            sorted(classified - shipped),
            [],
            "the gate names a script that is no longer shipped",
        )

    def test_a_non_utf8_input_reports_instead_of_raising(self) -> None:
        for script, templates in sorted(FILE_INPUTS.items()):
            for template in templates:
                with (
                    self.subTest(script=script, subcommand=template[0]),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    corrupt = Path(temporary) / "input.json"
                    corrupt.write_bytes(CORRUPT_BYTES)
                    completed = _run(script, template, corrupt)
                    self.assertNotIn("Traceback", completed.stderr or "", script)
                    self.assertNotEqual(
                        completed.returncode,
                        0,
                        "an unreadable input must not read as success",
                    )

    def test_a_non_utf8_file_under_the_workspace_root_does_not_raise(self) -> None:
        for script, (name, template) in sorted(ROOT_INPUTS.items()):
            with (
                self.subTest(script=script),
                tempfile.TemporaryDirectory() as temporary,
            ):
                corrupt = Path(temporary) / name
                corrupt.write_bytes(CORRUPT_BYTES)
                completed = _run(script, template, corrupt)
                self.assertNotIn("Traceback", completed.stderr or "", script)


if __name__ == "__main__":
    unittest.main()
