from __future__ import annotations

"""Neutral bootstrap context for public MAPI clients."""

from typing import Any, Callable


def agent_workshop_index() -> list[dict[str, Any]]:
    return [
        {
            "area": "memory",
            "purpose": "Create, search, inspect and relate durable memories.",
            "audience": "agent",
            "risk": "low",
        },
        {
            "area": "timeline",
            "purpose": "Inspect project and memory history.",
            "audience": "reader",
            "risk": "low",
        },
        {
            "area": "governance",
            "purpose": "Inspect quality, review queues and lifecycle state.",
            "audience": "maintainer",
            "risk": "medium",
        },
    ]


def agent_recommended_next_calls() -> dict[str, str]:
    return {
        "find": "Search before creating a new memory.",
        "read": "Inspect the full memory before relying on it.",
        "links": "Inspect relationships and provenance.",
        "write": "Use an explicit write or a proposal according to client policy.",
    }


def agent_bootstrap_protocol() -> dict[str, str]:
    return {
        "stage_1": "Select a project key.",
        "stage_2": "Search recent and relevant memories.",
        "stage_3": "Inspect source memories and links.",
        "stage_4": "Write only through an explicit or proposal path.",
    }


def known_systems_for_project(project_key: str | None) -> list[str]:
    key = str(project_key or "demo-project").strip() or "demo-project"
    return [key, "MAPI"]


def project_purpose_for(project_key: str | None) -> str:
    key = str(project_key or "demo-project").strip() or "demo-project"
    return f"Durable agent memory and governance context for {key}."


def build_bootstrap_agent_context_payload(
    *,
    project_key: str | None,
    limit: int,
    get_db_connection: Callable[[], Any],
    row_to_dict: Callable[[Any], dict[str, Any]],
    enrich_memory_dict: Callable[[dict[str, Any]], dict[str, Any]],
    normalize_optional_text: Callable[[Any], str | None],
) -> dict[str, Any]:
    resolved_project = normalize_optional_text(project_key) or "demo-project"
    safe_limit = max(1, min(int(limit), 50))
    recent: list[dict[str, Any]] = []
    connection = get_db_connection()
    try:
        rows = connection.execute(
            """
            SELECT *
            FROM memories
            WHERE project_key = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (resolved_project, safe_limit),
        ).fetchall()
        recent = [enrich_memory_dict(row_to_dict(row)) for row in rows]
    finally:
        connection.close()

    return {
        "status": "ok",
        "schema": "mapi_agent_bootstrap.v1",
        "project": {
            "project_key": resolved_project,
            "purpose": project_purpose_for(resolved_project),
            "known_systems": known_systems_for_project(resolved_project),
        },
        "protocol": agent_bootstrap_protocol(),
        "recommended_next_calls": agent_recommended_next_calls(),
        "workshop_index": agent_workshop_index(),
        "recent_memories": recent,
    }
