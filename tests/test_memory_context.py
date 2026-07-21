import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = (
    Path(__file__).parents[1] / "skills/solve-efficiently/scripts/memory_context.py"
)
spec = importlib.util.spec_from_file_location("memory_context", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.source = self.root / "source.txt"
        self.source.write_text("evidence", encoding="utf-8")

    def tearDown(self):
        self.td.cleanup()

    def rec(self, id="one", scope="p", expires=1, supersedes=None):
        now = datetime.now(timezone.utc)
        return {
            "id": id,
            "project_scope": scope,
            "content": "alpha decision",
            "source": str(self.source),
            "timestamp": now.isoformat(),
            "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "confidence": "high",
            "expires_at": (now + timedelta(days=expires)).isoformat(),
            "supersedes": supersedes or [],
        }

    def test_scope_and_demand_are_mandatory(self):
        with self.assertRaises(mod.MemoryContextError):
            mod.retrieve(self.root / "m.json", "x", project_scope="", demand=True)
        with self.assertRaises(mod.MemoryContextError):
            mod.retrieve(self.root / "m.json", "x", project_scope="p")

    def test_json_scope_expiry_supersedes_and_undo(self):
        store = self.root / "m.json"
        first = mod.write_native(store, self.rec(), project_scope="p")
        second = mod.write_native(
            store, self.rec("two", supersedes=["one"]), project_scope="p"
        )
        mod.write_native(store, self.rec("old", expires=-1), project_scope="p")
        got = mod.retrieve(
            store, "alpha", project_scope="p", demand=True, include_usage=True
        )
        self.assertEqual(["two"], [x["id"] for x in got["results"]])
        self.assertEqual(got["usage_metrics"]["expired_records"], 1)
        self.assertEqual(got["usage_metrics"]["superseded_records"], 1)
        with self.assertRaises(mod.MemoryContextError):
            mod.undo_native(store, second)
        self.assertTrue(first["after_sha256"])

    def test_supersession_applies_before_query_filtering(self):
        store = self.root / "m.json"
        old = self.rec("old")
        old["content"] = "pytest is required"
        replacement = self.rec("replacement", supersedes=["old"])
        replacement["content"] = "migrated to unittest"
        mod.write_native(store, old, project_scope="p")
        mod.write_native(store, replacement, project_scope="p")

        got = mod.retrieve(
            store, "pytest", project_scope="p", demand=True, include_usage=True
        )

        self.assertEqual(got["results"], [])
        self.assertEqual(got["usage_metrics"]["superseded_records"], 1)

    def test_whitespace_ids_cannot_bypass_supersession(self):
        store = self.root / "m.json"
        old = self.rec("old ")
        old["content"] = "pytest is required"
        replacement = self.rec("replacement", supersedes=[" old "])
        replacement["content"] = "migrated to unittest"
        mod.write_native(store, old, project_scope="p")
        mod.write_native(store, replacement, project_scope="p")

        got = mod.retrieve(store, "pytest", project_scope="p", demand=True)

        self.assertEqual(got["results"], [])

    def test_json_write_replaces_legacy_whitespace_id(self):
        store = self.root / "m.json"
        mod.write_native(store, self.rec("old"), project_scope="p")
        payload = json.loads(store.read_text(encoding="utf-8"))
        payload["records"][0]["id"] = "old "
        payload["records"][0]["content"] = "obsolete legacy content"
        store.write_text(json.dumps(payload), encoding="utf-8")
        updated = self.rec("old")
        updated["content"] = "updated canonical content"

        mod.write_native(store, updated, project_scope="p")

        records = json.loads(store.read_text(encoding="utf-8"))["records"]
        self.assertEqual(
            [(item["id"], item["content"]) for item in records],
            [("old", "updated canonical content")],
        )

    def test_sqlite_write_replaces_legacy_whitespace_id(self):
        store = self.root / "m.sqlite"
        mod.write_native(store, self.rec("old"), project_scope="p", provider="sqlite")
        with mod.closing(mod.sqlite3.connect(store)) as db:
            db.execute(
                "UPDATE memories SET id = ?, content = ? WHERE id = ?",
                ("old ", "obsolete legacy content", "old"),
            )
            db.commit()
        updated = self.rec("old")
        updated["content"] = "updated canonical content"

        mod.write_native(store, updated, project_scope="p", provider="sqlite")

        with mod.closing(mod.sqlite3.connect(store)) as db:
            rows = db.execute("SELECT id, content FROM memories").fetchall()
        self.assertEqual(rows, [("old", "updated canonical content")])

    def test_undo_restores_previous_state(self):
        store = self.root / "m.json"
        mod.write_native(store, self.rec(), project_scope="p")
        receipt = mod.write_native(store, self.rec("two"), project_scope="p")
        mod.undo_native(store, receipt)
        self.assertEqual(
            ["one"],
            [
                x["id"]
                for x in mod.retrieve(store, "alpha", project_scope="p", demand=True)[
                    "results"
                ]
            ],
        )

    def test_sqlite_roundtrip_and_undo(self):
        store = self.root / "m.sqlite"
        receipt = mod.write_native(
            store, self.rec(), project_scope="p", provider="sqlite"
        )
        self.assertEqual(
            "one",
            mod.retrieve(store, "alpha", project_scope="p", demand=True)["results"][0][
                "id"
            ],
        )
        mod.undo_native(store, receipt)
        self.assertFalse(store.exists())

    def test_source_hash_mismatch_is_rejected(self):
        rec = self.rec()
        rec["source_sha256"] = "0" * 64
        with self.assertRaises(mod.MemoryContextError):
            mod.write_native(self.root / "m.json", rec, project_scope="p")

    def test_retrieve_rejects_invalid_inputs_and_irrelevant_records(self):
        store = self.root / "m.json"
        mod.write_native(store, self.rec(), project_scope="p")

        self.assertEqual(
            ["one"],
            [
                item["id"]
                for item in mod.retrieve(
                    store, "alpha,", project_scope="p", demand=True
                )["results"]
            ],
        )
        self.assertEqual(
            [],
            mod.retrieve(store, "unrelated", project_scope="p", demand=True)["results"],
        )
        for invalid_limit in (0, -1, True):
            with self.subTest(limit=invalid_limit):
                with self.assertRaises(mod.MemoryContextError):
                    mod.retrieve(
                        store,
                        "alpha",
                        project_scope="p",
                        demand=True,
                        limit=invalid_limit,
                    )
        with self.assertRaises(mod.MemoryContextError):
            mod.retrieve(None, "alpha", project_scope="p", demand=True)
        with self.assertRaises(mod.MemoryContextError):
            mod.retrieve(store, "   ", project_scope="p", demand=True)

    def test_non_hex_source_hash_is_rejected(self):
        rec = self.rec()
        rec["source"] = str(self.root / "missing-source.txt")
        rec["source_sha256"] = "z" * 64
        with self.assertRaises(mod.MemoryContextError):
            mod.write_native(self.root / "m.json", rec, project_scope="p")

    def test_memu_cli_is_scope_filtered_and_never_installed(self):
        record = self.rec()

        class CP:
            returncode = 0
            stderr = ""
            stdout = json.dumps(
                {
                    "segments": [
                        record,
                        {**record, "id": "foreign", "project_scope": "other"},
                    ]
                }
            )

        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return CP()

        got = mod.retrieve(
            None,
            "alpha",
            project_scope="p",
            demand=True,
            provider="memu",
            memu_executable="memu",
            runner=runner,
        )
        self.assertEqual(["one"], [x["id"] for x in got["results"]])
        self.assertEqual("retrieve", calls[0][1])
        self.assertNotIn("install", calls[0])

    def test_memu_invalid_json_is_reported_as_memory_error(self):
        class CP:
            returncode = 0
            stderr = ""
            stdout = "not-json"

        with self.assertRaisesRegex(mod.MemoryContextError, "invalid JSON"):
            mod.retrieve(
                None,
                "alpha",
                project_scope="p",
                demand=True,
                provider="memu",
                memu_executable="memu",
                runner=lambda *_args, **_kwargs: CP(),
            )

    def test_malformed_provider_records_are_counted_and_skipped(self):
        record = self.rec()

        class CP:
            returncode = 0
            stderr = ""
            stdout = json.dumps(
                {
                    "segments": ["not-an-object", record],
                    "files": [{**record, "id": [], "supersedes": [{}]}],
                }
            )

        payload = mod.retrieve(
            None,
            "alpha",
            project_scope="p",
            demand=True,
            provider="memu",
            memu_executable="memu",
            runner=lambda *_args, **_kwargs: CP(),
            include_usage=True,
        )

        self.assertEqual([item["id"] for item in payload["results"]], ["one"])
        self.assertEqual(payload["usage_metrics"]["malformed_records"], 2)
        self.assertEqual(len(payload["warnings"]), 2)

    def test_memu_timeout_is_reported_as_memory_error(self):
        def timeout_runner(command, **_kwargs):
            raise subprocess.TimeoutExpired(command, 30)

        with self.assertRaisesRegex(mod.MemoryContextError, "timed out"):
            mod.retrieve(
                None,
                "alpha",
                project_scope="p",
                demand=True,
                provider="memu",
                memu_executable="memu",
                runner=timeout_runner,
            )

    def test_retrieval_usage_requires_selected_and_consumed_records(self):
        store = self.root / "m.json"
        mod.write_native(store, self.rec(), project_scope="p")
        legacy = mod.retrieve(store, "alpha", project_scope="p", demand=True)
        self.assertEqual(legacy["schema_version"], 1)
        self.assertNotIn("usage_metrics", legacy)
        payload = mod.retrieve(
            store, "alpha", project_scope="p", demand=True, include_usage=True
        )
        self.assertEqual(payload["schema_version"], 2)

        with self.assertRaisesRegex(mod.MemoryContextError, "must also be consumed"):
            mod.mark_retrieval_usage(payload, useful_ids=["one"])
        mod.mark_retrieval_usage(payload, consumed_ids=["one"], useful_ids=["one"])
        self.assertEqual(payload["usage_metrics"]["consumed_records"], 1)
        self.assertEqual(payload["usage_metrics"]["useful_records"], 1)
        self.assertEqual(payload["usage_metrics"]["selected_unconsumed_records"], 0)

    def test_duplicate_provider_ids_are_deduplicated_before_usage(self):
        record = self.rec(id="duplicate")

        class CP:
            returncode = 0
            stderr = ""
            stdout = json.dumps({"segments": [record], "files": [record]})

        payload = mod.retrieve(
            None,
            "alpha",
            project_scope="p",
            demand=True,
            provider="memu",
            memu_executable="memu",
            runner=lambda *_args, **_kwargs: CP(),
            include_usage=True,
        )
        mod.mark_retrieval_usage(
            payload, consumed_ids=["duplicate"], useful_ids=["duplicate"]
        )

        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["usage_metrics"]["duplicate_id_records"], 1)
        self.assertEqual(payload["usage_metrics"]["consumed_records"], 1)
        self.assertTrue(any("duplicate" in item for item in payload["warnings"]))


if __name__ == "__main__":
    unittest.main()
