from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN_ROOT / "scripts" / "run_extension_benchmarks.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_extension_benchmark_module", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


benchmark = load_module()


class ExtensionBenchmarkTests(unittest.TestCase):
    def test_extension_contract_passes_without_overclaiming(self) -> None:
        report = benchmark.run()
        self.assertTrue(report["passed"])
        self.assertFalse(report["liveCodexHookValidated"])
        self.assertFalse(report["runtimePerformanceMeasured"])
        self.assertFalse(report["semanticPromptQualityProven"])
        self.assertFalse(report["endToEndImprovementProven"])


if __name__ == "__main__":
    unittest.main()
