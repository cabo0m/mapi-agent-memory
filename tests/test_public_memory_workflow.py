from __future__ import annotations

from typing import Any


def test_agent_can_save_search_read_and_link(server: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("MCP_SURFACE_PROFILE", "agent")
    first = server.save_memory(
        content="The fictional agent keeps durable project notes in MAPI.",
        project_key="demo-project",
        source_event_ref="public-workflow:first",
    )
    second = server.save_memory(
        content="The fictional agent inspects provenance before acting.",
        project_key="demo-project",
        source_event_ref="public-workflow:second",
    )
    assert first["status"] == "created"
    assert second["status"] == "created"

    found = server.find_memories("durable project notes", project_key="demo-project")
    assert any(int(item["id"]) == int(first["memory_id"]) for item in found["items"])
    loaded = server.get_memory(int(first["memory_id"]))
    assert int(loaded["memory"]["id"]) == int(first["memory_id"])

    linked = server.link_memories(
        from_memory_id=int(first["memory_id"]),
        to_memory_id=int(second["memory_id"]),
        relation_type="supports",
        weight=0.8,
        origin="public-test",
    )
    assert linked["status"] == "created"
    links = server.get_memory_links(int(first["memory_id"]))
    assert links["link_count"] >= 1


def test_agent_proposal_does_not_create_a_memory(server: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("MCP_SURFACE_PROFILE", "agent")
    before = server.list_memories(project_key="demo-project", limit=100)
    result = server.propose_memory(
        content="A fictional uncertain note should enter review.",
        project_key="demo-project",
        source_event_ref="public-workflow:proposal",
    )
    after = server.list_memories(project_key="demo-project", limit=100)
    assert result["status"] == "proposed"
    assert result["memory_created"] is False
    assert after["count"] == before["count"]
