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
    def test_v4_protocol_does_not_reuse_invalid_preflights(self) -> None:
        manifest = fixtures.load_manifest(MANIFEST_PATH)
        protocol = json.loads(
            (PLUGIN_ROOT / "benchmarks/controller_ab_protocol.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(manifest["schema_version"], 2)
        self.assertTrue(manifest["corpus_id"].endswith("-v2"))
        self.assertTrue(protocol["protocol_id"].endswith("-v4"))
        previous = protocol["previous_protocol_evidence"]
        self.assertEqual(
            [item["verdict"] for item in previous],
            ["invalid", "invalid", "invalid"],
        )
        self.assertTrue(all(not item["reusable_for_v4_claims"] for item in previous))

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

    def test_incomplete_structural_recipe_is_rejected(self) -> None:
        manifest = fixtures.load_manifest(MANIFEST_PATH)
        tampered = json.loads(json.dumps(manifest))
        tampered["mode_recipes"]["parallel-packets"]["allowed_paths"] = [
            "work/a/result.txt"
        ]

        with self.assertRaisesRegex(fixtures.CorpusError, "two disjoint writes"):
            fixtures.expand_definitions(tampered)

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

    def test_modes_are_structurally_necessary_without_label_leakage(self) -> None:
        definitions = fixtures.expand_definitions(fixtures.load_manifest(MANIFEST_PATH))
        by_mode = {
            mode: next(item for item in definitions if item["expected_mode"] == mode)
            for mode in fixtures.MODES
        }

        solo = fixtures.actor_payload(by_mode["solo"])
        self.assertEqual(len(solo["work_contract"]["units"]), 1)
        self.assertEqual(solo["allowed_paths"], ["src/target.txt"])
        read_only = fixtures.actor_payload(by_mode["parallel-read-only"])
        self.assertEqual(read_only["allowed_paths"], [])
        self.assertTrue(
            all(unit["read_only"] for unit in read_only["work_contract"]["units"])
        )
        packets = fixtures.actor_payload(by_mode["parallel-packets"])
        self.assertEqual(
            packets["allowed_paths"], ["work/a/result.txt", "work/b/result.txt"]
        )
        self.assertEqual(
            len(
                {tuple(unit["ownership"]) for unit in packets["work_contract"]["units"]}
            ),
            2,
        )
        self.assertTrue(packets["verification"]["distinct_verifier_required"])
        staged = fixtures.actor_payload(by_mode["staged-verify"])
        executor, verifier = staged["work_contract"]["units"]
        self.assertEqual(verifier["depends_on"], [executor["id"]])
        self.assertTrue(verifier["read_only"])
        self.assertEqual(
            staged["verification"]["executable_check"],
            ["python", "verification/verify.py"],
        )
        for payload in (solo, read_only, packets, staged):
            encoded = json.dumps(payload, sort_keys=True)
            self.assertNotIn("expected_mode", encoded)
            self.assertNotIn("evaluators/", encoded)

    def test_promotion_is_sealed_and_excluded_from_pilot_configuration(self) -> None:
        manifest = fixtures.load_manifest(MANIFEST_PATH)
        promotion = manifest["splits"]["promotion"]
        self.assertTrue(promotion["held_out"])
        self.assertTrue(promotion["definition_sealed"])
        self.assertEqual(promotion["allowed_round"], "promotion")
        self.assertEqual(
            set(promotion["forbidden_purposes"]),
            {"development", "preflight", "pilot-debug"},
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
            output = Path(temporary).resolve() / "pilot-corpus"
            lock = fixtures.materialize(manifest, output, round_name="pilot")
            report = fixtures.validate_corpus(manifest, output, round_name="pilot")

            self.assertEqual(lock["schema_version"], 3)
            self.assertEqual(lock["materialized_round"], "pilot")
            self.assertEqual(len(lock["fixtures"]), 20)
            self.assertEqual(lock["materialized_fixture_count"], 20)
            self.assertFalse(lock["contains_run_results"])
            self.assertEqual(report["fixture_status"], "ready")
            self.assertEqual(report["ready_fixture_count"], 20)
            self.assertEqual(report["materialized_round"], "pilot")
            self.assertTrue(report["contract_valid"])
            self.assertFalse(report["contains_run_results"])
            self.assertFalse(report["end_to_end_improvement_proven"])
            self.assertTrue(
                all(item["git_identity"]["clean"] for item in lock["fixtures"])
            )
            definitions = [
                item
                for item in fixtures.expand_definitions(manifest)
                if item["split"] == "pilot"
            ]
            for mode in fixtures.MODES:
                definition = next(
                    item for item in definitions if item["expected_mode"] == mode
                )
                actor = output / definition["actor_path"]
                public = subprocess.run(
                    [sys.executable, "-B", "-m", "unittest", "tests.test_public"],
                    cwd=actor,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    public.returncode,
                    0 if mode == "parallel-read-only" else 1,
                    public.stdout + public.stderr,
                )
                if mode == "staged-verify":
                    verify = subprocess.run(
                        [sys.executable, "-B", "verification/verify.py"],
                        cwd=actor,
                        check=False,
                    )
                    self.assertEqual(verify.returncode, 1)
            post_check_report = fixtures.validate_corpus(
                manifest, output, round_name="pilot"
            )
            self.assertEqual(post_check_report["fixture_status"], "ready")
            self.assertEqual(post_check_report["ready_fixture_count"], 20)
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
            self.assertEqual(config["schema_version"], 3)
            self.assertEqual(config["round_name"], "pilot")
            self.assertEqual(len(config["tasks"]), 20)
            self.assertFalse(config["promotion_content_accessed"])
            self.assertTrue(
                all("controller-pilot-" in task_id for task_id in config["tasks"])
            )
            read_only = next(
                item
                for key, item in config["tasks"].items()
                if "parallel-read-only" in key
            )
            self.assertEqual(read_only["allow_changes"], ["__read_only_no_changes__"])
            self.assertFalse(config["bypass_sandbox"])
            with self.assertRaisesRegex(
                fixtures.CorpusError, "isolated promotion fixture bundle"
            ):
                fixtures.build_batch_config(
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
                    round_name="promotion",
                )

            promotion_root = Path(temporary).resolve() / "promotion-corpus"
            promotion_lock = fixtures.materialize(
                manifest, promotion_root, round_name="promotion"
            )
            self.assertEqual(promotion_lock["materialized_fixture_count"], 60)
            self.assertFalse(any(output.rglob("*promotion*")))
            promotion = fixtures.build_batch_config(
                manifest,
                materialized_root=promotion_root,
                task_contract=EVALUATION_TASKS_PATH,
                controller_protocol=PLUGIN_ROOT
                / "benchmarks"
                / "controller_ab_protocol.json",
                baseline_home=output / "baseline-home",
                candidate_home=output / "candidate-home",
                model="test-model",
                reasoning_effort="medium",
                round_name="promotion",
            )
            self.assertEqual(len(promotion["tasks"]), 60)
            self.assertTrue(promotion["promotion_content_accessed"])
            self.assertTrue(
                all(
                    "controller-promotion-" in task_id for task_id in promotion["tasks"]
                )
            )
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

            lock_path = output / "corpus-lock.json"
            original_lock = lock_path.read_text(encoding="utf-8")
            wrong_round = json.loads(original_lock)
            wrong_round["materialized_round"] = "promotion"
            lock_path.write_text(json.dumps(wrong_round), encoding="utf-8")
            crossed = fixtures.validate_corpus(manifest, output, round_name="pilot")
            self.assertFalse(crossed["contract_valid"])
            self.assertIn(
                "materialized round does not match requested round",
                crossed["contract_errors"],
            )
            lock_path.write_text(original_lock, encoding="utf-8")

            extra_id = json.loads(original_lock)
            extra = dict(extra_id["fixtures"][0])
            extra["fixture_id"] = "controller-promotion-injected"
            extra_id["fixtures"].append(extra)
            extra_id["fixture_ids"].append(extra["fixture_id"])
            extra_id["materialized_fixture_count"] += 1
            lock_path.write_text(json.dumps(extra_id), encoding="utf-8")
            injected = fixtures.validate_corpus(manifest, output, round_name="pilot")
            self.assertFalse(injected["contract_valid"])
            self.assertIn(
                "materialized fixture IDs do not exactly match round",
                injected["contract_errors"],
            )
            lock_path.write_text(original_lock, encoding="utf-8")

            first = definitions[0]
            target = output / first["actor_path"] / "src" / "target.txt"
            target.write_text("tampered\n", encoding="utf-8")
            tampered = fixtures.validate_corpus(manifest, output, round_name="pilot")
            status = next(
                item
                for item in tampered["fixtures"]
                if item["fixture_id"] == first["fixture_id"]
            )
            self.assertEqual(tampered["fixture_status"], "pending")
            self.assertEqual(tampered["ready_fixture_count"], 19)
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
