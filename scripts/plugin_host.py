#!/usr/bin/env python3
"""Resolve the installed plugin root and which host is running it.

One question, answered once, for every hook that needs it. Two hooks of one
plugin resolving different installs in one session is the condition
``selective_hooks`` names as fatal to the Stop gate, and a second private copy
of this lookup in a new hook is exactly how that divergence would start.
"""

from __future__ import annotations

import os
from pathlib import Path

FALLBACK_ROOT = Path(__file__).resolve().parents[1]


def resolve_host(default_root: Path | None = None) -> tuple[Path, bool]:
    """Return the plugin root and whether the running host is Claude Code.

    ``PLUGIN_ROOT`` is tried first because ``selective_hooks._roots`` does.

    The host answer comes from the same lookup as the root on purpose. Reading
    the root by validated precedence while deciding the host from a separate
    bare ``CLAUDE_PLUGIN_ROOT`` test let a stale variable resolve the root one
    way and describe the host the other, which put Claude Code wording in front
    of Codex for most of the catalogue.
    """
    for variable in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        value = os.environ.get(variable)
        if not value:
            continue
        try:
            root = Path(value).expanduser().resolve()
        except OSError:
            continue
        if (root / "skills").is_dir():
            return root, variable == "CLAUDE_PLUGIN_ROOT"
    return default_root if default_root is not None else FALLBACK_ROOT, False
