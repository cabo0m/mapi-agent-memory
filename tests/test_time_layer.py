from __future__ import annotations

import re

from app.memory_store import utc_now_iso as store_utc_now_iso

UTC_Z_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_memory_store_utc_now_iso_uses_z_suffix() -> None:
    assert UTC_Z_PATTERN.fullmatch(store_utc_now_iso())


def test_create_memory_returns_z_timestamps(server) -> None:
    result = server.create_memory(
        content="Test czasu Z w create_memory.",
        memory_type="project_note",
        summary_short="Z timestamp test",
        source="pytest",
        layer_code="projects",
        area_code="projects",
        scope_code="project",
        project_key="mapi",
    )

    memory = result["memory"]
    assert UTC_Z_PATTERN.fullmatch(memory["created_at"])
    assert UTC_Z_PATTERN.fullmatch(memory["last_accessed_at"])


def test_link_memories_returns_z_created_at(server) -> None:
    left = server.create_memory(
        content="Lewy test linka czasu.",
        memory_type="project_note",
        summary_short="left",
        source="pytest",
        layer_code="projects",
        area_code="projects",
        scope_code="project",
        project_key="mapi",
    )["memory"]["id"]
    right = server.create_memory(
        content="Prawy test linka czasu.",
        memory_type="project_note",
        summary_short="right",
        source="pytest",
        layer_code="projects",
        area_code="projects",
        scope_code="project",
        project_key="mapi",
    )["memory"]["id"]

    link = server.link_memories(int(left), int(right), "related_to", 0.7, "pytest")["link"]
    assert UTC_Z_PATTERN.fullmatch(link["created_at"])
