from __future__ import annotations

import sqlite3

import pytest

from app.timeline import (
    TIMELINE_INDEX_NAMES,
    TimelineValidationError,
    backfill_timeline,
    ensure_timeline_schema,
    initialize_timeline_connection,
    record_project_event,
    record_timeline_event,
    timeline_query,
)



def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, last_accessed_at TEXT, last_recalled_at TEXT, archived_at TEXT, source TEXT)")
    conn.execute("CREATE TABLE memory_links (id INTEGER PRIMARY KEY AUTOINCREMENT, from_memory_id INTEGER, to_memory_id INTEGER, relation_type TEXT, weight REAL)")
    conn.execute("CREATE TABLE sleep_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, status TEXT, mode TEXT, freedom_level INTEGER, scanned_count INTEGER, changed_count INTEGER, archived_count INTEGER, downgraded_count INTEGER, duplicate_count INTEGER, conflict_count INTEGER, created_summary_count INTEGER, rollback_of_run_id INTEGER)")
    conn.execute("CREATE TABLE sleep_run_actions (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, memory_id INTEGER, action_type TEXT, old_value TEXT, new_value TEXT, reason TEXT, created_at TEXT)")
    return conn



def test_ensure_timeline_schema_creates_table_link_columns_and_project_semantic_columns() -> None:
    conn = make_conn()
    ensure_timeline_schema(conn)

    timeline_columns = {row["name"] for row in conn.execute("PRAGMA table_info(timeline_events)").fetchall()}
    link_columns = {row["name"] for row in conn.execute("PRAGMA table_info(memory_links)").fetchall()}
    index_names = {row[1] for row in conn.execute("PRAGMA index_list(timeline_events)").fetchall()}

    assert {
        "event_time",
        "event_type",
        "created_at",
        "payload_json",
        "operation_id",
        "timeline_scope",
        "semantic_kind",
        "title",
        "project_key",
        "valid_at",
    }.issubset(timeline_columns)
    assert {"created_at", "archived_at"}.issubset(link_columns)
    assert set(TIMELINE_INDEX_NAMES).issubset(index_names)



def test_record_timeline_event_is_returned_by_timeline_query_with_payload() -> None:
    conn = make_conn()
    ensure_timeline_schema(conn)

    inserted_id = record_timeline_event(
        conn,
        event_type="memory.created",
        event_time="2026-04-17T10:00:00Z",
        memory_id=17,
        operation_id="op:abc",
        source_table="memories",
        source_row_id=17,
        origin="api",
        payload={"memory_type": "fact"},
        now_fn=lambda: "2026-04-17T10:00:01Z",
    )

    items = timeline_query(conn, limit=10)
    assert len(items) == 1
    assert items[0]["id"] == inserted_id
    assert items[0]["payload"] == {"memory_type": "fact"}
    assert items[0]["created_at"] == "2026-04-17T10:00:01Z"
    assert items[0]["operation_id"] == "op:abc"
    assert items[0]["timeline_scope"] == "system"
    assert items[0]["semantic_kind"] == "runtime_event"
    assert items[0]["valid_at"] == "2026-04-17T10:00:00Z"



def test_timeline_query_can_filter_by_operation_id() -> None:
    conn = make_conn()
    ensure_timeline_schema(conn)

    record_timeline_event(
        conn,
        event_type="memory.created",
        event_time="2026-04-17T10:00:00Z",
        memory_id=17,
        operation_id="op:one",
        origin="api",
        now_fn=lambda: "2026-04-17T10:00:01Z",
    )
    record_timeline_event(
        conn,
        event_type="memory.created",
        event_time="2026-04-17T10:01:00Z",
        memory_id=18,
        operation_id="op:two",
        origin="api",
        now_fn=lambda: "2026-04-17T10:01:01Z",
    )

    items = timeline_query(conn, limit=10, operation_id="op:two")
    assert len(items) == 1
    assert items[0]["memory_id"] == 18
    assert items[0]["operation_id"] == "op:two"


def test_new_operation_id_supports_prefixed_non_run_groups() -> None:
    from app.timeline import new_operation_id

    memory_op = new_operation_id("mem")
    link_op = new_operation_id("link")

    assert memory_op.startswith("mem:")
    assert link_op.startswith("link:")
    assert memory_op != link_op



def test_record_timeline_event_normalizes_plus_00_00_timestamps_for_runtime_compatibility() -> None:
    conn = make_conn()
    ensure_timeline_schema(conn)

    inserted_id = record_timeline_event(
        conn,
        event_type="memory.created",
        event_time="2026-04-17T10:00:00+00:00",
        origin="pytest",
        now_fn=lambda: "2026-04-17T10:00:01+00:00",
    )

    row = conn.execute("SELECT * FROM timeline_events WHERE id = ?", (inserted_id,)).fetchone()
    assert row is not None
    assert row["event_time"] == "2026-04-17T10:00:00Z"
    assert row["created_at"] == "2026-04-17T10:00:01Z"
    assert row["origin"] == "manual"



def test_record_timeline_event_rejects_naive_runtime_timestamp() -> None:
    conn = make_conn()
    ensure_timeline_schema(conn)

    with pytest.raises(TimelineValidationError):
        record_timeline_event(
            conn,
            event_type="memory.created",
            event_time="2026-04-17T10:00:00",
            origin="api",
            now_fn=lambda: "2026-04-17T10:00:01Z",
        )



def test_record_project_event_sets_project_semantics_and_payload() -> None:
    conn = make_conn()
    ensure_timeline_schema(conn)

    event_id = record_project_event(
        conn,
        project_key="mapi",
        event_type="project.milestone_recorded",
        title="Sandman V1 osiągnął stabilny stan",
        description="Preview i run działają poprawnie.",
        valid_at="2026-04-17T09:00:00Z",
        memory_ids=[21, 25],
        run_ids=[7],
        tags=["sandman", "v1"],
        status="completed",
        canonical=True,
        origin="manual",
        now_fn=lambda: "2026-04-17T09:05:00Z",
    )

    items = timeline_query(conn, limit=10, project_key="mapi")
    assert len(items) == 1
    assert items[0]["id"] == event_id
    assert items[0]["timeline_scope"] == "project"
    assert items[0]["semantic_kind"] == "milestone"
    assert items[0]["title"] == "Sandman V1 osiągnął stabilny stan"
    assert items[0]["project_key"] == "mapi"
    assert items[0]["valid_at"] == "2026-04-17T09:00:00Z"
    assert items[0]["payload"] == {
        "canonical": True,
        "category": "milestone",
        "derived_from_memory_ids": [21, 25],
        "derived_from_run_ids": [7],
        "description": "Preview i run działają poprawnie.",
        "status": "completed",
        "tags": ["sandman", "v1"],
    }



def test_timeline_query_can_filter_by_project_scope_and_sorts_by_valid_at() -> None:
    conn = make_conn()
    ensure_timeline_schema(conn)

    record_project_event(
        conn,
        project_key="mapi",
        event_type="project.note_recorded",
        title="Późniejszy zapis wcześniejszej decyzji",
        valid_at="2026-04-17T08:00:00Z",
        now_fn=lambda: "2026-04-17T10:00:00Z",
    )
    record_project_event(
        conn,
        project_key="mapi",
        event_type="project.phase_completed",
        title="Consolidation V1 działa",
        valid_at="2026-04-17T12:00:00Z",
        now_fn=lambda: "2026-04-17T12:05:00Z",
    )
    record_timeline_event(
        conn,
        event_type="memory.created",
        event_time="2026-04-17T11:00:00Z",
        memory_id=99,
        origin="api",
        now_fn=lambda: "2026-04-17T11:00:01Z",
    )

    items = timeline_query(conn, limit=10, project_key="mapi", timeline_scope="project")
    assert len(items) == 2
    assert items[0]["title"] == "Consolidation V1 działa"
    assert items[1]["title"] == "Późniejszy zapis wcześniejszej decyzji"



def test_backfill_timeline_is_non_duplicate_for_reconstructed_memory_created() -> None:
    conn = make_conn()
    ensure_timeline_schema(conn)
    conn.execute(
        "INSERT INTO memories (created_at, source) VALUES (?, ?)",
        ("2026-04-17T09:00:00Z", "api"),
    )

    first = backfill_timeline(conn)
    second = backfill_timeline(conn)
    rows = conn.execute("SELECT event_type, reconstructed, timeline_scope, semantic_kind FROM timeline_events").fetchall()

    assert first == 1
    assert second == 0
    assert len(rows) == 1
    assert rows[0]["event_type"] == "memory.created"
    assert rows[0]["reconstructed"] == 1
    assert rows[0]["timeline_scope"] == "system"
    assert rows[0]["semantic_kind"] == "backfill_snapshot"



def test_initialize_timeline_connection_can_skip_backfill() -> None:
    conn = make_conn()
    conn.execute(
        "INSERT INTO memories (created_at, source) VALUES (?, ?)",
        ("2026-04-17T09:00:00Z", "api"),
    )

    inserted = initialize_timeline_connection(conn, auto_backfill=False)
    rows = conn.execute("SELECT COUNT(*) AS count FROM timeline_events").fetchone()

    assert inserted == 0
    assert rows["count"] == 0
