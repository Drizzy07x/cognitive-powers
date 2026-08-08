from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "communicate-efficiently"
    / "scripts"
    / "communication_contract.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_communication_contract_module", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = load_module()


class CommunicationContractTests(unittest.TestCase):
    def test_routine_progress_selects_compact(self) -> None:
        result = contract.select_profile(
            {
                "kind": "progress",
                "complexity": "low",
                "consequence": "reversible",
                "unresolved": False,
                "evidence_count": 1,
            }
        )
        self.assertEqual(result["profile"], "compact")

    def test_irreversible_warning_overrides_low_complexity(self) -> None:
        result = contract.select_profile(
            {
                "kind": "warning",
                "complexity": "low",
                "consequence": "irreversible",
                "unresolved": False,
                "evidence_count": 0,
            }
        )
        self.assertEqual(result["profile"], "explicit")

    def test_assessment_rejects_short_output_that_loses_evidence(self) -> None:
        case = {
            "id": "lossy",
            "expected_profile": "compact",
            "max_words": 10,
            "required_facts": ["57 tests passed"],
            "exact_literals": ["liveProviderValidated=false"],
            "forbidden_filler": [],
        }
        result = contract.assess_output(case, "Done.")
        self.assertFalse(result["passed"])
        self.assertFalse(result["integrityPassed"])

    def test_receipts_use_provider_counts_and_compare_only_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline_source = root / "baseline-source.json"
            candidate_source = root / "candidate-source.json"
            baseline_source.write_text(
                json.dumps(
                    {
                        "provider": "fixture",
                        "model": "fixture",
                        "usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 400,
                            "output_tokens": 500,
                        },
                    }
                ),
                encoding="utf-8",
            )
            candidate_source.write_text(
                json.dumps(
                    {
                        "provider": "fixture",
                        "model": "fixture",
                        "usage": {
                            "input_tokens": 900,
                            "cached_input_tokens": 400,
                            "output_tokens": 250,
                        },
                    }
                ),
                encoding="utf-8",
            )
            baseline = contract.create_receipt(
                baseline_source,
                task_id="task-1",
                variant="baseline",
                success=True,
                quality_score=90,
                critical_failure=False,
            )
            candidate = contract.create_receipt(
                candidate_source,
                task_id="task-1",
                variant="adaptive",
                success=True,
                quality_score=92,
                critical_failure=False,
            )
            comparison = contract.compare_receipts(baseline, candidate)
            self.assertTrue(comparison["eligibleForEfficiencyClaim"])
            self.assertEqual(candidate["usage"]["freshInputTokens"], 500)
            self.assertEqual(
                comparison["metrics"]["totalTokens"]["reductionPercent"], 23.33
            )
            candidate["success"] = False
            self.assertFalse(
                contract.compare_receipts(baseline, candidate)[
                    "eligibleForEfficiencyClaim"
                ]
            )

    def test_receipt_rejects_cached_tokens_above_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "usage.json"
            source.write_text(
                json.dumps(
                    {
                        "usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 11,
                            "output_tokens": 2,
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(contract.ContractError):
                contract.create_receipt(
                    source,
                    task_id="task",
                    variant="candidate",
                    success=True,
                    quality_score=100,
                    critical_failure=False,
                )


class AnthropicUsageTests(unittest.TestCase):
    """Anthropic and Codex count a cached prompt differently."""

    def test_the_cached_prefix_is_added_back_into_total_input(self) -> None:
        total, cached, output, schema = contract.normalize_usage(
            {
                "input_tokens": 2,
                "cache_creation_input_tokens": 886,
                "cache_read_input_tokens": 335976,
                "output_tokens": 1793,
            }
        )
        self.assertEqual(schema, "anthropic")
        # A rename would have reported 2 input tokens for a 336k-token prompt.
        self.assertEqual(total, 2 + 886 + 335976)
        self.assertEqual(cached, 335976)
        self.assertEqual(total - cached, 888, "cache writes are billed as fresh")
        self.assertEqual(output, 1793)

    def test_a_cache_read_larger_than_uncached_input_is_normal(self) -> None:
        """The Codex guard would reject this; for Anthropic it is the usual case."""
        total, cached, _output, _schema = contract.normalize_usage(
            {
                "input_tokens": 1,
                "cache_read_input_tokens": 900000,
                "output_tokens": 10,
            }
        )
        self.assertGreater(cached, 1)
        self.assertEqual(total, 900001)

    def test_the_codex_shape_is_unchanged(self) -> None:
        total, cached, output, schema = contract.normalize_usage(
            {"input_tokens": 500, "cached_input_tokens": 200, "output_tokens": 30}
        )
        self.assertEqual((total, cached, output, schema), (500, 200, 30, "codex"))

    def test_a_cache_write_without_a_read_is_readable(self) -> None:
        """A first turn writes the cache and reads nothing back."""
        total, cached, output, schema = contract.normalize_usage(
            {
                "input_tokens": 100,
                "cache_creation_input_tokens": 50,
                "output_tokens": 10,
            }
        )
        self.assertEqual((total, cached, output, schema), (150, 0, 10, "anthropic"))

    def test_a_record_mixing_both_schemas_is_refused(self) -> None:
        """Codex input_tokens already contains what the Anthropic keys state."""
        with self.assertRaises(contract.ContractError):
            contract.normalize_usage(
                {
                    "input_tokens": 1000,
                    "cached_input_tokens": 900,
                    "cache_read_input_tokens": 900,
                    "output_tokens": 10,
                }
            )

    def test_the_codex_guard_still_holds(self) -> None:
        with self.assertRaises(contract.ContractError):
            contract.normalize_usage(
                {"input_tokens": 10, "cached_input_tokens": 11, "output_tokens": 1}
            )


class CrossSchemaComparisonTests(unittest.TestCase):
    def _receipt(self, schema: str) -> dict:
        return {
            "type": "communication_usage_evidence",
            "taskId": "t1",
            "success": True,
            "criticalFailure": False,
            "qualityScore": 90.0,
            "usage": {
                "inputTokens": 100,
                "cachedInputTokens": 10,
                "freshInputTokens": 90,
                "outputTokens": 20,
                "totalTokens": 120,
                "sourceSchema": schema,
            },
        }

    def test_receipts_from_different_providers_are_not_comparable(self) -> None:
        with self.assertRaises(contract.ContractError):
            contract.compare_receipts(
                self._receipt("codex"), self._receipt("anthropic")
            )

    def test_a_receipt_without_a_usage_object_reports_cleanly(self) -> None:
        """The schema check runs first, so .get on a list would traceback."""
        broken = self._receipt("codex")
        broken["usage"] = []
        with self.assertRaises(contract.ContractError):
            contract.compare_receipts(broken, self._receipt("codex"))

    def test_matching_schemas_still_compare(self) -> None:
        result = contract.compare_receipts(
            self._receipt("anthropic"), self._receipt("anthropic")
        )
        self.assertEqual(result["metrics"]["totalTokens"]["delta"], 0)


class TranscriptUsageTests(unittest.TestCase):
    """Claude Code repeats one message's usage across several rows."""

    USAGE = {
        "input_tokens": 5,
        "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": 900,
        "output_tokens": 50,
    }

    def _write(self, rows: list[dict]) -> Path:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        for row in rows:
            handle.write(json.dumps(row) + "\n")
        handle.close()
        path = Path(handle.name)
        self.addCleanup(path.unlink)
        return path

    def _row(self, identifier: str) -> dict:
        return {
            "type": "assistant",
            "sessionId": "s1",
            "message": {
                "id": identifier,
                "role": "assistant",
                "model": "claude-opus-5",
                "usage": dict(self.USAGE),
            },
        }

    def test_repeated_rows_for_one_message_are_counted_once(self) -> None:
        path = self._write([self._row("msg_a")] * 4 + [self._row("msg_b")])
        record = contract.usage_from_transcript(path)
        self.assertEqual(record["messageCount"], 2)
        self.assertEqual(record["usage"]["output_tokens"], 100)
        self.assertEqual(record["usage"]["cache_read_input_tokens"], 1800)

    def test_the_record_feeds_the_receipt_writer(self) -> None:
        path = self._write([self._row("msg_a")])
        record = contract.usage_from_transcript(path)
        total, cached, output, schema = contract.normalize_usage(record["usage"])
        self.assertEqual(schema, "anthropic")
        self.assertEqual((total, cached, output), (1005, 900, 50))

    def test_host_generated_rows_are_excluded_not_fatal(self) -> None:
        """Claude Code writes its own rows with a placeholder model name."""
        synthetic = self._row("msg_host")
        synthetic["message"]["model"] = "<synthetic>"
        path = self._write([self._row("msg_a"), synthetic])
        record = contract.usage_from_transcript(path)
        self.assertEqual(record["model"], "claude-opus-5")
        self.assertEqual(record["messageCount"], 1)
        self.assertEqual(record["hostGeneratedRowsExcluded"], 1)

    def test_a_session_filter_excludes_other_sessions(self) -> None:
        other = self._row("msg_c")
        other["sessionId"] = "s2"
        path = self._write([self._row("msg_a"), other])
        record = contract.usage_from_transcript(path, session_id="s1")
        self.assertEqual(record["messageCount"], 1)

    def test_unreadable_lines_are_reported_not_guessed(self) -> None:
        path = self._write([self._row("msg_a")])
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        record = contract.usage_from_transcript(path)
        self.assertEqual(record["unparsableLines"], 1)

    def test_a_transcript_without_usage_fails_loudly(self) -> None:
        path = self._write([{"type": "user", "message": {"role": "user"}}])
        with self.assertRaises(contract.ContractError):
            contract.usage_from_transcript(path)

    def test_the_host_version_is_pinned_into_the_record(self) -> None:
        row = self._row("msg_a")
        row["version"] = "2.1.219"
        path = self._write([row])
        record = contract.usage_from_transcript(path)
        self.assertEqual(record["hostVersions"], ["2.1.219"])

    def test_an_unrecognised_usage_shape_refuses_instead_of_undercounting(
        self,
    ) -> None:
        """A partially readable row would read as a real efficiency result."""
        renamed = self._row("msg_b")
        renamed["message"]["usage"].pop("cache_read_input_tokens")
        renamed["message"]["usage"]["cache_read_tokens"] = 900
        path = self._write([self._row("msg_a"), renamed])
        with self.assertRaises(contract.ContractError) as caught:
            contract.usage_from_transcript(path)
        self.assertIn("not a published interface", str(caught.exception))

    def test_mixed_models_are_refused(self) -> None:
        second = self._row("msg_b")
        second["message"]["model"] = "claude-sonnet-5"
        path = self._write([self._row("msg_a"), second])
        with self.assertRaises(contract.ContractError):
            contract.usage_from_transcript(path)


class SharedConversionTests(unittest.TestCase):
    """Both sides must reach the same code, not merely the same answer."""

    def test_the_writer_delegates_to_the_shared_module(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("provider_usage.py", source)
        self.assertNotIn(
            "cache_read_input_tokens",
            source.split("def usage_from_transcript", 1)[0],
            "the conversion must not be reimplemented beside the delegation",
        )

    def test_the_shared_module_ships_at_the_plugin_root(self) -> None:
        self.assertTrue((PLUGIN_ROOT / "scripts" / "provider_usage.py").is_file())


class DurableConsumerAgreementTests(unittest.TestCase):
    """The durable recorder re-derives totals and must not drift from here.

    It verifies rather than trusts the receipt, so if it reads fewer provider
    schemas than the writer accepts it rejects correct evidence.
    """

    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "work_state_for_usage",
            PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "work_state.py",
        )
        assert spec is not None and spec.loader is not None
        self.work_state = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.work_state)

    def test_both_implementations_derive_the_same_totals(self) -> None:
        cases = [
            {
                "input_tokens": 2,
                "cache_creation_input_tokens": 886,
                "cache_read_input_tokens": 335976,
                "output_tokens": 1793,
            },
            {"input_tokens": 1, "cache_read_input_tokens": 900000, "output_tokens": 10},
            {"input_tokens": 500, "cached_input_tokens": 200, "output_tokens": 30},
        ]
        for usage in cases:
            with self.subTest(usage=sorted(usage)):
                total, cached, output, schema = contract.normalize_usage(usage)
                derived = self.work_state._expected_communication_usage(usage)
                self.assertEqual(
                    derived,
                    {
                        "inputTokens": total,
                        "cachedInputTokens": cached,
                        "outputTokens": output,
                        "sourceSchema": schema,
                    },
                )

    def test_an_unreadable_record_cannot_match_a_receipt(self) -> None:
        derived = self.work_state._expected_communication_usage({"nonsense": 1})
        self.assertEqual(set(derived.values()), {None})

    def test_a_record_in_another_encoding_is_refused_not_tracebacked(self) -> None:
        """Provider usage records reach this reader from whatever wrote them.

        UnicodeDecodeError is a ValueError, caught by neither ``OSError`` nor
        ``json.JSONDecodeError``, so a transcript exported as UTF-16 escaped as
        a traceback rather than the error object the CLI documents.
        """
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "usage.json"
            record.write_bytes(b"\xff\xfe\x00b\x00a\x00d")

            with self.assertRaisesRegex(contract.ContractError, "cannot read"):
                contract.load_object(record)

    def test_a_quality_score_is_read_the_way_work_state_validates_it(self) -> None:
        """Both ends read one field; only one end used to read it strictly.

        ``work_state.py`` refuses a receipt whose ``qualityScore`` is not a real
        number in 0..100, bool excluded. The comparison here used ``float()``,
        so "high" and null raised out of the eligibility expression and True was
        read as a score of 1.0.
        """
        for value, expected in (
            (80, 80.0),
            (80.5, 80.5),
            ("high", None),
            (None, None),
            (True, None),
            (101, None),
            (-1, None),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    expected, contract._quality_score({"qualityScore": value})
                )
        self.assertIsNone(contract._quality_score({}))


if __name__ == "__main__":
    unittest.main()
