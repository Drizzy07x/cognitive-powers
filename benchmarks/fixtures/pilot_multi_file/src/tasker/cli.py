"""Public command-line interface."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .storage import TaskStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tasker")
    parser.add_argument("--store", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("title")
    commands.add_parser("list")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = TaskStore(args.store)
    if args.command == "add":
        print(json.dumps(asdict(store.add(args.title))))
        return 0
    print(json.dumps([asdict(task) for task in store.load()]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
