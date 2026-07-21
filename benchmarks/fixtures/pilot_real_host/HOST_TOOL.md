# Authorized host tool

`host_driver.js` starts the local HTTP application and a real Chromium instance.
It accepts public interaction primitives in `ACTIONS.json`:

```json
[
  {"action":"fill","label":"Search tickets","value":"QCU-217"},
  {"action":"press","label":"Search tickets","key":"Enter"},
  {"action":"click","name":"QCU-217"}
]
```

Supported actions are `fill` by label, `select` by label and option label,
`press` by label, and `click` by accessible name. Complete the remaining actions
from `WORKFLOW.md` in the same array.

Control types are fixed: use `fill` for **Search tickets**, **Assignee**, and
**Release note**; use `select` for **Priority**; use `press` for the search
`Enter`; and use `click` for the ticket and both action buttons.
The select primitive uses the same `value` property as fill:
`{"action":"select","label":"Priority","value":"High"}`. There is no
`option` property.

Run:

```powershell
node host_driver.js run ACTIONS.json
node host_driver.js observe
```

`run` writes `host-receipt.json` and `final.png`. `observe` uses a new browser
and writes `observer-receipt.json`. Report the SHA-256 values printed by both
commands. Do not use HTTP APIs, DOM injection, direct state edits, or internal
JavaScript evaluation.
