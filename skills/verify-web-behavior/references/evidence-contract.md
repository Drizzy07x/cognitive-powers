# Browser evidence contract

## Evidence levels

- `verified`: Playwright started, parsed a JSON report, executed at least one expected test, returned zero unexpected tests, and the requested behavior has a relevant assertion.
- `failed`: Playwright started and the relevant test failed, timed out, or was interrupted.
- `inconclusive`: the executable, config, browser, server, report, or meaningful assertion was missing.

Screenshots, videos, traces, HTML reports, generated tests, and CodeGraph results are supporting artifacts. None independently proves behavior.

## Stable test construction

- Prefer `getByRole`, `getByLabel`, `getByText`, or an explicit test id.
- Prefer auto-retrying locator assertions over sleeps or immediate DOM reads.
- Assert the user-visible outcome and, when relevant, the durable API or storage result.
- Do not update snapshots while verifying a claim.
- Mark a test that passes only after retry as flaky and investigate it before calling the surface reliable.

## Browser selection

Use one configured project for a narrow behavioral change. Add Chromium, Firefox, WebKit, branded channels, mobile viewports, or multiple operating systems only when the contract or affected surface requires them. A Chromium pass is not cross-browser evidence.

## Artifact integrity

The adapter stores outputs outside the repository and records SHA-256 hashes. A durable `record-web` receipt copies the normalized receipt and every declared artifact into session evidence. Missing, changed, empty, symlinked, or escaping artifacts invalidate the claim.
