from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "benchmarks" / "evaluators" / "research_quality.py"
SPEC = importlib.util.spec_from_file_location("research_quality", MODULE_PATH)
quality = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(quality)


class ResearchQualityTests(unittest.TestCase):
    def test_scores_complete_version_matched_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = root / "events.jsonl"
            events.write_text("", encoding="utf-8")
            message = root / "message.txt"
            message.write_text(
                "Recommendation: adopt compression.zstd from the standard library. "
                "Verified facts: ZstdFile handles files; compress and decompress are one-shot; "
                "ZstdCompressor and ZstdDecompressor are incremental. "
                "https://docs.python.org/3.14/library/compression.zstd.html "
                "https://docs.python.org/3.14/whatsnew/3.14.html "
                "Inference: therefore it meets the dependency-free requirement. "
                "Uncertainty/caveat: the module is optional on distributor builds; use an import "
                "capability check and fail fast as an unsupported build when absent.",
                encoding="utf-8",
            )

            report = quality.evaluate(root, events, message)

            self.assertEqual(report["score"], 100)
            self.assertEqual(report["critical_errors"], [])

    def test_unconditional_answer_without_optional_caveat_is_critical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = root / "events.jsonl"
            events.write_text("", encoding="utf-8")
            message = root / "message.txt"
            message.write_text("Always use compression.zstd.", encoding="utf-8")

            report = quality.evaluate(root, events, message)

            self.assertIn(
                "optional distributor-build caveat or mitigation is missing",
                report["critical_errors"],
            )

    def test_startup_prerequisite_is_an_operational_mitigation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = root / "events.jsonl"
            events.write_text("", encoding="utf-8")
            message = root / "message.txt"
            message.write_text(
                "Recommendation: use compression.zstd from the standard library. "
                "Verified facts: ZstdFile handles files; compress and decompress are one-shot; "
                "ZstdCompressor and ZstdDecompressor are incremental. "
                "https://docs.python.org/3.14/library/compression.zstd.html "
                "https://docs.python.org/3.14/whatsnew/3.14.html "
                "Inference: therefore it meets the API requirement. "
                "Uncertainty: it is optional on distributor builds; make a successful "
                "import compression.zstd an install-time prerequisite.",
                encoding="utf-8",
            )

            report = quality.evaluate(root, events, message)

            self.assertEqual(report["score"], 100)
            self.assertEqual(report["critical_errors"], [])

    def test_conditional_standardization_is_a_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = root / "events.jsonl"
            events.write_text("", encoding="utf-8")
            message = root / "message.txt"
            message.write_text(
                "Recommendation: standardize on compression.zstd only as a deployment "
                "prerequisite; it is Python's dependency-free API. Verified facts: ZstdFile "
                "handles files; compress and decompress are one-shot; ZstdCompressor and "
                "ZstdDecompressor are incremental. "
                "https://docs.python.org/3.14/library/compression.zstd.html "
                "https://docs.python.org/3.14/whatsnew/3.14.html "
                "Inference: therefore it fits qualified builds. Uncertainty: it is optional "
                "on distributor builds; use a capability check and reject unsupported builds.",
                encoding="utf-8",
            )

            report = quality.evaluate(root, events, message)

            self.assertEqual(report["score"], 100)
            self.assertEqual(report["critical_errors"], [])


if __name__ == "__main__":
    unittest.main()
