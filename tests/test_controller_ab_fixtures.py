from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "controller_ab_fixtures.py"
MANIFEST_PATH = (
    PLUGIN_ROOT / "benchmarks" / "confirmatory" / "controller_ab_corpus.json"
)
EVALUATION_TASKS_PATH = PLUGIN_ROOT / "benchmarks" / "evaluation_tasks.json"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_controller_ab_fixtures_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fixtures = load_module()


class ControllerABFixtureTests(unittest.TestCase):
    def test_manifest_expands_exact_confirmatory_matrix(self) -> None:
        manifest = fixtures.load_manifest(MANIFEST_PATH)
        definitions = fixtures.expand_definitions(manifest)
        cells: dict[tuple[str, str, str], int] = {}
        for item in definitions:
            key = (item["split"], item["expected_mode"], item["category"])
            cells[key] = cells.get(key, 0) + 1
            self.assertEqual(item["repetitions_per_arm"], 3)
            self.assertTrue(item["definition_id"].startswith("definition-"))
            self.assertEqual(len(item["expected_hashes"]["actor_source_sha256"]), 64)
            self.assertTrue(item["checks"]["hidden"])
            self.assertTrue(item["checks"]["quality"])
            self.assertTrue(item["git_identity"]["required"])

        self.assertEqual(len(definitions), 80)
        self.assertEqual(len({item["fixture_id"] for item in definitions}), 80)
        self.assertEqual(sum(item["split"] == "pilot" for item in definitions), 20)
        self.assertEqual(sum(item["split"] == "promotion" for item in definitions), 60)
        for mode in fixtures.MODES:
            for category in fixtures.CATEGORIES:
                self.assertEqual(cells[("pilot", mode, category)], 1)
                self.assertEqual(cells[("promotion", mode, category)], 3)
        self.assertEqual(
            fixtures.promotion_definition_seal(definitions),
            manifest["splits"]["promotion"]["definition_seal_sha256"],
        )

    def test_evaluation_contract_references_corpus_without_claiming_results(
        self,
    ) -> None:
        contract = json.loads(EVALUATION_TASKS_PATH.read_text(encoding="utf-8"))
        binding = contract["controller_confirmatory_corpus"]

        self.assertEqual(binding["fixture_count"], 80)
        self.assertEqual(binding["pilot_fixture_count"], 20)
        self.assertEqual(binding["promotion_fixture_count"], 60)
        self.assertEqual(binding["repetitions_per_arm"], 3)
        self.assertFalse(binding["contains_run_results"])
        self.assertFalse(binding["end_to_end_improvement_proven"])
        generated = fixtures.build_task_contract(fixtures.load_manifest(MANIFEST_PATH))
        self.assertEqual(generated, contract)
        self.assertEqual(len(contract["tasks"]), 80)
        self.assertEqual(len(contract["rounds"]["pilot"]["task_ids"]), 20)
        self.assertEqual(len(contract["rounds"]["promotion"]["task_ids"]), 60)
        self.assertEqual(contract["rounds"]["pilot"]["repetitions_per_task"], 3)

    def test_unmaterialized_corpus_fails_closed(self) -> None:
        manifest = fixtures.load_manifest(MANIFEST_PATH)
        report = fixtures.validate_corpus(manifest)
        tampered = json.loads(json.dumps(manifest))
        tampered["splits"]["promotion"]["definition_seal_sha256"] = "0" * 64
        invalid_seal = fixtures.validate_corpus(tampered)

        self.assertTrue(report["contract_valid"])
        self.assertEqual(report["fixture_count"], 80)
        self.assertEqual(report["ready_fixture_count"], 0)
        self.assertEqual(report["fixture_status"], "pending")
        self.assertTrue(
            all(item["fixture_status"] == "pending" for item in report["fixtures"])
        )
        self.assertFalse(report["end_to_end_improvement_proven"])
        self.assertFalse(invalid_seal["contract_valid"])
        self.assertIn("seal", invalid_seal["contract_errors"][0])

    def test_actor_payload_never_contains_expected_mode_or_evaluator_routes(
        self,
    ) -> None:
        definitions = fixtures.expand_definitions(fixtures.load_manifest(MANIFEST_PATH))
        promotion = [item for item in definitions if item["split"] == "promotion"]
        for definition in promotion:
            payload = fixtures.actor_payload(definition)
            encoded = json.dumps(payload, sort_keys=True)
            self.assertNotIn("expected_mode", encoded)
            self.assertNotIn("hidden_check", encoded)
            self.assertNotIn("quality_check", encoded)
            self.assertNotIn("evaluator_path", encoded)
            self.assertTrue(definition["actor_path"].startswith("actor/promotion/"))
            self.assertTrue(
                definition["evaluator_path"].startswith("evaluators/promotion/")
            )

    def test_materializer_requires_empty_absolute_path_outside_repository(self) -> None:
        manifest = fixtures.load_manifest(MANIFEST_PATH)
        with self.assertRaisesRegex(fixtures.CorpusError, "absolute"):
            fixtures.materialize(manifest, Path("relative-output"))
        with self.assertRaisesRegex(fixtures.CorpusError, "outside"):
            fixtures.materialize(manifest, PLUGIN_ROOT / "not-allowed")
        with tempfile.TemporaryDirectory() as temporary:
            occupied = Path(temporary) / "occupied"
            occupied.mkdir()
            (occupied / "file.txt").write_text("occupied", encoding="utf-8")
            with self.assertRaisesRegex(fixtures.CorpusError, "contain files"):
                fixtures.materialize(manifest, occupied)

    def test_materialized_corpus_is_ready_and_tampering_fails_closed(self) -> None:
        manifest = fixtures.load_manifest(MANIFEST_PATH)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve() / "controller-corpus"
            lock = fixtures.materialize(manifest, output)
            report = fixtures.validate_corpus(manifest, output)

            self.assertEqual(len(lock["fixtures"]), 80)
            self.assertFalse(lock["contains_run_results"])
            self.assertEqual(report["fixture_status"], "ready")
            self.assertEqual(report["ready_fixture_count"], 80)
            self.assertTrue(report["contract_valid"])
            self.assertFalse(report["contains_run_results"])
            self.assertFalse(report["end_to_end_improvement_proven"])
            self.assertTrue(
                all(item["git_identity"]["clean"] for item in lock["fixtures"])
            )
            config = fixtures.build_batch_config(
                manifest,
                materialized_root=output,
                task_contract=EVALUATION_TASKS_PATH,
                controller_protocol=PLUGIN_ROOT
                / "benchmarks"
                / "controller_ab_protocol.json",
                baseline_home=output / "baseline-home",
                candidate_home=output / "candidate-home",
                model="test-model",
                reasoning_effort="medium",
            )
            self.assertEqual(len(config["tasks"]), 80)
            read_only = next(
                item
                for key, item in config["tasks"].items()
                if "parallel-read-only" in key
            )
            self.assertEqual(read_only["allow_changes"], ["__read_only_no_changes__"])
            self.assertFalse(config["bypass_sandbox"])
            unsandboxed = fixtures.build_batch_config(
                manifest,
                materialized_root=output,
                task_contract=EVALUATION_TASKS_PATH,
                controller_protocol=PLUGIN_ROOT
                / "benchmarks"
                / "controller_ab_protocol.json",
                baseline_home=output / "baseline-home",
                candidate_home=output / "candidate-home",
                model="test-model",
                reasoning_effort="medium",
                bypass_sandbox=True,
            )
            self.assertTrue(unsandboxed["bypass_sandbox"])

            first = fixtures.expand_definitions(manifest)[0]
            target = output / first["actor_path"] / "src" / "scenario.txt"
            target.write_text("tampered\n", encoding="utf-8")
            tampered = fixtures.validate_corpus(manifest, output)
            status = next(
                item
                for item in tampered["fixtures"]
                if item["fixture_id"] == first["fixture_id"]
            )
            self.assertEqual(tampered["fixture_status"], "pending")
            self.assertEqual(tampered["ready_fixture_count"], 79)
            self.assertEqual(status["fixture_status"], "pending")
            self.assertTrue(
                any(
                    "hash" in reason or "clean" in reason
                    for reason in status["reasons"]
                )
            )

    def test_cli_reports_pending_without_materialization(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "validate"],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["fixture_status"], "pending")
        self.assertEqual(report["ready_fixture_count"], 0)


if __name__ == "__main__":
    unittest.main()
