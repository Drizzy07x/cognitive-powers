from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "benchmarks" / "evaluators" / "multi_file_quality.py"
SPEC = importlib.util.spec_from_file_location("multi_file_quality", MODULE_PATH)
quality = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(quality)


class MultiFileQualityTests(unittest.TestCase):
    def _write_completed_fixture(self, root: Path) -> None:
        package = root / "src" / "tasker"
        tests = root / "tests"
        package.mkdir(parents=True)
        tests.mkdir()
        (root / "src" / "__init__.py").write_text("", encoding="utf-8")
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "model.py").write_text(
            "from dataclasses import dataclass\n"
            "@dataclass(frozen=True)\n"
            "class Task:\n"
            " title: str\n completed: bool=False\n priority: str='normal'\n"
            " def __post_init__(self):\n"
            "  if self.priority not in {'low','normal','high'}: raise ValueError('invalid priority')\n",
            encoding="utf-8",
        )
        (package / "storage.py").write_text(
            "import json\nfrom dataclasses import asdict\nfrom pathlib import Path\nfrom .model import Task\n"
            "class TaskStore:\n"
            " def __init__(self,path): self.path=Path(path)\n"
            " def load(self):\n"
            "  if not self.path.exists(): return []\n"
            "  return [Task(**row) for row in json.loads(self.path.read_text())]\n"
            " def save(self,tasks): self.path.write_text(json.dumps([asdict(t) for t in tasks]))\n"
            " def add(self,title,priority='normal'):\n"
            "  tasks=self.load(); task=Task(title=title,priority=priority); tasks.append(task); self.save(tasks); return task\n",
            encoding="utf-8",
        )
        (package / "cli.py").write_text(
            "import argparse,json\nfrom dataclasses import asdict\nfrom pathlib import Path\nfrom .storage import TaskStore\n"
            "p=argparse.ArgumentParser(); p.add_argument('--store',type=Path,required=True); s=p.add_subparsers(dest='cmd',required=True)\n"
            "a=s.add_parser('add'); a.add_argument('title'); a.add_argument('--priority',choices=['low','normal','high'],default='normal'); s.add_parser('list')\n"
            "args=p.parse_args(); store=TaskStore(args.store)\n"
            "if args.cmd=='add': print(json.dumps(asdict(store.add(args.title,args.priority))))\n"
            "else: print(json.dumps([asdict(t) for t in store.load()]))\n",
            encoding="utf-8",
        )
        (tests / "test_priority.py").write_text(
            "# public compatibility regression\n# python -m src.tasker.cli --priority high\n",
            encoding="utf-8",
        )

    def test_scores_complete_public_and_compatible_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_completed_fixture(root)
            events = root / "events.jsonl"
            events.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "exit_code": 0,
                            "command": "python -m unittest discover -s tests",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            message = root / "message.txt"
            message.write_text(
                "Priority works through the CLI; legacy compatibility tests passed.",
                encoding="utf-8",
            )

            report = quality.evaluate(root, events, message)

            self.assertEqual(report["score"], 100)
            self.assertEqual(report["critical_errors"], [])

    def test_marks_missing_end_to_end_behavior_critical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "tests" / "test_placeholder.py").write_text("", encoding="utf-8")
            events = root / "events.jsonl"
            events.write_text("", encoding="utf-8")
            message = root / "message.txt"
            message.write_text("incomplete", encoding="utf-8")

            report = quality.evaluate(root, events, message)

            self.assertIn(
                "priority does not work end to end through the public CLI",
                report["critical_errors"],
            )


if __name__ == "__main__":
    unittest.main()
