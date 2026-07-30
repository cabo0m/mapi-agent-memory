from __future__ import annotations

"""Persistent Gemma Worker job storage for staged execution."""

import json
from datetime import datetime, timezone
from typing import Any

GEMMA_WORKER_JOB_STATUSES = {
    "created",
    "planned",
    "needs_approval",
    "approved",
    "running",
    "completed",
    "failed",
    "rejected",
    "cancelled",
}

GEMMA_WORKER_JOB_TRANSITIONS = {
    "created": {"planned", "needs_approval", "rejected", "cancelled", "failed"},
    "planned": {"needs_approval", "rejected", "cancelled", "failed"},
    "needs_approval": {"approved", "rejected", "cancelled", "failed"},
    "approved": {"running", "cancelled", "failed"},
    "running": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
    "rejected": set(),
    "cancelled": set(),
}

_JSON_ARRAY_FIELDS = {
    "allowed_actions_json": "allowed_actions",
    "acceptance_criteria_json": "acceptance_criteria",
}
_JSON_OBJECT_FIELDS = {
    "plan_json": "plan",
    "result_json": "result",
}
_UNSET = object()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_required_text(value: Any, field_name: str) -> str:
    normalized = normalize_optional_text(value)
    if normalized is None:
        raise ValueError(f"{field_name} is required")
    return normalized


def normalize_job_status(value: str | None, *, default: str = "created") -> str:
    normalized = (normalize_optional_text(value) or default).lower()
    if normalized not in GEMMA_WORKER_JOB_STATUSES:
        raise ValueError(
            f"job status must be one of: {', '.join(sorted(GEMMA_WORKER_JOB_STATUSES))}"
        )
    return normalized


def normalize_string_list(value: Any, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a JSON array")
    result: list[str] = []
    for index, item in enumerate(value):
        normalized = normalize_optional_text(item)
        if normalized is None:
            raise ValueError(f"{field_name}[{index}] must be a non-empty string")
        result.append(normalized)
    return result


def _encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _decode_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return value
    return json.loads(value)


def row_to_gemma_worker_job(row: Any) -> dict[str, Any]:
    item = dict(row)
    for raw_field, parsed_field in _JSON_ARRAY_FIELDS.items():
        parsed = _decode_json(item.get(raw_field))
        item[parsed_field] = parsed if isinstance(parsed, list) else []
    for raw_field, parsed_field in _JSON_OBJECT_FIELDS.items():
        item[parsed_field] = _decode_json(item.get(raw_field))
    return item


def require_gemma_worker_job(conn: Any, job_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM gemma_worker_jobs WHERE id = ?",
        (int(job_id),),
    ).fetchone()
    if row is None:
        raise ValueError(f"gemma worker job #{job_id} does not exist")
    return row_to_gemma_worker_job(row)


def create_gemma_worker_job(
    conn: Any,
    *,
    task: str,
    repo: str,
    project_key: str | None = None,
    context: str | None = None,
    allowed_actions: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    status: str = "created",
) -> dict[str, Any]:
    task_norm = normalize_required_text(task, "task")
    repo_norm = normalize_required_text(repo, "repo")
    status_norm = normalize_job_status(status)
    allowed_actions_norm = normalize_string_list(
        allowed_actions,
        field_name="allowed_actions",
    )
    acceptance_criteria_norm = normalize_string_list(
        acceptance_criteria,
        field_name="acceptance_criteria",
    )
    if not acceptance_criteria_norm:
        raise ValueError("acceptance_criteria must not be empty")
    now = utc_now_iso()
    cursor = conn.execute(
        """
        INSERT INTO gemma_worker_jobs (
            status,
            repo,
            project_key,
            task,
            context,
            allowed_actions_json,
            acceptance_criteria_json,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            status_norm,
            repo_norm,
            normalize_optional_text(project_key),
            task_norm,
            normalize_optional_text(context),
            _encode_json(allowed_actions_norm),
            _encode_json(acceptance_criteria_norm),
            now,
            now,
        ),
    )
    job_id = int(cursor.lastrowid)
    conn.commit()
    return require_gemma_worker_job(conn, job_id)


def update_gemma_worker_job(
    conn: Any,
    job_id: int,
    *,
    status: str | None = None,
    project_key: str | None | object = _UNSET,
    context: str | None | object = _UNSET,
    allowed_actions: list[str] | None | object = _UNSET,
    acceptance_criteria: list[str] | None | object = _UNSET,
    plan: dict[str, Any] | None | object = _UNSET,
    result: dict[str, Any] | None | object = _UNSET,
    error: str | None | object = _UNSET,
    approved_at: str | None | object = _UNSET,
    completed_at: str | None | object = _UNSET,
) -> dict[str, Any]:
    require_gemma_worker_job(conn, int(job_id))

    updates: list[str] = []
    params: list[Any] = []
    if status is not None:
        updates.append("status = ?")
        params.append(normalize_job_status(status))
    if project_key is not _UNSET:
        updates.append("project_key = ?")
        params.append(normalize_optional_text(project_key))
    if context is not _UNSET:
        updates.append("context = ?")
        params.append(normalize_optional_text(context))
    if allowed_actions is not _UNSET:
        updates.append("allowed_actions_json = ?")
        params.append(
            _encode_json(
                normalize_string_list(allowed_actions, field_name="allowed_actions")
            )
        )
    if acceptance_criteria is not _UNSET:
        normalized_acceptance = normalize_string_list(
            acceptance_criteria,
            field_name="acceptance_criteria",
        )
        if not normalized_acceptance:
            raise ValueError("acceptance_criteria must not be empty")
        updates.append("acceptance_criteria_json = ?")
        params.append(_encode_json(normalized_acceptance))
    if plan is not _UNSET:
        updates.append("plan_json = ?")
        params.append(None if plan is None else _encode_json(plan))
    if result is not _UNSET:
        updates.append("result_json = ?")
        params.append(None if result is None else _encode_json(result))
    if error is not _UNSET:
        updates.append("error = ?")
        params.append(normalize_optional_text(error))
    if approved_at is not _UNSET:
        updates.append("approved_at = ?")
        params.append(normalize_optional_text(approved_at))
    if completed_at is not _UNSET:
        updates.append("completed_at = ?")
        params.append(normalize_optional_text(completed_at))

    if not updates:
        return require_gemma_worker_job(conn, int(job_id))

    updates.append("updated_at = ?")
    params.append(utc_now_iso())
    params.append(int(job_id))
    conn.execute(
        f"UPDATE gemma_worker_jobs SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()
    return require_gemma_worker_job(conn, int(job_id))


def can_transition_job_status(current_status: str, next_status: str) -> bool:
    current = normalize_job_status(current_status)
    target = normalize_job_status(next_status)
    if current == target:
        return True
    return target in GEMMA_WORKER_JOB_TRANSITIONS[current]


def transition_gemma_worker_job_status(
    conn: Any,
    job_id: int,
    *,
    next_status: str,
    error: str | None | object = _UNSET,
    plan: dict[str, Any] | None | object = _UNSET,
    result: dict[str, Any] | None | object = _UNSET,
) -> dict[str, Any]:
    job = require_gemma_worker_job(conn, int(job_id))
    normalized_next = normalize_job_status(next_status)
    if not can_transition_job_status(job["status"], normalized_next):
        raise ValueError(
            f"invalid job status transition: {job['status']} -> {normalized_next}"
        )

    approved_at: str | None | object = _UNSET
    completed_at: str | None | object = _UNSET
    if normalized_next == "approved":
        approved_at = utc_now_iso()
    if normalized_next in {"completed", "failed"}:
        completed_at = utc_now_iso()

    return update_gemma_worker_job(
        conn,
        int(job_id),
        status=normalized_next,
        error=error,
        plan=plan,
        result=result,
        approved_at=approved_at,
        completed_at=completed_at,
    )
