# Visual verification contract

## Evidence layers

- Intent evidence: what the design is meant to express and preserve.
- Render evidence: screenshots tied to exact viewports and source state.
- Review evidence: named checks and reviewer notes against those renders.
- Behavioral evidence: Playwright assertions for interactions and user-visible outcomes.

Do not merge these layers into one unsupported quality claim.

## Minimum visual review

Use at least one mobile viewport at or below 480 CSS pixels and one desktop viewport at or above 1024 CSS pixels. Add intermediate widths when layout behavior changes there.

Required checks:

- `brief-fidelity`: render matches audience, direction, and declared dials.
- `hierarchy`: primary task and information order are clear.
- `consistency`: typography, spacing, color, shape, imagery, and components form a system.
- `responsive`: composition remains intentional at every captured width.
- `content-integrity`: real content and preserved constraints were not silently changed or fabricated.

Use `pass`, `fail`, or `not-evaluated`, with a concrete note. Add checks for motion, reduced motion, contrast, theme parity, loading states, or image fidelity only when relevant to the intent.

`visualContractPassed=true` means the required review checks passed, mobile and desktop screenshots were captured, and the associated Playwright run passed. It does not prove universal accessibility, cross-browser behavior, production performance, or objective aesthetic quality.
