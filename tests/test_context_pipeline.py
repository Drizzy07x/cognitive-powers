from __future__ import annotations

import importlib.util
import json
import sys
import unicodedata
import unittest
from datetime import datetime, timezone
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "solve-efficiently" / "scripts" / "context_pipeline.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_context_pipeline_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pipeline = load_module()


class ContextPipelineTests(unittest.TestCase):
    def test_pipeline_records_inclusion_truncation_and_exclusion(self) -> None:
        items = [
            pipeline.ContextItem("a", "source-a", "needle alpha", {"priority": 1}),
            pipeline.ContextItem("b", "source-b", "needle beta is longer"),
            pipeline.ContextItem("c", "source-c", "unrelated gamma"),
        ]
        runner = pipeline.ContextPipeline(
            pipeline.StaticContextProvider(items),
            [pipeline.NormalizeWhitespaceProcessor()],
            pipeline.RankedBudgetSelector(),
        )

        result = runner.run("needle", pipeline.ContextBudget(max_chars=20, max_items=2))

        self.assertEqual([item.item_id for item in result.items], ["a", "b"])
        decisions = {
            decision.item_id: decision for decision in result.receipt.decisions
        }
        self.assertEqual(decisions["a"].status, "included")
        self.assertEqual(decisions["b"].status, "truncated")
        self.assertEqual(decisions["c"].status, "excluded")
        self.assertEqual(decisions["c"].reason, "item_budget")
        self.assertEqual(result.receipt.selected_chars, 20)
        self.assertEqual(len(result.content), 20)
        self.assertFalse(result.receipt.end_to_end_improvement_proven)
        legacy = json.loads(result.receipt.to_json())
        self.assertEqual(legacy["schema_version"], 1)
        self.assertNotIn("usage_metrics", legacy)
        self.assertNotIn("usefulness", legacy["decisions"][0])
        serialized = json.loads(result.receipt.to_json(include_usage=True))
        self.assertEqual(serialized["schema_version"], 2)
        self.assertEqual(serialized["budget"], {"max_chars": 20, "max_items": 2})
        self.assertEqual(serialized["usage_metrics"]["selected_items"], 2)
        self.assertGreater(serialized["usage_metrics"]["estimated_selected_tokens"], 0)

    def test_length_does_not_outrank_relevance(self) -> None:
        """Ranking decided what the model reads, and it was ranking by size.

        ``str.count`` over unfiltered query words spends a hit on every "a" and
        every "the" in the text, so a long document that shares no content word
        with the query outscored the one item that named all three. Measured
        here before the fix: 281 against 6, after which the README took the
        whole character budget and the relevant item was excluded outright.
        """
        items = [
            pipeline.ContextItem(
                "relevant",
                "ledger.py",
                "def verify_receipt(ledger):\n    return chain_ok(ledger)\n",
                {},
            ),
            pipeline.ContextItem(
                "readme",
                "README.md",
                "This project has a lot of prose about installation and usage. " * 40,
                {},
            ),
        ]

        selection = pipeline.RankedBudgetSelector().select(
            items,
            "how does the ledger verify a receipt",
            pipeline.ContextBudget(max_items=1, max_chars=400),
        )

        self.assertEqual(["relevant"], [item.item_id for item in selection.items])

    def test_processor_filtered_item_remains_in_receipt(self) -> None:
        items = [
            pipeline.ContextItem("kept", "source", " useful \r\n"),
            pipeline.ContextItem("blank", "source", "   "),
        ]
        runner = pipeline.ContextPipeline(
            pipeline.StaticContextProvider(items),
            [pipeline.NormalizeWhitespaceProcessor()],
            pipeline.RankedBudgetSelector(),
        )

        result = runner.run("useful", pipeline.ContextBudget(100, 3))

        self.assertEqual(result.items[0].content, "useful")
        blank = next(
            decision
            for decision in result.receipt.decisions
            if decision.item_id == "blank"
        )
        self.assertEqual(blank.status, "excluded")
        self.assertEqual(blank.reason, "processor_filtered")

    def test_pipeline_rejects_ambiguous_duplicate_ids(self) -> None:
        runner = pipeline.ContextPipeline(
            pipeline.StaticContextProvider(
                [
                    pipeline.ContextItem("same", "one", "first"),
                    pipeline.ContextItem("same", "two", "second"),
                ]
            ),
            [],
            pipeline.RankedBudgetSelector(),
        )

        with self.assertRaisesRegex(ValueError, "duplicate item ids"):
            runner.run("first", pipeline.ContextBudget(100, 3))

    def test_context_lint_finds_duplicate_contradiction_stale_and_unconsumed(
        self,
    ) -> None:
        items = [
            pipeline.ContextItem(
                "old-a",
                "one",
                "Same fact",
                {
                    "fact_key": "version",
                    "fact_value": "1",
                    "valid_until": "2025-01-01T00:00:00Z",
                },
            ),
            pipeline.ContextItem(
                "new-b",
                "two",
                " same   fact ",
                {"fact_key": "version", "fact_value": "2"},
            ),
        ]
        runner = pipeline.ContextPipeline(
            pipeline.StaticContextProvider(items), [], pipeline.RankedBudgetSelector()
        )
        result = runner.run("fact", pipeline.ContextBudget(100, 3))

        issues = pipeline.lint_context(
            items,
            result.receipt,
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        codes = [issue.code for issue in issues]
        self.assertEqual(codes.count("duplicate"), 1)
        self.assertEqual(codes.count("contradiction"), 1)
        self.assertEqual(codes.count("stale"), 1)
        self.assertEqual(codes.count("unconsumed"), 1)

        result.receipt.mark_consumed(["new-b"])
        relinted = pipeline.lint_context(items, result.receipt)
        self.assertNotIn("unconsumed", [issue.code for issue in relinted])

    def test_context_lint_compares_content_independently_of_composition(self) -> None:
        """Content that renders identically must count as one duplicate.

        Two items differing only in Unicode composition cost the budget twice
        while telling the model the same thing.
        """
        composed = unicodedata.normalize("NFC", "El café está listo")
        decomposed = unicodedata.normalize("NFD", "El café está listo")
        self.assertNotEqual(composed.encode("utf-8"), decomposed.encode("utf-8"))

        issues = pipeline.lint_context(
            [
                pipeline.ContextItem("a", "one", composed),
                pipeline.ContextItem("b", "two", decomposed),
            ]
        )

        self.assertEqual([issue.code for issue in issues], ["duplicate"])
        self.assertEqual(issues[0].item_ids, ("a", "b"))

    def test_context_lint_compares_fact_keys_independently_of_composition(self) -> None:
        composed = unicodedata.normalize("NFC", "versión")
        decomposed = unicodedata.normalize("NFD", "versión")

        issues = pipeline.lint_context(
            [
                pipeline.ContextItem(
                    "a", "one", "first", {"fact_key": composed, "fact_value": "1"}
                ),
                pipeline.ContextItem(
                    "b", "two", "second", {"fact_key": decomposed, "fact_value": "2"}
                ),
            ]
        )

        self.assertEqual([issue.code for issue in issues], ["contradiction"])

    def test_context_lint_does_not_invent_composition_contradictions(self) -> None:
        """One value spelled two ways is not a conflict."""
        composed = unicodedata.normalize("NFC", "café")
        decomposed = unicodedata.normalize("NFD", "café")

        issues = pipeline.lint_context(
            [
                pipeline.ContextItem(
                    "a", "one", "first", {"fact_key": "drink", "fact_value": composed}
                ),
                pipeline.ContextItem(
                    "b",
                    "two",
                    "second",
                    {"fact_key": "drink", "fact_value": decomposed},
                ),
            ]
        )

        self.assertNotIn("contradiction", [issue.code for issue in issues])

    def test_mark_consumed_rejects_unknown_item(self) -> None:
        runner = pipeline.ContextPipeline(
            pipeline.StaticContextProvider(
                [pipeline.ContextItem("known", "s", "value")]
            ),
            [],
            pipeline.RankedBudgetSelector(),
        )
        result = runner.run("value", pipeline.ContextBudget(100, 1))

        with self.assertRaisesRegex(ValueError, "unknown context items"):
            result.receipt.mark_consumed(["missing"])

    def test_mark_consumed_rejects_context_that_was_not_selected(self) -> None:
        runner = pipeline.ContextPipeline(
            pipeline.StaticContextProvider(
                [
                    pipeline.ContextItem("selected", "s", "selected"),
                    pipeline.ContextItem("excluded", "s", "excluded"),
                ]
            ),
            [],
            pipeline.RankedBudgetSelector(),
        )
        result = runner.run("selected", pipeline.ContextBudget(100, 1))

        with self.assertRaisesRegex(ValueError, "unselected context items"):
            result.receipt.mark_consumed(["excluded"])

    def test_pipeline_rejects_selector_decisions_that_do_not_match_selection(
        self,
    ) -> None:
        class ForgedSelector:
            def select(self, items, query, budget):
                del query, budget
                item = items[0]
                forged = pipeline.ContextDecision(
                    item_id=item.item_id,
                    source="wrong-source",
                    status="excluded",
                    reason="forged",
                    original_chars=len(item.content),
                    selected_chars=0,
                    content_sha256="0" * 64,
                )
                return pipeline.ContextSelection((item,), (forged, forged))

        runner = pipeline.ContextPipeline(
            pipeline.StaticContextProvider(
                [pipeline.ContextItem("selected", "source", "selected")]
            ),
            [],
            ForgedSelector(),
        )

        with self.assertRaisesRegex(ValueError, "selector decisions"):
            runner.run("selected", pipeline.ContextBudget(100, 1))

    def test_pipeline_rejects_preclassified_or_consumed_selector_decisions(
        self,
    ) -> None:
        item = pipeline.ContextItem("item", "source", "value")

        class PreclassifiedSelector:
            def __init__(self, *, selected, status, consumed, usefulness):
                self.selected = selected
                self.status = status
                self.consumed = consumed
                self.usefulness = usefulness

            def select(self, items, query, budget):
                del query, budget
                decision = pipeline.ContextDecision(
                    item_id=items[0].item_id,
                    source=items[0].source,
                    status=self.status,
                    reason="preclassified",
                    original_chars=len(items[0].content),
                    selected_chars=len(items[0].content) if self.selected else 0,
                    content_sha256=pipeline._content_hash(items[0].content),
                    consumed=self.consumed,
                    usefulness=self.usefulness,
                )
                return pipeline.ContextSelection(
                    (items[0],) if self.selected else (), (decision,)
                )

        cases = (
            PreclassifiedSelector(
                selected=False,
                status="excluded",
                consumed=True,
                usefulness="unknown",
            ),
            PreclassifiedSelector(
                selected=True,
                status="included",
                consumed=False,
                usefulness="useful",
            ),
        )
        for selector in cases:
            with self.subTest(status=selector.status, usefulness=selector.usefulness):
                runner = pipeline.ContextPipeline(
                    pipeline.StaticContextProvider([item]), [], selector
                )
                with self.assertRaisesRegex(ValueError, "selector decisions"):
                    runner.run("value", pipeline.ContextBudget(100, 1))

    def test_usage_metrics_require_consumption_before_usefulness(self) -> None:
        item = pipeline.ContextItem("known", "s", "value")
        result = pipeline.ContextPipeline(
            pipeline.StaticContextProvider([item]),
            [],
            pipeline.RankedBudgetSelector(),
        ).run("value", pipeline.ContextBudget(100, 1))

        with self.assertRaisesRegex(ValueError, "consumed first"):
            result.receipt.mark_usefulness(["known"], "useful")
        result.receipt.mark_consumed(["known"])
        result.receipt.mark_usefulness(["known"], "useful")

        metrics = result.receipt.usage_metrics()
        self.assertEqual(metrics["consumed_items"], 1)
        self.assertEqual(metrics["useful_items"], 1)
        self.assertEqual(metrics["selected_unconsumed_items"], 0)

    def test_receipt_marks_expired_candidates_stale(self) -> None:
        item = pipeline.ContextItem(
            "old", "s", "value", {"valid_until": "2000-01-01T00:00:00Z"}
        )
        result = pipeline.ContextPipeline(
            pipeline.StaticContextProvider([item]),
            [],
            pipeline.RankedBudgetSelector(),
        ).run("value", pipeline.ContextBudget(100, 1))

        decision = result.receipt.decisions[0]
        self.assertEqual(result.items, ())
        self.assertEqual(decision.status, "excluded")
        self.assertEqual(decision.reason, "expired")
        self.assertTrue(decision.stale)
        self.assertEqual(result.receipt.selected_items, 0)
        self.assertEqual(result.receipt.usage_metrics()["stale_items"], 1)

    def test_processor_cannot_reintroduce_expired_context(self) -> None:
        expired = pipeline.ContextItem(
            "expired", "s", "old value", {"valid_until": "2000-01-01T00:00:00Z"}
        )

        class ReintroduceExpired:
            def process(self, items, query):
                del query
                return (*items, expired)

        runner = pipeline.ContextPipeline(
            pipeline.StaticContextProvider([expired]),
            [ReintroduceExpired()],
            pipeline.RankedBudgetSelector(),
        )

        with self.assertRaisesRegex(ValueError, "reintroduced expired"):
            runner.run("value", pipeline.ContextBudget(100, 1))

    def test_pipeline_rejects_malformed_expiry_fail_closed(self) -> None:
        item = pipeline.ContextItem(
            "bad", "s", "value", {"valid_until": "not-a-timestamp"}
        )
        runner = pipeline.ContextPipeline(
            pipeline.StaticContextProvider([item]),
            [],
            pipeline.RankedBudgetSelector(),
        )

        with self.assertRaisesRegex(ValueError, "invalid valid_until"):
            runner.run("value", pipeline.ContextBudget(100, 1))
        issues = pipeline.lint_context([item])
        self.assertIn("invalid-expiry", [issue.code for issue in issues])

    def test_usage_character_counts_match_rendered_content(self) -> None:
        items = [
            pipeline.ContextItem("a", "s", "alpha"),
            pipeline.ContextItem("b", "s", "beta"),
        ]
        result = pipeline.ContextPipeline(
            pipeline.StaticContextProvider(items), [], pipeline.RankedBudgetSelector()
        ).run("value", pipeline.ContextBudget(100, 2))

        self.assertEqual(result.receipt.selected_chars, len(result.content))
        self.assertEqual(
            result.receipt.usage_metrics()["selected_chars"], len(result.content)
        )


if __name__ == "__main__":
    unittest.main()
