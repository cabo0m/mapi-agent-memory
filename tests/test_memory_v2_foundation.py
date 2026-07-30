from __future__ import annotations

from typing import Any


def test_create_memory_persists_v2_fields(server: Any) -> None:
    result = server.create_memory(
        content="Plan wdrozenia pamięci v2 jest zaakceptowany.",
        memory_type="project_note",
        summary_short="Plan v2 zaakceptowany",
        project_key="mapi",
        entry_type="decision",
        truth_kind="decision",
        title="Plan Pamięci Jagody v2",
        source_context="Rozmowa projektowa",
        requires_user_confirmation=True,
        should_resurface_when=["przywróć pamięć", "wdrożenie v2"],
    )

    memory = result["memory"]
    assert result["status"] == "created"
    assert memory["schema_version"] == 2
    assert memory["entry_type"] == "decision"
    assert memory["type"] == "decision"
    assert memory["truth_kind"] == "decision"
    assert memory["title"] == "Plan Pamięci Jagody v2"
    assert memory["source_context"] == "Rozmowa projektowa"
    assert memory["requires_user_confirmation"] is True
    assert memory["should_resurface_when"] == ["przywróć pamięć", "wdrożenie v2"]
    assert memory["memory_v2_status"] == "active"
    assert memory["status"] == "active"
    audit = server.list_memory_audit(int(memory["id"]), event_type_prefix="memory_v2.")
    event_types = [item["event_type"] for item in audit["items"]]
    assert "memory_v2.created" in event_types


def test_list_memories_can_filter_by_truth_kind(server: Any, memory_factory) -> None:
    project_key = "truth-kind-test"
    fact_id = memory_factory(
        content="To jest potwierdzony fakt projektowy.",
        memory_type="project_note",
        summary_short="Fakt projektowy",
        truth_kind="fact",
        project_key=project_key,
    )
    dream_id = memory_factory(
        content="To jest sen Sandmana o projekcie.",
        memory_type="dream",
        summary_short="Sen projektowy",
        truth_kind="dream",
        entry_type="dream",
        project_key=project_key,
    )

    result = server.list_memories(limit=10, truth_kind="fact", project_key=project_key, sort_by="recent")
    ids = {int(item["id"]) for item in result["items"]}

    assert fact_id in ids
    assert dream_id not in ids
    assert all(item["truth_kind"] == "fact" for item in result["items"])


def test_truth_aware_retrieval_prefers_fact_over_dream(server: Any, memory_factory) -> None:
    fact_id = memory_factory(
        content="Warstwa pamięci rozdziela fakty od propozycji.",
        memory_type="project_note",
        summary_short="Rozdzielenie faktów",
        truth_kind="fact",
        entry_type="project",
        importance_score=0.8,
    )
    dream_id = memory_factory(
        content="Warstwa pamięci rozdziela fakty od propozycji.",
        memory_type="dream",
        summary_short="Rozdzielenie faktów",
        truth_kind="dream",
        entry_type="dream",
        importance_score=0.8,
    )

    result = server.find_memories("rozdziela fakty od propozycji", limit=10, debug=True)
    ids = [int(item["id"]) for item in result["items"]]

    assert fact_id in ids
    assert dream_id in ids
    assert ids.index(fact_id) < ids.index(dream_id)


def test_memory_v2_lifecycle_tools_update_state(server: Any) -> None:
    created = server.create_memory(
        content="Hipoteza robocza wymaga potwierdzenia.",
        memory_type="working_note",
        summary_short="Hipoteza robocza",
        entry_type="raw_note",
        truth_kind="proposal",
        memory_v2_status="proposed",
        requires_user_confirmation=True,
    )
    memory_id = int(created["memory"]["id"])

    confirmed = server.confirm_memory_v2(memory_id, notes="potwierdzone recznie")
    stale = server.mark_memory_stale(memory_id, notes="wymaga odswiezenia")
    replacement = server.create_memory(
        content="Nowsza wersja hipotezy została potwierdzona.",
        memory_type="working_note",
        summary_short="Nowsza hipoteza",
    )
    replacement_id = int(replacement["memory"]["id"])
    superseded = server.supersede_memory_v2(memory_id, replacement_id)
    archived = server.archive_memory_v2(memory_id)
    final_memory = server.get_memory(memory_id)["memory"]

    assert confirmed["status"] == "confirmed"
    assert confirmed["memory"]["memory_v2_status"] == "active"
    assert confirmed["memory"]["requires_user_confirmation"] is False
    assert stale["status"] == "stale"
    assert stale["memory"]["memory_v2_status"] == "stale"
    assert superseded["status"] == "superseded"
    assert superseded["memory"]["superseded_by_memory_id"] == replacement_id
    assert archived["status"] == "archived"
    assert final_memory["memory_v2_status"] == "archived"
    assert final_memory["state_code"] == "archived"
    audit = server.list_memory_audit(memory_id, event_type_prefix="memory_v2.")
    event_types = [item["event_type"] for item in audit["items"]]
    assert "memory_v2.confirmed" in event_types
    assert "memory_v2.marked_stale" in event_types
    assert "memory_v2.superseded" in event_types
    assert "memory_v2.archived" in event_types


def test_memory_v2_feature_flag_seeded_by_migration(server: Any) -> None:
    conn = server.get_db_connection()
    try:
        row = conn.execute(
            "SELECT flag_key, is_enabled, rollout_mode FROM feature_flags WHERE flag_key = 'memory_v2_enabled'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["flag_key"] == "memory_v2_enabled"
    assert int(row["is_enabled"]) == 1
    assert row["rollout_mode"] == "all"


def test_memory_v2_tools_return_disabled_when_flag_off(server: Any) -> None:
    server.set_feature_flag(key="memory_v2_enabled", enabled=False)
    created = server.create_memory(content="Probe", memory_type="project_note", summary_short="Probe")
    memory_id = int(created["memory"]["id"])

    assert server.propose_memory_capture(content="Zapamiętaj to")["status"] == "disabled"
    assert server.get_memory_restore_ritual()["status"] == "disabled"
    assert server.confirm_memory_v2(memory_id)["status"] == "disabled"
    assert server.mark_memory_stale(memory_id)["status"] == "disabled"
    assert server.archive_memory_v2(memory_id)["status"] == "disabled"
    replacement = server.create_memory(content="Replacement", memory_type="project_note", summary_short="Replacement")
    assert server.supersede_memory_v2(memory_id, int(replacement["memory"]["id"]))["status"] == "disabled"
