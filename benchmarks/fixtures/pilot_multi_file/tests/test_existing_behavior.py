from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.tasker.storage import TaskStore


class ExistingBehaviorTests(unittest.TestCase):
    def test_missing_store_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(TaskStore(Path(temporary) / "tasks.json").load(), [])

    def test_add_title_through_public_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary) / "tasks.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "src.tasker.cli",
                    "--store",
                    str(store),
                    "add",
                    "Ship",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["title"], "Ship")


if __name__ == "__main__":
    unittest.main()
