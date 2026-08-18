from __future__ import annotations

import mcp_surface


def _create_self_memory(memory_factory, **overrides):
    values = dict(
        content="Agent identity evidence",
        summary_short="Agent identity evidence",
        memory_type="identity",
        source="pytest",
        importance_score=0.9,
        confidence_score=1.0,
        tags="agent-self,subject:alpha",
        layer_code="identity",
        area_code="identity",
        state_code="validated",
        scope_code="project",
        identity_weight=0.9,
        project_key="alpha-self",
        entry_type="user_profile",
        truth_kind="fact",
    )
    values.update(overrides)
    return memory_factory(**values)


def test_self_snapshot_requires_explicit_self_evidence(server, memory_factory):
    identity_id = _create_self_memory(memory_factory)
    ordinary_id = memory_factory(
        content="ordinary project note", summary_short="ordinary", memory_type="project_note", source="pytest",
        importance_score=0.9, confidence_score=1.0, tags="ordinary", layer_code="projects", area_code="projects",
        state_code="validated", scope_code="project", identity_weight=0.0, project_key="alpha-self",
    )
    result = server.get_agent_self_snapshot(project_key="alpha-self", subject_key="alpha")
    assert result["status"] == "ok"
    assert identity_id in result["source_memory_ids"]
    assert ordinary_id not in result["source_memory_ids"]
    assert result["safety"]["semantic_similarity_used_as_identity_evidence"] is False


def test_foreign_global_identity_is_not_loaded(server, memory_factory):
    own = _create_self_memory(memory_factory)
    foreign = memory_factory(
        content="foreign identity", summary_short="foreign", memory_type="identity", source="pytest",
        importance_score=1.0, confidence_score=1.0, tags="subject:beta,agent-self", layer_code="identity", area_code="identity",
        state_code="validated", scope_code="global", identity_weight=1.0, project_key=None,
    )
    result = server.get_agent_self_snapshot(project_key="alpha-self", subject_key="alpha", include_global=True)
    assert own in result["source_memory_ids"]
    assert foreign not in result["source_memory_ids"]


def test_commitment_ledger_uses_explicit_commitment_markers(server, memory_factory):
    commitment = _create_self_memory(memory_factory, content="Never mutate without review", summary_short="Review before mutation", memory_type="guardrail", entry_type="decision", truth_kind="decision", tags="agent-self,subject:alpha,guardrail,safety", area_code="meta")
    _create_self_memory(memory_factory, content="likes concise answers", summary_short="concise", memory_type="preference", entry_type="user_profile", tags="agent-self,subject:alpha", area_code="preferences")
    result = server.get_agent_commitment_ledger(project_key="alpha-self", subject_key="alpha")
    assert [item["id"] for item in result["commitments"]] == [commitment]
    assert result["safety"]["explicit_evidence_only"] is True


def test_autobiographical_timeline_is_chronological_and_bounded(server, memory_factory):
    first = _create_self_memory(memory_factory, summary_short="first", content="first", layer_code="autobio", area_code="history", tags="agent-self,subject:alpha,milestone")
    second = _create_self_memory(memory_factory, summary_short="second", content="second", layer_code="autobio", area_code="history", tags="agent-self,subject:alpha,milestone")
    result = server.get_agent_autobiographical_timeline(project_key="alpha-self", subject_key="alpha", limit=10)
    ids = [item["id"] for item in result["events"]]
    assert ids[-2:] == [first, second]
    assert result["count"] <= 10


def test_capsule_is_source_linked_compact_and_deterministic(server, memory_factory):
    _create_self_memory(memory_factory, content="secret-ish full content", summary_short="Identity summary")
    first = server.get_agent_self_capsule(project_key="alpha-self", subject_key="alpha", include_content=False)
    second = server.get_agent_self_capsule(project_key="alpha-self", subject_key="alpha", include_content=False)
    assert first["capsule_fingerprint"] == second["capsule_fingerprint"]
    assert first["source_memory_ids"]
    assert "content" not in repr(first["identity"])
    assert first["safety"]["source_linked"] is True


def test_self_surfaces_are_read_only_and_workshop_visible(server, memory_factory):
    _create_self_memory(memory_factory)
    conn = server.get_db_connection()
    try:
        before = (int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]), int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]))
    finally:
        conn.close()
    server.get_agent_self_snapshot(project_key="alpha-self", subject_key="alpha")
    server.get_agent_commitment_ledger(project_key="alpha-self", subject_key="alpha")
    server.get_agent_autobiographical_timeline(project_key="alpha-self", subject_key="alpha")
    server.get_agent_self_capsule(project_key="alpha-self", subject_key="alpha")
    conn = server.get_db_connection()
    try:
        after = (int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]), int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]))
    finally:
        conn.close()
    assert before == after
    workshop = mcp_surface.open_workshop_payload("memory", profile="reader")
    actions = {item["action"] for item in workshop["actions"]}
    assert {"self_snapshot", "commitment_ledger", "autobiographical_timeline", "self_capsule"}.issubset(actions)
