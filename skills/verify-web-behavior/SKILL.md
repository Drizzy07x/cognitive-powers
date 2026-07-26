---
name: verify-web-behavior
description: Verify known browser behavior or regressions through an already-configured Playwright installation, capturing machine-readable evidence at the public user-visible seam.
when_to_use: Use for web UI defects, end-to-end flows, accessibility checks, flaky-test investigation, or visual change verification when the behavior to check is already known. Requires Playwright configured in the project; never installs it.
---

# Verify Web Behavior

Use Playwright as an optional evidence provider. Never treat navigation output, a screenshot, or a generated test as proof by itself.

## 1. Confirm the test surface

Run `scripts/browser_evidence.py --root <repo> --json probe`. Continue only when it finds both an existing Playwright configuration and executable. Do not install Playwright, download browsers, initialize a config, start a production deployment, or update snapshots without explicit authorization.

Read [evidence-contract.md](references/evidence-contract.md) before capturing or evaluating browser evidence.

## 2. Select the smallest useful test

Prefer an existing test at the public user-visible seam. If CodeGraph is fresh, use `solve-efficiently` to identify candidate callers and tests, then inspect the candidates. Select browsers by risk: start with the affected project; add another engine or viewport only when compatibility is part of the claim.

If the interface is unfamiliar or layout drift prevents a stable reproduction, use `explore-web-adaptively` to produce a navigation-only handoff. Inspect and replace its fail-closed placeholder before treating it as a Playwright test.

When the requested outcome includes visual direction, redesign fidelity, responsive composition, or screenshot-backed review, invoke `design-intentionally` first. Capture current mobile and desktop renders outside the repository, but keep visual review separate from behavioral assertions.

For a defect, make the smallest symptom-specific Playwright test fail before editing source. Use role, label, text, or test-id locators and auto-retrying web assertions. Avoid fixed sleeps and implementation-only assertions.

## 3. Capture evidence

Run `scripts/browser_evidence.py --root <repo> --json run` with explicit test selectors when possible. The adapter writes JSON results and Playwright artifacts outside the target repository, hashes them, and returns failure when no real test passed or Playwright reports an unexpected result.

For a durable regression cycle, wrap the exact adapter command with `execute-durably` `run-red` and `run-green`. Keep command, selector, browser project, and grep unchanged between phases.

## 4. Diagnose from the trace

Use assertion errors, steps, attachments, network activity, and the retained trace to distinguish the cause. A trace helps explain a failure; it does not override the test exit code. Classify retried success as flaky rather than clean.

## 5. Verify independently

Re-run the original unminimized flow and the smallest affected suite. Record a successful normalized receipt with `execute-durably record-web` when durable completion requires it. Keep contract and quality verdicts separate through `verify-delivery`.

Report the tested browser projects, exact selectors, pass/fail/flaky counts, trace availability, source state, and anything not exercised. Never claim cross-browser, visual, accessibility, console-error, or persistence coverage unless corresponding assertions actually ran.

A `visual_design_evidence` receipt can prove that a declared review ran against exact renders and a passing browser receipt. Because it sets `behavioralVerificationEligible=false` and `subjectiveQualityProven=false`, it cannot independently establish user-flow correctness or objective aesthetic quality.
