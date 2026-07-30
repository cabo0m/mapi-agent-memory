from __future__ import annotations

import sqlite3

from app import db_migrations


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _apply_until_0022(conn: sqlite3.Connection) -> list[str]:
    ran: list[str] = []
    db_migrations.ensure_schema_migrations_table(conn)
    for version, migration_fn in db_migrations.MIGRATION_SEQUENCE:
        if version == "0023_memory_v3_lifecycle_snapshots":
            break
        migration_fn(conn)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        ran.append(version)
    conn.commit()
    return ran


def test_lifecycle_snapshot_migration_creates_table_and_indexes() -> None:
    conn = _conn()

    ran = db_migrations.apply_migrations_through(conn, "0023_memory_v3_lifecycle_snapshots")

    assert "0023_memory_v3_lifecycle_snapshots" in ran
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(memory_lifecycle_snapshots)").fetchall()
    }
    index_names = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(memory_lifecycle_snapshots)").fetchall()
    }

    assert {"operation_key", "before_snapshot_json", "rollback_snapshot_json", "updated_at"}.issubset(columns)
    assert {
        "idx_memory_lifecycle_snapshots_status",
        "idx_memory_lifecycle_snapshots_new_memory",
        "idx_memory_lifecycle_snapshots_old_memory",
        "idx_memory_lifecycle_snapshots_created_at",
    }.issubset(index_names)


def test_lifecycle_snapshot_migration_is_idempotent() -> None:
    conn = _conn()

    first = db_migrations.apply_migrations_through(conn, "0023_memory_v3_lifecycle_snapshots")
    second = db_migrations.apply_migrations_through(conn, "0023_memory_v3_lifecycle_snapshots")

    assert "0023_memory_v3_lifecycle_snapshots" in first
    assert second == []


def test_lifecycle_snapshot_migration_runs_cleanly_on_fixture_up_to_0022() -> None:
    conn = _conn()

    prior = _apply_until_0022(conn)
    counts_before = {
        "memories": int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]),
        "memory_links": int(conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]),
        "memory_events": int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]),
    }
    remaining = db_migrations.apply_migrations_through(
        conn, "0023_memory_v3_lifecycle_snapshots"
    )
    counts_after = {
        "memories": int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]),
        "memory_links": int(conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]),
        "memory_events": int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]),
    }

    assert "0022_consolidation_rollback_preview_snapshots" in prior
    assert remaining == ["0023_memory_v3_lifecycle_snapshots"]
    assert counts_before == counts_after
    assert int(conn.execute("SELECT COUNT(*) FROM memory_lifecycle_snapshots").fetchone()[0]) == 0


def test_lifecycle_snapshot_migration_foreign_keys_are_enforced() -> None:
    conn = _conn()
    db_migrations.apply_all_migrations(conn)

    try:
        conn.execute(
            """
            INSERT INTO memory_lifecycle_snapshots (
                operation_key, operation_type, status, new_memory_id, old_memory_id, relation_kind, reason,
                input_fingerprint, candidate_set_fingerprint, preview_hash, before_snapshot_json, after_snapshot_json,
                link_snapshot_json, event_snapshot_json, applied_at, started_at, created_at, updated_at
            )
            VALUES (?, 'supersession', 'applied', ?, ?, ?, ?, ?, ?, ?, '{}', '{}', '{}', '{}', ?, ?, ?, ?)
            """,
            ("op-fk", 123, 456, "replacement", "fk check", "in", "cand", "prev", "2026-07-13T00:00:00Z", "2026-07-13T00:00:00Z", "2026-07-13T00:00:00Z", "2026-07-13T00:00:00Z"),
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("foreign key constraint should reject missing memory references")
