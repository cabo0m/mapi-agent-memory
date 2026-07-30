# Operations

## Daily operation

Run `mapi-doctor` before maintenance. Keep the runtime on loopback unless a separate authenticated proxy is deployed. Review profile and database path before applying any change.

## Backups

Stop or quiesce writes, create an SQLite-consistent backup, calculate a checksum and test restore into a separate path. Keep backup storage outside the repository. Retention preview tools never delete by themselves; apply stubs remain non-destructive in this candidate.

## Lifecycle maintenance

Follow `preview -> explicit apply -> audit -> rollback`. Preserve preview hashes and file/database fingerprints. Do not apply a preview after the database state changes.

## Sandman

The deterministic path is the default. Optional providers are proposal-only and disabled. Review proposals before any downstream maintenance.

## Health

`mapi-doctor` verifies database quick-check, migration tail, registry completeness, profile and optional capability availability.

## Shutdown

Stop the MCP process gracefully so active SQLite transactions can complete. Never copy a live database with ordinary file-copy semantics when writes may be active.
