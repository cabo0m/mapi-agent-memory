from __future__ import annotations

import sqlite3

from app import db_migrations


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _apply_until_0023(conn: sqlite3.Connection) -> None:
    for version, migration_fn in db_migrations.MIGRATION_SEQUENCE:
        if version == "0024_memory_capture_review_queue":
            break
        migration_fn(conn)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))


def test_migration_0024_creates_table_indexes_and_disabled_flag() -> None:
    conn = _make_conn()
    db_migrations.ensure_schema_migrations_table(conn)
    _apply_until_0023(conn)

    db_migrations._migration_0024_memory_capture_review_queue(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(memory_capture_review_items)").fetchall()
    }
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(memory_capture_review_items)").fetchall()
    }
    flag = conn.execute(
        "SELECT flag_key, is_enabled, rollout_mode FROM feature_flags WHERE flag_key = 'memory_v3_capture_reconciliation_enabled'"
    ).fetchone()

    assert {"proposal_key", "status", "proposal_json", "input_fingerprint", "created_at", "updated_at"}.issubset(columns)
    assert {
        "sqlite_autoindex_memory_capture_review_items_1",
        "idx_memory_capture_review_items_status",
        "idx_memory_capture_review_items_project_key",
        "idx_memory_capture_review_items_created_at",
        "idx_memory_capture_review_items_input_fingerprint",
    }.issubset(indexes)
    assert flag["flag_key"] == "memory_v3_capture_reconciliation_enabled"
    assert int(flag["is_enabled"]) == 0
    assert flag["rollout_mode"] == "off"


def test_migration_0024_is_idempotent_and_preserves_protected_table_counts() -> None:
    conn = _make_conn()
    db_migrations.ensure_schema_migrations_table(conn)
    _apply_until_0023(conn)

    protected_tables = [
        "memories",
        "memory_links",
        "memory_events",
        "memory_lifecycle_snapshots",
        "memory_consolidation_review_items",
    ]
    before = {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in protected_tables
    }

    db_migrations._migration_0024_memory_capture_review_queue(conn)
    db_migrations._migration_0024_memory_capture_review_queue(conn)

    after = {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in protected_tables
    }
    flags = conn.execute(
        "SELECT COUNT(*) FROM feature_flags WHERE flag_key = 'memory_v3_capture_reconciliation_enabled'"
    ).fetchone()[0]

    assert before == after
    assert int(flags) == 1


def test_apply_all_migrations_includes_0024_on_fixture_up_to_0023() -> None:
    conn = _make_conn()
    db_migrations.ensure_schema_migrations_table(conn)
    _apply_until_0023(conn)

    ran = db_migrations.apply_migrations_through(
        conn, "0027_memory_v3_pointer_lifecycle_execution"
    )
    versions = db_migrations.applied_migration_versions(conn)

    assert ran == [
        "0024_memory_capture_review_queue",
        "0025_memory_v3_policy_metadata",
        "0026_sandman_semantic_shadow_runs",
        "0027_memory_v3_pointer_lifecycle_execution",
    ]
    assert "0024_memory_capture_review_queue" in versions
    assert "0025_memory_v3_policy_metadata" in versions
    assert "0026_sandman_semantic_shadow_runs" in versions
