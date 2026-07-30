# Data model

The schema is created by `app/db_migrations.py`; never edit an already-applied migration.

Core entities:

- `memories` — content, summary, type, project/scope, provenance, quality, lifecycle and owner metadata;
- `memory_links` — typed directed relationships with origin and weight;
- `memory_events` — auditable per-memory changes;
- `timeline_events` — normalized project, memory, run and system events;
- lifecycle snapshots and execution tables — preview hashes, apply evidence and rollback state;
- capture/reconciliation tables — agent proposals and review outcomes;
- retention/review tables — candidate decisions and protected history;
- `sleep_runs` and action ledger — Sandman execution evidence;
- feature flags and owner catalogue — controlled governance metadata;
- `schema_migrations` — ordered migration ledger.

## Current state and lineage

Historical rows remain queryable. Current-state resolution follows lifecycle state and supersession pointers. Supersession does not rewrite historical content.

## Scope

The public distribution defaults to one local owner and project-aware namespaces. Historical compatibility columns remain in the schema, but public multi-user onboarding is not active.

## Integrity

Foreign keys are enabled by runtime connections. Release gates run `PRAGMA quick_check`, fresh-chain migration tests and linkage tests.
