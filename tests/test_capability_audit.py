from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "audit-capabilities" / "scripts" / "capability_audit.py"
)


def load_auditor():
    spec = importlib.util.spec_from_file_location(
        "test_capability_auditor", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


auditor = load_auditor()


def occurrence(event_id: str, observed_at: str, source: str | None = None):
    return {
        "event_id": event_id,
        "source": source or f"rollout_summaries/{event_id}.jsonl",
        "observed_at": observed_at,
    }


def pattern(**overrides):
    result = {
        "id": "release-check",
        "summary": "Repeat the release artifact publication checks",
        "candidate_name": "verify-release",
        "closest_skill": None,
        "occurrences": [
            occurrence("release-one", "2026-06-01"),
            occurrence("release-two", "2026-07-01"),
        ],
        "repository_paths": ["scripts/release_check.py"],
        "triggers": ["verify this release"],
        "workflow_steps": ["inspect artifacts", "run checks"],
        "validation_commands": ["py -3 scripts/release_check.py"],
    }
    result.update(overrides)
    return result


class CapabilityAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        skill = self.root / "skills" / "verify-delivery"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: verify-delivery\n"
            "description: Verify release and delivery claims with executable evidence.\n"
            "---\n",
            encoding="utf-8",
        )
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "release_check.py").write_text(
            "print('checked')\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def assess(self, *patterns):
        return auditor.assess(
            self.root,
            {"schema_version": 1, "patterns": list(patterns)},
            as_of=date(2026, 7, 20),
        )

    def test_declared_existing_home_forces_update(self) -> None:
        candidate = pattern(closest_skill="verify-delivery")

        result = self.assess(candidate)["recommendations"][0]

        self.assertEqual(result["action"], "update")
        self.assertEqual(result["closest_skill"], "verify-delivery")
        self.assertGreater(result["priority_score"], 0)

    def test_same_event_in_memory_and_rollout_is_not_recurrence(self) -> None:
        candidate = pattern(
            occurrences=[
                occurrence("same-task", "2026-07-01", "MEMORY.md"),
                occurrence("same-task", "2026-07-01", "rollout_summaries/task.jsonl"),
            ]
        )

        result = self.assess(candidate)["recommendations"][0]

        self.assertEqual(result["distinct_events"], 1)
        self.assertEqual(result["distinct_sources"], 2)
        self.assertEqual(result["action"], "reject")

    def test_stale_memory_with_missing_paths_is_rejected(self) -> None:
        candidate = pattern(
            occurrences=[
                occurrence("old-one", "2023-01-01"),
                occurrence("old-two", "2023-02-01"),
            ],
            repository_paths=["removed/release_check.py"],
        )

        result = self.assess(candidate)["recommendations"][0]

        self.assertEqual(result["action"], "reject")
        self.assertEqual(result["current_repository_paths"], [])
        self.assertEqual(
            result["missing_repository_paths"], ["removed/release_check.py"]
        )
        self.assertIn("stale", result["reasons"][0])

    def test_distinct_current_workflow_can_be_new(self) -> None:
        candidate = pattern(
            summary="Package translation catalogs for offline distribution",
            candidate_name="package-translations",
            repository_paths=["scripts/release_check.py"],
        )

        result = self.assess(candidate)["recommendations"][0]

        self.assertEqual(result["action"], "new")
        self.assertIsNone(result["likely_overlap"])

    def test_undeclared_likely_overlap_requires_review(self) -> None:
        candidate = pattern(
            summary="Verify release delivery evidence and claims",
            candidate_name="release-delivery-verifier",
        )

        result = self.assess(candidate)["recommendations"][0]

        self.assertEqual(result["action"], "review-overlap")
        self.assertEqual(result["likely_overlap"]["name"], "verify-delivery")

    def test_overlap_is_reachable_against_the_real_shipped_listings(self) -> None:
        """The synthetic skill above is one sentence; the shipped ones are not.

        Scored listings hold 33 to 81 terms against a candidate summary's six to
        ten, so dividing the shared terms by the union capped a perfect subset
        at roughly 6/53 -- under the 0.24 the caller compares against. Every
        pair of real shipped skills topped out at 0.1143, which means
        `review-overlap` could not fire for anything but a near-verbatim copy,
        and the only test covering it used a fixture where the two sizes
        happened to match. This one asks the tree that actually ships.
        """
        restatement = pattern(
            candidate_name="release-delivery-verifier",
            summary="Verify release delivery evidence and claims",
            repository_paths=["scripts/validate_all.py"],
        )
        unrelated = pattern(
            id="translations",
            candidate_name="package-translations",
            summary="Package translation catalogs for offline distribution",
            repository_paths=["scripts/validate_all.py"],
        )

        report = auditor.assess(
            PLUGIN_ROOT,
            {"schema_version": 1, "patterns": [restatement, unrelated]},
            as_of=date(2026, 7, 20),
        )
        by_id = {item["id"]: item for item in report["recommendations"]}

        self.assertEqual(
            "verify-delivery", by_id["release-check"]["likely_overlap"]["name"]
        )
        self.assertIsNone(by_id["translations"]["likely_overlap"])

    def test_future_evidence_is_invalid(self) -> None:
        candidate = pattern(
            occurrences=[
                occurrence("release-one", "2026-07-01"),
                occurrence("future", "2026-08-01"),
            ]
        )

        with self.assertRaisesRegex(auditor.AuditError, "future evidence"):
            self.assess(candidate)

    def test_invalid_pattern_shape_returns_a_contract_error(self) -> None:
        with self.assertRaisesRegex(
            auditor.AuditError, "every pattern must be an object"
        ):
            auditor.assess(
                self.root,
                {"schema_version": 1, "patterns": [["not", "an", "object"]]},
                as_of=date(2026, 7, 20),
            )

    def test_cli_returns_machine_readable_error(self) -> None:
        evidence = self.root / "invalid.json"
        evidence.write_text('{"schema_version": 2, "patterns": []}', encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "assess",
                "--root",
                str(self.root),
                "--evidence",
                str(evidence),
                "--as-of",
                "2026-07-20",
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("schema_version must be 1", json.loads(completed.stdout)["error"])

    def test_cli_accepts_evidence_from_stdin_without_a_packet_file(self) -> None:
        payload = {"schema_version": 1, "patterns": [pattern()]}

        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "assess",
                "--root",
                str(self.root),
                "--evidence",
                "-",
                "--as-of",
                "2026-07-20",
                "--json",
            ],
            input=json.dumps(payload),
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["recommendations"][0]["id"], "release-check")


class CapabilityInventoryTests(unittest.TestCase):
    """Both installed layouts count as existing coverage.

    A skill is a directory holding SKILL.md; a Claude Code slash command is a
    bare <name>.md. Globbing only for SKILL.md made every command location
    contribute nothing, so already-installed capabilities read as missing.
    """

    def test_slash_commands_are_inventoried_alongside_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = root / ".claude" / "commands"
            commands.mkdir(parents=True)
            (commands / "deploy.md").write_text(
                "---\nname: deploy\ndescription: ship it\n---\n\nbody\n",
                encoding="utf-8",
            )
            (commands / "review").mkdir()
            (commands / "review" / "security.md").write_text(
                "---\nname: security\ndescription: audit\n---\n\nbody\n",
                encoding="utf-8",
            )
            skill = root / ".claude" / "skills" / "probe"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: probe\ndescription: look\n---\n\nbody\n",
                encoding="utf-8",
            )
            found = {entry["name"] for entry in auditor.inventory_skills(root)}
        self.assertEqual(found, {"deploy", "security", "probe"})

    def test_a_command_without_frontmatter_still_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = root / ".claude" / "commands"
            commands.mkdir(parents=True)
            (commands / "commit.md").write_text("Commit the work.\n", encoding="utf-8")
            found = {entry["name"] for entry in auditor.inventory_skills(root)}
        self.assertEqual(found, {"commit"})


if __name__ == "__main__":
    unittest.main()
