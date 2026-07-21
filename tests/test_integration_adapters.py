from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "scripts" / "integration_adapters.py"
SPEC = importlib.util.spec_from_file_location("integration_adapters", MODULE_PATH)
adapters = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(adapters)


class IntegrationAdapterTests(unittest.TestCase):
    def test_missing_provider_falls_back_without_installing(self) -> None:
        with patch.object(adapters.shutil, "which", return_value=None):
            result = adapters.probe("graphify")
        self.assertFalse(result["available"])
        self.assertFalse(result["installedByProbe"])
        self.assertEqual(result["fallback"], "cognitive-powers-native")

    def test_configured_service_is_detected_without_network_call(self) -> None:
        result = adapters.probe(
            "nacos",
            environment={"NACOS_BASE_URL": "https://nacos.example.test?token=secret"},
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["configuration"], "https://nacos.example.test")
        self.assertFalse(result["liveValidated"])

    def test_execute_is_opt_in_and_uses_resolved_binary(self) -> None:
        completed = adapters.subprocess.CompletedProcess(
            ["C:/tools/graphify.exe", "--version"],
            0,
            stdout="graphify 1.0\n",
            stderr="",
        )
        with (
            patch.object(
                adapters.shutil, "which", return_value="C:/tools/graphify.exe"
            ),
            patch.object(adapters.subprocess, "run", return_value=completed) as run,
        ):
            passive = adapters.probe("graphify", execute=False)
            active = adapters.probe("graphify", execute=True)
        self.assertFalse(passive["liveValidated"])
        self.assertTrue(active["liveValidated"])
        run.assert_called_once()

    def test_unknown_adapter_is_rejected(self) -> None:
        with self.assertRaises(adapters.AdapterError):
            adapters.probe("evolver")

    def test_provider_specific_version_commands_are_preserved(self) -> None:
        self.assertEqual(adapters.SPECS["memu"].version_args, ("--help",))
        self.assertEqual(adapters.SPECS["obsidian"].version_args, ("version",))
        self.assertIn("lh", adapters.SPECS["lobehub"].executables)


if __name__ == "__main__":
    unittest.main()
