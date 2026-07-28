from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "create_compatibility_receipt.py"
IDENTITY = ROOT / "scripts" / "release_identity.py"


def load_module(path: Path = SCRIPT, name: str = "compatibility_receipt"):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def release_tag() -> str:
    """The tag under test is whatever this checkout declares, never a literal."""
    return load_module(IDENTITY, "release_identity_receipt_test").release_tag()


class CompatibilityReceiptTests(unittest.TestCase):
    def test_non_clean_scenario_requires_candidate_bound_real_evidence(self) -> None:
        module = load_module()
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            validation = root / "validation.json"
            installation = root / "installation.json"
            evidence = root / "evidence.json"
            validation.write_text(
                json.dumps(
                    {
                        "kind": "cognitive-powers-validation",
                        "passed": True,
                        "git": {"sha": commit, "dirty": False, "identityStable": True},
                    }
                ),
                encoding="utf-8",
            )
            installation.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "product": "cognitive-powers",
                        "commit": commit,
                        "tag": release_tag(),
                        "matched": True,
                        "readOnly": True,
                    }
                ),
                encoding="utf-8",
            )
            evidence.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "product": "cognitive-powers",
                        "candidateCommit": commit,
                        "candidateTag": release_tag(),
                        "scenarios": {
                            "rollback": {
                                "passed": True,
                                "finalTag": "v1.5.2",
                                "finalCommit": "b" * 40,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(module.ReceiptError, "scenario evidence"):
                module.create_receipt(
                    validation,
                    installation,
                    os_name="windows-latest",
                    python="3.11",
                    codex_cli_version="codex-cli 0.145.0",
                    scenario="rollback",
                    commit=commit,
                    workflow="validate.yml",
                    run_id="123",
                    run_attempt=1,
                )
            receipt = module.create_receipt(
                validation,
                installation,
                scenario_evidence_path=evidence,
                os_name="windows-latest",
                python="3.11",
                codex_cli_version="codex-cli 0.145.0",
                scenario="rollback",
                commit=commit,
                workflow="validate.yml",
                run_id="123",
                run_attempt=1,
            )
            self.assertEqual(receipt["scenarioEvidence"]["finalTag"], "v1.5.2")
            self.assertRegex(
                receipt["scenarioEvidence"]["reportSha256"], r"^[0-9a-f]{64}$"
            )

    def test_receipt_requires_and_binds_a_passing_real_installation_report(
        self,
    ) -> None:
        module = load_module()
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            validation = root / "validation.json"
            installation = root / "installation.json"
            validation.write_text(
                json.dumps(
                    {
                        "kind": "cognitive-powers-validation",
                        "passed": True,
                        "git": {
                            "sha": commit,
                            "dirty": False,
                            "identityStable": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            installation.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "product": "cognitive-powers",
                        "commit": commit,
                        "tag": release_tag(),
                        "matched": True,
                        "readOnly": True,
                    }
                ),
                encoding="utf-8",
            )

            receipt = module.create_receipt(
                validation,
                installation,
                os_name="windows-latest",
                python="3.11",
                codex_cli_version="codex-cli 0.145.0",
                scenario="clean-install",
                commit=commit,
                workflow="validate.yml",
                run_id="123",
                run_attempt=1,
            )

            self.assertEqual(receipt["scenario"], "clean-install")
            self.assertEqual(receipt["installation"]["commit"], commit)
            self.assertRegex(receipt["installation"]["reportSha256"], r"^[0-9a-f]{64}$")
            self.assertFalse(receipt["attestation"]["verified"])

            document = json.loads(installation.read_text(encoding="utf-8"))
            document["matched"] = False
            installation.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(module.ReceiptError, "installation report"):
                module.create_receipt(
                    validation,
                    installation,
                    os_name="windows-latest",
                    python="3.11",
                    codex_cli_version="codex-cli 0.145.0",
                    scenario="clean-install",
                    commit=commit,
                    workflow="validate.yml",
                    run_id="123",
                    run_attempt=1,
                )


if __name__ == "__main__":
    unittest.main()
