"""Every skill CLI documents a JSON error shape; a bad file must not traceback.

These entry points are invoked by the model, so a Python traceback is both an
undocumented result and one a caller cannot parse. A file that is not valid
UTF-8 raises ``UnicodeDecodeError``, which is a ``ValueError`` and not a
``json.JSONDecodeError``, so handlers that list only the latter let it escape.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# (label, argv after the interpreter, with the input path substituted for None)
INVOCATIONS = [
    (
        "research_protocol",
        [
            "skills/research-systematically/scripts/research_protocol.py",
            "--input",
            None,
            "preregister",
        ],
    ),
    (
        "knowledge_closeout",
        ["skills/verify-delivery/scripts/knowledge_closeout.py", "--input", None],
    ),
    (
        "capability_lifecycle",
        ["skills/audit-capabilities/scripts/capability_lifecycle.py", "--input", None],
    ),
    (
        "investigation_protocol",
        [
            "skills/diagnose-systematically/scripts/investigation_protocol.py",
            "--input",
            None,
            "route",
        ],
    ),
    (
        "prompt_contract",
        ["skills/engineer-prompts/scripts/prompt_contract.py", "validate", None],
    ),
    (
        "review_protocol",
        [
            "skills/verify-delivery/scripts/review_protocol.py",
            "--input",
            None,
            "select",
        ],
    ),
    (
        "coordination_report",
        ["skills/execute-durably/scripts/coordination_report.py", "--state", None],
    ),
    (
        "plan_compiler",
        ["skills/execute-durably/scripts/plan_compiler.py", "--input", None],
    ),
    (
        "capability_audit",
        [
            "skills/audit-capabilities/scripts/capability_audit.py",
            "assess",
            "--input",
            None,
        ],
    ),
    (
        "design_intent",
        [
            "skills/design-intentionally/scripts/design_intent.py",
            "create",
            "--input",
            None,
        ],
    ),
    (
        "communication_contract",
        [
            "skills/communicate-efficiently/scripts/communication_contract.py",
            "select",
            "--input",
            None,
        ],
    ),
]


class SkillCliErrorShapeTests(unittest.TestCase):
    def test_a_non_utf8_input_reports_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            corrupt = Path(temporary) / "input.json"
            corrupt.write_bytes(b'{"kind": "progress", "note": "\xff\xfe bad"}')
            for label, template in INVOCATIONS:
                argv = [sys.executable] + [
                    str(PLUGIN_ROOT / part)
                    if part and part.endswith(".py")
                    else (str(corrupt) if part is None else part)
                    for part in template
                ]
                with self.subTest(script=label):
                    completed = subprocess.run(
                        argv,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        cwd=str(PLUGIN_ROOT),
                        check=False,
                    )
                    self.assertNotIn("Traceback", completed.stderr or "", label)
                    self.assertNotEqual(
                        completed.returncode,
                        0,
                        "an unreadable input must not read as success",
                    )


if __name__ == "__main__":
    unittest.main()
