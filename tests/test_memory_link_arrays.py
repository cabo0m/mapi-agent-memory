from __future__ import annotations

from typing import Any


def _create_test_memory(server: Any, summary: str, *, content: str | None = None) -> int:
    result = server.create_memory(
        content=content or f"Content for {summary}",
        summary_short=summary,
        memory_type="project_note",
        source="pytest",
        importance_score=0.5,
        confidence_score=0.8,
        tags="pytest,links,array",
        project_key="mapi",
    )
    return int(result["memory"]["id"])


def test_get_memory_returns_merged_link_arrays(server: Any) -> None:
    left_id = _create_test_memory(server, "link-array-left")
    right_id = _create_test_memory(server, "link-array-right")
    context_id = _create_test_memory(server, "link-array-context")

    outgoing = server.link_memories(left_id, right_id, "related_to", 0.77, "pytest")["link"]
    incoming = server.link_memories(context_id, left_id, "context_for", 0.66, "pytest")["link"]

    result = server.get_memory(left_id)
    memory = result["memory"]

    assert result["memory_id"] == left_id
    assert result["link_count"] == 2
    assert result["outgoing_link_count"] == 1
    assert result["incoming_link_count"] == 1
    assert memory["link_count"] == 2
    assert memory["outgoing_link_count"] == 1
    assert memory["incoming_link_count"] == 1

    merged_by_id = {int(link["id"]): link for link in result["links"]}
    assert set(merged_by_id) == {int(outgoing["id"]), int(incoming["id"])}
    assert merged_by_id[int(outgoing["id"])] ["direction"] == "outgoing"
    assert merged_by_id[int(outgoing["id"])] ["other_memory_id"] == right_id
    assert merged_by_id[int(incoming["id"])] ["direction"] == "incoming"
    assert merged_by_id[int(incoming["id"])] ["other_memory_id"] == context_id

    assert memory["links"] == result["links"]
    assert memory["outgoing_links"] == result["outgoing_links"]
    assert memory["incoming_links"] == result["incoming_links"]


def test_get_memory_links_uses_same_shape(server: Any) -> None:
    left_id = _create_test_memory(server, "get-memory-links-left")
    right_id = _create_test_memory(server, "get-memory-links-right")
    link = server.link_memories(left_id, right_id, "supports", 0.81, "pytest", allow_legacy_unsafe=True)["link"]

    result = server.get_memory_links(left_id)

    assert result["memory_id"] == left_id
    assert result["link_count"] == 1
    assert result["outgoing_link_count"] == 1
    assert result["incoming_link_count"] == 0
    assert result["links"] == [
        {
            **link,
            "direction": "outgoing",
            "other_memory_id": right_id,
        }
    ]


def test_list_memories_include_links_attaches_graph_context(server: Any) -> None:
    left_id = _create_test_memory(server, "list-include-links-left")
    right_id = _create_test_memory(server, "list-include-links-right")
    server.link_memories(left_id, right_id, "related_to", 0.7, "pytest")

    result = server.list_memories(
        limit=10,
        project_key="mapi",
        text_query="list-include-links-left",
        include_links=True,
    )

    assert result["include_links"] is True
    assert result["count"] == 1
    item = result["items"][0]
    assert int(item["id"]) == left_id
    assert item["link_count"] == 1
    assert item["outgoing_link_count"] == 1
    assert item["incoming_link_count"] == 0
    assert item["links"][0]["direction"] == "outgoing"
    assert item["links"][0]["other_memory_id"] == right_id


def test_find_memories_include_links_attaches_graph_context(server: Any) -> None:
    left_id = _create_test_memory(
        server,
        "find-include-links-left",
        content="needle-find-include-links unique content",
    )
    right_id = _create_test_memory(server, "find-include-links-right")
    server.link_memories(right_id, left_id, "context_for", 0.72, "pytest")

    result = server.find_memories(
        text_query="needle-find-include-links",
        limit=10,
        project_key="mapi",
        include_links=True,
    )

    assert result["include_links"] is True
    assert result["count"] == 1
    item = result["items"][0]
    assert int(item["id"]) == left_id
    assert item["link_count"] == 1
    assert item["outgoing_link_count"] == 0
    assert item["incoming_link_count"] == 1
    assert item["links"][0]["direction"] == "incoming"
    assert item["links"][0]["other_memory_id"] == right_id
