#!/usr/bin/env python3
"""Measure Cognitive Powers workflow activation against the real host.

Outside the offline gate by construction: every run spawns ``claude`` and costs
money, which is the same reason the semantic and browser benchmark runners sit
outside it. What the gate does cover is everything this file delegates to --
corpus loading, transcript reading, arm verification and scoring are pure
functions with their own tests.

    python evals/run_activation_eval.py --arm full --reps 3 --quick
    python evals/run_activation_eval.py --arm none --arm instruction --arm full
    python evals/run_activation_eval.py --validate-only
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

EVALS_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = EVALS_ROOT.parent

for candidate in (EVALS_ROOT, PLUGIN_ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from activation_core import runner  # noqa: E402
from activation_core.arms import ARMS, DEFAULT_ARM, arm_names  # noqa: E402
from activation_core.cases import CorpusError, load_corpus, select  # noqa: E402
from activation_core.report import as_markdown  # noqa: E402
from activation_core.session import default_python  # noqa: E402

DEFAULT_CASES = EVALS_ROOT / "cases"
DEFAULT_ARTIFACTS = EVALS_ROOT / "artifacts"
MINIMUM_CASES_PER_SKILL = 3


def _under_covered(cases, installed: frozenset[str]) -> list[tuple[str, int]]:
    """Workflows carrying too few should-fire prompts to be worth a rate.

    Counts single-workflow cases only. A multi-workflow case exercises a
    composition rather than the workflow on its own, so counting it here would
    let two workflows cover each other and leave both untested alone.
    """
    counts = dict.fromkeys(installed, 0)
    for case in cases:
        if len(case.expect) == 1:
            counts[case.expect[0]] = counts.get(case.expect[0], 0) + 1
    return sorted(
        (name, count)
        for name, count in counts.items()
        if count < MINIMUM_CASES_PER_SKILL
    )


def _installed_workflows() -> frozenset[str]:
    """The workflows the plugin actually ships, read from the tree.

    Read rather than listed. A hand-maintained list would let a workflow be
    added without ever being measured, and the eval would report full coverage
    of a catalogue it had not seen.
    """
    from skill_routing import load_skill_triggers

    return frozenset(load_skill_triggers(PLUGIN_ROOT))


def _suite_label(args: argparse.Namespace, skills: list[str] | None) -> str:
    """Name the selection, so a report says which corpus produced its rates.

    A narrowed run and a whole-corpus run print the same tables, and without
    this the two are indistinguishable once the file is opened a week later.
    """
    if skills:
        return f"skills:{','.join(sorted(name.strip() for name in skills))}"
    if args.quick and not args.full:
        return "quick"
    return "full"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arm",
        action="append",
        choices=sorted(arm_names()),
        help="Arm to run; repeatable. Defaults to the shipped configuration.",
    )
    parser.add_argument("--reps", type=int, default=3, help="Repetitions per case.")
    parser.add_argument(
        "--skills",
        help="Comma-separated workflow names. The should-not-fire pool always runs.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Only cases marked quick in the corpus.",
    )
    parser.add_argument(
        "--full", action="store_true", help="The whole corpus. Overrides --quick."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=None,
        help=f"Directory for raw transcripts. Default {DEFAULT_ARTIFACTS} when --keep-transcripts.",
    )
    parser.add_argument("--keep-transcripts", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=runner.DEFAULT_WORKERS,
        help=(
            f"Concurrent sessions, 1 to {runner.MAX_WORKERS}. 1 is sequential. "
            "Each run already has its own workspace; the ceiling is the "
            "provider's patience, not isolation."
        ),
    )
    parser.add_argument(
        "--equivalence-margin",
        type=float,
        default=runner.DEFAULT_MARGIN,
        help=(
            "How close two arms must be before the run may call them "
            "equivalent rather than undecided. A judgement about what "
            "difference would be worth paying for, so it is declared here "
            "rather than derived."
        ),
    )
    parser.add_argument(
        "--no-stop-when-decided",
        dest="stop_when_decided",
        action="store_false",
        help=(
            "Run every planned invocation even after every arm pair has a "
            "verdict. Needed for per-skill rates over the whole corpus, and "
            "wasteful when the question is only which arm wins."
        ),
    )
    parser.add_argument("--max-cost-usd", type=float, default=0.75)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--python-executable", default=None)
    parser.add_argument("--claude", default="claude")
    parser.add_argument(
        "--floor",
        type=float,
        default=0.0,
        help="Fail when an arm's should-fire rate falls below this.",
    )
    parser.add_argument("--max-false-positive", type=float, default=1.0)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Load and check the corpus, print the plan, and spawn nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        installed = _installed_workflows()
        cases = load_corpus(args.cases, installed)
    except (CorpusError, OSError) as error:
        print(f"corpus is unusable: {error}", file=sys.stderr)
        return 2

    skills = args.skills.split(",") if args.skills else None
    chosen = select(cases, skills=skills, quick=args.quick and not args.full)
    if not chosen:
        print("selection matched no cases", file=sys.stderr)
        return 2

    arms = [ARMS[name] for name in (args.arm or [DEFAULT_ARM])]
    invocations = len(arms) * len(chosen) * args.reps

    if args.validate_only:
        print(
            f"corpus ok: {len(cases)} cases, {len(chosen)} selected, "
            f"{len(arms)} arm(s), {args.reps} repetition(s) "
            f"-> {invocations} host invocations"
        )
        thin = _under_covered(cases, installed)
        if thin:
            # A workflow measured by one prompt is a workflow whose rate is one
            # prompt's opinion. Reported as unmeasured rather than scored,
            # because a new workflow nobody wrote cases for would otherwise
            # appear in the corpus as silently absent.
            print(
                "workflows with fewer than "
                f"{MINIMUM_CASES_PER_SKILL} should-fire cases: "
                + ", ".join(f"{name} ({count})" for name, count in thin)
            )
            return 1
        return 0

    executable = (
        args.claude
        if Path(args.claude).is_absolute()
        else (shutil.which(args.claude) or args.claude)
    )
    artifacts = args.artifacts or (DEFAULT_ARTIFACTS if args.keep_transcripts else None)

    print(f"running {invocations} host invocations", file=sys.stderr)
    try:
        payload = runner.execute(
            cases=chosen,
            arms=arms,
            repetitions=args.reps,
            plugin_root=PLUGIN_ROOT,
            installed=installed,
            python_executable=args.python_executable or default_python(),
            model=args.model,
            max_cost_usd=args.max_cost_usd,
            timeout_seconds=args.timeout_seconds,
            claude_executable=executable,
            artifacts=artifacts,
            progress=lambda label: print(label, file=sys.stderr, flush=True),
            workers=args.workers,
            suite=_suite_label(args, skills),
            equivalence_margin=args.equivalence_margin,
            stop_when_decided=args.stop_when_decided,
        )
    except runner.RunnerError as error:
        print(f"run failed: {error}", file=sys.stderr)
        return 2

    runner.write_outputs(
        payload,
        json_path=args.json_output,
        markdown_path=args.markdown_output,
    )
    if args.markdown_output is None:
        print(as_markdown(payload))
    print(runner.summary_line(payload), file=sys.stderr)

    failures = runner.gate(
        payload, floor=args.floor, max_false_positive=args.max_false_positive
    )
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
