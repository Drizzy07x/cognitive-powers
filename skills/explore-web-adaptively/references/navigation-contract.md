# Adaptive navigation contract

## Evidence classification

- A completed Skyvern run means the provider terminated successfully according to its own agent logic.
- Structured output is extracted information, not proof that the underlying website state is correct.
- Screenshots, recordings, timelines, action histories, and remote artifact URLs support diagnosis.
- Only a relevant executable assertion may verify browser behavior.

Every normalized receipt must set `navigationOnly` to `true` and `verificationEligible` to `false`.

## Side-effect scopes

- `observe`: navigate and inspect without submitting forms, downloading files, or changing remote state.
- `interact`: permit reversible navigation and form filling but forbid final submission.
- `submit`: permit the explicitly described external mutation; require separate authorization.

A prompt cannot guarantee containment. Report the declared scope and review the timeline for deviations.

## Handoff requirements

The Playwright handoff must preserve the starting URL, original goal, run ID, receipt hash, and observed step count. It must fail closed until explicit deterministic actions and at least one user-visible assertion replace the placeholder.

Do not copy generated candidates into a target repository without inspecting and adapting them to its conventions.
