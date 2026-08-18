from __future__ import annotations

from typing import Any


def _count_rows(server: Any, table: str) -> int:
    conn = server.get_db_connection()
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def test_new_migrations_create_idempotency_table_and_recall_view(server: Any) -> None:
    conn = server.get_db_connection()
    try:
        versions = server.db_migrations.applied_migration_versions(conn)
        objects = {
            (str(row["type"]), str(row["name"]))
            for row in conn.execute(
                "SELECT type, name FROM sqlite_master WHERE name IN ('mcp_idempotency_requests','memory_recall_telemetry')"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "0033_mcp_idempotency_requests" in versions
    assert "0034_recall_importance_decoupling" in versions
    assert ("table", "mcp_idempotency_requests") in objects
    assert ("view", "memory_recall_telemetry") in objects


def test_direct_save_replays_once_with_same_idempotency_key(server: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("MCP_SURFACE_PROFILE", "agent")
    before = _count_rows(server, "memories")
    kwargs = {
        "content": "One idempotent durable public memory.",
        "project_key": "demo-project",
        "source_event_ref": "test:idempotent-save",
        "idempotency_key": "save-once-001",
    }

    first = server.save_memory(**kwargs)
    second = server.save_memory(**kwargs)
    after = _count_rows(server, "memories")

    assert first["status"] == "created"
    assert second["status"] == "created"
    assert first["memory_id"] == second["memory_id"]
    assert first["idempotency"]["replayed"] is False
    assert second["idempotency"]["replayed"] is True
    assert after == before + 1


def test_direct_save_rejects_key_reuse_with_different_payload(server: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("MCP_SURFACE_PROFILE", "agent")
    first = server.save_memory(
        content="First payload for one key.",
        project_key="demo-project",
        source_event_ref="test:idempotency-conflict:first",
        idempotency_key="save-conflict-001",
    )
    second = server.save_memory(
        content="Different payload for the same key.",
        project_key="demo-project",
        source_event_ref="test:idempotency-conflict:second",
        idempotency_key="save-conflict-001",
    )

    assert first["status"] == "created"
    assert second["status"] == "error"
    assert second["error"] == "idempotency_key_conflict"


def test_workshop_idempotency_replays_mutation_and_rejects_key_on_r0(server: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("MCP_SURFACE_PROFILE", "agent")
    payload = {
        "content": "Workshop-level idempotent memory.",
        "project_key": "demo-project",
        "source_event_ref": "test:workshop-idempotency",
    }
    first = server.run_workshop_action(
        area="memory",
        action="save",
        payload=payload,
        idempotency_key="workshop-save-001",
    )
    second = server.run_workshop_action(
        area="memory",
        action="save",
        payload=payload,
        idempotency_key="workshop-save-001",
    )

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert first["result"]["memory_id"] == second["result"]["memory_id"]
    assert first["result"]["idempotency"]["replayed"] is False
    assert second["result"]["idempotency"]["replayed"] is True

    read_only = server.run_workshop_action(
        area="memory",
        action="find",
        payload={"text_query": "idempotent"},
        idempotency_key="read-only-key",
    )
    assert read_only["status"] == "error"
    assert read_only["error"] == "idempotency_not_applicable"


def test_recall_changes_telemetry_but_not_durable_importance(server: Any, memory_factory: Any) -> None:
    memory_id = memory_factory(
        content="Recall telemetry must not inflate durable importance.",
        memory_type="project_note",
        summary_short="recall decoupling",
        importance_score=0.61,
        project_key="demo-project",
    )
    before = server.get_memory(memory_id)["memory"]

    first = server.recall_memory(
        memory_id=memory_id,
        strength=0.4,
        recall_type="retrieval",
        source="pytest",
    )
    second = server.recall_memory(
        memory_id=memory_id,
        strength=0.9,
        recall_type="manual",
        source="pytest",
    )
    after = server.get_memory(memory_id)["memory"]

    assert first["status"] == "recalled"
    assert second["status"] == "recalled"
    assert first["importance_decoupling"]["importance_changed"] is False
    assert second["importance_decoupling"]["importance_changed"] is False
    assert float(after["importance_score"]) == float(before["importance_score"])
    assert int(after["recall_count"]) == int(before["recall_count"]) + 2


def test_idempotent_recall_increments_once_and_replays_result(server: Any, memory_factory: Any) -> None:
    memory_id = memory_factory(
        content="Idempotent recall fixture.",
        memory_type="project_note",
        summary_short="idempotent recall",
        importance_score=0.72,
        project_key="demo-project",
    )
    before = server.get_memory(memory_id)["memory"]
    kwargs = {
        "memory_id": memory_id,
        "strength": 0.3,
        "recall_type": "retrieval",
        "source": "pytest",
        "idempotency_key": "recall-once-001",
    }

    first = server.recall_memory(**kwargs)
    second = server.recall_memory(**kwargs)
    after = server.get_memory(memory_id)["memory"]

    assert first["status"] == "recalled"
    assert second["status"] == "recalled"
    assert first["idempotency"]["replayed"] is False
    assert second["idempotency"]["replayed"] is True
    assert int(after["recall_count"]) == int(before["recall_count"]) + 1
    assert float(after["importance_score"]) == float(before["importance_score"])


def test_recall_telemetry_is_append_only_and_reports_source(server: Any, memory_factory: Any) -> None:
    memory_id = memory_factory(
        content="Recall telemetry inspection fixture.",
        memory_type="project_note",
        summary_short="recall telemetry",
        project_key="demo-project",
    )
    server.recall_memory(memory_id=memory_id, recall_type="retrieval", source="client-a")
    server.recall_memory(memory_id=memory_id, recall_type="manual", source="client-b")

    telemetry = server.get_memory_recall_telemetry(memory_id=memory_id, limit=10)

    assert telemetry["status"] == "ok"
    assert telemetry["read_only"] is True
    assert telemetry["recorded_event_count"] == 2
    assert telemetry["legacy_unattributed_recall_count"] == 0
    assert set(telemetry["sources_in_page"]) == {"client-a", "client-b"}
    assert set(telemetry["recall_types_in_page"]) == {"manual", "retrieval"}
    assert telemetry["invariants"]["importance_is_not_reconstructed"] is True
    assert telemetry["invariants"]["events_are_append_only"] is True
