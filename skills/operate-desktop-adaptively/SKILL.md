---
name: operate-desktop-adaptively
description: Operate and verify native Windows apps through existing QCU. Use for WPF, Win32, installers, or cross-app workflows requiring guarded desktop evidence.
---

# Operate Desktop Adaptively

Use Cognitive Powers as the control and verification plane and QCU as the optional desktop observation and input plane. Never install QCU or start live input merely because this skill was loaded.

## 1. Confirm the surface

Read [evidence-contract.md](references/evidence-contract.md), then probe without starting the sidecar:

```powershell
& $python <skill-root>/scripts/qcu_evidence.py --root <repo> --json probe
```

Continue only when `usable` is true. If QCU is absent, report the missing optional dependency; do not silently substitute raw OS input.

## 2. Keep one QCU session

Start or recover the persistent sidecar once:

```powershell
qcu sidecar-ready
qcu sidecar-open "<app-or-uri>" --expect-window "<title>" --brief
qcu sidecar-where --observe-profile fast --expect-window "<title>"
```

Batch adjacent current inputs with `qcu sidecar-do --brief --expect-window <title> --steps-json @steps.json`. Use `response.after_observation` and `response.fast_follow` before requesting another capture. If QCU returns `busy_no_queue`, wait for the active input to finish and issue a fresh command; never replay or queue the rejected batch.

Use balanced/full observation, OCR, or a frame prompt only when focus, layout, or exact text remains uncertain. Treat `capture.stale_frame_reused=true` as inconclusive evidence and refresh without restarting the application.

## 3. Verify the objective

Input success is not objective success. Check the final user-visible or application state, confirm the expected window is foreground, and preserve the raw JSON outputs used for the decision. Finish only after verification:

```powershell
qcu finish --reason objective_verified
```

Build a `qcu_desktop_transcript` matching the reference contract and normalize it outside the target repository:

```powershell
& $python <skill-root>/scripts/qcu_evidence.py --root <repo> --json normalize `
  --transcript <qcu-transcript.json> `
  --artifact-dir <external-empty-directory>
```

The normalizer fails closed on missing focus evidence, stale capture, rejected/busy input, absent real actions, missing objective verification, or missing deliberate finish.

## 4. Bind durable evidence

For a durable criterion, record the successful normalized receipt:

```powershell
& $python <execute-durably-root>/scripts/work_state.py --root <repo> record-desktop `
  --session <id> --criterion <criterion> --executor <agent-id> `
  --receipt <cognitive-qcu-receipt.json>
```

Require a different verifier through `$execute-durably`; QCU evidence is eligible to support behavioral verification but does not self-confirm a criterion. Report the expected window, action count, observation IDs, capture reliability, finish reason, and anything not exercised.
