from __future__ import annotations

import importlib.util
import json
import sys
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
        self.assertEqual(codes.count("unconsumed"), 2)

        result.receipt.mark_consumed(["old-a", "new-b"])
        relinted = pipeline.lint_context(items, result.receipt)
        self.assertNotIn("unconsumed", [issue.code for issue in relinted])

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

        self.assertTrue(result.receipt.decisions[0].stale)
        self.assertEqual(result.receipt.usage_metrics()["stale_items"], 1)

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
