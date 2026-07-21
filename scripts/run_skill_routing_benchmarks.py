#!/usr/bin/env python3
"""Deterministic skill routing and description-collision benchmark."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "benchmarks" / "skill_routing_cases.json"
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
            positive_total += 1
            positive_rank1 += int(rank == 1)
            positive_top_k += int(passed)
            results.append(
                {
                    "skill": name,
                    "kind": "positive",
                    "prompt": case["prompt"],
                    "rank": rank,
                    "top_k": top_k,
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

    rank1_rate = positive_rank1 / positive_total
    top_k_rate = positive_top_k / positive_total
    negative_rate = negative_passed / negative_total
    adversarial_rate = adversarial_passed / adversarial_total
    thresholds = data["thresholds"]
    collisions = description_collisions(
        descriptions, float(thresholds["max_collision_similarity"])
    )
    passed = (
        rank1_rate >= float(thresholds["min_rank1_rate"])
        and top_k_rate >= float(thresholds["min_top_k_rate"])
        and negative_rate >= float(thresholds["min_negative_rate"])
        and adversarial_rate >= float(thresholds["min_adversarial_rate"])
        and not collisions
        and all(
            bool(result["passed"])
            for result in results
            if result["kind"] != "positive" or int(result["top_k"]) == 1
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
        f"negative-owner={metrics['negative_owner_rate']:.2f} adversarial-owner={metrics['adversarial_owner_rate']:.2f} collisions={len(report['collisions'])}",
    ]
    for case in report["cases"]:
        if not case["passed"]:
            lines.append(f"FAIL {case['kind']} {case['skill']}: {case['prompt']}")
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
