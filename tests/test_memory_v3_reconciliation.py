from __future__ import annotations

import json
from typing import Any

import mcp_surface


def _create_memory(server: Any, **overrides: Any) -> int:
    payload = {
        "content": "Domyślna treść memory.",
        "memory_type": "project_note",
        "summary_short": "Memory summary",
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


def _queue_item(server: Any, *, content: str, **kwargs: Any) -> dict[str, Any]:
    return server.save_memory_capture_proposal(
        content=content,
        project_key="mapi",
        scope_code="project",
        source_context="pytest reconciliation",
        **kwargs,
    )["item"]


def _rewrite_item_proposal(server: Any, *, item_id: int, patch: dict[str, Any]) -> None:
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


def test_reconciliation_preview_returns_duplicate_existing_for_exact_match(server: Any) -> None:
    memory_id = _create_memory(
        server,
        content="Wdrożenie ma pozostać przy deterministic preview bez apply.",
        summary_short="Deterministic preview",
    )
    item = _queue_item(
        server,
        content="Wdrożenie ma pozostać przy deterministic preview bez apply.",
    )

    result = server.preview_memory_capture_reconciliation(
        item_id=int(item["id"]),
        include_semantic=False,
    )

    assert result["status"] == "preview_ready"
    assert result["schema_version"] == "memory_v3_capture_reconciliation_preview.v2"
    assert result["outcome"] == "duplicate_existing"
    assert result["recommended_action"] == "mark_duplicate_existing"
    assert result["matched_memory_ids"] == [memory_id]
    assert result["item"]["status"] == "pending"
    assert result["item"]["reconciliation_preview_hash"] == result["reconciliation_preview_hash"]


def test_reconciliation_preview_returns_reinforce_existing_for_same_source_event(server: Any) -> None:
    memory_id = _create_memory(
        server,
        content="Capture review queue ma zostać utrwalona na SQLite.",
        source_event_ref="evt-1",
    )
    item = _queue_item(
        server,
        content="Capture review queue wymaga jeszcze read-only preview i rollback contract.",
        source_event_ref="evt-1",
    )

    result = server.preview_memory_capture_reconciliation(
        item_id=int(item["id"]),
        include_semantic=False,
    )

    assert result["status"] == "preview_ready"
    assert result["outcome"] == "reinforce_existing"
    assert result["recommended_action"] == "reinforce_existing_memory"
    assert result["matched_memory_ids"] == [memory_id]


def test_reconciliation_preview_returns_create_version_for_explicit_target(server: Any) -> None:
    target_id = _create_memory(
        server,
        content="Stary kontrakt capture review queue.",
        summary_short="Old queue contract",
    )
    item = _queue_item(
        server,
        content="Nowy kontrakt capture review queue z deterministic reconciliation preview.",
    )
    _rewrite_item_proposal(
        server,
        item_id=int(item["id"]),
        patch={
            "supersedes_memory_id": target_id,
            "relation_kind": "refinement",
            "supersession_reason": "Nowa wersja doprecyzowuje kontrakt kolejki.",
        },
    )

    result = server.preview_memory_capture_reconciliation(
        item_id=int(item["id"]),
        include_semantic=False,
    )

    assert result["status"] == "preview_ready"
    assert result["outcome"] == "create_version"
    assert result["recommended_action"] == "create_version_candidate"
    assert result["evidence"]["explicit_target"]["memory_id"] == target_id
    assert result["planned_future_action"]["relation_kind"] == "refinement"
    assert result["planned_future_action"]["reason"] == "Nowa wersja doprecyzowuje kontrakt kolejki."


def test_reconciliation_preview_returns_conflict_review_for_explicit_contradiction(server: Any) -> None:
    target_id = _create_memory(server, content="Aktualny kontrakt mówi, że apply nie istnieje.")
    item = _queue_item(server, content="Nowa obserwacja przeczy temu kontraktowi.")
    _rewrite_item_proposal(
        server,
        item_id=int(item["id"]),
        patch={"is_contradiction": True, "contradiction_target_memory_id": target_id},
    )

    result = server.preview_memory_capture_reconciliation(
        item_id=int(item["id"]),
        include_semantic=False,
    )

    assert result["status"] == "preview_ready"
    assert result["outcome"] == "conflict_review"
    assert result["recommended_action"] == "manual_conflict_review"


def test_reconciliation_preview_returns_create_new_when_no_candidate_matches(server: Any) -> None:
    _create_memory(server, content="Supersession rollback snapshot contract.")
    item = _queue_item(server, content="Nowa decyzja dotyczy tylko capture reconciliation fingerprint matrix.")

    result = server.preview_memory_capture_reconciliation(
        item_id=int(item["id"]),
        include_semantic=False,
    )

    assert result["status"] == "preview_ready"
    assert result["outcome"] == "create_new"
    assert result["recommended_action"] == "create_new_memory"
    assert result["matched_memory_ids"] == []


def test_reconciliation_preview_returns_update_metadata_for_exact_content_with_metadata_diff(server: Any) -> None:
    memory_id = _create_memory(
        server,
        content="Deterministic reconciliation preview ma zostać bez apply.",
        summary_short="Old summary",
        title="Old title",
        tags="memory-v3,old",
    )
    item = _queue_item(server, content="Deterministic reconciliation preview ma zostać bez apply.")
    _rewrite_item_proposal(
        server,
        item_id=int(item["id"]),
        patch={
            "tags": "memory-v3,new",
        },
    )

    result = server.preview_memory_capture_reconciliation(
        item_id=int(item["id"]),
        include_semantic=False,
    )

    assert result["status"] == "preview_ready"
    assert result["outcome"] == "update_metadata_proposal"
    assert result["recommended_action"] == "review_metadata_only"
    assert result["matched_memory_ids"] == [memory_id]


def test_memory_workshop_exposes_capture_reconciliation_preview_action() -> None:
    payload = mcp_surface.open_workshop_payload("memory")

    action_names = {item["action"] for item in payload["actions"]}
    assert "capture_reconciliation_preview" in action_names


def test_run_workshop_action_supports_capture_reconciliation_preview(server: Any) -> None:
    _create_memory(
        server,
        content="Warsztat ma działać przez capture_reconciliation_preview.",
    )
    item = _queue_item(
        server,
        content="Warsztat ma działać przez capture_reconciliation_preview.",
    )

    result = server.run_workshop_action(
        "memory",
        "capture_reconciliation_preview",
        payload={"item_id": int(item["id"]), "include_semantic": False},
    )

    assert result["status"] == "ok"
    assert result["tool_name"] == "preview_memory_capture_reconciliation"
    assert result["result"]["outcome"] == "duplicate_existing"
