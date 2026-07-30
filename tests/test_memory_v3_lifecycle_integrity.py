from __future__ import annotations

from typing import Any

import mcp_surface


def _create_validated_memory(server: Any, **overrides: Any) -> int:
    payload = {
        "content": "Validated memory.",
        "memory_type": "project_note",
        "summary_short": "Validated memory",
        "project_key": "mapi",
        "scope_code": "project",
        "state_code": "validated",
        "memory_v2_status": "active",
        "truth_kind": "fact",
        "entry_type": "project",
        "confidence_score": 0.9,
        "importance_score": 0.75,
    }
    payload.update(overrides)
    return int(server.create_memory(**payload)["memory"]["id"])


def _table_counts(server: Any) -> dict[str, int]:
    conn = server.get_db_connection()
    try:
        return {
            "memories": int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]),
            "memory_links": int(conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]),
            "memory_events": int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]),
            "apply_snapshots": int(conn.execute("SELECT COUNT(*) FROM memory_consolidation_apply_snapshots").fetchone()[0]),
            "rollback_snapshots": int(conn.execute("SELECT COUNT(*) FROM memory_consolidation_rollback_snapshots").fetchone()[0]),
        }
    finally:
        conn.close()


def test_lifecycle_integrity_reports_clean_single_lineage(server: Any) -> None:
    base_id = _create_validated_memory(server, summary_short="Base v1")
    new_id = _create_validated_memory(
        server,
        summary_short="Base v2",
        supersedes_memory_id=base_id,
        state_code="active",
    )
    conn = server.get_db_connection()
    try:
        conn.execute(
            "UPDATE memories SET superseded_by_memory_id = ?, state_code = 'superseded', memory_v2_status = 'superseded' WHERE id = ?",
            (new_id, base_id),
        )
        conn.execute(
            "INSERT INTO memory_links (from_memory_id, to_memory_id, relation_type, weight, origin) VALUES (?, ?, 'supersedes', 1.0, 'pytest')",
            (new_id, base_id),
        )
        conn.commit()
    finally:
        conn.close()

    result = server.get_memory_lifecycle_integrity_report(project_key="mapi", include_debug=True)

    assert result["status"] == "warning"
    assert result["schema_version"] == "memory_v3_lifecycle_integrity_report.v1"
    assert result["summary"]["memories_checked"] == 2
    assert result["summary"]["critical_issues"] == 0
    assert result["issue_counts"] == {}
    assert result["findings"] == []
    assert "legacy state_code='active' is treated as canonical validated for lifecycle projection and integrity checks" in result["unsupported_metrics"]
    assert result["safety"]["read_only"] is True
    assert result["debug"]["lineage_components"]


def test_lifecycle_integrity_detects_projection_and_activity_mismatch(server: Any) -> None:
    memory_id = _create_validated_memory(server, memory_v2_status="stale")
    conn = server.get_db_connection()
    try:
        conn.execute("UPDATE memories SET activity_state = 'archived' WHERE id = ?", (memory_id,))
        conn.commit()
    finally:
        conn.close()

    result = server.get_memory_lifecycle_integrity_report(memory_id=memory_id)
    issue_codes = {item["issue_code"] for item in result["findings"]}

    assert result["status"] == "warning"
    assert issue_codes >= {"state_projection_mismatch", "activity_state_mismatch"}


def test_lifecycle_integrity_detects_missing_target_and_reverse_pointer_mismatch(server: Any) -> None:
    child_id = _create_validated_memory(server)
    other_id = _create_validated_memory(server)
    replaced_id = _create_validated_memory(server)
    conn = server.get_db_connection()
    try:
        conn.execute("UPDATE memories SET supersedes_memory_id = 999999 WHERE id = ?", (child_id,))
        conn.execute("UPDATE memories SET superseded_by_memory_id = ? WHERE id = ?", (other_id, replaced_id))
        conn.commit()
    finally:
        conn.close()

    result = server.get_memory_lifecycle_integrity_report(project_key="mapi")
    issue_codes = {item["issue_code"] for item in result["findings"]}

    assert result["status"] == "error"
    assert "supersedes_missing_target" in issue_codes
    assert "reverse_pointer_mismatch" in issue_codes
    assert any(child_id in item["memory_ids"] for item in result["findings"])
    assert any(replaced_id in item["memory_ids"] for item in result["findings"])


def test_lifecycle_integrity_detects_cycle_branch_and_multiple_heads(server: Any) -> None:
    a_id = _create_validated_memory(server, summary_short="A", state_code="active")
    b_id = _create_validated_memory(server, summary_short="B", state_code="active")
    c_id = _create_validated_memory(server, summary_short="C", state_code="validated")
    conn = server.get_db_connection()
    try:
        conn.execute("UPDATE memories SET supersedes_memory_id = ? WHERE id = ?", (a_id, b_id))
        conn.execute("UPDATE memories SET supersedes_memory_id = ? WHERE id = ?", (a_id, c_id))
        conn.execute("UPDATE memories SET supersedes_memory_id = ? WHERE id = ?", (c_id, a_id))
        conn.execute("INSERT INTO memory_links (from_memory_id, to_memory_id, relation_type, weight, origin) VALUES (?, ?, 'supersedes', 1.0, 'pytest')", (b_id, a_id))
        conn.execute("INSERT INTO memory_links (from_memory_id, to_memory_id, relation_type, weight, origin) VALUES (?, ?, 'supersedes', 1.0, 'pytest')", (c_id, a_id))
        conn.execute("INSERT INTO memory_links (from_memory_id, to_memory_id, relation_type, weight, origin) VALUES (?, ?, 'supersedes', 1.0, 'pytest')", (a_id, c_id))
        conn.commit()
    finally:
        conn.close()

    result = server.get_memory_lifecycle_integrity_report(project_key="mapi", sample_limit=20)
    issue_codes = {item["issue_code"] for item in result["findings"]}

    assert result["status"] == "error"
    assert issue_codes >= {"supersession_cycle", "supersession_branch", "multiple_active_heads"}


def test_lifecycle_integrity_detects_cross_project_cross_scope_and_link_mismatch(server: Any) -> None:
    old_id = _create_validated_memory(server, project_key="mapi", scope_code="project")
    new_id = _create_validated_memory(
        server,
        project_key="other-project",
        scope_code="global",
    )
    conn = server.get_db_connection()
    try:
        conn.execute("UPDATE memories SET supersedes_memory_id = ? WHERE id = ?", (old_id, new_id))
        conn.commit()
    finally:
        conn.close()

    result = server.get_memory_lifecycle_integrity_report(project_key="other-project")
    issue_codes = {item["issue_code"] for item in result["findings"]}

    assert result["status"] == "error"
    assert issue_codes >= {"cross_project_supersession", "cross_scope_supersession", "supersedes_link_field_mismatch"}


def test_lifecycle_integrity_reports_superseded_without_lineage(server: Any) -> None:
    _create_validated_memory(server, state_code="superseded", memory_v2_status="superseded")

    result = server.get_memory_lifecycle_integrity_report(project_key="mapi")

    assert "superseded_without_lineage" in {item["issue_code"] for item in result["findings"]}


def test_lifecycle_integrity_filters_and_sample_limit(server: Any) -> None:
    for index in range(5):
        memory_id = _create_validated_memory(server, summary_short=f"Broken {index}", scope_code="conversation", project_key="mapi")
        conn = server.get_db_connection()
        try:
            conn.execute("UPDATE memories SET supersedes_memory_id = ? WHERE id = ?", (900000 + index, memory_id))
            conn.commit()
        finally:
            conn.close()

    other_id = _create_validated_memory(server, project_key="other-project", scope_code="project")
    conn = server.get_db_connection()
    try:
        conn.execute("UPDATE memories SET supersedes_memory_id = 123456 WHERE id = ?", (other_id,))
        conn.commit()
    finally:
        conn.close()

    result = server.get_memory_lifecycle_integrity_report(
        project_key="mapi",
        scope_code="conversation",
        include_archived=True,
        sample_limit=2,
    )

    assert result["filters"]["project_key"] == "mapi"
    assert result["filters"]["scope_code"] == "conversation"
    assert len(result["findings"]) == 2
    assert result["issue_counts"]["supersedes_missing_target"] >= 5


def test_lifecycle_integrity_is_read_only(server: Any) -> None:
    _create_validated_memory(server)
    before = _table_counts(server)

    result = server.get_memory_lifecycle_integrity_report(project_key="mapi")

    after = _table_counts(server)
    assert result["safety"]["read_only"] is True
    assert result["safety"]["mutations_performed"] == 0
    assert before == after


def test_lifecycle_integrity_workshop_action_is_exposed(server: Any) -> None:
    _create_validated_memory(server)
    payload = mcp_surface.open_workshop_payload("memory")
    action = next(item for item in payload["actions"] if item["action"] == "lifecycle_integrity")

    result = server.run_workshop_action(
        "memory",
        "lifecycle_integrity",
        payload={"project_key": "mapi", "sample_limit": 5, "include_debug": True},
    )

    assert action["tool_name"] == "get_memory_lifecycle_integrity_report"
    assert result["status"] == "ok"
    assert result["tool_name"] == "get_memory_lifecycle_integrity_report"
    assert result["result"]["schema_version"] == "memory_v3_lifecycle_integrity_report.v1"
