# Priority feature gap

Add an optional task priority across the model, JSON storage, and public CLI.

- Accepted priorities are `low`, `normal`, and `high`; the default is `normal`.
- `add TITLE --priority PRIORITY` must persist the selected priority.
- `list` must expose each task's priority in its JSON output.
- Existing JSON records without a `priority` field must continue to load as
  `normal`.
- Invalid priority input must fail without changing the store.

Preserve the existing `add TITLE` behavior and JSON fields. Add focused tests
that prove the new behavior through the public CLI and compatibility boundary.
