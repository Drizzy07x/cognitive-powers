---
name: explore-web-adaptively
description: Discover and document unfamiliar, changing, or weakly structured browser workflows through an existing Skyvern Cloud or self-hosted API. Use for exploratory navigation, cross-site workflows, structured extraction, layout drift, or producing a Playwright test candidate when deterministic browser steps are not yet known.
---

# Explore Web Adaptively

Use Skyvern for discovery, never as the final judge of browser behavior. Prefer `$verify-web-behavior` when a stable Playwright reproduction already exists.

## 1. Bound the exploration

Read [navigation-contract.md](references/navigation-contract.md). Freeze the starting URL, one concrete goal, extraction schema if any, maximum steps, and allowed side-effect scope. Obtain explicit authorization before a task may submit, purchase, publish, send, delete, upload, or persist data.

Run `scripts/skyvern_evidence.py --json probe`. Do not install Skyvern, start a server, create an account, or consume a paid API without authorization. Treat a missing API key or endpoint as unavailable, not as a successful fallback.

## 2. Discover or ingest

Use `run --execute` only for an authorized live task. Default to `--side-effect-scope observe`; the adapter adds a no-submit constraint and caps steps. Use `ingest` for an existing Skyvern run response. Both modes write normalized, hashed artifacts outside the target repository.

Record status, output, step count, failure reason, screenshots/recording references, timeline metadata, and the exact request. Do not claim that remote artifact URLs were preserved unless their bytes were downloaded and hashed.

## 3. Produce a deterministic handoff

Run `handoff` on the normalized receipt. It creates a Playwright candidate outside the repository that intentionally fails until a developer replaces the discovery placeholder with explicit actions and a user-visible assertion.

Inspect the candidate before copying it into a project. Use stable locators and keep the requested outcome separate from Skyvern's own completion judgment.

## 4. Verify through Playwright

Invoke `$verify-web-behavior` and establish a real red/green cycle. Skyvern output, `page.validate`, screenshots, recordings, extracted text, and a `completed` run are navigation evidence only. They cannot complete a behavioral criterion.

Use `$execute-durably record-navigation` only for a criterion about discovery itself. Use `record-web` for the later Playwright evidence.

Report the provider, API contract, run ID, final status, step count, side-effect scope, artifact hashes, generated candidate, and all unverified surfaces.
