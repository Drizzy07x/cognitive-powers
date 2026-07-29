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

# Function words carry no evidence about which workflow a prompt wants, but
# cosine similarity spends real mass on them: "add a newline at the end of the
# file" scored 0.264 against verify-web-behavior, three quarters of it from
# "at", "the", and the "end" of "end-to-end flows". That put ordinary editing
# above the firing threshold and genuine multi-file work below it, so the gate
# could only be set high enough to silence both.
STOPWORDS = frozenset(
    """
    a an and are as at be been but by can do does for from had has have how i
    if in into is it its may must never not of on only or over per should so
    than that the their then there these they this those to use used uses
    using was were what when where which while who why will with without you
    your yours just also very more most much some any each both other another
    """.split()
)


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
    tokens: list[str] = []
    for raw in TOKEN_PATTERN.findall(text.casefold()):
        if raw in STOPWORDS:
            continue
        stemmed = _stem(raw)
        if stemmed in STOPWORDS:
            continue
        tokens.append(stemmed)
    return tokens


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


def _unseen_weight(document_count: int) -> float:
    """Weight for a query token that no skill description uses.

    ``_idf`` scores a token held by one description at ``log((1+N)/2)+1``; a
    token held by none is at least that rare, so it takes the count-zero value
    of the same curve. The former fallback of 1.0 was the weight of a token
    every skill shares, which let a prompt made mostly of off-domain words
    score as though those words were unremarkable.
    """
    return math.log(1 + document_count) + 1


def _vector(
    tokens: Iterable[str], idf: Mapping[str, float], unseen: float = 1.0
) -> dict[str, float]:
    counts = Counter(tokens)
    return {token: count * idf.get(token, unseen) for token, count in counts.items()}


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    common = set(left) & set(right)
    numerator = sum(left[token] * right[token] for token in common)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def _explicit_skill_requested(query: str, name: str) -> bool:
    # A skill is named the same way whether it is typed as it appears on disk
    # or as ordinary prose, so "solve-efficiently", "solve efficiently", and
    # "solve_efficiently" are one request. Only the separator is loosened; the
    # words themselves still have to match in order.
    spelling = re.escape(name.casefold()).replace(r"\-", "[-_ ]")
    pattern = rf"(?<![a-z0-9_-])\$?{spelling}(?![a-z0-9_-])"
    return re.search(pattern, query.casefold()) is not None


def _document(name: str, description: str) -> list[str]:
    return tokenize(f"{name.replace('-', ' ')} {description}")


def shared_token_count(query: str, name: str, descriptions: Mapping[str, str]) -> int:
    """Distinct content words the prompt and this skill's listing both use.

    The score alone cannot tell a real match from an incidental one: "reformat
    this file" reaches solve-efficiently on the single word "file", and scores
    as high doing it as a genuine multi-file request does on four words. The
    count is what separates them, so the hook gates on it.
    """
    return len(set(tokenize(query)) & set(_document(name, descriptions[name])))


def rank_skills(query: str, descriptions: Mapping[str, str]) -> list[tuple[str, float]]:
    names = list(descriptions)
    documents = [_document(name, descriptions[name]) for name in names]
    idf = _idf(documents)
    query_tokens = tokenize(query)
    query_vector = _vector(query_tokens, idf, _unseen_weight(len(documents)))
    ranked: list[tuple[str, float]] = []
    for name, tokens in zip(names, documents):
        score = _cosine(query_vector, _vector(tokens, idf))
        if _explicit_skill_requested(query, name):
            score += 2.0
        ranked.append((name, round(score, 8)))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))


# Calibrated against benchmarks/skill_routing_cases.json plus off-domain
# prompts (renames, commits, typo fixes, small talk, formatting). Suggesting a
# workflow for "fix the typo in the README" costs more than staying quiet,
# because the agent stops reading a channel that is usually wrong -- but so
# does staying quiet on the work the plugin exists for, which is what a single
# high floor used to do to two positives in five.
#
# What separates the two is not the score. Ordinary editing and genuine
# workflow requests overlap almost exactly in score; they do not overlap in how
# much vocabulary they share with the skill they land on. Across the case file
# no off-domain prompt shares more than one content word with its top skill,
# and all but one positive shares at least two.
MIN_SHARED_TOKENS = 2
# Below this the shared words are too thin a base to name a workflow even when
# there are several of them.
MIN_SCORE = 0.05
# Either a strong absolute match, or a decisive lead over the runner-up. The
# old gate required both, so a prompt that beat every other skill by 0.2 was
# still discarded for scoring 0.251 against a 0.27 floor.
CONFIDENT_SCORE = 0.14
DECISIVE_MARGIN = 0.03
# A tie is disqualifying on its own terms and cannot be outvoted by a high
# score: two skills the prompt fits equally well are two skills whose wording
# it matched as a family, and a confident score only means it matched the
# family strongly.
MIN_MARGIN = 0.02
# rank_skills adds this when the prompt names a skill outright; such a request
# is explicit rather than inferred and always clears the bar.
EXPLICIT_REQUEST_SCORE = 2.0

# The plugin's own name is a request for its workflows, and solve-efficiently
# advertises exactly that ("when Cognitive Powers is requested by name"). Only
# individual skill names used to be recognised, so the one phrase a user is
# most likely to reach for when the plugin seems idle -- its name -- was the
# one phrase that routed nowhere. It is also the one phrase that survives the
# prompt being written in a language these English descriptions cannot score.
PLUGIN_ALIAS_PATTERN = re.compile(r"(?<![a-z0-9_-])cognitive[-_ ]?powers(?![a-z0-9_-])")


def decide(query: str, descriptions: Mapping[str, str]) -> dict[str, object]:
    """Whether to name a skill for this prompt, and which one.

    Lives beside ``rank_skills`` for the reason in the module docstring: the
    thresholds are as much of the routing decision as the ordering is, and a
    hook holding its own copy of them can pass every checked-in case while
    staying silent at runtime. That is not hypothetical -- it is what shipped.
    The benchmark scored ranking only, reported 0.96, and never observed that
    a third of those ranked-first prompts produced no suggestion at all.
    """
    if len(descriptions) < 2:
        # Margin is meaningless without a runner-up to compare against.
        return {"status": "skipped", "reason": "not enough skills to rank"}

    ranking = rank_skills(query, descriptions)
    name, score = ranking[0]
    margin = round(score - ranking[1][1], 8)
    shared = shared_token_count(query, name, descriptions)
    outcome: dict[str, object] = {
        "status": "suggested",
        "skill": name,
        "score": score,
        "margin": margin,
        "shared_tokens": shared,
    }
    blocked = dict(outcome, status="below-threshold")

    # An explicit request is not an inference, so it answers to no threshold:
    # the user named the skill, or named the plugin and left the choice of
    # workflow to the ranking.
    if score >= EXPLICIT_REQUEST_SCORE:
        return dict(outcome, reason="named skill")
    if PLUGIN_ALIAS_PATTERN.search(query.casefold()):
        return dict(outcome, reason="named plugin")
    if shared < MIN_SHARED_TOKENS:
        return dict(blocked, reason="too few shared words")
    if score < MIN_SCORE:
        return dict(blocked, reason="weak match")
    if margin < MIN_MARGIN:
        return dict(blocked, reason="near tie")
    if score < CONFIDENT_SCORE and margin < DECISIVE_MARGIN:
        return dict(blocked, reason="no clear winner")
    return dict(outcome, reason="description match")


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
