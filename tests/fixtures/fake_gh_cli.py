#!/usr/bin/env python3
"""Minimal GitHub CLI fixture for the isolated installer transaction tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if argv[:2] == ["auth", "status"] or argv[:2] == ["auth", "setup-git"]:
        return 0
    allowed = {
        "repos/Drizzy07x/cognitive-powers/git/ref/tags/v1.6.0",
        "repos/Drizzy07x/cognitive-powers/commits/v1.6.0",
        "repos/Drizzy07x/cognitive-powers/commits/v1.5.2",
    }
    if argv[:1] != ["api"] or len(argv) < 2 or argv[1] not in allowed:
        return 64
    state = json.loads(Path(os.environ["FAKE_CODEX_STATE"]).read_text(encoding="utf-8"))
    if argv[1].endswith("/commits/v1.6.0"):
        print(json.dumps({"sha": state["release_commit"]}))
    elif argv[1].endswith("/commits/v1.5.2"):
        print(json.dumps({"sha": state["previous_commit"]}))
    else:
        print(json.dumps({"ref": "refs/tags/v1.6.0"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
