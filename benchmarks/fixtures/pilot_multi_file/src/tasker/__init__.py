"""Small JSON-backed task application."""

from .model import Task
from .storage import TaskStore

__all__ = ["Task", "TaskStore"]
