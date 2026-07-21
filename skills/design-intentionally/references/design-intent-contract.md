# Design intent contract

## Brief input

Supply a JSON object containing:

- `project_root`: existing target repository or application directory.
- `page_kind`: `marketing`, `product`, `dashboard`, `portfolio`, `commerce`, `editorial`, or `public-service`.
- `mode`: `greenfield`, `preserve`, or `overhaul`.
- `audience`: concrete primary audience.
- `direction`: non-empty list of desired visual or experiential qualities.
- `avoid`: optional rejected directions or patterns.
- `brand_constraints`, `content_constraints`, `preserve`: arrays of exact constraints.
- `references`: objects with `kind`, `value`, and `note`.
- `existing_system`: current component or design system, when present.
- `dials`: optional overrides for `variance`, `motion`, and `density`, each from 1–10.

Missing page kind, audience, direction, or redesign preservation constraints produce `readyToImplement=false` and named `unresolvedChoices`; they do not get guessed silently.

## Normalized intent

The output records the source brief path and hash, inferred or overridden dials, a foundation decision, preserved and rejected constraints, and an intent hash. Dials guide tradeoffs:

- `variance`: symmetry and repetition versus expressive composition.
- `motion`: static transitions versus prominent spatial movement.
- `density`: breathing room versus information per viewport.

The values are control signals, not a quality score. Existing systems are reused by default. When no system exists, selection remains pending until dependencies and requirements are inspected.

Keep the normalized artifact outside the target repository. Use `--allow-workspace-output` only when the user explicitly wants a versioned design specification.
