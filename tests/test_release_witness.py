from __future__ import annotations

import importlib.util
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "scripts" / "release_witness.py"
SPEC = importlib.util.spec_from_file_location("release_witness", MODULE_PATH)
witness = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(witness)

POLICY_PATH = PLUGIN_ROOT / "scripts" / "storage_policy.py"
POLICY_SPEC = importlib.util.spec_from_file_location(
    "storage_policy_for_witness_tests", POLICY_PATH
)
storage_policy = importlib.util.module_from_spec(POLICY_SPEC)
assert POLICY_SPEC.loader is not None
sys.modules[POLICY_SPEC.name] = storage_policy
POLICY_SPEC.loader.exec_module(storage_policy)


def git(root: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)


def make_repository(parent: Path) -> Path:
    root = parent / "plugin"
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "fixture", "version": "1.0.0"}), encoding="utf-8"
    )
    (root / "source.txt").write_text("bound source\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")
    git(root, "tag", "v1.0.0")
    return root


def passing_receipt(root: Path) -> dict:
    identity = witness.repository_identity(root)
    _, source_sha256 = witness.source_records(root)
    return {
        "schemaVersion": 1,
        "kind": "cognitive-powers-validation",
        "createdAt": "2026-07-21T00:00:00Z",
        "git": {
            "sha": identity["sha"],
            "dirty": False,
            "status": [],
            "initialSha": identity["sha"],
            "identityStable": True,
        },
        "source": {
            "sha256": source_sha256,
            "fileCount": 2,
            "algorithm": witness.SOURCE_IDENTITY_ALGORITHM,
            "initialSha256": source_sha256,
            "identityStable": True,
        },
        "commands": [
            {
                "name": name,
                "category": "offline",
                "command": ["python", *argv],
                "exitCode": 0,
                "passed": True,
                "durationSeconds": 0.1,
                "stdoutSha256": "0" * 64,
                "stderrSha256": "0" * 64,
            }
            for name, argv in witness.EXPECTED_OFFLINE_COMMANDS
        ],
        "offline": {
            "requested": True,
            "complete": True,
            "passed": True,
            "expectedCommands": len(witness.EXPECTED_OFFLINE_COMMANDS),
            "executedCommands": len(witness.EXPECTED_OFFLINE_COMMANDS),
        },
        "live": {
            "requested": False,
            "complete": False,
            "validated": False,
            "expectedCommands": 0,
            "executedCommands": 0,
        },
        "passed": True,
    }


class ReleaseWitnessTests(unittest.TestCase):
    def test_release_records_use_the_shared_generated_tree_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_repository(Path(temporary))
            before = witness.source_records(root)
            for name in ("node_modules", ".next", "homes", "runs", "storage"):
                directory = root / name
                directory.mkdir()
                (directory / "generated.bin").write_bytes(b"generated")

            after = witness.source_records(root)

        self.assertEqual(after, before)

    def test_witness_requires_bound_passing_offline_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = make_repository(parent)
            receipt = parent / "validation.json"
            receipt.write_text(json.dumps(passing_receipt(root)), encoding="utf-8")
            payload = witness.create_witness(root, [receipt])
            self.assertTrue(payload["releaseReady"])
            self.assertFalse(payload["liveIntegrationsValidated"])
            self.assertEqual(witness.verify_witness(root, payload), [])

    def test_unvalidated_witness_is_not_release_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_repository(Path(temporary))
            payload = witness.create_witness(root, [])
        self.assertFalse(payload["releaseReady"])

    def test_dirty_tree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = make_repository(parent)
            receipt = parent / "validation.json"
            receipt.write_text(json.dumps(passing_receipt(root)), encoding="utf-8")
            (root / "source.txt").write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(witness.WitnessError, "clean Git worktree"):
                witness.create_witness(root, [receipt])

    def test_wrong_or_missing_release_tag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = make_repository(parent)
            receipt = parent / "validation.json"
            receipt.write_text(json.dumps(passing_receipt(root)), encoding="utf-8")
            git(root, "tag", "-d", "v1.0.0")
            git(root, "tag", "v9.9.9")
            with self.assertRaisesRegex(witness.WitnessError, "tagged exactly v1.0.0"):
                witness.create_witness(root, [receipt])

    def test_missing_sha_and_incomplete_results_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = make_repository(parent)
            value = passing_receipt(root)
            value["git"].pop("sha")
            receipt = parent / "validation.json"
            receipt.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(witness.WitnessError, "SHA is absent or stale"):
                witness.create_witness(root, [receipt])

            value = passing_receipt(root)
            value["offline"]["complete"] = False
            receipt.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(witness.WitnessError, "incomplete offline"):
                witness.create_witness(root, [receipt])

    def test_failed_command_cannot_be_relabelled_as_passing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = make_repository(parent)
            value = passing_receipt(root)
            value["commands"][0]["exitCode"] = 9
            receipt = parent / "validation.json"
            receipt.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(witness.WitnessError, "malformed command"):
                witness.create_witness(root, [receipt])

    def test_failed_live_command_cannot_be_hidden_by_top_level_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = make_repository(parent)
            value = passing_receipt(root)
            value["commands"].append(
                {
                    "name": "live-1",
                    "category": "live",
                    "command": ["provider", "probe"],
                    "exitCode": 7,
                    "passed": False,
                    "durationSeconds": 0.1,
                    "stdoutSha256": "0" * 64,
                    "stderrSha256": "0" * 64,
                }
            )
            value["live"] = {
                "requested": True,
                "complete": True,
                "validated": False,
                "expectedCommands": 1,
                "executedCommands": 1,
            }
            value["passed"] = True
            receipt = parent / "validation.json"
            receipt.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(witness.WitnessError, "live validation"):
                witness.create_witness(root, [receipt])

    def test_verify_revalidates_embedded_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = make_repository(parent)
            receipt = parent / "validation.json"
            receipt.write_text(json.dumps(passing_receipt(root)), encoding="utf-8")
            payload = witness.create_witness(root, [receipt])
            payload["validations"][0]["commands"] = []
            payload["validations"][0]["offline"]["passed"] = False
            payload["validations"][0]["source"]["sha256"] = "f" * 64
            errors = witness.verify_witness(root, payload)
            self.assertTrue(errors)
            self.assertTrue(any("source identity" in error for error in errors))

    def test_verify_rejects_tampered_header_flags_inventory_and_executable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = make_repository(parent)
            receipt = parent / "validation.json"
            receipt.write_text(json.dumps(passing_receipt(root)), encoding="utf-8")
            payload = witness.create_witness(root, [receipt])
            mutations = {
                "schema": lambda value: value.__setitem__("schemaVersion", 999),
                "timestamp": lambda value: value.__setitem__(
                    "createdAt", "not-a-timestamp"
                ),
                "plugin": lambda value: value.__setitem__("plugin", "other-plugin"),
                "version": lambda value: value.__setitem__("version", "0.0.0"),
                "git-dirty": lambda value: value["git"].__setitem__("dirty", True),
                "release-ready": lambda value: value.__setitem__("releaseReady", False),
                "live-validated": lambda value: value.__setitem__(
                    "liveIntegrationsValidated", True
                ),
                "file-inventory": lambda value: value["files"].pop(),
                "command-executable": lambda value: value["validations"][0]["commands"][
                    0
                ]["command"].__setitem__(0, "not-python"),
                "non-finite-duration": lambda value: value["validations"][0][
                    "commands"
                ][0].__setitem__("durationSeconds", float("nan")),
            }
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    changed = copy.deepcopy(payload)
                    mutate(changed)
                    self.assertTrue(witness.verify_witness(root, changed))

    def test_create_rejects_incoherent_offline_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = make_repository(parent)
            value = passing_receipt(root)
            value["commands"][0]["command"][0] = "not-python"
            receipt = parent / "validation.json"
            receipt.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(witness.WitnessError, "executable"):
                witness.create_witness(root, [receipt])

    def test_interpreter_is_recognised_in_either_platform_spelling(self) -> None:
        # Receipts are written on twelve machines and aggregated on one, so the
        # interpreter path in a receipt is spelled for the platform that wrote
        # it. Taking the basename with the reader's own flavour made every
        # Windows receipt unreadable on the Linux aggregator, which rejected a
        # release whose twelve cells had all passed.
        spellings = [
            r"C:\hostedtoolcache\windows\Python\3.11.9\x64\python.exe",
            "/opt/hostedtoolcache/Python/3.13.14/x64/bin/python3.13",
            "/usr/bin/python3",
        ]
        for spelling in spellings:
            with self.subTest(executable=spelling):
                self.assertTrue(witness._is_python_executable(spelling))
        self.assertFalse(witness._is_python_executable(r"C:\tools\not-python.exe"))
        self.assertFalse(witness._is_python_executable("/usr/bin/not-python"))

    def test_source_records_use_the_receipt_identity_scheme(self) -> None:
        # The witness refuses receipts whose algorithm is not the shared
        # normalized scheme, so its own digest must be computed under that
        # scheme. Hashing raw bytes agreed with receipts only while no tracked
        # file sat in the worktree with CRLF content; one such file and twelve
        # valid receipts were rejected as "absent or stale".
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "plugin"
            (root / ".codex-plugin").mkdir(parents=True)
            (root / ".codex-plugin" / "plugin.json").write_text(
                json.dumps({"name": "fixture", "version": "1.0.0"}),
                encoding="utf-8",
            )
            (root / "source.txt").write_text("bound source\n", encoding="utf-8")
            # The worktree spelling of a text file on a CRLF-translating
            # checkout: raw bytes differ from the committed LF form.
            (root / "notes.txt").write_bytes(b"first\r\nsecond\r\n")
            git(root, "init", "-q")
            git(root, "config", "user.name", "Fixture")
            git(root, "config", "user.email", "fixture@example.invalid")
            git(root, "add", ".")
            git(root, "commit", "-qm", "fixture")
            git(root, "tag", "v1.0.0")

            identity = storage_policy.source_identity(root)
            _, witnessed = witness.source_records(root)

            self.assertEqual(witnessed, identity["sha256"])

            value = passing_receipt(root)
            value["source"]["sha256"] = identity["sha256"]
            value["source"]["initialSha256"] = identity["sha256"]
            receipt = parent / "validation.json"
            receipt.write_text(json.dumps(value), encoding="utf-8")
            created = witness.create_witness(root, [receipt])
        self.assertTrue(created)

    def test_create_accepts_a_receipt_written_on_another_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = make_repository(parent)
            value = passing_receipt(root)
            foreign = r"C:\hostedtoolcache\windows\Python\3.11.9\x64\python.exe"
            for command in value["commands"]:
                command["command"][0] = foreign
            receipt = parent / "validation.json"
            receipt.write_text(json.dumps(value), encoding="utf-8")
            created = witness.create_witness(root, [receipt])
        self.assertTrue(created)

    def test_cli_arguments_remain_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = make_repository(parent)
            receipt = parent / "validation.json"
            output = parent / "witness.json"
            receipt.write_text(json.dumps(passing_receipt(root)), encoding="utf-8")
            exit_code = witness.main(
                [
                    "--root",
                    str(root),
                    "--receipt",
                    str(receipt),
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue(
                json.loads(output.read_text(encoding="utf-8"))["releaseReady"]
            )


if __name__ == "__main__":
    unittest.main()
