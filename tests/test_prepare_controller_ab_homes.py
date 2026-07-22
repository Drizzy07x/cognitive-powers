from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import prepare_controller_ab_homes as homes  # noqa: E402


class PrepareControllerAbHomesTests(unittest.TestCase):
    def test_minimal_config_enables_only_cognitive_powers(self) -> None:
        config = homes._minimal_config("gpt-test", "medium")
        self.assertIn('[plugins."cognitive-powers@personal"]', config)
        self.assertNotIn("context-mode", config)
        self.assertNotIn("openai-bundled", config)
        self.assertIn("memories = false", config)
        self.assertIn("multi_agent = true", config)

    def test_dirty_source_is_rejected(self) -> None:
        completed = mock.Mock(returncode=0, stdout="abc\n", stderr="")
        dirty = mock.Mock(returncode=0, stdout=" M file.py\n", stderr="")
        with mock.patch.object(homes.subprocess, "run", side_effect=[completed, dirty]):
            with self.assertRaisesRegex(homes.HomePreparationError, "must be clean"):
                homes._git_identity(Path("."))

    def test_copy_plugin_excludes_runtime_and_git_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            (source / ".git").mkdir(parents=True)
            (source / "__pycache__").mkdir()
            (source / "scripts").mkdir()
            (source / ".git" / "HEAD").write_text("ref", encoding="utf-8")
            (source / "__pycache__" / "x.pyc").write_bytes(b"cache")
            (source / "scripts" / "run.py").write_text("pass\n", encoding="utf-8")
            homes._copy_plugin(source, destination)
            self.assertTrue((destination / "scripts" / "run.py").is_file())
            self.assertFalse((destination / ".git").exists())
            self.assertFalse((destination / "__pycache__").exists())

    def test_login_must_be_chatgpt(self) -> None:
        completed = mock.Mock(
            returncode=0, stdout="Logged in using API key\n", stderr=""
        )
        with mock.patch.object(homes.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(homes.HomePreparationError, "ChatGPT"):
                homes._login_status("codex", Path("home"))


if __name__ == "__main__":
    unittest.main()
