#!/usr/bin/env python3
"""Score a frozen current-source decision from claim-level evidence."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Sequence


DOC_URL = "https://docs.python.org/3.14/library/compression.zstd.html"
WHATS_NEW_URL = "https://docs.python.org/3.14/whatsnew/3.14.html"


def evaluate(fixture: Path, events: Path, message: Path) -> dict[str, Any]:
    del fixture, events
    text = message.read_text(encoding="utf-8").casefold()
    evidence: list[str] = []
    critical: list[str] = []
    score = 0

    recommendation = (
        "compression.zstd" in text
        and any(
            marker in text
            for marker in ("recommend", "use ", "adopt", "standardize", "usar", "adoptar", "estandarizar")
        )
    )
    if recommendation:
        score += 30
        evidence.append("recommends the Python 3.14 standard-library API")
    else:
        critical.append("recommendation does not select compression.zstd for the stated constraints")

    api_surface = all(
        marker in text
        for marker in (
            "zstdfile",
            "compress",
            "decompress",
            "zstdcompressor",
            "zstddecompressor",
        )
    )
    if api_surface:
        score += 20
        evidence.append("maps file, one-shot, and incremental requirements to documented APIs")
    else:
        evidence.append("documented API coverage is incomplete")

    optional = "optional" in text or "opcional" in text
    distributor = any(marker in text for marker in ("distributor", "distribution", "distribuidor"))
    explicit_mitigation = any(
        marker in text
        for marker in (
            "capability",
            "import check",
            "import probe",
            "fail fast",
            "unsupported build",
            "comprobar",
            "verificar import",
        )
    )
    startup_gate = (
        any(marker in text for marker in ("startup", "install-time", "installation"))
        and any(marker in text for marker in ("check", "prerequisite", "require", "reject"))
    )
    import_gate = "import compression.zstd" in text
    mitigation = explicit_mitigation or startup_gate or import_gate
    if optional and distributor and mitigation:
        score += 20
        evidence.append("preserves the optional-module caveat with an operational mitigation")
    else:
        critical.append("optional distributor-build caveat or mitigation is missing")

    if DOC_URL in text and WHATS_NEW_URL in text:
        score += 15
        evidence.append("cites both exact version-matched official Python pages")
    else:
        critical.append("material claims lack both exact Python 3.14 primary-source URLs")

    separates = (
        any(marker in text for marker in ("verified fact", "facts", "hechos verificados"))
        and any(marker in text for marker in ("inference", "therefore", "therefore", "inferencia", "por tanto"))
        and any(marker in text for marker in ("uncertainty", "caveat", "uncertainty", "incertidumbre", "limitación", "limitacion"))
    )
    if separates:
        score += 10
        evidence.append("separates verified facts, inference, and unresolved uncertainty")
    else:
        evidence.append("fact, inference, and uncertainty separation is incomplete")

    urls = re.findall(r"https?://[^\s)>\]]+", text)
    secondary = [url for url in urls if not url.startswith("https://docs.python.org/3.14/")]
    if not secondary:
        score += 5
        evidence.append("uses no secondary or version-mismatched source")
    else:
        critical.append("secondary or version-mismatched URLs were used as evidence")

    return {"score": score, "evidence": evidence, "critical_errors": critical}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--message", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(evaluate(args.fixture, args.events, args.message)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
