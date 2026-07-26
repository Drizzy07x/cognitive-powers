"""Internal durability primitives loaded by the public work_state facade."""

from . import durability as _durability
from . import evidence_payloads as _evidence_payloads
from . import storage as _storage

EXPORTS = [
    "SCHEMA_VERSION",
    "MIGRATION_POLICY_SCHEMA_VERSION",
    "LOCK_TIMEOUT_SECONDS",
    "LOCK_STALE_SECONDS",
    "OUTPUT_TAIL_CHARS",
    "LEDGER_CHECKPOINT_INTERVAL",
    "LEDGER_MAX_EVENTS",
    "IGNORED_DIRECTORIES",
    "IGNORED_SOURCE_SUFFIXES",
    "IGNORED_SOURCE_FILES",
    "VALID_VERDICTS",
    "RUNNABLE_STATUSES",
    "WorkStateError",
    "EvidenceStaleError",
    "utc_now",
    "sanitize_identifier",
    "resolve_root",
    "resolve_data_root",
    "project_key",
    "_is_within",
    "session_directory",
    "_sha256_file",
    "_ignored_source_directory",
    "source_fingerprint",
    "_atomic_write_text",
    "_atomic_write_json",
    "session_lock",
    "_state_path",
    "state_migration_report",
    "_read_ledger_events",
    "_latest_ledger_snapshot",
    "load_state",
    "_append_ledger",
    "_state_digest",
    "_state_delta",
    "_atomic_write_recovery",
    "_compact_ledger_unlocked",
    "_encode_ledger_events",
]

globals().update({name: getattr(_durability, name) for name in EXPORTS})

__all__ = [
    *[name for name in EXPORTS if not name.startswith("_")],
    "_durability",
    "_evidence_payloads",
    "_storage",
]
