from __future__ import annotations

import json
from typing import Any


HASH_ALGORITHM = "sha256:canonical-json:v1"


def _create_memory(server: Any, *, project_key: str, content: str, **overrides: Any) -> int:
    payload = {
        "content": content,
        "memory_type": "project_note",
        "summary_short": "Contract memory",
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
    return int(server.create_memory(**payload)["memory"]["id"])


def _queue_item(
    server: Any,
    *,
    project_key: str | None,
    content: str,
    scope_code: str | None = "project",
    **kwargs: Any,
) -> dict[str, Any]:
    return server.save_memory_capture_proposal(
        content=content,
        project_key=project_key,
        scope_code=scope_code,
        source_context="pytest reconciliation contract",
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


def _preview(server: Any, item: dict[str, Any], *, include_semantic: bool = False) -> dict[str, Any]:
    return server.preview_memory_capture_reconciliation(
        item_id=int(item["id"]),
        include_semantic=include_semantic,
    )


def _assert_contract(
    result: dict[str, Any],
    *,
    outcome: str,
    confidence_band: str,
    reason_code: str,
    future_action: str,
    apply_eligible: bool,
) -> None:
    assert result["status"] == "preview_ready"
    assert result["schema_version"] == "memory_v3_capture_reconciliation_preview.v2"
    assert result["outcome"] == outcome
    assert result["confidence_band"] == confidence_band
    assert reason_code in result["reason_codes"]
    assert result["reason_codes"] == sorted(set(result["reason_codes"]))
    assert result["planned_future_action"]["action"] == future_action
    assert result["planned_future_action"]["apply_supported"] is apply_eligible
    assert result["planned_future_action"]["preview_memory_mutations_performed"] == 0
    assert result["hash_algorithm"] == HASH_ALGORITHM
    assert result["guard"]["allowed"] is True
    assert result["guard"]["apply_eligible"] is apply_eligible
    assert result["guard"]["blockers"] == []
    assert result["safety"]["apply_supported"] is False


def test_reconciliation_preview_contract_covers_deterministic_outcome_matrix(server: Any) -> None:
    duplicate_project = "contract-duplicate"
    duplicate_id = _create_memory(server, project_key=duplicate_project, content="Exact contract content.")
    duplicate = _preview(
        server,
        _queue_item(server, project_key=duplicate_project, content="Exact contract content."),
    )

    reinforce_project = "contract-reinforce"
    reinforce_id = _create_memory(
        server,
        project_key=reinforce_project,
        content="Existing source event content.",
        source_event_ref="contract-event",
    )
    reinforce = _preview(
        server,
        _queue_item(
            server,
            project_key=reinforce_project,
            content="Changed source event content.",
            source_event_ref="contract-event",
        ),
    )

    version_project = "contract-version"
    version_id = _create_memory(server, project_key=version_project, content="Old version contract.")
    version_item = _queue_item(server, project_key=version_project, content="New version contract.")
    _rewrite_item_proposal(
        server,
        item_id=int(version_item["id"]),
        patch={
            "supersedes_memory_id": version_id,
            "relation_kind": "replacement",
            "supersession_reason": "The new contract replaces the old contract.",
        },
    )
    version = _preview(server, version_item)

    conflict_project = "contract-conflict"
    conflict_id = _create_memory(server, project_key=conflict_project, content="Current contract is true.")
    conflict_item = _queue_item(server, project_key=conflict_project, content="Evidence contradicts current contract.")
    _rewrite_item_proposal(
        server,
        item_id=int(conflict_item["id"]),
        patch={"is_contradiction": True, "contradiction_target_memory_id": conflict_id},
    )
    conflict = _preview(server, conflict_item)

    metadata_project = "contract-metadata"
    metadata_id = _create_memory(
        server,
        project_key=metadata_project,
        content="Metadata-only contract.",
        tags="old,contract",
    )
    metadata_item = _queue_item(server, project_key=metadata_project, content="Metadata-only contract.")
    _rewrite_item_proposal(server, item_id=int(metadata_item["id"]), patch={"tags": "new,contract"})
    metadata = _preview(server, metadata_item)

    create_new = _preview(
        server,
        _queue_item(server, project_key="contract-create-new", content="Completely isolated new contract."),
    )

    cases = [
        (duplicate, "duplicate_existing", "deterministic_high", "exact_content_match", "mark_duplicate", duplicate_id, True),
        (reinforce, "reinforce_existing", "deterministic_high", "same_source_event_ref", "reinforce_existing", reinforce_id, True),
        (version, "create_version", "deterministic_high", "explicit_valid_target", "create_version", version_id, True),
        (conflict, "conflict_review", "deterministic_high", "explicit_contradiction", "conflict_review", conflict_id, True),
        (metadata, "update_metadata_proposal", "deterministic_high", "metadata_only_difference", "metadata_review", metadata_id, False),
        (create_new, "create_new", "deterministic_medium", "no_hard_match", "create_new", None, True),
    ]
    for result, outcome, band, reason, action, primary_memory_id, apply_eligible in cases:
        _assert_contract(
            result,
            outcome=outcome,
            confidence_band=band,
            reason_code=reason,
            future_action=action,
            apply_eligible=apply_eligible,
        )
        assert result["planned_future_action"]["primary_memory_id"] == primary_memory_id
        assert set(result["planned_future_action"]) >= {
            "action",
            "apply_supported",
            "requires_approved_item",
            "requires_expected_preview_hash",
            "memory_mutation_planned",
            "primary_memory_id",
            "target_memory_id",
            "relation_kind",
            "reason",
            "auto_resolve",
        }

    assert version["planned_future_action"]["target_memory_id"] == version_id
    assert version["planned_future_action"]["relation_kind"] == "replacement"
    assert version["planned_future_action"]["reason"] == "The new contract replaces the old contract."
    assert conflict["planned_future_action"]["target_memory_id"] == conflict_id
    assert conflict["planned_future_action"]["create_candidate_memory"] is True
    assert conflict["planned_future_action"]["auto_resolve"] is False


def test_reconciliation_preview_contract_covers_abstain_reasons(server: Any) -> None:
    semantic_project = "contract-semantic-abstain"
    candidate_id = _create_memory(server, project_key=semantic_project, content="Unrelated semantic candidate.")
    semantic_item = _queue_item(server, project_key=semantic_project, content="Fresh words without lexical evidence.")
    original = server._base.search_semantic

    def _fake_search_semantic(*, query: str, top_k: int = 10, project_key: str | None = None) -> dict[str, Any]:
        return {"status": "ok", "results": [{"memory_id": candidate_id, "similarity": 0.99}]}

    server._base.search_semantic = _fake_search_semantic
    try:
        semantic = _preview(server, semantic_item, include_semantic=True)
    finally:
        server._base.search_semantic = original

    missing_scope = _preview(
        server,
        _queue_item(
            server,
            project_key=None,
            scope_code=None,
            content="Missing project and scope must produce a conservative reconciliation preview.",
        ),
    )

    _assert_contract(
        semantic,
        outcome="abstain",
        confidence_band="insufficient",
        reason_code="strong_semantic_ambiguity",
        future_action="none",
        apply_eligible=False,
    )
    _assert_contract(
        missing_scope,
        outcome="abstain",
        confidence_band="insufficient",
        reason_code="missing_project_or_scope",
        future_action="none",
        apply_eligible=False,
    )
    assert semantic["matched_memory_ids"] == [candidate_id]
    assert "semantic_shortlist_only" in semantic["unsupported_metrics"]


def test_reconciliation_preview_only_updates_queue_metadata(server: Any) -> None:
    project_key = "contract-read-only"
    _create_memory(server, project_key=project_key, content="Protected table baseline.")
    item = _queue_item(server, project_key=project_key, content="Protected table baseline.")
    protected_tables = ("memories", "memory_links", "memory_events", "memory_lifecycle_snapshots")

    def _counts() -> dict[str, int]:
        conn = server.get_db_connection()
        try:
            return {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in protected_tables
            }
        finally:
            conn.close()

    before = _counts()
    result = _preview(server, item)
    after = _counts()

    assert result["status"] == "preview_ready"
    assert before == after
    assert result["safety"]["memory_mutations_performed"] == 0
    assert result["safety"]["protected_table_mutations_performed"] == 0
    assert result["safety"]["queue_row_updates_performed"] == 1


def test_preview_v2_requires_complete_create_version_contract_and_hashes_it(server: Any) -> None:
    project_key = "contract-version-prerequisites"
    target_id = _create_memory(server, project_key=project_key, content="Old executable version contract.")
    item = _queue_item(server, project_key=project_key, content="New executable version contract.")
    _rewrite_item_proposal(server, item_id=int(item["id"]), patch={"supersedes_memory_id": target_id})

    missing_relation = _preview(server, item)
    assert missing_relation["outcome"] == "abstain"
    assert "missing_supersession_relation_kind" in missing_relation["reason_codes"]
    assert "missing_supersession_reason" in missing_relation["reason_codes"]

    _rewrite_item_proposal(
        server,
        item_id=int(item["id"]),
        patch={"relation_kind": "association", "supersession_reason": "Unsupported relation test."},
    )
    unsupported_relation = _preview(server, item)
    assert unsupported_relation["outcome"] == "abstain"
    assert "unsupported_supersession_relation_kind" in unsupported_relation["reason_codes"]
    assert unsupported_relation["reconciliation_preview_hash"] != missing_relation["reconciliation_preview_hash"]

    _rewrite_item_proposal(server, item_id=int(item["id"]), patch={"relation_kind": "correction"})
    correction = _preview(server, item)
    assert correction["outcome"] == "create_version"
    assert correction["guard"]["apply_eligible"] is True
    assert correction["planned_future_action"]["relation_kind"] == "correction"
    assert correction["planned_future_action"]["reason"] == "Unsupported relation test."
    assert correction["reconciliation_preview_hash"] != unsupported_relation["reconciliation_preview_hash"]

    _rewrite_item_proposal(
        server,
        item_id=int(item["id"]),
        patch={"supersession_reason": "A different correction reason."},
    )
    changed_reason = _preview(server, item)
    assert changed_reason["outcome"] == "create_version"
    assert changed_reason["reconciliation_preview_hash"] != correction["reconciliation_preview_hash"]


def test_preview_v2_skip_is_apply_eligible_but_metadata_is_proposal_only(server: Any) -> None:
    skip_item = _queue_item(
        server,
        project_key="contract-skip",
        content="Transient capture that should be closed without a memory write.",
    )
    _rewrite_item_proposal(server, item_id=int(skip_item["id"]), patch={"skip_transient": True})
    skip = _preview(server, skip_item)

    assert skip["outcome"] == "skip_transient"
    assert skip["guard"]["apply_eligible"] is True
    assert skip["planned_future_action"]["apply_supported"] is True
    assert skip["planned_future_action"]["memory_mutation_planned"] is False

    metadata_project = "contract-metadata-proposal-only"
    _create_memory(
        server,
        project_key=metadata_project,
        content="Metadata proposal-only content.",
        tags="old,metadata",
    )
    metadata_item = _queue_item(server, project_key=metadata_project, content="Metadata proposal-only content.")
    _rewrite_item_proposal(server, item_id=int(metadata_item["id"]), patch={"tags": "new,metadata"})
    metadata = _preview(server, metadata_item)

    assert metadata["outcome"] == "update_metadata_proposal"
    assert metadata["guard"]["apply_eligible"] is False
    assert metadata["planned_future_action"]["apply_supported"] is False


def test_preview_v2_conflict_requires_explicit_target(server: Any) -> None:
    item = _queue_item(
        server,
        project_key="contract-conflict-target",
        content="Contradiction signal without an explicit target must abstain.",
    )
    _rewrite_item_proposal(server, item_id=int(item["id"]), patch={"is_contradiction": True})

    result = _preview(server, item)

    assert result["outcome"] == "abstain"
    assert result["guard"]["apply_eligible"] is False
    assert "missing_conflict_target" in result["reason_codes"]
