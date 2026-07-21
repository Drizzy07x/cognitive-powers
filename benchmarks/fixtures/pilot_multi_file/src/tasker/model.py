"""Task domain model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    title: str
    completed: bool = False
