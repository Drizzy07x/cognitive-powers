#!/usr/bin/env python3
"""One conversion between provider token-usage shapes.

The receipt writer and the durable recorder both need this. The recorder
re-derives receipt totals rather than trusting them, so the two must agree
exactly: if the recorder reads fewer provider shapes than the writer accepts,
it rejects correct evidence. Keeping one implementation removes that failure
mode instead of detecting it after the fact.
"""

from __future__ import annotations

from typing import Any

ANTHROPIC_KEYS = ("cache_read_input_tokens", "cache_creation_input_tokens")
CODEX_CACHE_KEY = "cached_input_tokens"


class UsageError(ValueError):
    """Raised when a usage object cannot be read as either provider shape."""


def _usage_int(usage: dict[str, Any], key: str, *, default: int | None = None) -> int:
    value = usage.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UsageError(f"usage.{key} must be a non-negative integer")
    return value


def normalize_usage(usage: dict[str, Any]) -> tuple[int, int, int, str]:
    """Return ``(total_input, cached_input, output, schema)``.

    This is a conversion, not a rename. The providers disagree about what
    ``input_tokens`` counts:

    - Codex reports one ``input_tokens`` that already contains the cached
      prefix, alongside ``cached_input_tokens``.
    - Anthropic reports ``input_tokens`` for uncached input only, and states
      the cached prefix separately as ``cache_read_input_tokens`` plus
      ``cache_creation_input_tokens``.

    Aliasing the Anthropic field would report a total that omits the cached
    prefix and would trip the cached-exceeds-input guard, because a cache read
    routinely dwarfs the uncached remainder.
    """
    if not isinstance(usage, dict):
        raise UsageError("usage must be an object")
    anthropic = [key for key in ANTHROPIC_KEYS if key in usage]
    if anthropic and CODEX_CACHE_KEY in usage:
        # The two spellings mean different things: the Codex total already
        # contains the cached prefix the Anthropic keys state separately.
        # Guessing which one is authoritative would silently inflate or
        # deflate the total, so refuse the record instead.
        raise UsageError(
            "usage mixes provider schemas: "
            f"{CODEX_CACHE_KEY} with {', '.join(anthropic)}"
        )
    if anthropic:
        uncached = _usage_int(usage, "input_tokens")
        # Either key alone selects this branch, so neither may be required: a
        # first turn writes the cache without reading it, and a fully cached
        # turn reads without writing.
        cache_read = _usage_int(usage, "cache_read_input_tokens", default=0)
        cache_creation = _usage_int(usage, "cache_creation_input_tokens", default=0)
        output_tokens = _usage_int(usage, "output_tokens")
        # Writing the cache is billed as fresh input: it was not read back.
        return (
            uncached + cache_creation + cache_read,
            cache_read,
            output_tokens,
            "anthropic",
        )
    input_tokens = _usage_int(usage, "input_tokens")
    cached_tokens = _usage_int(usage, "cached_input_tokens")
    output_tokens = _usage_int(usage, "output_tokens")
    if cached_tokens > input_tokens:
        raise UsageError("cached input tokens cannot exceed input tokens")
    return input_tokens, cached_tokens, output_tokens, "codex"
