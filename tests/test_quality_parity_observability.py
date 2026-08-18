from __future__ import annotations

from typing import Any

import mcp_surface
from app.operations_observability import operations_observability_payload


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance_ms(self, value: float) -> None:
        self.value += float(value) / 1000.0


def _memory(memory_factory: Any, *, project: str, summary: str) -> int:
    return int(
        memory_factory(
            content=summary,
            memory_type="project_checkpoint",
            summary_short=summary,
            source="pytest",
            importance_score=0.8,
            confidence_score=1.0,
            scope_code="project",
            project_key=project,
        )
    )


def test_legacy_graph_audit_distinguishes_trusted_debt_and_invalid(server: Any, memory_factory: Any) -> None:
    old_id = _memory(memory_factory, project="demo-project", summary="Old version")
    new_id = _memory(memory_factory, project="demo-project", summary="New version")
    left = _memory(memory_factory, project="demo-project", summary="Legacy support left")
    right = _memory(memory_factory, project="demo-project", summary="Legacy support right")
    foreign = _memory(memory_factory, project="project-b", summary="Foreign project")

    conn = server.get_db_connection()
    try:
        conn.execute("UPDATE memories SET supersedes_memory_id=? WHERE id=?", (old_id, new_id))
        conn.execute("UPDATE memories SET superseded_by_memory_id=? WHERE id=?", (new_id, old_id))
        conn.commit()
    finally:
        conn.close()

    trusted = int(server.link_memories(new_id, old_id, "supersedes", 1.0, "memory_linking_pass_v1", allow_legacy_unsafe=True)["link"]["id"])
    debt = int(server.link_memories(left, right, "supports", 1.0, "consolidation_v1_auto", allow_legacy_unsafe=True)["link"]["id"])
    invalid = int(server.link_memories(left, foreign, "refines", 1.0, "memory_write:user_explicit", allow_legacy_unsafe=True)["link"]["id"])

    result = server.get_legacy_graph_audit(include_trusted=True, sample_limit=1000)
    items = {int(item["link_id"]): item for item in result["candidates"]}

    assert items[trusted]["classification"] == "trusted"
    assert items[debt]["classification"] == "legacy_unverified"
    assert items[invalid]["classification"] == "invalid"
    assert result["remediation"]["auto_apply_allowed"] is False
    assert result["safety"]["semantic_similarity_used_for_classification"] is False
    assert result["safety"]["content_used_for_classification"] is False


def test_canonical_truth_review_and_graph_audit_are_read_only(server: Any, memory_factory: Any) -> None:
    left = _memory(memory_factory, project="demo-project", summary="Review left")
    right = _memory(memory_factory, project="demo-project", summary="Review right")
    server.link_memories(left, right, "supports", 1.0, "consolidation_v1_auto", allow_legacy_unsafe=True)

    conn = server.get_db_connection()
    try:
        before = (
            int(conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]),
            int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]),
        )
    finally:
        conn.close()

    review = server.get_canonical_truth_review(project_key="demo-project")
    audit = server.get_legacy_graph_audit(project_key="demo-project", include_candidates=False)

    conn = server.get_db_connection()
    try:
        after = (
            int(conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]),
            int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]),
        )
    finally:
        conn.close()

    assert before == after
    assert review["safety"]["read_only"] is True
    assert audit["safety"]["read_only"] is True
    assert audit["safety"]["mutations_performed"] == 0


def _runtime() -> dict[str, Any]:
    return {
        "status": "ready",
        "mutations_allowed": True,
        "reason_codes": [],
        "runtime": {"commit_sha": "abc", "profile": "reader", "runtime_mode": "public", "schema_tail": "0034", "pid": 1},
        "repository": {"dirty": False, "worktrees": []},
    }


def _transport() -> dict[str, Any]:
    return {
        "status": "ok",
        "transport": "http",
        "stateful_session": True,
        "backpressure": {"max_in_flight_posts": 16, "retry_after_seconds": 1, "keepalive_seconds": 30, "active_posts": 0, "max_observed_posts": 1, "accepted_total": 10, "rejected_total": 0},
        "overload_contract": {"status_code": 429, "retry_after_seconds": 1},
        "connection_reuse": {"server_keepalive_seconds": 30},
    }


def _embeddings() -> dict[str, Any]:
    coverage = {"total": 10, "with_embedding": 10, "without_embedding": 0, "coverage_pct": 100.0}
    return {"status": "ok", "model": "test", "embedding_dim": 384, "storage_coverage": coverage, "retrieval_eligible_coverage": coverage, "orphan_or_archived_embedding_rows": 0}


def _provider() -> dict[str, Any]:
    return {
        "status": "ok",
        "project_key": "demo-project",
        "shadow_status_counts": {},
        "feature_flags": {},
        "latency": {},
        "usage": {"total_tokens": 0},
        "estimated_cost_usd_total": 0.0,
        "estimated_cost_reason_code": None,
        "provider_failure_categories": {},
        "last_provider_failure_timestamp": None,
        "abstain": {},
        "model_queue": {"routed_proposal_count": 0, "operator_reviewed_count": 0, "operator_approved_count": 0, "operator_rejected_count": 0, "operator_acceptance_rate": None},
        "canary": {},
        "unsupported_metrics": [],
    }


def _graph() -> dict[str, Any]:
    return {
        "status": "ok",
        "schema": "mapi_legacy_graph_audit.v1",
        "summary": {"active_links_scanned": 0, "trusted_count": 0, "legacy_unverified_count": 0, "invalid_count": 0, "redundant_count": 0, "canonical_truth_review_count": 0, "heuristic_association_review_count": 0, "priority_debt_count": 0, "legacy_graph_debt_count": 0},
        "remediation": {"auto_apply_allowed": False, "debt_sources": []},
    }


def _retrieval() -> dict[str, Any]:
    return {"status": "ok", "cases_run": 2, "passed": 2, "warnings": [], "failures": []}


def test_operations_dashboard_is_bounded_content_free_and_read_only() -> None:
    result = operations_observability_payload(
        project_key="demo-project",
        timeout_budget_ms=1500,
        include_debug=True,
        get_runtime_readiness=lambda **_: _runtime(),
        get_transport_status=_transport,
        get_embedding_stats=_embeddings,
        get_retrieval_qa=lambda **_: _retrieval(),
        get_provider_observability=lambda **_: _provider(),
        get_legacy_graph_audit=lambda **_: _graph(),
    )

    assert result["status"] == "ok"
    assert result["report"]["partial"] is False
    assert result["health"] == {"status": "healthy", "warnings": []}
    assert result["safety"] == {
        "read_only": True,
        "mutations_performed": 0,
        "model_calls_performed": 0,
        "raw_memory_content_exposed": False,
        "raw_secrets_exposed": False,
    }
    assert "content" not in repr(result["sections"]).casefold()


def test_operations_budget_skips_expensive_tail_when_budget_is_exhausted() -> None:
    clock = _Clock()
    retrieval_calls = 0

    def timed(value: dict[str, Any], elapsed_ms: float):
        def callback(**_: Any) -> dict[str, Any]:
            clock.advance_ms(elapsed_ms)
            return value
        return callback

    def retrieval(**_: Any) -> dict[str, Any]:
        nonlocal retrieval_calls
        retrieval_calls += 1
        return _retrieval()

    result = operations_observability_payload(
        project_key="demo-project",
        timeout_budget_ms=500,
        include_debug=False,
        get_runtime_readiness=timed(_runtime(), 90),
        get_transport_status=timed(_transport(), 10),
        get_embedding_stats=timed(_embeddings(), 30),
        get_retrieval_qa=retrieval,
        get_provider_observability=timed(_provider(), 80),
        get_legacy_graph_audit=timed(_graph(), 20),
        monotonic=clock,
    )

    assert result["status"] == "partial"
    assert result["report"]["sections"]["retrieval_quality"]["status"] == "skipped_budget"
    assert retrieval_calls == 0


def test_governance_exposes_reader_safe_r6_observability_actions() -> None:
    workshop = mcp_surface.open_workshop_payload("governance", profile="reader")
    actions = {item["action"]: item for item in workshop["actions"]}
    for action in ("operations_dashboard", "canonical_truth_review", "legacy_graph_audit"):
        assert actions[action]["risk_class"] == "R0"
        assert actions[action]["access_requirement"] == "reader"
