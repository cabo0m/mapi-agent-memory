# MAPI implementation guide

This guide describes the extension points that are present in this release
candidate. Examples use the real workshop, registry, permission, migration, and
dispatch contracts. Run every verification command from the repository root.

## Runtime request flow

An MCP workshop call follows this path:

```text
MCP client
-> FastMCP (`server_core.py`)
-> compact surface (`mcp_surface.py`)
-> workshop catalogue (`app.workshops.catalog.WORKSHOPS`)
-> permission guard (`app.workshops.access_policy`)
-> dispatcher (`app.workshops.runner.run_workshop_action_payload`)
-> runtime registry (`app.workshops.runtime_registry`)
-> handler
-> service layer
-> SQLite
-> audit/timeline response
```

`server.py` is the thin public entry point. Importing it composes the runtime
and binds handlers declared by workshop packages. `mapi-server` calls
`app.runtime.server_runtime.run_server`.

The compact MCP surface exposes workshop-oriented tools rather than every
handler as a separate public tool. `mcp_surface.lookup_workshop_action` resolves
the declared action. `profile_allows` and
`app.workshops.access_policy.profile_allows_requirement` enforce the selected
profile. Unknown profiles fail closed to `reader`.

`run_workshop_action_payload` rejects unknown actions, checks the profile,
decodes JSON, validates fields against both the handler signature and
`payload_schema`, applies the runtime freshness guard, resolves the handler, and
records security evidence for `R3` execution.

## Complete example workshop

The example adds two actions backed by an `example_notes` table:

```text
app/workshops/example/
    __init__.py
    manifest.py
    handlers.py
```

The snippets are compiled by `tests/test_public_documentation.py`. They are
complete module examples, but they are documentation only and are not
registered in the shipped catalogue.

### `app/workshops/example/__init__.py`

<!-- example-module: app/workshops/example/__init__.py -->
```python
from __future__ import annotations

from .handlers import TOOL_NAMES, bind_handlers
from .manifest import WORKSHOP

__all__ = ["WORKSHOP", "TOOL_NAMES", "bind_handlers"]
```

### `app/workshops/example/manifest.py`

<!-- example-module: app/workshops/example/manifest.py -->
```python
from __future__ import annotations

from app.workshops.contracts import Workshop, WorkshopAction

WORKSHOP = Workshop(
    area="example",
    purpose="Read and create validated example notes.",
    min_profile="reader",
    risk="medium",
    recommended_first_action="get_note",
    actions=(
        WorkshopAction(
            action="get_note",
            tool_name="example_get_note",
            purpose="Read one example note.",
            min_profile="reader",
            risk="low",
            risk_class="R0",
            payload_schema={"note_id": "int"},
        ),
        WorkshopAction(
            action="create_note",
            tool_name="example_create_note",
            purpose="Create one explicitly authorized example note.",
            min_profile="agent",
            risk="medium",
            risk_class="R1",
            payload_schema={
                "title": "str",
                "body": "str",
                "request_id": "str",
            },
        ),
    ),
    guardrails=(
        "The write requires the agent profile and a caller request identifier.",
    ),
)
```

### `app/workshops/example/handlers.py`

<!-- example-module: app/workshops/example/handlers.py -->
```python
from __future__ import annotations

import sqlite3
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.memory_config import DB_PATH
from .manifest import WORKSHOP

TOOL_NAMES = tuple(action.tool_name for action in WORKSHOP.actions)


class CreateNotePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(min_length=8, max_length=128)


def example_get_note(note_id: int) -> dict[str, Any]:
    if note_id < 1:
        return {"status": "error", "error": "invalid_note_id"}
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, title, body, request_id FROM example_notes WHERE id = ?",
            (note_id,),
        ).fetchone()
    if row is None:
        return {"status": "error", "error": "note_not_found"}
    return {"status": "ok", "note": dict(row)}


def example_create_note(
    title: str,
    body: str,
    request_id: str,
) -> dict[str, Any]:
    payload = CreateNotePayload(
        title=title,
        body=body,
        request_id=request_id,
    )
    with sqlite3.connect(DB_PATH) as connection:
        existing = connection.execute(
            "SELECT id FROM example_notes WHERE request_id = ?",
            (payload.request_id,),
        ).fetchone()
        if existing is not None:
            return {
                "status": "ok",
                "note_id": int(existing[0]),
                "idempotent_replay": True,
            }
        cursor = connection.execute(
            """
            INSERT INTO example_notes(title, body, request_id)
            VALUES (?, ?, ?)
            """,
            (payload.title, payload.body, payload.request_id),
        )
        connection.commit()
    return {
        "status": "ok",
        "note_id": int(cursor.lastrowid),
        "idempotent_replay": False,
    }


def bind_handlers(_provider: Any) -> dict[str, Any]:
    return {
        "example_get_note": example_get_note,
        "example_create_note": example_create_note,
    }
```

Direct binding is appropriate when handlers live in the workshop package. A
workshop that exposes functions from `server_core.py` or a runtime service may
instead use `getattr(provider, tool_name)` like existing packages.

### Catalogue and permission registration

Import the package and add it once to `WORKSHOP_PACKAGES` in
`app/workshops/catalog.py`:

<!-- example-fragment: catalogue-registration -->
```python
from app.workshops import example

WORKSHOP_PACKAGES = (
    # Existing packages...
    example,
)
```

The catalogue rejects duplicate areas, duplicate action names, and tool names
owned by more than one workshop.

Add the mutating tool to the canonical policy in
`app/workshops/access_policy.py`:

<!-- example-fragment: access-policy-registration -->
```python
OPERATOR_WRITE_TOOLS = frozenset(
    {
        # Existing tools...
        "example_create_note",
    }
)
```

This assignment makes `example_create_note` an `agent`/`R1` action. The
read-only action is not in a write or maintenance set and therefore resolves to
`reader`/`R0`. Do not trust `min_profile` or `risk_class` supplied by a client;
the server derives effective access from the catalogue and policy.

## Adding a read-only action

A read-only action:

1. has no externally observable mutation;
2. uses an explicit payload schema;
3. is classified `R0`;
4. normally permits the `reader` profile;
5. returns stable machine-readable errors.

Minimal handler:

<!-- example-fragment: readonly-handler -->
```python
def get_widget(widget_id: int) -> dict[str, object]:
    if widget_id < 1:
        return {"status": "error", "error": "invalid_widget_id"}
    widget = load_widget(widget_id)
    if widget is None:
        return {"status": "error", "error": "widget_not_found"}
    return {"status": "ok", "widget": widget}
```

Minimal contract test:

<!-- example-fragment: readonly-test -->
```python
def test_get_widget_rejects_invalid_identifier() -> None:
    assert get_widget(0) == {
        "status": "error",
        "error": "invalid_widget_id",
    }
```

Do not label an action read-only if it updates recall counters, access
timestamps, queues, audit state, caches stored in the database, or files.

## Adding a mutating action

Mutations require all of the following:

- strict input validation;
- authorization derived from the server-selected profile;
- an appropriate `R1`, `R2`, or `R3` risk class;
- idempotency when client retries are possible;
- audit evidence identifying intent and outcome;
- a preview when the affected set is broad or data-dependent;
- a verified backup for destructive or hard-to-reconstruct state;
- rollback material or an explicit explanation of why rollback is impossible.

Use `R1` for narrow explicit writes, `R2` for maintenance operations that can
affect lifecycle or queues, and `R3` for dangerous administrative or protected
lifecycle operations. The policy in `app/workshops/access_policy.py` is the
authority.

Test the contract through `run_workshop_action_payload`, not only by calling
the handler:

<!-- example-fragment: mutation-contract-test -->
```python
def test_create_note_requires_agent_profile(monkeypatch) -> None:
    monkeypatch.setenv("MCP_SURFACE_PROFILE", "reader")
    result = run_workshop_action_payload(
        area="example",
        action="create_note",
        payload={
            "title": "Release note",
            "body": "The migration was verified.",
            "request_id": "request-0001",
        },
        normalize_optional_text=lambda value: str(value).strip() or None,
    )
    assert result["status"] == "error"
    assert result["error"] == "insufficient_profile"
```

For `R3`, the dispatcher records started, completed, or failed security audit
events. Domain audit and timeline records remain the handler/service
responsibility.

## Guarded preview and apply

Broad or destructive changes must bind apply to the exact reviewed preview:

```text
preview
-> canonical payload
-> preview hash
-> explicit apply
-> hash verification
-> audit
-> rollback material
```

Concrete pattern:

<!-- example-fragment: guarded-preview-apply -->
```python
import hashlib
import json


def canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def preview_change(ids: list[int]) -> dict[str, object]:
    payload = {
        "schema": "example_change_preview.v1",
        "ids": sorted(set(ids)),
    }
    preview_hash = hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()
    return {"payload": payload, "preview_hash": preview_hash}


def apply_change(
    ids: list[int],
    expected_preview_hash: str,
) -> dict[str, object]:
    current = preview_change(ids)
    if current["preview_hash"] != expected_preview_hash:
        return {"status": "error", "error": "stale_preview_hash"}
    backup = create_verified_backup()
    run_id = record_apply_started(current, backup)
    try:
        result = execute_transaction(current["payload"])
        record_apply_completed(run_id, result)
    except Exception:
        record_apply_failed(run_id)
        raise
    return {
        "status": "ok",
        "run_id": run_id,
        "rollback_material": backup,
    }
```

Canonicalization must cover every field that affects selection or mutation.
Apply recomputes the preview from current state and rejects stale hashes. It
must never accept a client-provided affected set without deriving and checking
it again. A mismatch is an error, not a prompt to continue.

## Migration example

Assume the current tail is `0032_retire_bridge_mailbox`. The next identifier is
`0033_example_notes`.

<!-- example-fragment: migration-function -->
```python
def migration_0033_example_notes(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS example_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            request_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_example_notes_created_at
        ON example_notes(created_at)
        """
    )
```

Register the identifier and function once in `MIGRATION_SEQUENCE` in
`app/db_migrations.py`, following the existing tuple format. Migration
functions must be idempotent because startup and operator tooling may evaluate
the chain more than once.

Fresh-chain test:

<!-- example-fragment: fresh-migration-test -->
```python
def test_fresh_chain_contains_example_notes(tmp_path: Path) -> None:
    database = tmp_path / "fresh.db"
    with sqlite3.connect(database) as connection:
        apply_all_migrations(connection)
        row = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'example_notes'
            """
        ).fetchone()
    assert row is not None
```

Upgrade test:

<!-- example-fragment: upgrade-migration-test -->
```python
def test_upgrade_0032_to_0033_preserves_memories(tmp_path: Path) -> None:
    database = create_database_at_version(tmp_path, "0032_retire_bridge_mailbox")
    before = count_memories(database)
    migrate_database(database)
    assert count_memories(database) == before
    assert latest_migration(database) == "0033_example_notes"
```

Never edit a migration that may have been applied. Add a new migration instead.
Before an upgrade, stop writers, create and verify a SQLite backup, record the
current tail, then run `mapi-migrate` and `mapi-doctor`. Database rollback means
stopping the new runtime, restoring the verified backup, and running code
compatible with the restored schema. A reverse migration is not implied.

## Permission and validation failures

Stable examples:

Insufficient profile:

```json
{"status":"error","error":"insufficient_profile","profile":"reader","required":"agent"}
```

Admin surface disabled:

```json
{"status":"error","error":"insufficient_profile","profile":"maintainer","required":"admin"}
```

An `admin` request normalizes to `maintainer` unless
`MAPI_ADMIN_TOOLS_ENABLED=true`.

Unknown workshop or action:

```json
{"status":"error","error":"unknown_workshop_action","area":"example","action":"missing"}
```

Invalid payload:

```json
{"status":"error","error":"invalid_workshop_payload","validation_errors":[{"field":"note_id","code":"invalid_type"}]}
```

Stale preview:

```json
{"status":"error","error":"stale_preview_hash"}
```

Do not convert these failures into automatic retries with broader permissions.

## Registry debugging

Import the runtime before validating bindings:

```bash
python -c "import server; from app.workshops.runtime_registry import validate_workshop_handler_registry as v; print(v())"
```

The result must have `complete: True`, with empty `unresolved` and `extra`
lists.

Common failures:

- **Unresolved handler:** the manifest tool name does not match the bound
  function, the provider was not composed, or `bind_handlers` omitted it.
- **Extra handler:** a handler was bound but has no catalogue owner.
- **Duplicate tool registration:** two workshops declare the same
  `tool_name`, or a FastMCP override did not remove the inherited local tool.
- **Missing workshop package:** the package is absent from
  `WORKSHOP_PACKAGES`.
- **Manifest drift:** `TOOL_NAMES`, actions, handler signatures, or payload
  schemas disagree.
- **Capability drift:** `docs/CAPABILITIES.md` differs from the registry
  renderer.

Useful commands:

```bash
python -m pytest tests/test_workshop_package_registry.py -q
python -m pytest tests/test_public_surface.py -q
python scripts/check_mcp_surface.py
mapi-doctor
```

## Capability documentation generation

Run:

```bash
mapi-capabilities
```

This rewrites `docs/CAPABILITIES.md` from
`app.workshops.catalog.WORKSHOPS`. Regenerate it whenever a workshop, action,
profile, risk class, tool name, or capability property changes.

The drift test is:

```bash
python -m pytest tests/test_public_packaging.py::test_capability_document_matches_registry -q
```

Do not hand-edit the generated catalogue.

## Memory write routing

`app/memory/write_routing.py` distinguishes explicit writes from uncertain
agent material. Use:

- direct explicit write when the client deliberately authorizes persistence;
- agent proposal when generated or uncertain material requires review;
- maintenance write for controlled lifecycle/governance operations;
- capture reconciliation for validated queued proposals.

See `app/memory/capture_queue.py` and `app/memory/reconciliation.py`. A payload
field must never grant itself permission to select a stronger route.

## Lifecycle and lineage

Current-state resolution lives in `app/memory/current_state.py`. Supersession
preserves old rows as history and identifies a current head. Lifecycle previews
and apply modules bind decisions to hashes and snapshots. Rollback uses recorded
evidence; it is not an instruction to delete history.

## Retrieval

Lexical and project-aware retrieval are core capabilities. Project aliases are
explicit in `app/memory/project_keys.py`. Semantic retrieval is optional and
isolated in `app/memory/semantic.py` and `vector_store.py`. Retrieval debugging
must explain project resolution, filters, and why an item matched.

## Sandman

`app/sandman` contains deterministic providers, validation, routing, and
observability. External providers are disabled by default, proposal-only, and
treated as untrusted. Provider output must pass schema and policy validation
before it can enter a review queue. A model must not create autonomous facts.

## Testing an extension

Recommended sequence:

```bash
# Focused unit and contract tests
python -m pytest tests/test_your_extension.py -q

# Registry and permission contracts
python -m pytest tests/test_workshop_package_registry.py tests/test_public_surface.py -q

# Migration chain and upgrade coverage
python -m pytest tests/test_db_migrations.py tests/test_public_packaging.py -q

# Documentation examples and links
python -m pytest tests/test_public_documentation.py -q

# Generated capability verification
mapi-capabilities
python -m pytest tests/test_public_packaging.py::test_capability_document_matches_registry -q

# Full release gate
python -m pytest -q
ruff check .
python -m compileall -q app mapi scripts
python scripts/audit_public_repository.py
git diff --check
```

Clean-install smoke tests must run from a temporary directory outside the
repository, with `PYTHONPATH` unset and a fresh database. The release gate must
exercise migration, repeatable demo seed, doctor, model-free startup, a real MCP
client, memory write, lexical search, read, links, timeline, and denial of an
admin-only action.
