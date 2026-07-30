from __future__ import annotations

"""Owned compatibility handlers for timeline-aware runtime wrappers."""

from typing import Any

from app.runtime import server_runtime as _runtime


def get_timeline(
    limit: int = 200,
    offset: int = 0,
    event_type: str | None = None,
    project_key: str | None = None,
    memory_id: int | None = None,
    include_debug: bool = False,
) -> dict[str, Any]:
    return _runtime.get_timeline(
        limit=limit,
        offset=offset,
        event_type=event_type,
        project_key=project_key,
        memory_id=memory_id,
        include_debug=include_debug,
    )


def get_project_timeline(
    project_key: str,
    limit: int = 200,
    offset: int = 0,
    include_debug: bool = False,
) -> dict[str, Any]:
    return _runtime.get_project_timeline(
        project_key=project_key,
        limit=limit,
        offset=offset,
        include_debug=include_debug,
    )


def record_project_timeline_event(
    project_key: str,
    event_type: str,
    title: str,
    summary: str | None = None,
    status: str | None = None,
    memory_ids_json: str | None = None,
    source: str | None = None,
    valid_at: str | None = None,
    payload_json: str | None = None,
) -> dict[str, Any]:
    return _runtime.record_project_timeline_event(
        project_key=project_key,
        event_type=event_type,
        title=title,
        summary=summary,
        status=status,
        memory_ids_json=memory_ids_json,
        source=source,
        valid_at=valid_at,
        payload_json=payload_json,
    )


def get_memory_timeline(memory_id: int, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    return _runtime.get_memory_timeline(memory_id=memory_id, limit=limit, offset=offset)


def backfill_timeline() -> dict[str, Any]:
    return _runtime.backfill_timeline()


def run_conflicts_v1(notes: str | None = None) -> dict[str, Any]:
    return _runtime.run_conflicts_v1(notes=notes)


def run_sandman_v1(
    freedom_level: int = 1,
    notes: str | None = None,
    workspace_key: str | None = None,
    project_key: str | None = None,
) -> dict[str, Any]:
    return _runtime.run_sandman_v1(
        freedom_level=freedom_level,
        notes=notes,
        workspace_key=workspace_key,
        project_key=project_key,
    )


def run_sandman_ai(freedom_level: int = 1, notes: str | None = None) -> dict[str, Any]:
    return _runtime.run_sandman_ai(freedom_level=freedom_level, notes=notes)


def run_consolidation_v1(notes: str | None = None) -> dict[str, Any]:
    return _runtime.run_consolidation_v1(notes=notes)
