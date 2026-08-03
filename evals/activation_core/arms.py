#!/usr/bin/env python3
"""The three configurations of injected context the experiment compares.

An arm is a set of environment toggles plus a statement of what the session is
then expected to contain. The second half is not decoration. The whole
experiment is a claim about text this plugin's own hooks inject, and the hooks
degrade silently by design, so an arm that failed to take effect looks exactly
like an arm that took effect and changed nothing. Every run is therefore
checked against ``expects_index`` and ``expects_instruction`` before its result
is allowed to count.
"""

from __future__ import annotations

from typing import Mapping, NamedTuple

# Markers matched against the text the hooks actually emitted, taken from the
# hook source rather than paraphrased. Matching on a phrase the hook does not
# print would report every arm as unrealized; matching on one shared by both
# renderings would make the two injections indistinguishable.
INDEX_MARKER = "check whether one of these trigger conditions"
SESSION_INSTRUCTION_MARKER = "this session already lists each one's name"
PROMPT_INSTRUCTION_MARKER = "check this session's workflow index"

DISABLE_ACTIVATION = "COGNITIVE_POWERS_DISABLE_ACTIVATION"
ENABLE_ACTIVATION_INDEX = "COGNITIVE_POWERS_ENABLE_ACTIVATION_INDEX"
DISABLE_ROUTER = "COGNITIVE_POWERS_DISABLE_ROUTER"


class Arm(NamedTuple):
    """One configuration under test."""

    name: str
    summary: str
    env: Mapping[str, str]
    expects_index: bool
    expects_instruction: bool


ARMS: dict[str, Arm] = {
    "none": Arm(
        name="none",
        summary="No injection at all. Skills are discovered only from what the "
        "host preloads, which is the behaviour before the activation mission.",
        env={DISABLE_ACTIVATION: "1", DISABLE_ROUTER: "1"},
        expects_index=False,
        expects_instruction=False,
    ),
    "instruction": Arm(
        name="instruction",
        summary="Standing instruction only, with no session-start catalogue "
        "under it. This is what the plugin ships, because the baseline found "
        "the catalogue equivalent to it and 743 tokens dearer.",
        env={},
        expects_index=False,
        expects_instruction=True,
    ),
    "full": Arm(
        name="full",
        summary="Catalogue and instruction, as shipped through 1.9.0. Kept as "
        "an arm after it stopped being the default, because the decision that "
        "retired it is only re-runnable while the arm that lost still exists.",
        env={ENABLE_ACTIVATION_INDEX: "1"},
        expects_index=True,
        expects_instruction=True,
    ),
}

DEFAULT_ARM = "instruction"

# Every toggle any arm can set. An arm that does not set one must clear it, or
# the operator's own exported value silently becomes part of the measurement.
ALL_TOGGLES = (DISABLE_ACTIVATION, ENABLE_ACTIVATION_INDEX, DISABLE_ROUTER)


def arm(name: str) -> Arm:
    if name not in ARMS:
        known = ", ".join(sorted(ARMS))
        raise KeyError(f"unknown arm {name!r}; known arms are {known}")
    return ARMS[name]


def arm_names() -> tuple[str, ...]:
    return tuple(ARMS)
