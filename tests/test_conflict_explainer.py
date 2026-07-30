from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from app import conflict_explainer, db_migrations
from app.conflict_logic import build_conflict_context_bundle


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_migrations.apply_all_migrations(conn)
    return conn


def _insert_memory(
    conn: sqlite3.Connection,
    *,
    content: str,
    summary_short: str = "test",
    memory_type: str = "fact",
    source: str = "pytest",
    importance_score: float = 0.5,
    confidence_score: float = 0.5,
    evidence_count: int = 1,
    valid_from: str | None = None,
    valid_to: str | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
) -> int:
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO memories (
            content, summary_short, memory_type, source,
            importance_score, confidence_score, evidence_count,
            valid_from, valid_to,
            created_at, last_accessed_at, activity_state,
            contradiction_flag
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0)
        """,
        (
            content,
            summary_short,
            memory_type,
            source,
            importance_score,
            confidence_score,
            evidence_count,
            valid_from,
            valid_to,
            created_at,
            created_at,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _check_contract(result: dict[str, Any]) -> None:
    required = [
        "conflict_kind",
        "conflict_reason",
        "explanation",
        "confidence",
        "base_memory_ids",
        "context_memory_ids",
        "supporting_link_ids",
        "timeline_event_ids",
        "suggested_relation",
        "suggested_action",
        "needs_human_review",
        "debug",
    ]
    for field in required:
        assert field in result, f"Missing required field: {field}"
    assert result["conflict_kind"] in {
        "temporal_conflict",
        "scope_conflict",
        "source_conflict",
        "summary_detail_conflict",
        "definition_conflict",
        "unresolved_real_conflict",
    }
    assert 0.0 <= result["confidence"] <= 1.0
    assert isinstance(result["base_memory_ids"], list) and len(result["base_memory_ids"]) == 2
    assert result["suggested_relation"] in {"supersedes", "relates_to", "contradicts"}
    assert isinstance(result["needs_human_review"], bool)


@pytest.mark.smoke
def test_explain_conflict_pair_returns_contract() -> None:
    conn = make_conn()
    a = _insert_memory(conn, content="Funkcja działa poprawnie.", summary_short="status funkcji")
    b = _insert_memory(conn, content="Funkcja nie działa.", summary_short="status funkcji")
    result = conflict_explainer.explain_conflict_pair(conn, a, b)
    _check_contract(result)
    assert result["base_memory_ids"] == sorted([a, b])


@pytest.mark.regression
def test_classify_conflict_kind_returns_temporal_signal_scores() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn,
        content="System korzysta z bazy Postgres.",
        summary_short="baza danych",
        valid_to="2025-12-31T23:59:59Z",
        created_at="2025-01-01T00:00:00Z",
    )
    b = _insert_memory(
        conn,
        content="System korzysta z bazy SQLite.",
        summary_short="baza danych",
        created_at="2026-03-01T00:00:00Z",
    )

    bundle = build_conflict_context_bundle(conn, a, b)
    result = conflict_explainer.classify_conflict_kind(bundle)

    assert result["conflict_kind"] == "temporal_conflict"
    assert result["signal_scores"]["temporal_conflict"] >= 0.8
    assert any("valid_to" in item for item in result["signals"])


@pytest.mark.smoke
def test_unresolved_conflict_detected_from_phrase() -> None:
    conn = make_conn()
    a = _insert_memory(conn, content="Moduł jest aktywny.", summary_short="stan modułu")
    b = _insert_memory(conn, content="Moduł nie jest aktywny.", summary_short="stan modułu")
    result = conflict_explainer.explain_conflict_pair(conn, a, b)
    assert result["conflict_kind"] == "unresolved_real_conflict"
    assert result["suggested_relation"] == "contradicts"
    assert result["needs_human_review"] is True


@pytest.mark.smoke
def test_temporal_conflict_from_valid_to() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn,
        content="System korzysta z bazy Postgres.",
        summary_short="baza danych",
        valid_to="2025-12-31T23:59:59Z",
    )
    b = _insert_memory(
        conn,
        content="System korzysta z bazy SQLite.",
        summary_short="baza danych",
    )
    result = conflict_explainer.explain_conflict_pair(conn, a, b)
    assert result["conflict_kind"] == "temporal_conflict"
    assert result["suggested_relation"] == "supersedes"
    assert result["suggested_action"] == "set_valid_to_on_older_memory"
    assert result["needs_human_review"] is False


@pytest.mark.regression
def test_scope_conflict_from_different_memory_type() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn,
        content="Projekt używa Pythona.",
        summary_short="język projektu",
        memory_type="fact",
    )
    b = _insert_memory(
        conn,
        content="Projekt nie ma ustalonego języka.",
        summary_short="język projektu",
        memory_type="working",
    )
    result = conflict_explainer.explain_conflict_pair(conn, a, b)
    assert result["conflict_kind"] == "scope_conflict"
    assert result["suggested_relation"] == "relates_to"
    assert result["needs_human_review"] is False


@pytest.mark.regression
def test_source_conflict_from_confidence_gap() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn,
        content="Limit requestów wynosi 100 na minutę.",
        summary_short="limit API",
        confidence_score=0.9,
        source="docs_official",
    )
    b = _insert_memory(
        conn,
        content="Limit requestów wynosi 200 na minutę.",
        summary_short="limit API",
        confidence_score=0.3,
        source="slack_note",
    )
    result = conflict_explainer.explain_conflict_pair(conn, a, b)
    assert result["conflict_kind"] == "source_conflict"
    assert result["needs_human_review"] is True


@pytest.mark.regression
def test_summary_detail_conflict_same_summary_length_diff() -> None:
    conn = make_conn()
    short_content = "API v2 jest nowe."
    long_content = "API v2 wprowadza breaking changes w endpointach /users i /orders. " * 8
    a = _insert_memory(conn, content=short_content, summary_short="API v2")
    b = _insert_memory(conn, content=long_content, summary_short="API v2")
    result = conflict_explainer.explain_conflict_pair(conn, a, b)
    assert result["conflict_kind"] == "summary_detail_conflict"
    assert result["suggested_relation"] == "relates_to"
    assert result["needs_human_review"] is False


@pytest.mark.regression
def test_explain_conflict_is_idempotent() -> None:
    conn = make_conn()
    a = _insert_memory(conn, content="Cache działa.", summary_short="cache")
    b = _insert_memory(conn, content="Cache nie działa.", summary_short="cache")
    r1 = conflict_explainer.explain_conflict_pair(conn, a, b)
    r2 = conflict_explainer.explain_conflict_pair(conn, a, b)
    assert r1["conflict_kind"] == r2["conflict_kind"]
    assert r1["confidence"] == r2["confidence"]
    assert r1["suggested_relation"] == r2["suggested_relation"]


@pytest.mark.regression
def test_explain_conflict_does_not_mutate_memories() -> None:
    conn = make_conn()
    a = _insert_memory(conn, content="Wersja 1.0 jest stabilna.", summary_short="wersja")
    b = _insert_memory(conn, content="Wersja 1.0 nie jest stabilna.", summary_short="wersja")

    before_a = dict(conn.execute("SELECT * FROM memories WHERE id = ?", (a,)).fetchone())
    before_b = dict(conn.execute("SELECT * FROM memories WHERE id = ?", (b,)).fetchone())

    conflict_explainer.explain_conflict_pair(conn, a, b)

    after_a = dict(conn.execute("SELECT * FROM memories WHERE id = ?", (a,)).fetchone())
    after_b = dict(conn.execute("SELECT * FROM memories WHERE id = ?", (b,)).fetchone())

    assert before_a == after_a
    assert before_b == after_b


@pytest.mark.regression
def test_explain_conflict_symmetric_input_order() -> None:
    conn = make_conn()
    a = _insert_memory(conn, content="Serwer jest online.", summary_short="serwer")
    b = _insert_memory(conn, content="Serwer nie jest online.", summary_short="serwer")
    r_ab = conflict_explainer.explain_conflict_pair(conn, a, b)
    r_ba = conflict_explainer.explain_conflict_pair(conn, b, a)
    assert r_ab["conflict_kind"] == r_ba["conflict_kind"]
    assert r_ab["base_memory_ids"] == r_ba["base_memory_ids"]


@pytest.mark.regression
def test_explain_conflict_context_memory_ids_excludes_base() -> None:
    conn = make_conn()
    a = _insert_memory(conn, content="Endpoint /health zwraca 200.", summary_short="health check")
    b = _insert_memory(conn, content="Endpoint /health nie zwraca 200.", summary_short="health check")
    _insert_memory(conn, content="Health check jest skonfigurowany.", summary_short="health check")
    result = conflict_explainer.explain_conflict_pair(conn, a, b)
    for mid in result["context_memory_ids"]:
        assert mid not in result["base_memory_ids"], f"context_memory_ids contains base id {mid}"


# ---------------------------------------------------------------------------
# preview_resolution tests
# ---------------------------------------------------------------------------

def _check_preview_contract(result: dict[str, Any]) -> None:
    required = [
        "memory_a_id", "memory_b_id", "conflict_kind", "confidence",
        "proposed_changes", "can_auto_apply", "skip_reason",
        "explanation_summary", "needs_human_review",
    ]
    for field in required:
        assert field in result, f"Missing required field: {field}"
    assert isinstance(result["proposed_changes"], list)
    assert len(result["proposed_changes"]) >= 1
    assert isinstance(result["can_auto_apply"], bool)


@pytest.mark.smoke
def test_preview_resolution_returns_contract() -> None:
    conn = make_conn()
    a = _insert_memory(conn, content="Funkcja działa.", summary_short="status")
    b = _insert_memory(conn, content="Funkcja nie działa.", summary_short="status")
    result = conflict_explainer.preview_resolution(conn, a, b)
    _check_preview_contract(result)


@pytest.mark.smoke
def test_preview_resolution_temporal_proposes_supersedes() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn, content="Baza to Postgres.", summary_short="baza",
        valid_to="2025-12-31T00:00:00Z", created_at="2025-01-01T00:00:00Z",
    )
    b = _insert_memory(
        conn, content="Baza to SQLite.", summary_short="baza",
        created_at="2026-01-01T00:00:00Z",
    )
    result = conflict_explainer.preview_resolution(conn, a, b)
    assert result["conflict_kind"] == "temporal_conflict"
    link_changes = [c for c in result["proposed_changes"] if c["action"] == "create_link"]
    assert len(link_changes) >= 1
    assert link_changes[0]["relation_type"] == "supersedes"
    assert result["can_auto_apply"] is True
    assert result["skip_reason"] is None


@pytest.mark.regression
def test_preview_resolution_temporal_also_proposes_set_valid_to() -> None:
    conn = make_conn()
    older = _insert_memory(
        conn, content="Cache wyłączony.", summary_short="cache",
        created_at="2025-01-01T00:00:00Z",
    )
    newer = _insert_memory(
        conn, content="Cache włączony.", summary_short="cache",
        created_at="2026-03-01T00:00:00Z",
    )
    # No valid_to set on older — should propose set_valid_to
    result = conflict_explainer.preview_resolution(conn, older, newer)
    valid_to_changes = [c for c in result["proposed_changes"] if c["action"] == "set_valid_to"]
    assert len(valid_to_changes) == 1
    assert valid_to_changes[0]["memory_id"] == older


@pytest.mark.regression
def test_preview_resolution_unresolved_cannot_auto_apply() -> None:
    conn = make_conn()
    a = _insert_memory(conn, content="System jest dostępny.", summary_short="dostępność")
    b = _insert_memory(conn, content="System nie jest dostępny.", summary_short="dostępność")
    result = conflict_explainer.preview_resolution(conn, a, b)
    assert result["conflict_kind"] == "unresolved_real_conflict"
    assert result["can_auto_apply"] is False
    assert result["skip_reason"] == "needs_human_review"
    assert result["needs_human_review"] is True


@pytest.mark.regression
def test_preview_resolution_source_conflict_cannot_auto_apply() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn, content="Limit to 100 rps.", summary_short="limit",
        confidence_score=0.9, source="docs",
    )
    b = _insert_memory(
        conn, content="Limit to 200 rps.", summary_short="limit",
        confidence_score=0.3, source="slack",
    )
    result = conflict_explainer.preview_resolution(conn, a, b)
    assert result["conflict_kind"] == "source_conflict"
    assert result["can_auto_apply"] is False
    assert result["needs_human_review"] is True


@pytest.mark.regression
def test_preview_resolution_link_already_exists_blocks_auto_apply() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn, content="Baza to Postgres.", summary_short="baza",
        valid_to="2025-12-31T00:00:00Z", created_at="2025-01-01T00:00:00Z",
    )
    b = _insert_memory(
        conn, content="Baza to SQLite.", summary_short="baza",
        created_at="2026-01-01T00:00:00Z",
    )
    # Pre-create the supersedes link so can_auto_apply should be blocked
    conn.execute(
        "INSERT INTO memory_links (from_memory_id, to_memory_id, relation_type, weight, origin) VALUES (?, ?, 'supersedes', 0.9, 'test')",
        (b, a),
    )
    conn.commit()
    result = conflict_explainer.preview_resolution(conn, a, b)
    assert result["can_auto_apply"] is False
    assert result["skip_reason"] == "link_already_exists"


@pytest.mark.regression
def test_preview_resolution_does_not_mutate() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn, content="Baza to Postgres.", summary_short="baza",
        valid_to="2025-12-31T00:00:00Z", created_at="2025-01-01T00:00:00Z",
    )
    b = _insert_memory(
        conn, content="Baza to SQLite.", summary_short="baza",
        created_at="2026-01-01T00:00:00Z",
    )
    links_before = conn.execute("SELECT COUNT(*) AS c FROM memory_links").fetchone()["c"]
    conflict_explainer.preview_resolution(conn, a, b)
    links_after = conn.execute("SELECT COUNT(*) AS c FROM memory_links").fetchone()["c"]
    assert links_before == links_after


# ---------------------------------------------------------------------------
# apply_resolution tests
# ---------------------------------------------------------------------------

@pytest.mark.smoke
def test_apply_resolution_temporal_creates_link_and_sets_valid_to() -> None:
    conn = make_conn()
    older = _insert_memory(
        conn, content="Cache wyłączony.", summary_short="cache",
        created_at="2025-01-01T00:00:00Z",
        valid_to="2025-12-31T00:00:00Z",  # explicit valid_to → confidence 0.8
    )
    newer = _insert_memory(
        conn, content="Cache włączony.", summary_short="cache",
        created_at="2026-01-01T00:00:00Z",
    )
    result = conflict_explainer.apply_resolution(conn, older, newer)
    conn.commit()

    assert result["status"] == "applied"
    assert result["conflict_kind"] == "temporal_conflict"
    assert len(result["applied_changes"]) >= 1

    link_changes = [c for c in result["applied_changes"] if c["action"] == "create_link"]
    assert len(link_changes) == 1
    assert link_changes[0]["relation_type"] == "supersedes"
    link_id = link_changes[0]["link_id"]
    assert conn.execute("SELECT id FROM memory_links WHERE id = ?", (link_id,)).fetchone() is not None
    # valid_to was already set on older → no set_valid_to change proposed
    assert not any(c["action"] == "set_valid_to" for c in result["applied_changes"])


@pytest.mark.regression
def test_apply_resolution_sets_valid_to_on_older_without_it() -> None:
    # Two memories with different valid_from → temporal confidence 0.65 (= threshold)
    # Neither has valid_to set → set_valid_to will be proposed and applied
    conn = make_conn()
    older = _insert_memory(
        conn, content="Baza to Postgres.", summary_short="baza",
        created_at="2025-01-01T00:00:00Z",
        valid_from="2024-01-01T00:00:00Z",
    )
    newer = _insert_memory(
        conn, content="Baza to SQLite.", summary_short="baza",
        created_at="2026-01-01T00:00:00Z",
        valid_from="2026-01-01T00:00:00Z",
    )
    result = conflict_explainer.apply_resolution(conn, older, newer)
    conn.commit()

    assert result["status"] == "applied"
    valid_to_changes = [c for c in result["applied_changes"] if c["action"] == "set_valid_to"]
    assert len(valid_to_changes) == 1
    assert valid_to_changes[0]["memory_id"] == older
    assert valid_to_changes[0]["old_valid_to"] is None
    assert valid_to_changes[0]["new_valid_to"] is not None
    row = conn.execute("SELECT valid_to FROM memories WHERE id = ?", (older,)).fetchone()
    assert row["valid_to"] is not None


@pytest.mark.smoke
def test_apply_resolution_skipped_when_human_review_needed() -> None:
    conn = make_conn()
    a = _insert_memory(conn, content="System jest dostępny.", summary_short="dostępność")
    b = _insert_memory(conn, content="System nie jest dostępny.", summary_short="dostępność")
    result = conflict_explainer.apply_resolution(conn, a, b)

    assert result["status"] == "skipped"
    assert result["skip_reason"] == "needs_human_review"
    assert result["applied_changes"] == []
    links = conn.execute("SELECT COUNT(*) AS c FROM memory_links").fetchone()["c"]
    assert links == 0


@pytest.mark.regression
def test_apply_resolution_idempotent_when_link_already_exists() -> None:
    conn = make_conn()
    older = _insert_memory(
        conn, content="Cache wyłączony.", summary_short="cache",
        created_at="2025-01-01T00:00:00Z",
        valid_to="2025-12-31T00:00:00Z",
    )
    newer = _insert_memory(
        conn, content="Cache włączony.", summary_short="cache",
        created_at="2026-01-01T00:00:00Z",
    )
    # Apply once
    conflict_explainer.apply_resolution(conn, older, newer)
    conn.commit()
    links_after_first = conn.execute("SELECT COUNT(*) AS c FROM memory_links").fetchone()["c"]

    # Apply again — should be skipped because link already exists
    result2 = conflict_explainer.apply_resolution(conn, older, newer)
    assert result2["status"] == "skipped"
    assert result2["skip_reason"] == "link_already_exists"
    links_after_second = conn.execute("SELECT COUNT(*) AS c FROM memory_links").fetchone()["c"]
    assert links_after_second == links_after_first


@pytest.mark.regression
def test_apply_resolution_scope_conflict_creates_relates_to_link() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn, content="Projekt używa Pythona.", summary_short="język projektu",
        memory_type="fact",
    )
    b = _insert_memory(
        conn, content="Projekt nie ma ustalonego języka.", summary_short="język projektu",
        memory_type="working",
    )
    result = conflict_explainer.apply_resolution(conn, a, b)
    conn.commit()

    assert result["status"] == "applied"
    assert result["conflict_kind"] == "scope_conflict"
    link_changes = [c for c in result["applied_changes"] if c["action"] == "create_link"]
    assert len(link_changes) == 1
    assert link_changes[0]["relation_type"] == "relates_to"


@pytest.mark.regression
def test_apply_resolution_returns_correct_memory_ids() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn, content="Baza to Postgres.", summary_short="baza",
        created_at="2025-01-01T00:00:00Z",
    )
    b = _insert_memory(
        conn, content="Baza to SQLite.", summary_short="baza",
        created_at="2026-01-01T00:00:00Z",
    )
    result = conflict_explainer.apply_resolution(conn, a, b)
    assert result["memory_a_id"] == a
    assert result["memory_b_id"] == b


# ---------------------------------------------------------------------------
# definition_conflict tests
# ---------------------------------------------------------------------------

@pytest.mark.smoke
def test_definition_conflict_detected_from_high_lexical_overlap() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn,
        content="Moduł autoryzacji weryfikuje tożsamość użytkownika przez token sesji i uprawnienia roli systemu.",
        summary_short="autoryzacja",
        memory_type="fact",
    )
    b = _insert_memory(
        conn,
        content="Moduł autoryzacji sprawdza uprawnienia roli i weryfikuje token sesji dla żądania użytkownika.",
        summary_short="autoryzacja",
        memory_type="fact",
    )
    result = conflict_explainer.explain_conflict_pair(conn, a, b)
    assert result["conflict_kind"] == "definition_conflict"
    assert result["suggested_relation"] == "relates_to"
    assert result["suggested_action"] == "add_clarification_note"
    assert result["needs_human_review"] is True


@pytest.mark.regression
def test_definition_conflict_classify_returns_signal_scores() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn,
        content="Usługa cache przechowuje wyniki zapytań i przyspiesza dostęp do danych przez mechanizm TTL.",
        summary_short="cache",
        memory_type="fact",
    )
    b = _insert_memory(
        conn,
        content="Usługa cache przyspiesza dostęp do wyników zapytań przez mechanizm TTL i przechowuje dane sesji.",
        summary_short="cache",
        memory_type="fact",
    )
    bundle = build_conflict_context_bundle(conn, a, b)
    result = conflict_explainer.classify_conflict_kind(bundle)
    assert result["conflict_kind"] == "definition_conflict"
    assert result["signal_scores"]["definition_conflict"] >= 0.55
    assert result["signal_scores"]["unresolved_real_conflict"] < 0.5
    assert any("lexical overlap" in s for s in result["signals"])


@pytest.mark.regression
def test_definition_conflict_not_detected_when_negation_present() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn,
        content="Moduł autoryzacji weryfikuje tożsamość użytkownika przez token sesji i uprawnienia roli.",
        summary_short="autoryzacja",
        memory_type="fact",
    )
    b = _insert_memory(
        conn,
        content="Moduł autoryzacji nie weryfikuje tożsamości użytkownika przez token sesji.",
        summary_short="autoryzacja",
        memory_type="fact",
    )
    result = conflict_explainer.explain_conflict_pair(conn, a, b)
    # negation present → should be unresolved_real_conflict, not definition_conflict
    assert result["conflict_kind"] == "unresolved_real_conflict"


@pytest.mark.regression
def test_source_quality_score_basic() -> None:
    mem = {"source": "manual", "confidence_score": 0.9, "evidence_count": 5}
    score = conflict_explainer.source_quality_score(mem, supports_count=0)
    # source_tier=1.0*0.5 + confidence=0.9*0.3 + evidence=0.5*0.15 + supports=0.0
    assert 0.8 <= score <= 1.0


@pytest.mark.regression
def test_source_quality_score_ai_vs_manual() -> None:
    manual_mem = {"source": "manual", "confidence_score": 0.8, "evidence_count": 3}
    ai_mem = {"source": "ai_generated", "confidence_score": 0.6, "evidence_count": 1}
    score_manual = conflict_explainer.source_quality_score(manual_mem)
    score_ai = conflict_explainer.source_quality_score(ai_mem)
    assert score_manual > score_ai


@pytest.mark.regression
def test_source_quality_breakdown_contract() -> None:
    mem = {"source": "user", "confidence_score": 0.7, "evidence_count": 2}
    result = conflict_explainer.source_quality_breakdown(mem, supports_count=1)
    assert "total_score" in result
    assert "source" in result
    assert "source_tier" in result
    assert "source_tier_key" in result
    assert "confidence_score" in result
    assert "evidence_count" in result
    assert "evidence_score" in result
    assert "supports_count" in result
    assert "components" in result
    assert result["supports_count"] == 1
    assert result["source_tier_key"] == "user"
    assert 0.0 <= result["total_score"] <= 1.0
    comps = result["components"]
    assert "source_tier_weighted" in comps
    assert "confidence_weighted" in comps
    assert "evidence_weighted" in comps
    assert "supports_weighted" in comps
    assert abs(
        comps["source_tier_weighted"] + comps["confidence_weighted"]
        + comps["evidence_weighted"] + comps["supports_weighted"]
        - result["total_score"]
    ) < 0.01


@pytest.mark.regression
def test_source_quality_gap_boosts_source_conflict_signal() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn,
        content="Baza produkcyjna używa PostgreSQL.",
        summary_short="baza danych",
        memory_type="fact",
        source="manual",
        confidence_score=0.95,
        evidence_count=8,
    )
    b = _insert_memory(
        conn,
        content="Baza produkcyjna używa SQLite.",
        summary_short="baza danych",
        memory_type="fact",
        source="ai_generated",
        confidence_score=0.4,
        evidence_count=1,
    )
    result = conflict_explainer.explain_conflict_pair(conn, a, b)
    assert result["conflict_kind"] == "source_conflict"
    assert result["confidence"] >= 0.55


@pytest.mark.regression
def test_definition_conflict_apply_resolution_is_skipped() -> None:
    conn = make_conn()
    a = _insert_memory(
        conn,
        content="Moduł autoryzacji weryfikuje tożsamość użytkownika przez token sesji i uprawnienia roli systemu.",
        summary_short="autoryzacja",
        memory_type="fact",
    )
    b = _insert_memory(
        conn,
        content="Moduł autoryzacji sprawdza uprawnienia roli i weryfikuje token sesji dla żądania użytkownika.",
        summary_short="autoryzacja",
        memory_type="fact",
    )
    result = conflict_explainer.apply_resolution(conn, a, b)
    assert result["status"] == "skipped"
    assert result["skip_reason"] == "needs_human_review"
    assert result["applied_changes"] == []
