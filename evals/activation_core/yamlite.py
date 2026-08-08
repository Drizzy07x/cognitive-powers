#!/usr/bin/env python3
"""Read the subset of YAML the activation corpus is written in.

The corpus is YAML because that is what a case file is expected to look like,
and because a human edits it far more often than a program writes it. It is
parsed here rather than by a library because this project ships no runtime
dependency, and the reason applies with unusual force to an eval harness:
several components exist to report whether an installation works, and a
measurement that needed installing first would be reporting on its own
prerequisites.

The subset is deliberately narrow -- block mappings, block sequences, plain and
quoted scalars, comments, and nothing else. Anchors, flow collections, multiple
documents, multi-line scalars and tags are rejected by name rather than skipped,
because a corpus file that silently loses a case is a corpus that overstates
every rate computed from it.
"""

from __future__ import annotations

import re
from typing import Any

SCALAR_TRUE = frozenset({"true", "yes", "on"})
SCALAR_FALSE = frozenset({"false", "no", "off"})
SCALAR_NULL = frozenset({"null", "~", ""})

# Constructs that mean something this parser does not implement. Each is
# refused with its own name so the error tells the author what to remove
# instead of pointing at the line that failed to become a case.
UNSUPPORTED = (
    ("&", "anchors"),
    ("*", "aliases"),
    ("!", "tags"),
    ("|", "literal block scalars"),
    (">", "folded block scalars"),
)

KEY_PATTERN = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:(?P<rest>\s.*|)$")
INT_PATTERN = re.compile(r"^-?\d+$")
FLOAT_PATTERN = re.compile(r"^-?\d+\.\d+$")


class YamlError(ValueError):
    """Raised when the document is malformed or outside the supported subset."""


class _Line:
    __slots__ = ("number", "indent", "text")

    def __init__(self, number: int, indent: int, text: str) -> None:
        self.number = number
        self.indent = indent
        self.text = text


def _strip_comment(raw: str) -> str:
    """Drop a trailing comment that is not inside a quoted scalar."""
    quote: str | None = None
    escaped = False
    for index, char in enumerate(raw):
        if escaped:
            escaped = False
            continue
        # A backslash escape only exists inside a double-quoted scalar; the
        # single-quoted form doubles the quote instead. Without this, the
        # escaped quote in a prompt like "say \" now" closed the scalar early,
        # a later '#' truncated the line, and the case failed to load at all --
        # for a prompt whose only sin was containing a quotation mark.
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            continue
        # A '#' opens a comment only at the start of a token, which is the YAML
        # rule and not a convenience: without the preceding-space test, "bug#1"
        # or a URL fragment inside a plain scalar would be truncated and the
        # case would still load, just carrying a different prompt than the one
        # written. A '#' that does follow a space ends a plain scalar even
        # mid-sentence, which is why the corpus writes every prompt quoted.
        if char == "#" and (index == 0 or raw[index - 1] in " \t"):
            return raw[:index]
    return raw


def _tokenize(text: str) -> list[_Line]:
    lines: list[_Line] = []
    for number, raw in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError(f"line {number}: tab used for indentation")
        body = _strip_comment(raw).rstrip()
        if not body.strip():
            continue
        if body.strip() == "---":
            continue
        if body.strip() == "...":
            continue
        indent = len(body) - len(body.lstrip(" "))
        lines.append(_Line(number, indent, body.strip()))
    return lines


# Three chained str.replace calls read the escapes in the wrong order: the
# first pass rewrote the "\n" that the second pass was still meant to see as
# half of an escaped backslash, so the literal "a\\nb" came back as "a\" plus a
# newline instead of "a\nb". One left-to-right scan cannot double-read a
# character it has already consumed. An escape this parser does not know is
# left as written rather than guessed at.
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}


def _unescape(inner: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(inner):
        char = inner[index]
        if char != "\\" or index + 1 >= len(inner):
            out.append(char)
            index += 1
            continue
        following = inner[index + 1]
        out.append(_ESCAPES.get(following, "\\" + following))
        index += 2
    return "".join(out)


def _scalar(raw: str, number: int) -> Any:
    text = raw.strip()
    if not text:
        return None
    if text[0] in "'\"":
        if len(text) < 2 or text[-1] != text[0]:
            raise YamlError(f"line {number}: unterminated quoted scalar")
        inner = text[1:-1]
        if text[0] == '"':
            return _unescape(inner)
        return inner.replace("''", "'")
    # The empty flow forms are the one exception. "forbid: []" is how an author
    # writes "nothing here", and rejecting it would push every empty list onto
    # a block form that reads as an omission rather than as a stated zero.
    if text == "[]":
        return []
    if text == "{}":
        return {}
    if text[0] in "[{":
        raise YamlError(f"line {number}: flow collections are not supported")
    for marker, name in UNSUPPORTED:
        if text.startswith(marker):
            raise YamlError(f"line {number}: {name} are not supported")
    lowered = text.lower()
    if lowered in SCALAR_TRUE:
        return True
    if lowered in SCALAR_FALSE:
        return False
    if lowered in SCALAR_NULL:
        return None
    if INT_PATTERN.fullmatch(text):
        return int(text)
    if FLOAT_PATTERN.fullmatch(text):
        return float(text)
    return text


def _parse_block(lines: list[_Line], start: int, indent: int) -> tuple[Any, int]:
    if start >= len(lines):
        return None, start
    if lines[start].text.startswith("- "):
        return _parse_sequence(lines, start, indent)
    if lines[start].text == "-":
        return _parse_sequence(lines, start, indent)
    return _parse_mapping(lines, start, indent)


def _parse_sequence(lines: list[_Line], start: int, indent: int) -> tuple[Any, int]:
    items: list[Any] = []
    index = start
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        if not (line.text == "-" or line.text.startswith("- ")):
            break
        rest = line.text[1:].strip()
        index += 1
        if not rest:
            child_indent = lines[index].indent if index < len(lines) else -1
            if child_indent <= indent:
                items.append(None)
                continue
            value, index = _parse_block(lines, index, child_indent)
            items.append(value)
            continue
        match = KEY_PATTERN.match(rest)
        if match is None:
            items.append(_scalar(rest, line.number))
            continue
        # An inline "- key: value" opens a mapping whose remaining keys are
        # indented past the dash. Re-reading it as a fresh mapping at that
        # deeper indent is what lets the common case list of records parse
        # without a separate code path for the first key of each record.
        inline = _Line(line.number, indent + 2, rest)
        rebuilt = [inline, *lines[index:]]
        value, consumed = _parse_mapping(rebuilt, 0, indent + 2)
        items.append(value)
        index += consumed - 1
    return items, index


def _parse_mapping(lines: list[_Line], start: int, indent: int) -> tuple[Any, int]:
    mapping: dict[str, Any] = {}
    index = start
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        if line.text.startswith("- "):
            break
        match = KEY_PATTERN.match(line.text)
        if match is None:
            raise YamlError(f"line {line.number}: expected 'key: value'")
        key = match.group("key")
        if key in mapping:
            raise YamlError(f"line {line.number}: duplicate key {key!r}")
        rest = match.group("rest").strip()
        index += 1
        if rest:
            mapping[key] = _scalar(rest, line.number)
            continue
        # A sequence may sit at its own key's indent -- the most common way
        # anyone writes one. The guard below used to reject that before this
        # test could see it: the value became None and the dash lines then fell
        # out of the mapping loop as "unexpected indentation", so the shape the
        # comment here claimed to support was the one shape that could not
        # parse. The dead re-assignment that followed is gone with it.
        child = lines[index] if index < len(lines) else None
        own_indent_sequence = (
            child is not None
            and child.indent == indent
            and (child.text == "-" or child.text.startswith("- "))
        )
        if child is None or (child.indent <= indent and not own_indent_sequence):
            mapping[key] = None
            continue
        mapping[key], index = _parse_block(lines, index, child.indent)
    return mapping, index


def loads(text: str) -> Any:
    """Parse a document, or raise ``YamlError`` naming the offending line."""
    lines = _tokenize(text)
    if not lines:
        return None
    base = lines[0].indent
    value, consumed = _parse_block(lines, 0, base)
    if consumed != len(lines):
        raise YamlError(f"line {lines[consumed].number}: unexpected indentation")
    return value
