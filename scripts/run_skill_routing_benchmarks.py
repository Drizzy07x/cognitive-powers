#!/usr/bin/env python3
"""Deterministic skill routing and description-collision benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping, Sequence

try:
    from scripts.skill_routing import (
        decide,
        description_collisions,
        load_skill_descriptions,
        rank_skills,
    )
except ModuleNotFoundError:  # Direct script execution places scripts/ on sys.path.
    from skill_routing import (
        decide,
        description_collisions,
        load_skill_descriptions,
        rank_skills,
    )


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "benchmarks" / "skill_routing_cases.json"


def _rank_of(name: str, ranking: Sequence[tuple[str, float]]) -> int:
    for index, (candidate, _) in enumerate(ranking, start=1):
        if candidate == name:
            return index
    raise ValueError(f"skill missing from ranking: {name}")


def evaluate(root: Path, cases_path: Path) -> dict[str, object]:
    descriptions = load_skill_descriptions(root)
    data = json.loads(cases_path.read_text(encoding="utf-8"))
    case_names = [entry["name"] for entry in data["skills"]]
    if len(case_names) != len(set(case_names)):
        raise ValueError("duplicate skill case")
    if set(case_names) != set(descriptions):
        missing = sorted(set(descriptions) - set(case_names))
        unknown = sorted(set(case_names) - set(descriptions))
        raise ValueError(
            f"routing cases do not match skills; missing={missing}, unknown={unknown}"
        )

    results: list[dict[str, object]] = []
    positive_total = 0
    positive_rank1 = 0
    positive_top_k = 0
    positive_suggested = 0
    negative_total = 0
    negative_passed = 0
    adversarial_total = 0
    adversarial_passed = 0
    for entry in data["skills"]:
        name = str(entry["name"])
        for kind in ("positives", "negatives", "adversarial"):
            if not entry.get(kind):
                raise ValueError(f"{name} requires at least one {kind} case")
        for case in entry["positives"]:
            ranking = rank_skills(str(case["prompt"]), descriptions)
            rank = _rank_of(name, ranking)
            top_k = int(case.get("top_k", 3))
            passed = rank <= top_k
            # Ranking first is not the deliverable; being named to the agent
            # is. Scoring only the ordering is how a suite reporting 0.96
            # coexisted with a hook that stayed silent on a third of these.
            outcome = decide(str(case["prompt"]), descriptions)
            suggested = (
                outcome["status"] == "suggested" and outcome.get("skill") == name
            )
            positive_total += 1
            positive_rank1 += int(rank == 1)
            positive_top_k += int(passed)
            positive_suggested += int(suggested)
            results.append(
                {
                    "skill": name,
                    "kind": "positive",
                    "prompt": case["prompt"],
                    "rank": rank,
                    "top_k": top_k,
                    "suggested": suggested,
                    "suggestion_reason": outcome.get("reason"),
                    "passed": passed,
                }
            )
        for case in entry["negatives"]:
            owner = str(case["owner"])
            if owner not in descriptions or owner == name:
                raise ValueError(f"invalid negative owner for {name}: {owner}")
            ranking = rank_skills(str(case["prompt"]), descriptions)
            owner_rank = _rank_of(owner, ranking)
            skill_rank = _rank_of(name, ranking)
            passed = owner_rank < skill_rank
            negative_total += 1
            negative_passed += int(passed)
            results.append(
                {
                    "skill": name,
                    "kind": "negative",
                    "prompt": case["prompt"],
                    "owner": owner,
                    "owner_rank": owner_rank,
                    "rank": skill_rank,
                    "passed": passed,
                }
            )
        for case in entry["adversarial"]:
            owner = str(case.get("owner", name))
            ranking = rank_skills(str(case["prompt"]), descriptions)
            owner_rank = _rank_of(owner, ranking)
            top_k = int(case.get("top_k", 1))
            passed = owner_rank <= top_k
            adversarial_total += 1
            adversarial_passed += int(passed)
            results.append(
                {
                    "skill": name,
                    "kind": "adversarial",
                    "prompt": case["prompt"],
                    "owner": owner,
                    "owner_rank": owner_rank,
                    "top_k": top_k,
                    "passed": passed,
                }
            )

    # Ordinary work that owns no workflow. Recall is only worth measuring
    # against the noise it costs, and a threshold change that lifts recall by
    # firing on everything must fail here rather than look like an improvement.
    quiet_total = 0
    quiet_passed = 0
    for prompt in data.get("quiet", []):
        outcome = decide(str(prompt), descriptions)
        passed = outcome["status"] != "suggested"
        quiet_total += 1
        quiet_passed += int(passed)
        results.append(
            {
                "skill": "-",
                "kind": "quiet",
                "prompt": prompt,
                "suggested": None if passed else outcome.get("skill"),
                "suggestion_reason": outcome.get("reason"),
                "passed": passed,
            }
        )

    # The same requests written in Spanish. The descriptions are English and
    # the scorer is lexical, so this measures the translation layer rather than
    # the ranking: before it existed these scored near zero and the router was
    # silent on every one of them. Their own off-domain corpus is scored too,
    # because a lexicon wide enough to match anything would read as a win here
    # and as noise to the user.
    spanish_total = 0
    spanish_passed = 0
    for case in data.get("spanish", []):
        owner = str(case["owner"])
        if owner not in descriptions:
            raise ValueError(f"unknown owner for a Spanish case: {owner}")
        outcome = decide(str(case["prompt"]), descriptions)
        passed = outcome["status"] == "suggested" and outcome.get("skill") == owner
        spanish_total += 1
        spanish_passed += int(passed)
        results.append(
            {
                "skill": owner,
                "kind": "spanish",
                "prompt": case["prompt"],
                "suggested": outcome.get("skill")
                if outcome["status"] == "suggested"
                else None,
                "suggestion_reason": outcome.get("reason"),
                "passed": passed,
            }
        )
    for prompt in data.get("spanish_quiet", []):
        outcome = decide(str(prompt), descriptions)
        passed = outcome["status"] != "suggested"
        quiet_total += 1
        quiet_passed += int(passed)
        results.append(
            {
                "skill": "-",
                "kind": "quiet",
                "prompt": prompt,
                "suggested": None if passed else outcome.get("skill"),
                "suggestion_reason": outcome.get("reason"),
                "passed": passed,
            }
        )

    rank1_rate = positive_rank1 / positive_total
    top_k_rate = positive_top_k / positive_total
    suggestion_rate = positive_suggested / positive_total
    spanish_rate = spanish_passed / spanish_total if spanish_total else 1.0
    quiet_rate = quiet_passed / quiet_total if quiet_total else 1.0
    negative_rate = negative_passed / negative_total
    adversarial_rate = adversarial_passed / adversarial_total
    thresholds = data["thresholds"]
    collisions = description_collisions(
        descriptions, float(thresholds["max_collision_similarity"])
    )
    passed = (
        rank1_rate >= float(thresholds["min_rank1_rate"])
        and top_k_rate >= float(thresholds["min_top_k_rate"])
        and suggestion_rate >= float(thresholds["min_suggestion_rate"])
        and quiet_rate >= float(thresholds["min_quiet_rate"])
        and negative_rate >= float(thresholds["min_negative_rate"])
        and adversarial_rate >= float(thresholds["min_adversarial_rate"])
        and spanish_rate >= float(thresholds["min_spanish_rate"])
        and not collisions
        and all(
            bool(result["passed"])
            for result in results
            # Spanish rides on its rate alone. A lexicon cannot be complete,
            # and demanding every case here would be answered by widening it
            # until it matches anything -- which the quiet corpus, held to the
            # full bar in both languages, exists to make expensive. Every other
            # kind keeps the bar it had.
            if result["kind"] != "spanish"
            and (result["kind"] != "positive" or int(result["top_k"]) == 1)
        )
    )
    return {
        "schema_version": 1,
        "suite": "cognitive-powers-skill-routing",
        "passed": passed,
        "skill_count": len(descriptions),
        "metrics": {
            "positive_cases": positive_total,
            "rank1_rate": round(rank1_rate, 4),
            "top_k_rate": round(top_k_rate, 4),
            "suggestion_rate": round(suggestion_rate, 4),
            "spanish_cases": spanish_total,
            "spanish_rate": round(spanish_rate, 4),
            "quiet_cases": quiet_total,
            "quiet_rate": round(quiet_rate, 4),
            "negative_cases": negative_total,
            "negative_owner_rate": round(negative_rate, 4),
            "adversarial_cases": adversarial_total,
            "adversarial_owner_rate": round(adversarial_rate, 4),
        },
        "thresholds": thresholds,
        "collisions": collisions,
        "cases": results,
        "end_to_end_improvement_proven": False,
    }


def format_report(report: Mapping[str, object]) -> str:
    metrics = report["metrics"]
    assert isinstance(metrics, Mapping)
    lines = [
        "Skill routing benchmark",
        f"skills={report['skill_count']} positives={metrics['positive_cases']} rank1={metrics['rank1_rate']:.2f} top-k={metrics['top_k_rate']:.2f}",
        f"suggested={metrics['suggestion_rate']:.2f} spanish={metrics['spanish_rate']:.2f} of {metrics['spanish_cases']} quiet={metrics['quiet_rate']:.2f} of {metrics['quiet_cases']}",
        f"negative-owner={metrics['negative_owner_rate']:.2f} adversarial-owner={metrics['adversarial_owner_rate']:.2f} collisions={len(report['collisions'])}",
    ]
    for case in report["cases"]:
        # Spanish counts toward its rate, not toward the pass/fail gate, so it
        # is reported as a miss rather than as a failure it does not cause.
        label = "MISS" if case["kind"] == "spanish" else "FAIL"
        if not case["passed"]:
            lines.append(f"{label} {case['kind']} {case['skill']}: {case['prompt']}")
        elif case["kind"] == "positive" and not case["suggested"]:
            lines.append(f"SILENT positive {case['skill']}: {case['prompt']}")
    lines.append("PASS suite" if report["passed"] else "FAIL suite")
    lines.append(
        "This deterministic contract does not prove end-to-end model improvement."
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PLUGIN_ROOT)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = evaluate(args.root.resolve(), args.cases.resolve())
    print(
        json.dumps(report, indent=2, ensure_ascii=False)
        if args.json
        else format_report(report)
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
