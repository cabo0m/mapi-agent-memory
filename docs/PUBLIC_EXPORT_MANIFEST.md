# Public export manifest

## Source

- source commit: `72a3a06780e3c250241aaed476db610df1af9ec3`;
- export strategy: reviewed tracked snapshot, no source Git history;
- target history: one new root commit after release gates.

## Included categories

- memory, lifecycle, governance, timeline and Sandman application modules;
- workshop manifests, handlers, registry and access policy;
- safe MCP runtime composition;
- selected contract/unit/migration tests;
- generic retention preview/apply-validation tools;
- public packaging, CLI, deterministic synthetic seed and documentation.

## Excluded categories

- source `.git` and all history;
- databases, memories, vectors, embeddings and runtime state;
- backups, logs, artifacts, archives and recovery evidence;
- private deployment files and platform launchers;
- private operational scripts, prompts and handoffs;
- historical documentation and archived reports;
- public multi-user onboarding, invite/self-registration and retired bridge components;
- private identity bootstrap and personal defaults;
- untested browser/demo applications.

## Deliberately rewritten

- product README and all public documentation;
- environment template and path defaults;
- surface profiles and owner defaults;
- bootstrap context;
- project aliases and retrieval QA fixtures;
- package metadata and CLI commands;
- synthetic demo seed;
- optional-provider gates.
- Apache-2.0 licensing, public author metadata, and security contact;
- English-only public documentation and Git-metadata release auditing.

## Compatibility differences

- new public Git history; the audit accepts no remote before publication or
  exactly one canonical `origin` for `cabo0m/mapi-agent-memory` afterward;
- Apache License 2.0 instead of an undecided distribution status;
- `reader`, `agent`, `maintainer`, `admin` profiles;
- `admin` requires an additional explicit enablement gate;
- model providers and semantic retrieval are optional;
- no populated database or private runtime identity;
- single-instance public positioning, no onboarding product.

The exact allowlisted file inventory is machine-readable in `public_file_manifest.json`.
Regenerate it through the audit tool rather than editing it manually:

```bash
python scripts/audit_public_repository.py --write-manifest
```
