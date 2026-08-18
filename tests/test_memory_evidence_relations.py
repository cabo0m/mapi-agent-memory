from __future__ import annotations

from typing import Any

from app import sandman_agent


def _memory(
    memory_factory,
    *,
    project_key: str = "demo-project",
    summary: str,
    source_event_ref: str | None = None,
) -> int:
    return int(
        memory_factory(
            content=summary,
            memory_type="project_checkpoint",
            summary_short=summary,
            source="pytest",
            importance_score=0.8,
            confidence_score=1.0,
            tags="R5D,evidence-relation",
            layer_code="projects",
            area_code="projects",
            state_code="validated",
            scope_code="project",
            project_key=project_key,
            source_event_ref=source_event_ref,
        )
    )


def _counts(server: Any) -> dict[str, int]:
    conn = server.get_db_connection()
    try:
        return {
            "links": int(conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]),
            "events": int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]),
        }
    finally:
        conn.close()


def test_supports_apply_requires_confirmation_and_is_idempotent(server: Any, memory_factory) -> None:
    source_ref = "pytest:r5d:same-source"
    supporting = _memory(memory_factory, summary="Supporting observation", source_event_ref=source_ref)
    supported = _memory(memory_factory, summary="Supported claim", source_event_ref=source_ref)
    preview = server.preview_memory_relation(
        relation="supports",
        from_memory_id=supporting,
        to_memory_id=supported,
        evidence_kind="same_source_event_ref",
        evidence_ref=source_ref,
        reason="Both memories are grounded in the same durable source event.",
        project_key="demo-project",
        include_debug=True,
    )
    before = _counts(server)

    blocked = server.apply_memory_relation(
        relation="supports",
        from_memory_id=supporting,
        to_memory_id=supported,
        evidence_kind="same_source_event_ref",
        evidence_ref=source_ref,
        reason="Both memories are grounded in the same durable source event.",
        expected_preview_hash=preview["preview_hash"],
        applied_by="pytest",
        confirm_evidence_bound_relation=False,
        project_key="demo-project",
    )
    assert blocked["status"] == "blocked"
    assert blocked["blocking_reasons"] == ["explicit_relation_confirmation_required"]
    assert _counts(server) == before

    applied = server.apply_memory_relation(
        relation="supports",
        from_memory_id=supporting,
        to_memory_id=supported,
        evidence_kind="same_source_event_ref",
        evidence_ref=source_ref,
        reason="Both memories are grounded in the same durable source event.",
        expected_preview_hash=preview["preview_hash"],
        applied_by="pytest",
        confirm_evidence_bound_relation=True,
        project_key="demo-project",
        include_debug=True,
    )
    assert applied["status"] == "applied"
    assert applied["link"]["relation_type"] == "supports"
    assert applied["link"]["origin"].startswith("memory_v3_evidence_relation:")
    assert len(applied["event_ids"]) == 2
    assert _counts(server) == {"links": before["links"] + 1, "events": before["events"] + 2}

    replay = server.apply_memory_relation(
        relation="supports",
        from_memory_id=supporting,
        to_memory_id=supported,
        evidence_kind="same_source_event_ref",
        evidence_ref=source_ref,
        reason="Both memories are grounded in the same durable source event.",
        expected_preview_hash=preview["preview_hash"],
        applied_by="pytest",
        confirm_evidence_bound_relation=True,
        project_key="demo-project",
    )
    assert replay["status"] == "already_applied"
    assert replay["link"]["id"] == applied["link"]["id"]
    assert _counts(server) == {"links": before["links"] + 1, "events": before["events"] + 2}


def test_supports_same_source_requires_exact_source_ref(server: Any, memory_factory) -> None:
    left = _memory(memory_factory, summary="Support left", source_event_ref="source:r5d:a")
    right = _memory(memory_factory, summary="Support right", source_event_ref="source:r5d:b")

    preview = server.preview_memory_relation(
        "supports",
        left,
        right,
        evidence_kind="same_source_event_ref",
        evidence_ref="source:r5d:a",
        reason="Attempted shared-source support.",
    )

    assert preview["status"] == "blocked"
    assert "same_source_event_ref_required" in preview["blocking_reasons"]
    assert preview["safety"]["apply_supported"] is False


def test_explicit_support_attestation_requires_auditable_ref(server: Any, memory_factory) -> None:
    left = _memory(memory_factory, summary="Attested support left")
    right = _memory(memory_factory, summary="Attested support right")

    bad = server.preview_memory_relation(
        "supports",
        left,
        right,
        evidence_kind="explicit_support_attestation",
        evidence_ref="free form",
        reason="Explicit reviewed support assertion.",
    )
    good = server.preview_memory_relation(
        "supports",
        left,
        right,
        evidence_kind="explicit_support_attestation",
        evidence_ref=f"operator:pytest:memory:{left}:supports:{right}",
        reason="Explicit reviewed support assertion.",
    )

    assert bad["status"] == "blocked"
    assert "unsupported_attestation_ref_format" in bad["blocking_reasons"]
    assert good["status"] == "preview_ready"
    assert good["safety"]["apply_supported"] is True


def test_derived_from_materializes_explicit_source_memory_reference(server: Any, memory_factory) -> None:
    source = _memory(memory_factory, summary="Source memory")
    derived = _memory(memory_factory, summary="Derived memory")
    preview = server.preview_memory_relation(
        relation="derived_from",
        from_memory_id=derived,
        to_memory_id=source,
        evidence_kind="explicit_source_memory_reference",
        evidence_ref=f"memory:{source}",
        reason="The derived memory was explicitly produced from this source memory.",
        project_key="demo-project",
    )
    before = _counts(server)

    assert preview["status"] == "preview_ready"
    assert preview["evidence"]["explicit_source_memory_reference"] is True
    applied = server.apply_memory_relation(
        relation="derived_from",
        from_memory_id=derived,
        to_memory_id=source,
        evidence_kind="explicit_source_memory_reference",
        evidence_ref=f"memory:{source}",
        reason="The derived memory was explicitly produced from this source memory.",
        expected_preview_hash=preview["preview_hash"],
        applied_by="pytest",
        confirm_evidence_bound_relation=True,
        project_key="demo-project",
    )

    assert applied["status"] == "applied"
    assert applied["link"]["from_memory_id"] == derived
    assert applied["link"]["to_memory_id"] == source
    assert applied["link"]["relation_type"] == "derived_from"
    assert _counts(server) == {"links": before["links"] + 1, "events": before["events"] + 2}


def test_derived_from_wrong_source_reference_and_cross_project_fail_closed(server: Any, memory_factory) -> None:
    source = _memory(memory_factory, summary="Source memory")
    derived = _memory(memory_factory, summary="Derived memory")
    other_project = _memory(memory_factory, project_key="project-b", summary="Other project source")

    wrong_ref = server.preview_memory_relation(
        "derived_from",
        derived,
        source,
        evidence_kind="explicit_source_memory_reference",
        evidence_ref=f"memory:{source + 999}",
        reason="Wrong source reference must not pass.",
    )
    cross_project = server.preview_memory_relation(
        "derived_from",
        derived,
        other_project,
        evidence_kind="explicit_source_memory_reference",
        evidence_ref=f"memory:{other_project}",
        reason="Cross-project derivation must not pass implicitly.",
    )

    assert wrong_ref["status"] == "blocked"
    assert "evidence_ref_must_equal_source_memory_reference" in wrong_ref["blocking_reasons"]
    assert cross_project["status"] == "blocked"
    assert "domain_mismatch" in cross_project["blocking_reasons"]


def test_evidence_relation_stale_preview_blocks_apply(server: Any, memory_factory) -> None:
    source = _memory(memory_factory, summary="Stale source")
    derived = _memory(memory_factory, summary="Stale derived")
    preview = server.preview_memory_relation(
        "derived_from",
        derived,
        source,
        evidence_kind="explicit_source_memory_reference",
        evidence_ref=f"memory:{source}",
        reason="Stale preview test.",
    )

    stale = server.apply_memory_relation(
        relation="derived_from",
        from_memory_id=derived,
        to_memory_id=source,
        evidence_kind="explicit_source_memory_reference",
        evidence_ref=f"memory:{source}",
        reason="Stale preview test.",
        expected_preview_hash="deadbeef",
        applied_by="pytest",
        confirm_evidence_bound_relation=True,
    )

    assert stale["status"] == "stale_preview"
    assert stale["blocking_reasons"] == ["expected_preview_hash_mismatch"]
    assert stale["current_preview_hash"] == preview["preview_hash"]


def test_relation_rollback_archives_only_materialized_link_and_keeps_audit(server: Any, memory_factory) -> None:
    source = _memory(memory_factory, summary="Rollback source")
    derived = _memory(memory_factory, summary="Rollback derived")
    preview = server.preview_memory_relation(
        "derived_from",
        derived,
        source,
        evidence_kind="explicit_source_memory_reference",
        evidence_ref=f"memory:{source}",
        reason="Rollback contract test.",
    )
    applied = server.apply_memory_relation(
        relation="derived_from",
        from_memory_id=derived,
        to_memory_id=source,
        evidence_kind="explicit_source_memory_reference",
        evidence_ref=f"memory:{source}",
        reason="Rollback contract test.",
        expected_preview_hash=preview["preview_hash"],
        applied_by="pytest",
        confirm_evidence_bound_relation=True,
    )
    before_rollback = _counts(server)
    rollback_preview = server.preview_memory_relation_rollback(applied["link"]["id"], include_debug=True)

    assert rollback_preview["status"] == "preview_ready"
    rolled_back = server.rollback_memory_relation(
        link_id=applied["link"]["id"],
        expected_rollback_preview_hash=rollback_preview["rollback_preview_hash"],
        rolled_back_by="pytest",
        notes="restore graph state",
        include_debug=True,
    )
    assert rolled_back["status"] == "rolled_back"
    assert _counts(server) == {"links": before_rollback["links"], "events": before_rollback["events"] + 2}

    conn = server.get_db_connection()
    try:
        row = conn.execute("SELECT archived_at FROM memory_links WHERE id=?", (applied["link"]["id"],)).fetchone()
        assert row["archived_at"] is not None
    finally:
        conn.close()
    links = server.get_memory_links(derived)
    assert applied["link"]["id"] not in {item["id"] for item in links["links"]}
    repeated = server.rollback_memory_relation(
        link_id=applied["link"]["id"],
        expected_rollback_preview_hash=rollback_preview["rollback_preview_hash"],
        rolled_back_by="pytest",
    )
    assert repeated["status"] == "already_rolled_back"


def test_relation_rollback_refuses_legacy_supports_link(server: Any, memory_factory) -> None:
    left = _memory(memory_factory, summary="Legacy left")
    right = _memory(memory_factory, summary="Legacy right")
    legacy = server.link_memories(left, right, "supports", 1.0, "pytest:legacy", allow_legacy_unsafe=True)["link"]

    preview = server.preview_memory_relation_rollback(legacy["id"])

    assert preview["status"] == "blocked"
    assert "link_not_created_by_evidence_relation_apply" in preview["blocking_reasons"]


def test_workshop_relation_apply_and_rollback_contract(server: Any, memory_factory, monkeypatch) -> None:
    monkeypatch.setenv("MCP_SURFACE_PROFILE", "maintainer")
    source = _memory(memory_factory, summary="Workshop source")
    derived = _memory(memory_factory, summary="Workshop derived")
    preview = server.run_workshop_action(
        "memory",
        "relation_preview",
        payload={
            "relation": "derived_from",
            "from_memory_id": derived,
            "to_memory_id": source,
            "evidence_kind": "explicit_source_memory_reference",
            "evidence_ref": f"memory:{source}",
            "reason": "Workshop path test.",
        },
    )
    assert preview["status"] == "ok"
    assert preview["result"]["status"] == "preview_ready"

    applied = server.run_workshop_action(
        "memory",
        "relation_apply",
        payload={
            "relation": "derived_from",
            "from_memory_id": derived,
            "to_memory_id": source,
            "evidence_kind": "explicit_source_memory_reference",
            "evidence_ref": f"memory:{source}",
            "reason": "Workshop path test.",
            "expected_preview_hash": preview["result"]["preview_hash"],
            "applied_by": "pytest",
            "confirm_evidence_bound_relation": True,
        },
    )
    assert applied["status"] == "ok"
    assert applied["result"]["status"] == "applied"



def test_internal_link_memories_blocks_canonical_truth_relations_by_default(server: Any, memory_factory) -> None:
    left = _memory(memory_factory, summary="Internal guard left")
    right = _memory(memory_factory, summary="Internal guard right")

    for relation in ("supports", "contradicts", "supersedes", "refines", "derived_from"):
        result = server.link_memories(left, right, relation, 1.0, "pytest:guard")
        assert result["status"] == "blocked"
        assert result["error"] == "canonical_relation_requires_evidence_bound_route"
        assert result["legacy_unsafe_available"] is True

    allowed = server.link_memories(left, right, "related_to", 0.5, "pytest:guard")
    assert allowed["status"] == "created"


def test_sandman_legacy_link_tool_blocks_canonical_truth_relations(server: Any, memory_factory) -> None:
    left = _memory(memory_factory, summary="Sandman left")
    right = _memory(memory_factory, summary="Sandman right")
    conn = server.get_db_connection()
    try:
        for relation in ("supports", "contradicts", "supersedes", "refines", "derived_from"):
            result = sandman_agent._tool_link_memories(conn, left, right, relation, 0.8)
            assert result["status"] == "blocked"
            assert result["reason"] == "canonical_relation_requires_evidence_bound_route"
        allowed = sandman_agent._tool_link_memories(conn, left, right, "related_to", 0.8)
        assert allowed["status"] in {"created", "already_exists"}
    finally:
        conn.close()
