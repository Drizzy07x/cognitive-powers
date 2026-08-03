#!/usr/bin/env python3
"""Load and validate the activation corpus.

Validation is strict and total: every case file is checked before any process
is spawned, because the alternative is discovering a typo in case ninety after
paying for eighty-nine runs. Unknown keys are errors rather than being ignored,
since a misspelled ``expect`` would otherwise turn a should-fire case into a
should-not-fire case and report the inversion as a pass.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from .yamlite import YamlError, loads

SUPPORTED_VERSION = 1
LANGUAGES = frozenset({"en", "es"})
FIXTURES = frozenset({"webshop", "pylib", "paper", "bare"})
MODES = frozenset({"all", "any"})
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

REQUIRED_KEYS = frozenset({"id", "prompt", "expect"})
OPTIONAL_KEYS = frozenset(
    {"lang", "forbid", "fixture", "quick", "mode", "why", "notes"}
)
KNOWN_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS


class CorpusError(ValueError):
    """Raised when the corpus is unusable as written."""


class Case(NamedTuple):
    """One prompt, its expectation, and where it came from."""

    case_id: str
    prompt: str
    lang: str
    expect: tuple[str, ...]
    forbid: tuple[str, ...]
    fixture: str
    quick: bool
    mode: str
    source: str

    @property
    def should_fire(self) -> bool:
        """True when some workflow is required to fire for this prompt."""
        return bool(self.expect)

    @property
    def polarity(self) -> str:
        return "should-fire" if self.should_fire else "should-not-fire"

    @property
    def multi(self) -> bool:
        return len(self.expect) > 1

    def satisfied_by(self, fired: Iterable[str]) -> bool:
        """Whether an observed set of invocations passes this case.

        One rule for both polarities, deliberately. Keeping under-triggering
        and over-triggering in a single pass definition is what stops a
        configuration from being reported as healthy while it fails one of them:
        a should-not-fire case passes by staying silent, so noise is a failed
        run rather than a separate metric nobody reads.
        """
        observed = set(fired)
        if observed & set(self.forbid):
            return False
        if not self.expect:
            return not observed
        wanted = set(self.expect)
        if self.mode == "any":
            return bool(observed & wanted)
        return wanted <= observed


def _string_list(value: Any, field: str, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CorpusError(f"{where}: {field} must be a list")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CorpusError(f"{where}: {field} entries must be non-empty strings")
        items.append(item.strip())
    return tuple(items)


def _case(raw: Any, source: str, index: int, known_skills: frozenset[str]) -> Case:
    where = f"{source}[{index}]"
    if not isinstance(raw, dict):
        raise CorpusError(f"{where}: case must be a mapping")
    unknown = sorted(set(raw) - KNOWN_KEYS)
    if unknown:
        raise CorpusError(f"{where}: unknown keys {', '.join(unknown)}")
    missing = sorted(REQUIRED_KEYS - set(raw))
    if missing:
        raise CorpusError(f"{where}: missing keys {', '.join(missing)}")

    case_id = raw["id"]
    if not isinstance(case_id, str) or not ID_PATTERN.fullmatch(case_id):
        raise CorpusError(f"{where}: id must be lowercase kebab-case")
    prompt = raw["prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise CorpusError(f"{where}: prompt must be a non-empty string")

    lang = raw.get("lang") or "en"
    if lang not in LANGUAGES:
        raise CorpusError(f"{where}: lang must be one of {sorted(LANGUAGES)}")
    fixture = raw.get("fixture") or "bare"
    if fixture not in FIXTURES:
        raise CorpusError(f"{where}: fixture must be one of {sorted(FIXTURES)}")
    mode = raw.get("mode") or "all"
    if mode not in MODES:
        raise CorpusError(f"{where}: mode must be one of {sorted(MODES)}")
    quick = raw.get("quick", False)
    if not isinstance(quick, bool):
        raise CorpusError(f"{where}: quick must be true or false")

    expect = _string_list(raw.get("expect"), "expect", where)
    forbid = _string_list(raw.get("forbid"), "forbid", where)
    # A name that does not exist can never be observed, so a case naming one
    # would score as a permanent miss and read as a broken workflow rather than
    # as a broken case file.
    for field, names in (("expect", expect), ("forbid", forbid)):
        for name in names:
            if name not in known_skills:
                raise CorpusError(f"{where}: {field} names unknown workflow {name!r}")
    overlap = sorted(set(expect) & set(forbid))
    if overlap:
        raise CorpusError(
            f"{where}: {', '.join(overlap)} is both expected and forbidden"
        )
    if len(set(expect)) != len(expect):
        raise CorpusError(f"{where}: expect repeats a workflow")
    if mode == "any" and len(expect) < 2:
        raise CorpusError(f"{where}: mode 'any' needs at least two expected workflows")

    return Case(
        case_id=case_id,
        prompt=prompt.strip(),
        lang=lang,
        expect=expect,
        forbid=forbid,
        fixture=fixture,
        quick=quick,
        mode=mode,
        source=source,
    )


def load_file(path: Path, known_skills: frozenset[str]) -> list[Case]:
    try:
        document = loads(path.read_text(encoding="utf-8"))
    except (OSError, YamlError) as error:
        raise CorpusError(f"{path.name}: {error}") from error
    if not isinstance(document, dict):
        raise CorpusError(f"{path.name}: top level must be a mapping")
    if document.get("version") != SUPPORTED_VERSION:
        raise CorpusError(f"{path.name}: version must be {SUPPORTED_VERSION}")
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise CorpusError(f"{path.name}: cases must be a non-empty list")
    return [
        _case(raw, path.name, index, known_skills)
        for index, raw in enumerate(raw_cases)
    ]


def load_corpus(directory: Path, known_skills: frozenset[str]) -> list[Case]:
    """Load every case file, or raise with all defects named at once."""
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise CorpusError(f"no case files under {directory}")
    cases: list[Case] = []
    errors: list[str] = []
    for path in paths:
        try:
            cases.extend(load_file(path, known_skills))
        except CorpusError as error:
            errors.append(str(error))
    if errors:
        raise CorpusError("; ".join(errors))
    seen: dict[str, str] = {}
    for case in cases:
        if case.case_id in seen:
            errors.append(
                f"duplicate case id {case.case_id!r} in {seen[case.case_id]} "
                f"and {case.source}"
            )
        seen[case.case_id] = case.source
    if errors:
        raise CorpusError("; ".join(errors))
    return cases


def select(
    cases: list[Case],
    *,
    skills: Iterable[str] | None = None,
    quick: bool = False,
) -> list[Case]:
    """Narrow the corpus, keeping the negative pool whenever anything runs.

    A skill filter that also dropped the should-not-fire cases would report an
    activation rate with no false-positive rate beside it, which is the number
    this corpus exists to keep company.
    """
    chosen = list(cases)
    if skills is not None:
        wanted = {name.strip() for name in skills if name.strip()}
        chosen = [
            case
            for case in chosen
            if not case.should_fire or (set(case.expect) & wanted)
        ]
    if quick:
        chosen = [case for case in chosen if case.quick]
    return chosen
