from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SERVER = PLUGIN_ROOT / "mcp" / "evidence_server.py"
CLAUDE_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"


def load_server():
    spec = importlib.util.spec_from_file_location("evidence_server", SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvidenceServerProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = load_server()

    def exchange(self, *messages: dict[str, object]) -> list[dict[str, object]]:
        source = StringIO("".join(json.dumps(m) + "\n" for m in messages))
        sink = StringIO()
        self.server.serve(source, sink)
        return [json.loads(line) for line in sink.getvalue().splitlines()]

    def test_initialize_echoes_the_protocol_version_the_client_asked_for(self) -> None:
        responses = self.exchange(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )
        result = responses[0]["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertEqual(result["serverInfo"]["name"], "cognitive-powers-evidence")
        self.assertIn("tools", result["capabilities"])

    def test_a_notification_is_never_answered(self) -> None:
        self.assertEqual(
            self.exchange({"jsonrpc": "2.0", "method": "notifications/initialized"}), []
        )

    def test_malformed_input_does_not_end_the_server(self) -> None:
        responses = self.exchange_raw(
            "not json\n", '{"jsonrpc":"2.0","id":7,"method":"ping"}\n'
        )
        self.assertEqual([item["id"] for item in responses], [7])

    def exchange_raw(self, *lines: str) -> list[dict[str, object]]:
        sink = StringIO()
        self.server.serve(StringIO("".join(lines)), sink)
        return [json.loads(line) for line in sink.getvalue().splitlines()]

    def test_every_listed_tool_declares_a_schema(self) -> None:
        responses = self.exchange(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        tools = responses[0]["result"]["tools"]
        self.assertTrue(tools)
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertTrue(tool["description"].strip())
                self.assertEqual(tool["inputSchema"]["type"], "object")
                self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_an_unsupported_method_is_a_protocol_error(self) -> None:
        responses = self.exchange(
            {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}
        )
        self.assertEqual(responses[0]["error"]["code"], -32601)


class EvidenceServerBoundaryTests(unittest.TestCase):
    """The server publishes an inspection surface; it must stay one."""

    def setUp(self) -> None:
        self.server = load_server()

    def test_no_published_tool_can_reach_a_mutating_subcommand(self) -> None:
        mutating = {
            "storage-gc",
            "init",
            "compact",
            "plan-packets",
            "start-packet",
            "complete-packet",
            "reopen-packet",
            "run",
            "run-red",
            "run-green",
            "record",
            "verify",
            "reopen",
            "complete",
        }
        for name, tool in self.server.TOOLS.items():
            with self.subTest(tool=name):
                self.assertFalse(mutating.intersection(tool["subcommand"]))

    def test_a_tool_name_outside_the_allowlist_cannot_run_anything(self) -> None:
        with self.assertRaises(self.server.ToolError):
            self.server.call_tool("storage-gc", {})
        with self.assertRaises(self.server.ToolError):
            self.server.call_tool("../../work_state.py", {})

    def test_a_missing_required_argument_is_refused_before_any_subprocess(self) -> None:
        with self.assertRaises(self.server.ToolError):
            self.server.call_tool("summarize_durable_session", {})
        with self.assertRaises(self.server.ToolError):
            self.server.call_tool("summarize_durable_session", {"session": "   "})

    def test_an_unreadable_session_is_a_tool_result_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(os.environ)
            environment["COGNITIVE_POWERS_DATA"] = temporary
            completed = subprocess.run(
                [sys.executable, str(SERVER)],
                input=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "tools/call",
                        "params": {
                            "name": "summarize_durable_session",
                            "arguments": {"session": "absent"},
                        },
                    }
                )
                + "\n",
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        response = json.loads(completed.stdout.strip())
        self.assertTrue(response["result"]["isError"])
        self.assertNotIn("Traceback", response["result"]["content"][0]["text"])

    def test_storage_inspection_runs_against_an_isolated_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(os.environ)
            environment["COGNITIVE_POWERS_DATA"] = temporary
            completed = subprocess.run(
                [sys.executable, str(SERVER)],
                input=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 10,
                        "method": "tools/call",
                        "params": {
                            "name": "inspect_evidence_storage",
                            "arguments": {"largest": 3},
                        },
                    }
                )
                + "\n",
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            response = json.loads(completed.stdout.strip())
            self.assertFalse(response["result"]["isError"], completed.stderr)
            report = response["result"]["structuredContent"]
            self.assertEqual(
                Path(report["data_root"]).resolve(), Path(temporary).resolve()
            )


class EvidenceServerPackagingTests(unittest.TestCase):
    def test_the_manifest_declares_the_server_with_a_resolvable_command(self) -> None:
        manifest = json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))
        servers = manifest["mcpServers"]
        entry = servers["cognitive-powers-evidence"]
        # The command must not be a bare interpreter name. On Windows python3
        # resolves to a Microsoft Store stub that exits without running Python,
        # which is why the plugin makes the operator name the interpreter.
        self.assertEqual(entry["command"], "${user_config.python_executable}")
        self.assertEqual(
            entry["args"], ["${CLAUDE_PLUGIN_ROOT}/mcp/evidence_server.py"]
        )
        self.assertTrue(SERVER.is_file())

    def test_doctor_reports_the_declared_server_and_notices_a_missing_script(
        self,
    ) -> None:
        spec = importlib.util.spec_from_file_location(
            "doctor", PLUGIN_ROOT / "scripts" / "doctor.py"
        )
        assert spec is not None and spec.loader is not None
        doctor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(doctor)

        surfaces = doctor.host_surfaces(PLUGIN_ROOT)["surfaces"]
        claude = {surface["host"]: surface for surface in surfaces}["claude-code"]
        self.assertEqual(claude["mcpServers"], ["cognitive-powers-evidence"])
        self.assertTrue(claude["mcpServerScriptsPresent"])

        # A declared server whose script is absent starts on every session and
        # fails there, so the packaging report has to be able to say so.
        absent = {"args": ["${CLAUDE_PLUGIN_ROOT}/mcp/not-shipped.py"]}
        self.assertFalse(doctor._mcp_server_script_exists(PLUGIN_ROOT, absent))

    def test_the_server_imports_no_third_party_module(self) -> None:
        """A server that needed installing to report on the installation is
        self-defeating, and the plugin ships no dependency of its own."""
        source = SERVER.read_text(encoding="utf-8")
        allowed = {
            "__future__",
            "json",
            "os",
            "subprocess",
            "sys",
            "pathlib",
            "typing",
        }
        imported = set()
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("import "):
                imported.add(stripped.removeprefix("import ").split(".")[0].strip())
            elif stripped.startswith("from "):
                imported.add(stripped.removeprefix("from ").split()[0].split(".")[0])
        self.assertEqual(imported - allowed, set())


if __name__ == "__main__":
    unittest.main()
