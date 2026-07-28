#!/usr/bin/env python3
"""Deterministic lexical ranking of skill descriptions.

Extracted from the routing benchmark so that the prompt-submit hook and the
benchmark score a prompt with exactly the same code. A hook carrying its own
copy of this logic could satisfy every checked-in routing case while ranking
something else at runtime, which is the one failure the benchmark exists to
rule out.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def _stem(token: str) -> str:
    for suffix in (
        "ization",
        "ation",
        "ments",
        "ment",
        "ingly",
        "edly",
        "ing",
        "ies",
        "ed",
        "es",
        "s",
    ):
        if token.endswith(suffix) and len(token) > len(suffix) + 3:
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> list[str]:
    return [_stem(token) for token in TOKEN_PATTERN.findall(text.casefold())]


def load_skill_descriptions(root: Path) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for skill_file in sorted((root / "skills").glob("*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\n(.*?)\n---", text, re.DOTALL)
        if match is None:
            raise ValueError(f"missing frontmatter: {skill_file}")
        name_match = re.search(r"^name:\s*(.+?)\s*$", match.group(1), re.MULTILINE)
        description_match = re.search(
            r"^description:\s*(.+?)\s*$", match.group(1), re.MULTILINE
        )
        if name_match is None or description_match is None:
            raise ValueError(f"missing name or single-line description: {skill_file}")
        name = name_match.group(1).strip(" \"'")
        description = description_match.group(1).strip(" \"'")
        # Rank the text the host actually lists. Claude Code appends
        # when_to_use to description in the skill listing, so scoring the
        # description alone would measure something the model never sees.
        trigger_match = re.search(
            r"^when_to_use:\s*(.+?)\s*$", match.group(1), re.MULTILINE
        )
        if trigger_match is not None:
            # Stripped outside the f-string: a backslash inside a replacement
            # field is a syntax error before Python 3.12, and the support
            # matrix still declares 3.11.
            trigger = trigger_match.group(1).strip(" \"'")
            description = f"{description} {trigger}"
        if name in descriptions:
            raise ValueError(f"duplicate skill name: {name}")
        descriptions[name] = description
    return descriptions


def _idf(documents: Sequence[Sequence[str]]) -> dict[str, float]:
    document_count = len(documents)
    frequencies: Counter[str] = Counter()
    for document in documents:
        frequencies.update(set(document))
    return {
        token: math.log((1 + document_count) / (1 + count)) + 1
        for token, count in frequencies.items()
    }


def _vector(tokens: Iterable[str], idf: Mapping[str, float]) -> dict[str, float]:
    counts = Counter(tokens)
    return {token: count * idf.get(token, 1.0) for token, count in counts.items()}


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    common = set(left) & set(right)
    numerator = sum(left[token] * right[token] for token in common)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def _explicit_skill_requested(query: str, name: str) -> bool:
    pattern = rf"(?<![a-z0-9_-])\$?{re.escape(name.casefold())}(?![a-z0-9_-])"
    return re.search(pattern, query.casefold()) is not None


def rank_skills(query: str, descriptions: Mapping[str, str]) -> list[tuple[str, float]]:
    names = list(descriptions)
    documents = [
        tokenize(f"{name.replace('-', ' ')} {descriptions[name]}") for name in names
    ]
    idf = _idf(documents)
    query_tokens = tokenize(query)
    query_vector = _vector(query_tokens, idf)
    ranked: list[tuple[str, float]] = []
    for name, tokens in zip(names, documents):
        score = _cosine(query_vector, _vector(tokens, idf))
        if _explicit_skill_requested(query, name):
            score += 2.0
        ranked.append((name, round(score, 8)))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))


def description_collisions(
    descriptions: Mapping[str, str], threshold: float
) -> list[dict[str, object]]:
    names = list(descriptions)
    documents = [tokenize(descriptions[name]) for name in names]
    idf = _idf(documents)
    vectors = [_vector(tokens, idf) for tokens in documents]
    collisions: list[dict[str, object]] = []
    for left_index, left_name in enumerate(names):
        for right_index in range(left_index + 1, len(names)):
            similarity = _cosine(vectors[left_index], vectors[right_index])
            if similarity >= threshold:
                collisions.append(
                    {
                        "left": left_name,
                        "right": names[right_index],
                        "similarity": round(similarity, 4),
                    }
                )
    return sorted(
        collisions, key=lambda item: (-float(item["similarity"]), str(item["left"]))
    )
