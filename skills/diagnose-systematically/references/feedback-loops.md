# Diagnostic feedback loops

Choose the cheapest signal that reaches the reported behavior:

1. Focused unit, integration, or end-to-end test.
2. CLI or HTTP invocation with a known input and asserted output.
3. Headless UI flow with assertions on visible state, logs, or network behavior.
4. Replay of a captured request, trace, event stream, or dataset.
5. Minimal harness around the affected module.
6. Seeded property, fuzz, stress, differential, or bisection runner.

A useful signal is:

- **Symptom-specific:** it fails for the behavior the user reported, not merely a nearby error.
- **Red-capable:** the defective state has actually produced a failing verdict.
- **Deterministic:** repeated runs agree, or an intermittent failure has a measured reproduction rate.
- **Tight:** setup and execution are short enough to run after every meaningful probe.
- **Unattended:** the agent can execute and interpret it without invented human observations.

An application starting, a command returning zero, a string existing in source, or a silently skipped test is not by itself a defect-specific signal.
