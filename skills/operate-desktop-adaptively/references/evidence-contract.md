# QCU desktop evidence contract

The normalizer accepts one JSON object:

```json
{
  "schemaVersion": 1,
  "type": "qcu_desktop_transcript",
  "provider": "quick-computer-use",
  "qcuVersion": "0.1.0",
  "sessionId": "session-1",
  "objective": "Save a note in Notepad",
  "expectedWindow": "Notepad",
  "realActions": true,
  "commands": [
    {"name": "sidecar-ready", "argv": ["qcu", "sidecar-ready"], "exitCode": 0, "result": {"sidecar_status": "running"}},
    {"name": "sidecar-where", "argv": ["qcu", "sidecar-where"], "exitCode": 0, "result": {"focus": {"expected": [{"target": "Notepad", "status": "foreground"}]}, "capture": {"stale_frame_reused": false}}},
    {"name": "sidecar-do", "argv": ["qcu", "sidecar-do"], "exitCode": 0, "result": {"response": {"status": "success"}}},
    {"name": "sidecar-where", "argv": ["qcu", "sidecar-where"], "exitCode": 0, "result": {"observation_id": "obs-final", "focus": {"expected": [{"target": "Notepad", "status": "foreground"}]}, "capture": {"stale_frame_reused": false}}},
    {"name": "finish", "argv": ["qcu", "finish", "--reason", "objective_verified"], "exitCode": 0, "result": {"reason": "objective_verified"}}
  ],
  "finalVerification": {
    "objectiveSatisfied": true,
    "expectedWindowForeground": true,
    "observationId": "obs-final",
    "evidence": "The saved note is visible in the expected window."
  }
}
```

`commands` must preserve the actual argv, exit code, and parsed QCU JSON result in execution order. Do not reconstruct successful outputs after the task.

A valid receipt requires:

- one `sidecar-ready` before any `sidecar-do`;
- at least one foreground focus observation for `expectedWindow`;
- at least one real `sidecar-do` action whose parsed result reports `status=success`;
- no `busy_no_queue`, failed/rejected action, or reused stale frame;
- explicit final objective verification with non-empty evidence, bound by observation ID to fresh foreground QCU output at or after the last action;
- one final `finish` whose argv and result identify `objective_verified`;
- one stable session ID and a non-empty QCU version.

The normalized receipt copies and hashes the source transcript. Offline fixtures validate this contract only; they do not demonstrate that QCU controlled a real desktop. Independent review remains required before durable completion.
