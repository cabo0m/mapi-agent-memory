from __future__ import annotations

import json
from typing import Any

import mcp_surface


PROTECTED_TABLES = (
    "memories",
    "memory_links",
    "memory_events",
    "memory_lifecycle_snapshots",
    "timeline_events",
    "memory_capture_review_items",
)


def create_memory(server: Any, *, project_key: str, content: str, **overrides: Any) -> int:
    payload = {
        "content": content,
        "memory_type": "project_note",
        "summary_short": "Apply contract memory",
        "project_key": project_key,
        "scope_code": "project",
        "state_code": "validated",
        "memory_v2_status": "active",
        "truth_kind": "fact",
        "entry_type": "project",
        "confidence_score": 0.9,
        "importance_score": 0.75,
    }
    payload.update(overrides)
    return int(server._base._create_memory_direct(**payload)["memory"]["id"])


def queue_item(
    server: Any,
    *,
    project_key: str,
    content: str,
    source_event_ref: str | None = None,
) -> dict[str, Any]:
    return server.save_memory_capture_proposal(
        content=content,
        project_key=project_key,
        scope_code="project",
        source_context="pytest guarded reconciliation apply",
        source_event_ref=source_event_ref,
    )["item"]


def rewrite_proposal(server: Any, *, item_id: int, patch: dict[str, Any]) -> None:
    current = server.get_memory_capture_review_item(item_id)["item"]["proposal"]
    current.update(patch)
    conn = server.get_db_connection()
    try:
        conn.execute(
            "UPDATE memory_capture_review_items SET proposal_json = ? WHERE id = ?",
            (json.dumps(current, ensure_ascii=False, sort_keys=True), int(item_id)),
        )
        conn.commit()
    finally:
        conn.close()


def enable_reconciliation(server: Any, project_key: str) -> None:
    server.upsert_feature_flag(
        flag_key="memory_v3_capture_reconciliation_enabled",
        is_enabled=True,
        rollout_mode="projects",
        allowed_project_keys=project_key,
        allowed_scope_codes="project",
        read_only_mode=False,
        notes="pytest V3-B04 fixture only",
    )


def enable_read_only_reconciliation(server: Any, project_key: str) -> None:
    server.upsert_feature_flag(
        flag_key="memory_v3_capture_reconciliation_enabled",
        is_enabled=True,
        rollout_mode="projects_and_scopes",
        allowed_project_keys=project_key,
        allowed_scope_codes="project",
        read_only_mode=True,
        notes="pytest review-only rollout fixture",
    )


def prepare_approved_item(
    server: Any,
    *,
    project_key: str,
    content: str,
    source_event_ref: str | None = None,
    proposal_patch: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    item = queue_item(
        server,
        project_key=project_key,
        content=content,
        source_event_ref=source_event_ref,
    )
    if proposal_patch:
        rewrite_proposal(server, item_id=int(item["id"]), patch=proposal_patch)
    preview = server.preview_memory_capture_reconciliation(
        item_id=int(item["id"]),
        include_semantic=False,
    )
    reviewed = server.review_memory_capture_item(
        int(item["id"]),
        "approve",
        reviewed_by="pytest-operator",
        review_note="approved for guarded fixture apply",
    )
    assert reviewed["status"] == "updated"
    return item, preview


def apply_item(
    server: Any,
    item: dict[str, Any],
    preview: dict[str, Any],
    *,
    applied_by: str = "pytest-operator",
    confirm_protected: bool = False,
) -> dict[str, Any]:
    return server.apply_memory_capture_reconciliation(
        item_id=int(item["id"]),
        expected_preview_hash=preview["reconciliation_preview_hash"],
        applied_by=applied_by,
        notes="pytest guarded apply",
        confirm_protected=confirm_protected,
    )


def table_snapshot(server: Any) -> dict[str, list[tuple[Any, ...]]]:
    conn = server.get_db_connection()
    try:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
            for table in PROTECTED_TABLES
        }
    finally:
        conn.close()


def test_capture_apply_requires_flag_approved_item_operator_and_fresh_v2_preview(server: Any) -> None:
    project_key = "apply-guards"
    item = queue_item(server, project_key=project_key, content="Guarded apply must require every prerequisite.")
    preview = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=False)
    before = table_snapshot(server)

    flag_off = apply_item(server, item, preview)
    assert flag_off["status"] == "blocked"
    assert "memory_v3_capture_reconciliation_feature_flag_off" in flag_off["blocking_reasons"]
    assert table_snapshot(server) == before

    enable_reconciliation(server, project_key)
    pending = apply_item(server, item, preview)
    assert pending["status"] == "blocked"
    assert "item_status_not_approved:pending" in pending["blocking_reasons"]

    server.review_memory_capture_item(int(item["id"]), "approve", reviewed_by="pytest-operator")
    missing_operator = apply_item(server, item, preview, applied_by=" ")
    assert missing_operator["status"] == "blocked"
    assert any("applied_by" in reason for reason in missing_operator["blocking_reasons"])

    stale = server.apply_memory_capture_reconciliation(
        item_id=int(item["id"]),
        expected_preview_hash="stale-hash",
        applied_by="pytest-operator",
    )
    assert stale["status"] == "stale_preview"
    assert server.get_memory_capture_review_item(int(item["id"]))["item"]["status"] == "approved"

    conn = server.get_db_connection()
    try:
        stored = json.loads(conn.execute(
            "SELECT reconciliation_json FROM memory_capture_review_items WHERE id = ?",
            (int(item["id"]),),
        ).fetchone()[0])
        stored["schema_version"] = "memory_v3_capture_reconciliation_preview.v1"
        conn.execute(
            "UPDATE memory_capture_review_items SET reconciliation_json = ? WHERE id = ?",
            (json.dumps(stored, ensure_ascii=False, sort_keys=True), int(item["id"])),
        )
        conn.commit()
    finally:
        conn.close()
    v1 = apply_item(server, item, preview)
    assert v1["status"] == "blocked"
    assert "preview_schema_v2_required" in v1["blocking_reasons"]


def test_capture_apply_read_only_mode_is_fail_closed_before_any_write(server: Any) -> None:
    project_key = "apply-read-only"
    item, preview = prepare_approved_item(
        server,
        project_key=project_key,
        content="Review-only reconciliation must never apply this capture.",
    )
    enable_read_only_reconciliation(server, project_key)
    before = table_snapshot(server)

    result = apply_item(server, item, preview)

    assert result["status"] == "blocked"
    assert result["blocking_reasons"] == ["reconciliation_apply_blocked_read_only_mode"]
    assert table_snapshot(server) == before
    assert server.get_memory_capture_review_item(int(item["id"]))["item"]["status"] == "approved"


def test_capture_apply_enabled_non_read_only_mode_preserves_existing_apply_path(server: Any) -> None:
    project_key = "apply-non-read-only-control"
    item, preview = prepare_approved_item(
        server,
        project_key=project_key,
        content="Enabled non-read-only reconciliation keeps the guarded apply path.",
    )
    enable_reconciliation(server, project_key)

    result = apply_item(server, item, preview)

    assert result["status"] == "applied"
    assert result["outcome"] == "create_new"
    assert server.get_memory_capture_review_item(int(item["id"]))["item"]["status"] == "applied"


def test_capture_apply_blocks_rejected_and_expired_items(server: Any) -> None:
    project_key = "apply-terminal-guards"
    enable_reconciliation(server, project_key)
    for decision in ("rejected", "expired"):
        item = queue_item(
            server,
            project_key=project_key,
            content=f"Terminal queue status {decision} must block guarded apply.",
        )
        preview = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=False)
        if decision == "rejected":
            server.review_memory_capture_item(
                int(item["id"]),
                "reject",
                reviewed_by="pytest-operator",
                review_note="fixture rejection",
            )
        else:
            server.expire_memory_capture_item(int(item["id"]), reason="fixture expiration")
        result = apply_item(server, item, preview)
        assert result["status"] == "blocked"
        assert f"item_status_not_approved:{decision}" in result["blocking_reasons"]


def test_capture_apply_create_new_creates_one_validated_memory_and_audit(server: Any) -> None:
    project_key = "apply-create-new"
    enable_reconciliation(server, project_key)
    item, preview = prepare_approved_item(
        server,
        project_key=project_key,
        content="A new validated memory is created atomically from approved capture.",
    )
    before_count = len(table_snapshot(server)["memories"])

    result = apply_item(server, item, preview)

    assert result["status"] == "applied"
    assert result["schema_version"] == "memory_v3_capture_reconciliation_apply.v1"
    assert result["outcome"] == "create_new"
    assert result["queue_status_before"] == "approved"
    assert result["queue_status_after"] == "applied"
    assert result["safety"] == {"atomic": True, "model_auto_apply": False, "operator_required": True}
    assert len(table_snapshot(server)["memories"]) == before_count + 1
    created = server.get_memory(int(result["created_memory_id"]))["memory"]
    assert created["state_code"] == "validated"
    assert created["memory_v2_status"] == "active"
    assert created["requires_user_confirmation"] is False
    assert created["validation_source"] == "memory_v3_capture_apply"
    assert item["proposal_key"] in created["source"]
    events = server.query_sql(
        query="SELECT event_type FROM memory_events WHERE memory_id = ? ORDER BY id",
        params_json=f'[{int(result["created_memory_id"])}]',
    )["rows"]
    assert [row["event_type"] for row in events] == ["memory_v2.created", "memory_v3.capture_applied"]
    queue = server.get_memory_capture_review_item(int(item["id"]))["item"]
    assert queue["status"] == "applied"
    assert queue["created_memory_id"] == result["created_memory_id"]
    assert queue["reconciliation"]["apply_audit"]["result_fingerprint"]


def test_capture_apply_duplicate_and_skip_are_queue_only(server: Any) -> None:
    project_key = "apply-queue-only"
    enable_reconciliation(server, project_key)
    duplicate_id = create_memory(server, project_key=project_key, content="Exact duplicate queue-only content.")
    duplicate_item, duplicate_preview = prepare_approved_item(
        server,
        project_key=project_key,
        content="Exact duplicate queue-only content.",
    )
    skip_item, skip_preview = prepare_approved_item(
        server,
        project_key=project_key,
        content="Explicit transient capture closes queue without protected writes.",
        proposal_patch={"skip_transient": True},
    )
    before = table_snapshot(server)

    duplicate = apply_item(server, duplicate_item, duplicate_preview)
    after_duplicate = table_snapshot(server)
    skip = apply_item(server, skip_item, skip_preview)
    after_skip = table_snapshot(server)

    assert duplicate["status"] == "applied"
    assert duplicate["outcome"] == "duplicate_existing"
    assert duplicate["primary_memory_id"] == duplicate_id
    assert duplicate["created_memory_id"] is None
    assert skip["status"] == "applied"
    assert skip["outcome"] == "skip_transient"
    assert skip["created_memory_id"] is None
    for table in PROTECTED_TABLES[:-1]:
        assert after_duplicate[table] == before[table]
        assert after_skip[table] == before[table]


def test_capture_apply_reinforce_adds_one_event_without_changing_target(server: Any) -> None:
    project_key = "apply-reinforce"
    enable_reconciliation(server, project_key)
    target_id = create_memory(
        server,
        project_key=project_key,
        content="Original source event memory content remains unchanged.",
        source_event_ref="apply-reinforce-event",
    )
    item, preview = prepare_approved_item(
        server,
        project_key=project_key,
        content="New evidence from the same source event reinforces the memory.",
        source_event_ref="apply-reinforce-event",
    )
    before = server.get_memory(target_id)["memory"]

    result = apply_item(server, item, preview)

    after = server.get_memory(target_id)["memory"]
    assert result["status"] == "applied"
    assert result["outcome"] == "reinforce_existing"
    assert result["created_memory_id"] is None
    assert after["content"] == before["content"]
    assert after["evidence_count"] == before["evidence_count"]
    events = server.query_sql(
        query="SELECT event_type FROM memory_events WHERE memory_id = ? AND event_type = 'memory_v3.capture_reinforced'",
        params_json=f"[{target_id}]",
    )["rows"]
    assert len(events) == 1


def test_capture_apply_metadata_and_abstain_never_execute(server: Any) -> None:
    project_key = "apply-unsupported"
    enable_reconciliation(server, project_key)
    create_memory(
        server,
        project_key=project_key,
        content="Metadata remains proposal-only in B04.",
        tags="old,metadata",
    )
    metadata_item, metadata_preview = prepare_approved_item(
        server,
        project_key=project_key,
        content="Metadata remains proposal-only in B04.",
        proposal_patch={"tags": "new,metadata"},
    )
    abstain_item, abstain_preview = prepare_approved_item(
        server,
        project_key=project_key,
        content="Conflict signal without target must abstain from apply.",
        proposal_patch={"is_contradiction": True},
    )
    before = table_snapshot(server)

    metadata = apply_item(server, metadata_item, metadata_preview)
    abstain = apply_item(server, abstain_item, abstain_preview)

    assert metadata["status"] == "outcome_not_supported"
    assert metadata["outcome"] == "update_metadata_proposal"
    assert abstain["status"] == "blocked"
    assert abstain["outcome"] == "abstain"
    assert "outcome_not_supported:abstain" in abstain["blocking_reasons"]
    assert table_snapshot(server) == before


def test_capture_apply_workshop_action_is_medium_risk_and_not_public() -> None:
    agent_workshop = mcp_surface.open_workshop_payload("memory", profile="agent")
    assert "capture_apply" not in {item["action"] for item in agent_workshop["actions"]}
    workshop = mcp_surface.open_workshop_payload("memory", profile="maintainer")
    action = next(item for item in workshop["actions"] if item["action"] == "capture_apply")
    assert action["tool_name"] == "apply_memory_capture_reconciliation"
    assert action["risk"] == "medium"
    public_tools = mcp_surface.visible_tool_names("public") or set()
    assert "apply_memory_capture_reconciliation" not in public_tools
