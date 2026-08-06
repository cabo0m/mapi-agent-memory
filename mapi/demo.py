from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any


def run_demo_database(path: Path) -> dict[str, Any]:
    """Run the product demo in an explicitly supplied, disposable database."""
    from app import db_migrations
    from app.runtime.context import configure_runtime_context

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    configure_runtime_context(root=path.parent, data_dir=path.parent, db_path=path)

    import server_core

    connection = server_core.get_db_connection()
    try:
        db_migrations.apply_all_migrations(connection)
        old = server_core._insert_memory(
            connection,
            content="Use SQLite for the application database.",
            summary_short="Application database decision: SQLite",
            memory_type="decision",
            project_key="mapi-product-demo",
            scope_code="project",
            source="synthetic-demo",
            source_context="isolated public product demo",
            source_event_ref="mapi-project-memory-demo:sqlite",
            truth_kind="decision",
            confidence_score=1.0,
            importance_score=0.8,
            ensure_embedding=False,
        )
        new = server_core._insert_memory(
            connection,
            content="Replace SQLite with PostgreSQL for the application database.",
            summary_short="Application database decision: PostgreSQL",
            memory_type="decision",
            project_key="mapi-product-demo",
            scope_code="project",
            source="synthetic-demo",
            source_context="isolated public product demo",
            source_event_ref="mapi-project-memory-demo:postgresql",
            truth_kind="decision",
            confidence_score=1.0,
            importance_score=0.8,
            ensure_embedding=False,
        )
        connection.commit()
    finally:
        connection.close()

    old_id = int(old["id"])
    new_id = int(new["id"])
    reason = "The PostgreSQL decision replaces the earlier SQLite decision."
    preview = server_core.preview_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason=reason,
    )
    if preview.get("status") != "preview_ready" or not preview.get("preview_hash"):
        raise RuntimeError(f"Supersession preview failed: {preview}")
    applied = server_core.apply_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason=reason,
        expected_preview_hash=str(preview["preview_hash"]),
        applied_by="mapi-demo",
        notes="Explicit confirmation for fictional demo decisions in a disposable database.",
        confirm_protected=True,
    )
    if applied.get("status") != "applied":
        raise RuntimeError(f"Supersession apply failed: {applied}")

    current = server_core.get_memory_current_state(old_id, include_history=True)
    links = server_core.get_memory_links(new_id)
    current_record = current.get("current") or {}
    history = current.get("history") or []
    relation = next(
        (
            item
            for item in links.get("links", [])
            if int(item.get("from_memory_id") or 0) == new_id
            and int(item.get("to_memory_id") or 0) == old_id
            and item.get("relation_type") == "supersedes"
        ),
        None,
    )
    if int(current_record.get("id") or 0) != new_id:
        raise RuntimeError(f"Current state is incorrect: {current}")
    if [int(item["id"]) for item in history] != [old_id]:
        raise RuntimeError(f"Preserved history is incorrect: {current}")
    if relation is None:
        raise RuntimeError(f"Supersession relationship is missing: {links}")

    lines = [
        "Current decision: PostgreSQL",
        "Previous decision: SQLite",
        "Relationship: PostgreSQL supersedes SQLite",
        f"Current record ID: {new_id}",
        f"Previous record ID: {old_id}",
        f"Preview hash: {preview['preview_hash']}",
    ]
    return {
        "status": "ok",
        "database": str(path),
        "project_key": "mapi-product-demo",
        "current_memory_id": new_id,
        "previous_memory_id": old_id,
        "relation": "supersedes",
        "preview_hash": preview["preview_hash"],
        "apply_run_id": applied.get("run_id"),
        "human_output": "\n".join(lines),
    }


def run_isolated_demo() -> dict[str, Any]:
    """Run the demo in a temporary directory that is removed automatically."""
    with tempfile.TemporaryDirectory(prefix="mapi-project-memory-demo-") as directory:
        return run_demo_database(Path(directory) / "demo.db")
