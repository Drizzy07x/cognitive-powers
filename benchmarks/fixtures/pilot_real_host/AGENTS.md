# Real-host fixture instructions

Read `WORKFLOW.md` and `HOST_TOOL.md` before acting. Complete the workflow only
through `host_driver.js`, which launches the real local web host and Chromium and
uses public labels, roles, and keyboard input. Do not edit `state.json`, the host,
or the driver directly. Create `ACTIONS.json`, run it once, then run `observe` in
a fresh browser and report the receipt and screenshot hashes.
