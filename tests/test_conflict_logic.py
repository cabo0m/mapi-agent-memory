from __future__ import annotations

from typing import Any

import pytest

from app.conflict_logic import build_conflict_clusters, build_conflict_context_bundle, build_minimal_conflict_context


@pytest.mark.regression
def test_build_minimal_conflict_context_returns_memories_and_direct_contradiction_link(server: Any, memory_factory) -> None:
    memory_a_id = memory_factory(
        content="MAPI działa stabilnie i poprawnie.",
        memory_type="project_note",
        summary_short="Stan MAPI",
        source="pytest",
        importance_score=0.85,
        confidence_score=0.9,
        tags="agent,api,stabilne",
        valid_from="2026-04-20T10:00:00Z",
    )
    memory_b_id = memory_factory(
        content="MAPI nie działa stabilnie i poprawnie.",
        memory_type="project_note",
        summary_short="Stan MAPI",
        source="pytest",
        importance_score=0.8,
        confidence_score=0.88,
        tags="agent,api,konflikt",
        valid_to="2026-04-21T10:00:00Z",
    )
    server.link_memories(memory_a_id, memory_b_id, "contradicts", 1.0, "pytest")

    conn = server.get_db_connection()
    try:
        context = build_minimal_conflict_context(conn, memory_a_id, memory_b_id)
    finally:
        conn.close()

    assert context["memory_a_id"] == memory_a_id
    assert context["memory_b_id"] == memory_b_id
    assert context["summary_short_shared"] == "Stan MAPI"
    assert context["memory_type_shared"] == "project_note"
    assert context["contradiction_link_exists"] is True
    assert [item["id"] for item in context["base_memories"]] == [memory_a_id, memory_b_id]
    assert context["base_memories"][0]["valid_from"] == "2026-04-20T10:00:00Z"
    assert context["base_memories"][1]["valid_to"] == "2026-04-21T10:00:00Z"
    assert len(context["direct_links"]) == 1
    assert context["direct_links"][0]["relation_type"] == "contradicts"
    assert context["direct_links"][0]["from_memory_id"] == memory_a_id
    assert context["direct_links"][0]["to_memory_id"] == memory_b_id


@pytest.mark.regression
def test_build_minimal_conflict_context_is_deterministic_for_reversed_input(server: Any, memory_factory) -> None:
    lower_id = memory_factory(
        content="Projekt MAPI działa stabilnie.",
        memory_type="project_note",
        summary_short="Kolejnosc konfliktu",
        source="pytest",
        importance_score=0.7,
        confidence_score=0.8,
    )
    higher_id = memory_factory(
        content="Projekt MAPI nie działa stabilnie.",
        memory_type="project_note",
        summary_short="Kolejnosc konfliktu",
        source="pytest",
        importance_score=0.72,
        confidence_score=0.82,
    )
    server.link_memories(higher_id, lower_id, "contradicts", 0.9, "pytest")

    conn = server.get_db_connection()
    try:
        context = build_minimal_conflict_context(conn, higher_id, lower_id)
    finally:
        conn.close()

    assert context["memory_a_id"] == lower_id
    assert context["memory_b_id"] == higher_id
    assert [item["id"] for item in context["base_memories"]] == [lower_id, higher_id]
    assert len(context["direct_links"]) == 1
    assert context["direct_links"][0]["from_memory_id"] == higher_id
    assert context["direct_links"][0]["to_memory_id"] == lower_id


@pytest.mark.regression
def test_build_minimal_conflict_context_raises_for_missing_memory(server: Any, memory_factory) -> None:
    existing_id = memory_factory(
        content="Istniejace wspomnienie do testu brakujacej pary.",
        memory_type="project_note",
        summary_short="Brakujacy konflikt",
        source="pytest",
        importance_score=0.6,
        confidence_score=0.7,
    )

    conn = server.get_db_connection()
    try:
        with pytest.raises(FileNotFoundError, match="id=999999"):
            build_minimal_conflict_context(conn, existing_id, 999999)
    finally:
        conn.close()


@pytest.mark.regression
def test_build_conflict_context_bundle_collects_summary_related_memories(server: Any, memory_factory) -> None:
    memory_a_id = memory_factory(
        content="MAPI działa stabilnie.",
        memory_type="project_note",
        summary_short="Bundle summary",
        source="pytest",
        project_key="mapi",
    )
    memory_b_id = memory_factory(
        content="MAPI nie działa stabilnie.",
        memory_type="project_note",
        summary_short="Bundle summary",
        source="pytest",
        project_key="mapi",
    )
    related_summary_id = memory_factory(
        content="Streszczenie poboczne o tym samym summary.",
        memory_type="project_note",
        summary_short="Bundle summary",
        source="pytest",
        project_key="inne-projektowe-tlo",
    )
    unrelated_id = memory_factory(
        content="Zupelnie inny wpis.",
        memory_type="project_note",
        summary_short="Other summary",
        source="pytest",
        project_key="inne-projektowe-tlo",
    )

    conn = server.get_db_connection()
    try:
        bundle = build_conflict_context_bundle(conn, memory_a_id, memory_b_id)
    finally:
        conn.close()

    assert bundle["context_memory_count"] == 1
    assert [item["id"] for item in bundle["context_memories"]] == [related_summary_id]
    assert bundle["context_memories"][0]["context_reasons"] == ["shared_summary_short"]
    assert unrelated_id not in {item["id"] for item in bundle["context_memories"]}


@pytest.mark.regression
def test_build_conflict_context_bundle_adds_both_reasons_for_deduplicated_memory(server: Any, memory_factory) -> None:
    memory_a_id = memory_factory(
        content="Pierwszy wpis konfliktowy działa.",
        memory_type="project_note",
        summary_short="Dual reasons summary",
        source="pytest",
        project_key="mapi",
    )
    memory_b_id = memory_factory(
        content="Drugi wpis konfliktowy nie działa.",
        memory_type="project_note",
        summary_short="Dual reasons summary",
        source="pytest",
        project_key="mapi",
    )
    dual_related_id = memory_factory(
        content="Wspomnienie kontekstowe pasuje i po summary, i po project_key.",
        memory_type="project_note",
        summary_short="Dual reasons summary",
        source="pytest",
        project_key="mapi",
    )

    conn = server.get_db_connection()
    try:
        bundle = build_conflict_context_bundle(conn, memory_a_id, memory_b_id)
    finally:
        conn.close()

    assert bundle["project_key_shared"] == "mapi"
    assert bundle["context_memory_count"] == 1
    assert bundle["context_memories"][0]["id"] == dual_related_id
    assert bundle["context_memories"][0]["context_reasons"] == ["shared_project_key", "shared_summary_short"]


@pytest.mark.regression
def test_build_conflict_context_bundle_respects_limit_and_order(server: Any, memory_factory) -> None:
    memory_a_id = memory_factory(
        content="Wpis A działa.",
        memory_type="project_note",
        summary_short="Limit summary",
        source="pytest",
        project_key="mapi",
    )
    memory_b_id = memory_factory(
        content="Wpis B nie działa.",
        memory_type="project_note",
        summary_short="Limit summary",
        source="pytest",
        project_key="mapi",
    )
    first_related_id = memory_factory(
        content="Pierwszy kontekst po id.",
        memory_type="project_note",
        summary_short="Limit summary",
        source="pytest",
        project_key="mapi",
    )
    second_related_id = memory_factory(
        content="Drugi kontekst po id.",
        memory_type="project_note",
        summary_short="Limit summary",
        source="pytest",
        project_key="mapi",
    )

    conn = server.get_db_connection()
    try:
        bundle = build_conflict_context_bundle(conn, memory_b_id, memory_a_id, related_limit=1)
    finally:
        conn.close()

    assert bundle["memory_a_id"] == memory_a_id
    assert bundle["memory_b_id"] == memory_b_id
    assert bundle["related_limit"] == 1
    assert bundle["context_memory_count"] == 1
    assert [item["id"] for item in bundle["context_memories"]] == [first_related_id]
    assert second_related_id not in {item["id"] for item in bundle["context_memories"]}


# --- E8-S2-T1: Conflict clusters ---

@pytest.mark.regression
def test_build_conflict_clusters_empty_db(server: Any) -> None:
    conn = server.get_db_connection()
    try:
        clusters = build_conflict_clusters(conn)
    finally:
        conn.close()
    assert clusters == []


@pytest.mark.regression
def test_build_conflict_clusters_two_separate_pairs(server: Any, memory_factory) -> None:
    a = memory_factory(content="Cache używa Redisa.", memory_type="fact", summary_short="cache", source="pytest")
    b = memory_factory(content="Cache używa Memcacheda.", memory_type="fact", summary_short="cache", source="pytest")
    c = memory_factory(content="Baza to Postgres.", memory_type="fact", summary_short="baza", source="pytest")
    d = memory_factory(content="Baza to SQLite.", memory_type="fact", summary_short="baza", source="pytest")

    server.link_memories(a, b, "contradicts", 1.0, "pytest")
    server.link_memories(c, d, "supersedes", 1.0, "pytest")

    conn = server.get_db_connection()
    try:
        clusters = build_conflict_clusters(conn)
    finally:
        conn.close()

    assert len(clusters) == 2
    sizes = sorted(cl["size"] for cl in clusters)
    assert sizes == [2, 2]
    all_members = {mid for cl in clusters for mid in cl["member_ids"]}
    assert all_members == {a, b, c, d}


@pytest.mark.regression
def test_build_conflict_clusters_merged_by_shared_node(server: Any, memory_factory) -> None:
    a = memory_factory(content="System X używa cache A.", memory_type="fact", summary_short="system x cache", source="pytest")
    b = memory_factory(content="System X używa cache B.", memory_type="fact", summary_short="system x cache", source="pytest")
    c = memory_factory(content="System X używa cache C.", memory_type="fact", summary_short="system x cache", source="pytest")

    server.link_memories(a, b, "contradicts", 1.0, "pytest")
    server.link_memories(a, c, "contradicts", 1.0, "pytest")

    conn = server.get_db_connection()
    try:
        clusters = build_conflict_clusters(conn)
    finally:
        conn.close()

    assert len(clusters) == 1
    assert clusters[0]["size"] == 3
    assert set(clusters[0]["member_ids"]) == {a, b, c}
    # a has degree 2, should be central
    assert clusters[0]["central_memory_id"] == a
    # a causes 2 contradictions, should be divergence source
    assert clusters[0]["divergence_source_id"] == a


@pytest.mark.regression
def test_build_conflict_clusters_isolated_memory_not_in_clusters(server: Any, memory_factory) -> None:
    a = memory_factory(content="Moduł X działa.", memory_type="fact", summary_short="moduł x status", source="pytest")
    b = memory_factory(content="Moduł X nie działa.", memory_type="fact", summary_short="moduł x status", source="pytest")
    isolated = memory_factory(content="Niezwiązana notatka.", memory_type="fact", summary_short="inne", source="pytest")

    server.link_memories(a, b, "contradicts", 1.0, "pytest")

    conn = server.get_db_connection()
    try:
        clusters = build_conflict_clusters(conn)
    finally:
        conn.close()

    all_members = {mid for cl in clusters for mid in cl["member_ids"]}
    assert isolated not in all_members
    assert {a, b}.issubset(all_members)


@pytest.mark.regression
def test_build_conflict_clusters_has_unresolved_flag(server: Any, memory_factory) -> None:
    # Use content with a negation pair so run_conflicts_v1 sets contradiction_flag
    a = memory_factory(content="Moduł deployment działa.", memory_type="fact", summary_short="status deployment", source="pytest")
    b = memory_factory(content="Moduł deployment nie działa.", memory_type="fact", summary_short="status deployment", source="pytest")
    server.run_conflicts_v1()

    conn = server.get_db_connection()
    try:
        clusters = build_conflict_clusters(conn)
    finally:
        conn.close()

    assert len(clusters) >= 1
    relevant = [cl for cl in clusters if a in cl["member_ids"] or b in cl["member_ids"]]
    assert relevant
    assert relevant[0]["has_unresolved"] is True
