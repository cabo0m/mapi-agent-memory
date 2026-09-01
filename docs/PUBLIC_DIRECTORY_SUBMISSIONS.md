# Public directory submission package

This neutral package can be adapted for MCP catalogues, GitHub awesome lists, open-source documentation and community posts. It is preparation material, not evidence that a submission has been made.

## Core fields

- Name: **MAPI**
- Repository: `https://github.com/cabo0m/mapi-agent-memory`
- Category: **Memory & Knowledge**
- Runtime: **Python**
- Transport: **HTTP MCP**
- License: **Apache License 2.0**
- Publisher: **Michał Chlewicki / MorenaTech**

Short description:

> Persistent, auditable project memory for Codex, ChatGPT and other MCP-compatible AI clients.

More cautious alternative:

> Persistent, auditable project memory for Codex and other MCP-compatible AI clients.

## Long description

MAPI is a self-hosted MCP memory server for project decisions, corrections, rules, progress and next steps. It resolves current state while preserving historical records and lineage, separates explicit writes from proposals, and keeps project boundaries visible. Records can carry provenance and confidence metadata. Conflict review, guarded lifecycle operations, preview hashes, audit evidence and rollback support controlled maintenance without treating an opaque vector index as the source of truth. The model-free local core runs on Python and SQLite; optional semantic and provider integrations are disabled by default.

## Reproducible product proofs

MAPI includes deterministic, model-free scenarios for decision supersession and conflicting-source provenance. The proofs use synthetic data on disposable databases: one resolves PostgreSQL as current while retaining the earlier SQLite decision and lineage; the other preserves two conflicting records, their provenance and an unresolved review state without silent overwrite.

- Methodology and expected results: [PRODUCT_PROOFS.md](https://github.com/cabo0m/mapi-agent-memory/blob/main/docs/PRODUCT_PROOFS.md)
- Reproduction command: `python scripts/run_product_proofs.py`

These scenarios demonstrate specific governance and lineage properties. They are not comparative benchmark results and do not claim that MAPI eliminates hallucinations or guarantees correct memory.

## Tags

`mcp`, `mcp-server`, `agent-memory`, `persistent-memory`, `project-memory`, `codex`, `chatgpt`, `ai-agents`, `python`, `sqlite`, `self-hosted`, `local-first`, `audit`

## Limitations

- self-hosted and local-first; no hosted SaaS;
- Python 3.11 or 3.12;
- SQLite single-writer characteristics;
- ChatGPT web requires a separately secured remote HTTPS deployment;
- remote authentication is experimental and outside the quickstart;
- Docker and macOS are not verified in this candidate.

## Community post copy

MAPI is an Apache 2.0, self-hosted and local-first project memory server for MCP clients, currently published as a Public Release Candidate / Developer Preview. It helps assistants carry decisions, corrections, rules, progress and next steps across sessions while retaining provenance and auditable history. Reproducible, model-free proofs show SQLite being superseded by PostgreSQL while history remains available, and two conflicting source records remaining visible with provenance instead of being silently overwritten. The project runs locally on Python and SQLite and does not offer hosted SaaS.
