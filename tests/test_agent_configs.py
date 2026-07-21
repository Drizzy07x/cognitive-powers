from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = PLUGIN_ROOT / ".codex" / "agents"


class AgentConfigTests(unittest.TestCase):
    def load_agent(self, name: str) -> dict[str, object]:
        with (AGENT_ROOT / f"{name}.toml").open("rb") as source:
            return tomllib.load(source)

    def test_agents_use_the_supported_minimal_contract_without_fixed_models(
        self,
    ) -> None:
        for name in ("executor", "verifier", "test-writer"):
            with self.subTest(name=name):
                config = self.load_agent(name)
                self.assertEqual(
                    set(config), {"name", "description", "developer_instructions"}
                )
                self.assertEqual(config["name"], name)
                self.assertNotIn("model", config)
                self.assertTrue(str(config["description"]).strip())
                self.assertTrue(str(config["developer_instructions"]).strip())

    def test_executor_enforces_assigned_routes_and_honest_test_evidence(self) -> None:
        instructions = str(
            self.load_agent("executor")["developer_instructions"]
        ).casefold()
        self.assertIn("exclusive ownership boundaries", instructions)
        self.assertIn("never claim a test ran when it did not", instructions)

    def test_verifier_is_read_only_and_distinguishes_observation(self) -> None:
        instructions = str(
            self.load_agent("verifier")["developer_instructions"]
        ).casefold()
        self.assertIn("read-only", instructions)
        self.assertIn("do not modify files", instructions)
        self.assertIn("observed results from inference", instructions)

    def test_test_writer_requires_real_red_and_green_evidence(self) -> None:
        instructions = str(
            self.load_agent("test-writer")["developer_instructions"]
        ).casefold()
        self.assertIn("real red state", instructions)
        self.assertIn("do not claim test-driven evidence", instructions)
        self.assertIn("rerun the same test for green", instructions)


if __name__ == "__main__":
    unittest.main()
