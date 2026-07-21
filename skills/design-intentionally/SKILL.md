---
name: design-intentionally
description: Define, implement, redesign, or audit deliberate web interface direction. Use for branded UI, screenshot-driven work, responsive visual QA, and fidelity claims.
---

# Design Intentionally

Translate visual intent into inspectable constraints, then verify the rendered result. Do not substitute personal taste for the brief.

## 1. Read the interface

Inspect the existing application, dependencies, assets, copy, routes, screenshots, and user references before choosing an aesthetic. Identify:

- page kind and audience;
- greenfield, preserve, or overhaul mode;
- desired character and explicitly rejected directions;
- brand, content, route, and interaction constraints;
- existing component or design system.

Ask one focused question only when two materially different directions remain plausible. Otherwise state one concise design read and proceed.

Create a normalized intent with `scripts/design_intent.py create --brief <brief.json> --output <intent.json>`. Keep workflow evidence outside the target repository. Read [design-intent-contract.md](references/design-intent-contract.md) for the schema.

## 2. Choose context, not defaults

Use the existing stack and system unless the user requested a replacement or it cannot satisfy the brief. Invoke `$use-current-docs` before relying on a version-sensitive component, animation, or design-system API.

Treat variance, motion, and density as 1–10 control signals, not aesthetic scores. Do not force React, Tailwind, dark mode, gradients, particular fonts, icon libraries, or animation packages.

For a new interface, read [greenfield.md](references/greenfield.md). For a redesign, read [redesign.md](references/redesign.md) before editing.

## 3. Implement coherently

Establish hierarchy, typography, spacing, color, shape, imagery, and motion as one system. Avoid repeated layout formulas or decorative elements that do not serve the content, but allow any pattern explicitly supported by the brand or task.

Preserve real content. Never invent testimonials, customers, product metrics, certifications, screenshots, or operational state. Use supplied assets, generate authorized references, or mark missing assets clearly.

Respect responsive behavior and existing functionality. Motion must express the intent and must degrade coherently when reduced motion is requested by the platform.

For React or Next.js work, run the bounded static review in [frontend-performance.md](references/frontend-performance.md). Treat its findings as version-bound candidates, then measure the affected bundle or runtime behavior before claiming an optimization.

## 4. Verify the render

Invoke `$verify-web-behavior` for executable browser checks. Exercise relevant interactions and capture real renders at mobile and desktop widths. A screenshot supports visual review but does not prove behavior.

Create typed visual evidence with `scripts/design_evidence.py create`. It binds the current intent, normalized Playwright receipt, reviewer checks, viewports, screenshots, and hashes. Read [visual-verification.md](references/visual-verification.md) before judging the result.

The visual receipt must remain `behavioralVerificationEligible=false` and `subjectiveQualityProven=false`. It can demonstrate that the declared review contract ran against specific renders; it cannot make taste objective.

## 5. Deliver honestly

Report the design read, preserved constraints, material visual changes, tested viewports and interactions, and remaining visual or behavioral gaps. Invoke `$verify-delivery` for a separate release or quality verdict and `$communicate-efficiently` for the final handoff.
