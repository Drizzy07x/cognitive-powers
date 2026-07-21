#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills/solve-efficiently/scripts/memory_context.py"
spec = importlib.util.spec_from_file_location("memory_context", SCRIPT)
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(ROOT / "benchmarks/memory_cases.json"))
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    cases = json.loads(Path(ns.cases).read_text(encoding="utf-8"))["cases"]
    reports = []
    with tempfile.TemporaryDirectory() as td:
        source = Path(td) / "evidence.txt"
        source.write_text("benchmark evidence")
        store = Path(td) / "memory.json"
        now = datetime.now(timezone.utc)
        for case in cases:
            for item in case["records"]:
                rec = {
                    **item,
                    "project_scope": case["project_scope"],
                    "source": str(source),
                    "timestamp": now.isoformat(),
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "expires_at": (
                        now + timedelta(days=item.pop("expires_in_days"))
                    ).isoformat(),
                }
                memory.write_native(store, rec, project_scope=case["project_scope"])
            ids = [
                x["id"]
                for x in memory.retrieve(
                    store,
                    case["query"],
                    project_scope=case["project_scope"],
                    demand=True,
                )["results"]
            ]
            passed = all(x in ids for x in case["expected_ids"]) and all(
                x not in ids for x in case["excluded_ids"]
            )
            reports.append({"id": case["id"], "passed": passed, "actual_ids": ids})
    result = {"passed": all(x["passed"] for x in reports), "cases": reports}
    print(
        json.dumps(result, indent=2)
        if ns.json
        else "\n".join(
            f"{x['id']}: {'PASS' if x['passed'] else 'FAIL'}" for x in reports
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
