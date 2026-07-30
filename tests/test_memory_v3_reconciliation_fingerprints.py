from __future__ import annotations

from typing import Any


def _create_memory(server: Any, **overrides: Any) -> int:
    payload = {
        "content": "Fingerprint baseline.",
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


def _queue_item(server: Any, *, content: str) -> dict[str, Any]:
    return server.save_memory_capture_proposal(
        content=content,
        project_key="mapi",
        scope_code="project",
        source_context="pytest reconciliation fingerprints",
    )["item"]


def test_reconciliation_preview_is_idempotent_for_same_inputs(server: Any) -> None:
    _create_memory(server, content="Idempotent preview content.")
    item = _queue_item(server, content="Idempotent preview content.")

    first = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=False)
    second = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=False)

    assert first["status"] == "preview_ready"
    assert second["status"] == "preview_ready"
    assert first["input_fingerprint"] == second["input_fingerprint"]
    assert first["candidate_set_fingerprint"] == second["candidate_set_fingerprint"]
    assert first["reconciliation_preview_hash"] == second["reconciliation_preview_hash"]


def test_reconciliation_candidate_set_fingerprint_ignores_last_accessed_at(server: Any) -> None:
    memory_id = _create_memory(server, content="Stable candidate snapshot content.")
    item = _queue_item(server, content="Stable candidate snapshot content.")

    first = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=False)
    conn = server.get_db_connection()
    try:
        conn.execute("UPDATE memories SET last_accessed_at = '2026-07-13T10:00:00Z' WHERE id = ?", (memory_id,))
        conn.commit()
    finally:
        conn.close()
    second = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=False)

    assert first["candidate_set_fingerprint"] == second["candidate_set_fingerprint"]
    assert first["reconciliation_preview_hash"] == second["reconciliation_preview_hash"]


def test_reconciliation_candidate_set_fingerprint_changes_when_candidate_changes(server: Any) -> None:
    memory_id = _create_memory(server, content="Original candidate content.")
    item = _queue_item(server, content="Original candidate content.")

    first = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=False)
    conn = server.get_db_connection()
    try:
        conn.execute(
            "UPDATE memories SET content = 'Changed candidate content.' WHERE id = ?",
            (memory_id,),
        )
        conn.commit()
    finally:
        conn.close()
    second = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=False)

    assert first["candidate_set_fingerprint"] != second["candidate_set_fingerprint"]
    assert first["reconciliation_preview_hash"] != second["reconciliation_preview_hash"]


def test_reconciliation_preview_reports_semantic_unavailable_without_failing(server: Any) -> None:
    _create_memory(server, content="Semantic unavailable fallback content.")
    item = _queue_item(server, content="Semantic unavailable fallback content.")
    original = server._base.search_semantic

    def _semantic_error(*, query: str, top_k: int = 10, project_key: str | None = None) -> dict[str, Any]:
        return {"status": "error", "error": "semantic offline"}

    server._base.search_semantic = _semantic_error
    try:
        result = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=True)
    finally:
        server._base.search_semantic = original

    assert result["status"] == "preview_ready"
    assert "semantic_search_unavailable" in result["unsupported_metrics"]


def test_strong_semantic_shortlist_only_abstains_without_driving_duplicate_outcome(server: Any) -> None:
    candidate_id = _create_memory(server, content="Completely different stored content.")
    item = _queue_item(server, content="Fresh content with no exact or lexical overlap.")
    original = server._base.search_semantic

    def _fake_search_semantic(*, query: str, top_k: int = 10, project_key: str | None = None) -> dict[str, Any]:
        return {
            "status": "ok",
            "results": [{"memory_id": candidate_id, "similarity": 0.99}],
        }

    server._base.search_semantic = _fake_search_semantic
    try:
        result = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=True)
    finally:
        server._base.search_semantic = original

    assert result["status"] == "preview_ready"
    assert result["matched_memory_ids"] == [candidate_id]
    assert result["outcome"] == "abstain"
    assert result["confidence_band"] == "insufficient"
    assert "strong_semantic_ambiguity" in result["reason_codes"]
    assert result["planned_future_action"]["action"] == "none"
    assert result["guard"]["apply_eligible"] is False
    assert "semantic_shortlist_only" in result["unsupported_metrics"]


def test_weak_semantic_shortlist_only_allows_create_new(server: Any) -> None:
    candidate_id = _create_memory(server, content="Unrelated stored baseline.")
    item = _queue_item(server, content="Brand new capture with separate vocabulary.")
    original = server._base.search_semantic

    def _fake_search_semantic(*, query: str, top_k: int = 10, project_key: str | None = None) -> dict[str, Any]:
        return {
            "status": "ok",
            "results": [{"memory_id": candidate_id, "similarity": 0.40}],
        }

    server._base.search_semantic = _fake_search_semantic
    try:
        result = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=True)
    finally:
        server._base.search_semantic = original

    assert result["status"] == "preview_ready"
    assert result["outcome"] == "create_new"
    assert result["confidence_band"] == "deterministic_medium"
    assert "no_hard_match" in result["reason_codes"]
    assert result["guard"]["apply_eligible"] is True


def test_semantic_similarity_crossing_ambiguity_threshold_changes_hash_and_outcome(server: Any) -> None:
    candidate_id = _create_memory(server, content="Threshold candidate without lexical overlap.")
    item = _queue_item(server, content="Independent capture vocabulary for fingerprinting.")
    original = server._base.search_semantic
    similarity = 0.80

    def _fake_search_semantic(*, query: str, top_k: int = 10, project_key: str | None = None) -> dict[str, Any]:
        return {
            "status": "ok",
            "results": [{"memory_id": candidate_id, "similarity": similarity}],
        }

    server._base.search_semantic = _fake_search_semantic
    try:
        below = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=True)
        similarity = 0.90
        above = server.preview_memory_capture_reconciliation(item_id=int(item["id"]), include_semantic=True)
    finally:
        server._base.search_semantic = original

    assert below["outcome"] == "create_new"
    assert above["outcome"] == "abstain"
    assert below["candidate_set_fingerprint"] != above["candidate_set_fingerprint"]
    assert below["reconciliation_preview_hash"] != above["reconciliation_preview_hash"]
