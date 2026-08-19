from __future__ import annotations

import mcp_surface


def _seed_technical_agent_identity(server) -> int:
    conn = server.get_db_connection()
    try:
        created = server._insert_memory(
            conn,
            content="Agent is the configured agent identity for this MAPI instance.",
            summary_short="Agent identity: Agent",
            memory_type="identity",
            source="mapi-init",
            importance_score=0.9,
            confidence_score=1.0,
            tags="agent-self,self-model,self-evidence,identity,bootstrap,subject:agent,agent:agent",
            layer_code="identity",
            area_code="identity",
            state_code="validated",
            scope_code="project",
            identity_weight=1.0,
            project_key="agent-self",
            entry_type="user_profile",
            truth_kind="fact",
            title="Agent identity: Agent",
            source_context="Generated from explicit first-run operator configuration.",
            source_event_ref="mapi-init:agent:identity",
            importance_level="high",
            priority="high",
            ensure_embedding=False,
        )
        conn.commit()
        return int(created["id"])
    finally:
        conn.close()


def test_first_run_onboarding_names_assistant_and_builds_user_profile(server, monkeypatch) -> None:
    monkeypatch.setenv("MAPI_AGENT_SUBJECT_KEY", "agent")
    monkeypatch.setenv("MAPI_AGENT_PROJECT_KEY", "agent-self")
    monkeypatch.setenv("MAPI_AGENT_DISPLAY_NAME", "Agent")
    old_identity_id = _seed_technical_agent_identity(server)

    bootstrap = server.bootstrap_agent_context()
    assert bootstrap["current_project"]["active_project_key"] is None
    assert "demo-project" not in repr(bootstrap)
    onboarding = bootstrap["onboarding"]
    assert onboarding["status"] == "onboarding_required"
    assert onboarding["current_step"] == "agent_name"
    assert onboarding["onboarding_required"] is True
    assert "Jak chcesz" in onboarding["next_question"]

    step1 = server.advance_polaris_onboarding("agent_name", "Nova")
    assert step1["current_step"] == "user_name"
    assert len(step1["created_memory_ids"]) == 1
    new_identity_id = int(step1["created_memory_ids"][0])

    conn = server.get_db_connection()
    try:
        old = conn.execute("SELECT * FROM memories WHERE id=?", (old_identity_id,)).fetchone()
        new = conn.execute("SELECT * FROM memories WHERE id=?", (new_identity_id,)).fetchone()
        assert old["state_code"] == "superseded"
        assert int(old["superseded_by_memory_id"]) == new_identity_id
        assert int(new["supersedes_memory_id"]) == old_identity_id
        assert new["source_event_ref"] == "polaris-onboarding:v1:agent_name"
    finally:
        conn.close()

    snapshot = server.get_agent_self_snapshot()
    assert snapshot["subject"]["display_name"] == "Nova"
    assert new_identity_id in snapshot["source_memory_ids"]
    assert old_identity_id not in snapshot["source_memory_ids"]

    assert server.advance_polaris_onboarding("user_name", "Adam")["current_step"] == "work_context"
    assert server.advance_polaris_onboarding("work_context", "Tworzę oprogramowanie i chcę pomocy w pracy.")["current_step"] == "memory_policy"
    assert server.advance_polaris_onboarding("memory_policy", "ask_when_unsure")["current_step"] == "memory_exclusions"
    assert server.advance_polaris_onboarding("memory_exclusions", skip=True)["current_step"] == "first_project"
    completed = server.advance_polaris_onboarding("first_project", skip=True)
    assert completed["status"] == "completed"
    assert completed["onboarding_required"] is False
    assert completed["summary"] == {
        "assistant_name": "Nova",
        "user_name": "Adam",
        "memory_policy": "ask_when_unsure",
        "first_project": None,
    }

    conn = server.get_db_connection()
    try:
        rows = conn.execute(
            "SELECT source_event_ref, content FROM memories WHERE source='polaris-onboarding' ORDER BY id"
        ).fetchall()
        refs = {str(row["source_event_ref"]) for row in rows}
        assert {
            "polaris-onboarding:v1:agent_name",
            "polaris-onboarding:v1:user_name",
            "polaris-onboarding:v1:work_context",
            "polaris-onboarding:v1:memory_policy",
        }.issubset(refs)
        assert "polaris-onboarding:v1:memory_exclusions" not in refs
        assert "polaris-onboarding:v1:first_project" not in refs
    finally:
        conn.close()

    again = server.bootstrap_agent_context()
    assert again["onboarding"]["status"] == "completed"
    assert again["onboarding"]["onboarding_required"] is False
    assert "assistant_instruction" not in again


def test_onboarding_can_be_skipped_without_blocking_bootstrap(server) -> None:
    initial = server.get_polaris_onboarding()
    assert initial["onboarding_required"] is True
    skipped = server.skip_polaris_onboarding("Chcę od razu pracować")
    assert skipped["status"] == "skipped"
    assert skipped["onboarding_required"] is False
    bootstrap = server.bootstrap_agent_context()
    assert bootstrap["onboarding"]["status"] == "skipped"
    assert bootstrap["current_project"]["active_project_key"] is None


def test_onboarding_is_visible_in_memory_workshop() -> None:
    workshop = mcp_surface.open_workshop_payload("memory", profile="clean_operator")
    actions = {item["action"] for item in workshop["actions"]}
    assert {"onboarding_status", "onboarding_advance", "onboarding_skip"}.issubset(actions)
