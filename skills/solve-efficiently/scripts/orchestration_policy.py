#!/usr/bin/env python3
"""Compatibility wrapper for the canonical orchestration runtime."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


CANONICAL_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "orchestration_policy.py"
)
SPEC = importlib.util.spec_from_file_location(
    "cognitive_powers_orchestration_policy", CANONICAL_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {CANONICAL_PATH}")
_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = _MODULE
SPEC.loader.exec_module(_MODULE)

REQUEST_MODES = _MODULE.REQUEST_MODES
DURABLE_SIGNALS = _MODULE.DURABLE_SIGNALS
OrchestrationError = _MODULE.OrchestrationError
select_intensity = _MODULE.select_intensity
select_agent_plan = _MODULE.select_agent_plan
explain_agent_plan = _MODULE.explain_agent_plan
validate_worker_result = _MODULE.validate_worker_result
evaluate_cases = _MODULE.evaluate_cases
evaluate_agent_cases = _MODULE.evaluate_agent_cases
main = _MODULE.main


if __name__ == "__main__":
    raise SystemExit(main())
