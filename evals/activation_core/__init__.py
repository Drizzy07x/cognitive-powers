"""Measure whether Cognitive Powers workflows activate on real requests.

The pieces are separated by what they need to run. ``yamlite``, ``cases``,
``transcript``, ``scoring`` and ``report`` are pure: no processes, no network,
no filesystem beyond reading a corpus file, which is why the whole judgement
path is unit-testable offline and belongs in the gate. ``session`` and
``runner`` spawn the host, cost money, and stay outside it -- the same split the
two provider-dependent benchmark runners already live under.
"""

from __future__ import annotations

__all__ = [
    "arms",
    "cases",
    "fixtures",
    "report",
    "runner",
    "scoring",
    "session",
    "transcript",
    "yamlite",
]
