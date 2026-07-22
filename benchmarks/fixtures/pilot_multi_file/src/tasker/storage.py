"""JSON persistence for tasks."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .model import Task


class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Task]:
        if not self.path.exists():
            return []
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        return [Task(**row) for row in rows]

    def save(self, tasks: list[Task]) -> None:
        self.path.write_text(
            json.dumps([asdict(task) for task in tasks]), encoding="utf-8"
        )

    def add(self, title: str) -> Task:
        tasks = self.load()
        task = Task(title=title)
        tasks.append(task)
        self.save(tasks)
        return task
