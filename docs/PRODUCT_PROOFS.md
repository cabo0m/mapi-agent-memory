# Reproducible product proofs

These proofs demonstrate two MAPI product properties on fresh, disposable SQLite
databases. They use fictional data, make no external API or model calls, and delete
the database after each run.

## Run both proofs

From the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\run_product_proofs.py
```

Portable equivalent when the package and development dependencies are installed in
the active environment:

```bash
python scripts/run_product_proofs.py
```

Run one scenario:

```powershell
.\.venv\Scripts\python.exe scripts\proofs\decision_supersession.py
.\.venv\Scripts\python.exe scripts\proofs\conflict_provenance.py
```

Each command writes stable, machine-readable JSON to stdout and a concise human
report to stderr. A failed setup step or assertion returns a non-zero exit code and a
JSON failure payload.

## Scenario A: decision supersession

The proof stores an earlier SQLite decision and a later PostgreSQL decision, then
uses MAPI's guarded supersession preview and apply contract. It verifies:

- PostgreSQL is the single current head;
- SQLite remains in history;
- the `supersedes` relationship exists;
- provenance references for both decisions remain visible.

## Scenario B: conflict and provenance

The proof stores two incompatible backup-time claims from different fictional
sources: 02:00 UTC and 04:00 UTC. It uses the existing capture reconciliation,
operator review, guarded apply, conflict registry, and conflict-cluster paths. It
verifies:

- both records and both provenance chains are preserved;
- the first record is not overwritten;
- the `contradicts` relationship exists;
- the reconciliation outcome is `conflict_review`;
- both records enter the unresolved `conflicted` state;
- no automatic winner is selected.

The public capture API does not accept contradiction-classification fields directly.
In production those fields are supplied by classification before review. To keep
this proof deterministic and offline, the fixture writes only that classifier output
(`is_contradiction`, target ID, and reason) into its disposable review item. All
subsequent preview, review, apply, provenance, and conflict checks use normal MAPI
product paths.

## Determinism and safety

The JSON omits temporary paths, generated IDs, timestamps, and preview hashes while
still asserting that the guarded hash exists. Two runs therefore produce identical
semantic JSON. Proof databases live in OS temporary directories and are removed
automatically. The scripts do not read or mutate an existing MAPI database.

These are property demonstrations, not comparative benchmarks and not evidence of
market superiority. They intentionally do not include RAG benchmarks, website work,
or Phase 7 packaging.
