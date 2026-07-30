from __future__ import annotations

import json
from typing import Any

import pytest

from tests.test_memory_v3_reconciliation_apply import (
    apply_item,
    create_memory,
    enable_reconciliation,
    prepare_approved_item,
    table_snapshot,
)


IMMUTABLE_TARGET_FIELDS = (
    "content",
    "summary_short",
    "title",
    "tags",
    "importance_score",
    "confidence_score",
    "evidence_count",
    "valid_from",
    "valid_to",
    "supersedes_memory_id",
    "superseded_by_memory_id",
    "activity_state",
    "project_key",
    "scope_code",
    "workspace_id",
)


def _set_lifecycle(
    server: Any,
    *,
    memory_id: int,
    state_code: str,
    memory_v2_status: str,
    activity_state: str = "active",
    contradiction_flag: int = 0,
) -> None:
    conn = server.get_db_connection()
    try:
        conn.execute(
            """
            UPDATE memories
            SET state_code = ?, memory_v2_status = ?, activity_state = ?, contradiction_flag = ?
            WHERE id = ?
            """,
            (state_code, memory_v2_status, activity_state, int(contradiction_flag), int(memory_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _prepare_conflict(server: Any, *, project_key: str, target_id: int):
    return prepare_approved_item(
        server,
        project_key=project_key,
        content="The lifecycle-controlled observation contradicts the target.",
        proposal_patch={
            "is_contradiction": True,
            "contradiction_target_memory_id": int(target_id),
            "conflict_reason": "Conflicting evidence requires unresolved review.",
        },
    )


def _transition_events(server: Any, memory_ids: list[int]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in memory_ids)
    conn = server.get_db_connection()
    try:
        rows = conn.execute(
            f"SELECT id, memory_id, payload_json FROM memory_events WHERE event_type = 'memory_v3.conflict_state_entered' AND memory_id IN ({placeholders}) ORDER BY id",
            tuple(memory_ids),
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "memory_id": int(row["memory_id"]),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


@pytest.mark.parametrize("target_state", ["validated", "active"])
def test_conflict_review_transitions_validated_and_legacy_active_targets_to_conflicted(
    server: Any,
    target_state: str,
) -> None:
    project_key = f"conflict-lifecycle-{target_state}"
    enable_reconciliation(server, project_key)
    target_id = create_memory(
        server,
        project_key=project_key,
        content="Original target content and metadata must remain unchanged.",
        title="Stable target title",
        tags="stable,target",
        summary_short="Stable target summary",
    )
    _set_lifecycle(
        server,
        memory_id=target_id,
        state_code=target_state,
        memory_v2_status="active",
    )
    target_before = server.get_memory(target_id)["memory"]
    item, preview = _prepare_conflict(server, project_key=project_key, target_id=target_id)

    assert preview["outcome"] == "conflict_review"
    assert preview["guard"]["apply_eligible"] is True
    lifecycle_evidence = preview["evidence"]["explicit_target"]["lifecycle"]
    assert lifecycle_evidence["canonical_state"] == "validated"
    assert lifecycle_evidence["transition_to_conflicted_allowed"] is True

    result = apply_item(server, item, preview)

    assert result["status"] == "applied"
    created_id = int(result["created_memory_id"])
    for memory_id in (target_id, created_id):
        memory = server.get_memory(memory_id)["memory"]
        assert memory["state_code"] == "conflicted"
        assert memory["memory_v2_status"] == "contradicted"
        assert int(memory["contradiction_flag"]) == 1
        assert memory["activity_state"] == "active"
        report = server.get_memory_lifecycle_integrity_report(memory_id=memory_id)
        pair_findings = [
            finding["issue_code"]
            for finding in report["findings"]
            if memory_id in finding["memory_ids"]
        ]
        assert not {"state_projection_mismatch", "activity_state_mismatch", "unknown_state_code"} & set(pair_findings)

    target_after = server.get_memory(target_id)["memory"]
    for field in IMMUTABLE_TARGET_FIELDS:
        assert target_after[field] == target_before[field]

    events = _transition_events(server, [target_id, created_id])
    assert {event["memory_id"] for event in events} == {target_id, created_id}
    assert len(events) == 2
    for event in events:
        payload = event["payload"]
        assert payload["item_id"] == int(item["id"])
        assert payload["proposal_key"] == item["proposal_key"]
        assert payload["applied_by"] == "pytest-operator"
        assert payload["preview_hash"] == preview["reconciliation_preview_hash"]
        assert payload["new_state_code"] == "conflicted"
        assert payload["new_memory_v2_status"] == "contradicted"
        assert payload["source"] == "memory_v3_capture_conflict_review"

    audit = result["apply_audit"]
    assert audit["lifecycle_transitions"] == result["lifecycle_transitions"]
    assert len(audit["lifecycle_transitions"]) == 2
    assert audit["result_fingerprint"]

    event_count = len(_transition_events(server, [target_id, created_id]))
    repeated = apply_item(server, item, preview)
    assert repeated["status"] == "already_applied"
    assert repeated["apply_audit"] == audit
    assert repeated["lifecycle_transitions"] == audit["lifecycle_transitions"]
    assert len(_transition_events(server, [target_id, created_id])) == event_count


def test_conflict_review_preserves_already_conflicted_target_without_false_transition_event(server: Any) -> None:
    project_key = "conflict-lifecycle-already-conflicted"
    enable_reconciliation(server, project_key)
    target_id = create_memory(server, project_key=project_key, content="Already conflicted target remains conflicted.")
    _set_lifecycle(
        server,
        memory_id=target_id,
        state_code="conflicted",
        memory_v2_status="contradicted",
        contradiction_flag=1,
    )
    item, preview = _prepare_conflict(server, project_key=project_key, target_id=target_id)

    assert preview["outcome"] == "conflict_review"
    assert preview["evidence"]["explicit_target"]["lifecycle"]["canonical_state"] == "conflicted"
    result = apply_item(server, item, preview)

    assert result["status"] == "applied"
    created_id = int(result["created_memory_id"])
    events = _transition_events(server, [target_id, created_id])
    assert [event["memory_id"] for event in events] == [created_id]
    target_transition = next(
        transition
        for transition in result["lifecycle_transitions"]
        if int(transition["memory_id"]) == target_id
    )
    assert target_transition["old_state_code"] == "conflicted"
    assert target_transition["new_state_code"] == "conflicted"
    assert target_transition["transition_event_id"] is None


@pytest.mark.parametrize(
    ("state_code", "memory_v2_status", "activity_state", "expected_reason"),
    [
        ("candidate", "proposed", "active", "conflict_target_state_not_eligible"),
        ("stale", "stale", "active", "conflict_target_state_not_eligible"),
        ("archived", "archived", "archived", "conflict_target_state_not_eligible"),
        ("superseded", "superseded", "active", "conflict_target_state_not_eligible"),
        ("unknown_fixture_state", "active", "active", "conflict_target_state_unknown"),
    ],
)
def test_conflict_review_abstains_for_ineligible_or_unknown_target_state(
    server: Any,
    state_code: str,
    memory_v2_status: str,
    activity_state: str,
    expected_reason: str,
) -> None:
    project_key = f"conflict-lifecycle-blocked-{state_code}"
    enable_reconciliation(server, project_key)
    target_id = create_memory(server, project_key=project_key, content=f"Blocked target state {state_code}.")
    _set_lifecycle(
        server,
        memory_id=target_id,
        state_code=state_code,
        memory_v2_status=memory_v2_status,
        activity_state=activity_state,
    )
    item, preview = _prepare_conflict(server, project_key=project_key, target_id=target_id)

    assert preview["status"] == "preview_ready"
    assert preview["outcome"] == "abstain"
    assert preview["guard"]["allowed"] is True
    assert preview["guard"]["apply_eligible"] is False
    assert preview["planned_future_action"]["apply_supported"] is False
    assert preview["operator_next_action"] == "manual_review"
    assert expected_reason in preview["reason_codes"]
    before = table_snapshot(server)

    result = apply_item(server, item, preview)

    assert result["status"] == "blocked"
    assert table_snapshot(server) == before
