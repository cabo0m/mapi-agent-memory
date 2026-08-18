# Quality parity closure

This document defines capability parity between the public VPS runtime and the internal reference runtime. Parity is measured by behavior, safety contracts and operational capability, not by identical filenames or raw MCP tool count.

## In scope

The public VPS runtime now covers the reference runtime's reusable agent-memory quality stack:

- project-scoped bootstrap and continuity restoration;
- evidence-first Agent Self Model with identity snapshot, commitments, autobiographical timeline and compact capsule;
- deterministic Self Delta with snapshot fingerprint validation, reclassification, uncertainty and supersession reporting;
- controlled source-bound Self Narrative with deterministic rendering and optional stateless structured provider planning;
- lexical, semantic and hybrid retrieval, stable cursor pagination, compact projections and a portable golden retrieval corpus;
- source-bound Gravity preview, shadow comparison and bounded Context Engine injection;
- Memory Steward before/after/session-close/nightly planning;
- current-state lineage, supersession, evidence-bound relations, graph debt audit and guarded repair/rollback;
- Sandman deterministic/provider routing, proposal-only external-model boundary and observability;
- provenance capture/backfill, conversation archive and evidence-first day reconstruction;
- MCP backpressure/session reliability, idempotency and structured capability contracts;
- remote authentication boundary and loopback-safe runtime policy;
- portable doctor diagnostics, recovery planning, migration CLI and package/runtime health checks.

## Intentional divergences

These differences are deliberate and are not parity defects:

1. **No branded private identity defaults.** The public runtime uses configurable `MAPI_AGENT_SUBJECT_KEY`, `MAPI_AGENT_DISPLAY_NAME` and `MAPI_AGENT_PROJECT_KEY` rather than shipping an internal assistant identity or owner profile.
2. **No private showcase/browser UI.** Internal award/demo pages, local memory browser and legacy HTTP convenience routes are presentation/operator surfaces, not MCP memory-runtime capabilities.
3. **No one-off private namespace repair.** Deployment-specific data correction routines are not copied into the general public product. Reusable guarded graph repair remains available.
4. **Portable deployment commands replace local helper scripts.** Schema migration and recovery use installed CLI entry points instead of reference-machine launcher helpers.
5. **Neutral modules replace identity-bound module names.** Self-model and Gravity behavior is implemented under reusable agent-oriented modules rather than preserving internal naming.

## Safety equivalence

Public parity does not mean copying unsafe or identity-specific behavior. The public implementation preserves these stronger boundaries:

- semantic similarity or Gravity cannot create durable truth relations by themselves;
- providers cannot mutate memory through Self Narrative and cannot write its prose;
- provider-planned narrative may select only allowlisted claim IDs; source IDs are derived host-side;
- recovery execution is local CLI/operator-only, uses JSON argv and never `shell=True`;
- remote operation requires an explicit authentication boundary and the runtime remains loopback-safe by default;
- self evidence is explicit: ordinary project notes are not silently promoted into agent identity.

## Parity metric

Raw endpoint count is informational only. The public VPS surface may expose more tools because reusable capabilities are split into explicit neutral contracts. A parity defect exists only when a reusable reference capability has neither a public equivalent nor an explicit, justified exclusion above.

The final release gate is: full unit suite, compile check, dependency check, wheel corpus verification, public repository audit (excluding repository-history policy that is intentionally evaluated at release state), and a clean Git worktree after commit.
