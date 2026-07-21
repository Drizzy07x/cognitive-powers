"""Load configuration with environment values overriding the file."""

from __future__ import annotations

import os
from collections.abc import Mapping


def load_timeout(
    file_values: Mapping[str, str], environment: Mapping[str, str] | None = None
) -> float:
    """Apply configuration precedence: environment overrides file timeout."""
    environment = os.environ if environment is None else environment
    raw_timeout = environment.get("APP_TIMEOUT", file_values.get("timeout", "30"))
    timeout = float(raw_timeout)
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    return timeout
