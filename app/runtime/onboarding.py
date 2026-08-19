from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

POLARIS_ONBOARDING_SCHEMA = "polaris_onboarding.v1"
POLARIS_ONBOARDING_VERSION = 1
ONBOARDING_STATUSES = frozenset({"not_started", "in_progress", "completed", "skipped"})
ONBOARDING_STEPS = (
    "agent_name",
    "user_name",
    "work_context",
    "memory_policy",
    "memory_exclusions",
    "first_project",
)
MEMORY_POLICIES = frozenset({"automatic_important", "ask_when_unsure", "explicit_only"})

_STEP_QUESTIONS = {
    "agent_name": "Jak chcesz, żebym się nazywał/a? Możesz nadać mi imię albo poprosić, żebym sam/a je wybrał/a.",
    "user_name": "A jak mam zwracać się do Ciebie?",
    "work_context": "Czym się zajmujesz i w czym przede wszystkim mam Ci pomagać?",
    "memory_policy": (
        "Jak mam podchodzić do pamięci: zapisywać samodzielnie ważne rzeczy, "
        "pytać gdy nie jestem pewien/pewna, czy zapisywać tylko na wyraźne polecenie?"
    ),
    "memory_exclusions": "Czy są informacje, których nie chcesz, żebym zapisywał/a w trwałej pamięci?",
    "first_project": "Czy chcesz od razu utworzyć pierwszy projekt, czy na razie pracujemy bez projektu?",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_onboarding_schema(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS polaris_onboarding (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            schema_version INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('not_started','in_progress','completed','skipped')),
            current_step TEXT,
            answers_json TEXT NOT NULL CHECK (json_valid(answers_json)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            skipped_at TEXT,
            skip_reason TEXT
        )
        """
    )
    now = utc_now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO polaris_onboarding (
            id, schema_version, status, current_step, answers_json,
            created_at, updated_at, completed_at, skipped_at, skip_reason
        ) VALUES (1, ?, 'not_started', 'agent_name', '{}', ?, ?, NULL, NULL, NULL)
        """,
        (POLARIS_ONBOARDING_VERSION, now, now),
    )


def _row_to_state(row: Any) -> dict[str, Any]:
    try:
        answers = json.loads(str(row["answers_json"] or "{}"))
    except (json.JSONDecodeError, TypeError):
        answers = {}
    if not isinstance(answers, dict):
        answers = {}
    return {
        "schema_version": int(row["schema_version"] or POLARIS_ONBOARDING_VERSION),
        "status": str(row["status"]),
        "current_step": None if row["current_step"] is None else str(row["current_step"]),
        "answers": answers,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "skipped_at": row["skipped_at"],
        "skip_reason": row["skip_reason"],
    }


def get_onboarding_state(conn: Any) -> dict[str, Any]:
    ensure_onboarding_schema(conn)
    row = conn.execute("SELECT * FROM polaris_onboarding WHERE id=1").fetchone()
    if row is None:
        raise RuntimeError("polaris_onboarding_state_missing")
    return _row_to_state(row)


def _next_step(step: str) -> str | None:
    try:
        index = ONBOARDING_STEPS.index(step)
    except ValueError as exc:
        raise ValueError("invalid_onboarding_step") from exc
    return ONBOARDING_STEPS[index + 1] if index + 1 < len(ONBOARDING_STEPS) else None


def _normalize_answer(step: str, value: Any, *, skip: bool) -> Any:
    text = str(value or "").strip()
    if skip:
        if step not in {"work_context", "memory_exclusions", "first_project"}:
            raise ValueError("onboarding_step_cannot_be_skipped")
        return None
    if step == "memory_policy":
        normalized = text.casefold()
        if normalized not in MEMORY_POLICIES:
            raise ValueError("invalid_memory_policy")
        return normalized
    limits = {
        "agent_name": 80,
        "user_name": 120,
        "work_context": 2000,
        "memory_exclusions": 2000,
        "first_project": 200,
    }
    if not text:
        raise ValueError("onboarding_value_required")
    if len(text) > limits.get(step, 2000):
        raise ValueError("onboarding_value_too_long")
    return text


def advance_onboarding_state(
    conn: Any,
    *,
    step: str,
    value: Any = None,
    skip: bool = False,
) -> dict[str, Any]:
    normalized_step = str(step or "").strip().casefold()
    if normalized_step not in ONBOARDING_STEPS:
        raise ValueError("invalid_onboarding_step")
    state = get_onboarding_state(conn)
    if state["status"] in {"completed", "skipped"}:
        raise ValueError("onboarding_already_finished")
    current = state["current_step"] or ONBOARDING_STEPS[0]
    if normalized_step != current:
        raise ValueError(f"onboarding_step_out_of_order:expected={current}")
    answer = _normalize_answer(normalized_step, value, skip=bool(skip))
    answers = dict(state["answers"])
    answers[normalized_step] = answer
    next_step = _next_step(normalized_step)
    now = utc_now_iso()
    status = "completed" if next_step is None else "in_progress"
    conn.execute(
        """
        UPDATE polaris_onboarding
        SET schema_version=?, status=?, current_step=?, answers_json=?,
            updated_at=?, completed_at=CASE WHEN ?='completed' THEN ? ELSE completed_at END
        WHERE id=1
        """,
        (
            POLARIS_ONBOARDING_VERSION,
            status,
            next_step,
            json.dumps(answers, ensure_ascii=False, sort_keys=True),
            now,
            status,
            now,
        ),
    )
    return get_onboarding_state(conn)


def skip_onboarding_state(conn: Any, *, reason: str | None = None) -> dict[str, Any]:
    state = get_onboarding_state(conn)
    if state["status"] == "completed":
        raise ValueError("completed_onboarding_cannot_be_skipped")
    now = utc_now_iso()
    normalized_reason = str(reason or "").strip() or None
    if normalized_reason and len(normalized_reason) > 500:
        raise ValueError("onboarding_skip_reason_too_long")
    conn.execute(
        """
        UPDATE polaris_onboarding
        SET status='skipped', current_step=NULL, updated_at=?, skipped_at=?, skip_reason=?
        WHERE id=1
        """,
        (now, now, normalized_reason),
    )
    return get_onboarding_state(conn)


def persisted_agent_name(conn: Any) -> str | None:
    try:
        state = get_onboarding_state(conn)
    except Exception:
        return None
    value = state["answers"].get("agent_name")
    text = str(value or "").strip()
    return text or None


def build_onboarding_payload(conn: Any) -> dict[str, Any]:
    state = get_onboarding_state(conn)
    status = state["status"]
    step = state["current_step"]
    required = status in {"not_started", "in_progress"}
    answers = state["answers"]
    payload = {
        "status": "onboarding_required" if required else status,
        "schema": POLARIS_ONBOARDING_SCHEMA,
        "version": POLARIS_ONBOARDING_VERSION,
        "onboarding_required": required,
        "current_step": step,
        "next_question": _STEP_QUESTIONS.get(str(step)) if required else None,
        "can_skip_entire_onboarding": required,
        "memory_policy_options": sorted(MEMORY_POLICIES),
        "product": {
            "name": "Polaris",
            "role": "persistent memory and continuity layer for a personal AI assistant",
            "capabilities": [
                "remember durable facts across chats",
                "keep project context and decisions",
                "retrieve earlier agreements and commitments",
                "maintain a source-linked assistant self-model",
                "separate global user context from project context",
            ],
        },
        "answers": answers,
        "assistant_instruction": (
            "Introduce Polaris briefly and ask exactly next_question. After the user answers, BEFORE replying, "
            "you MUST persist that answer through the compact MCP surface: call run_workshop_action with "
            "area='memory', action='onboarding_advance' and payload containing the current step, the resolved "
            "answer value and skip=false. Do not merely acknowledge an answer in chat. If the user delegates a "
            "choice to you, for example asks you to choose your own assistant name, choose a concrete value and "
            "immediately persist that chosen value before announcing it. Only after the tool succeeds should you "
            "acknowledge the saved answer and ask the next_question returned by the tool. Do not invent answers "
            "the user did not provide or delegate."
            if required
            else "Onboarding is finished; continue normal work."
        ),
        "next_action": (
            {
                "required_before_reply_after_user_answer": True,
                "tool": "run_workshop_action",
                "area": "memory",
                "action": "onboarding_advance",
                "payload_template": {
                    "step": step,
                    "value": "<resolved user answer or delegated assistant choice>",
                    "skip": False,
                },
                "delegated_choice_rule": (
                    "If the user asks you to choose the assistant name, choose one concrete name and use that exact "
                    "name as value. Persist it before telling the user the choice."
                    if step == "agent_name"
                    else None
                ),
            }
            if required
            else None
        ),
        "timestamps": {
            "created_at": state["created_at"],
            "updated_at": state["updated_at"],
            "completed_at": state["completed_at"],
            "skipped_at": state["skipped_at"],
        },
    }
    if status == "completed":
        payload["summary"] = {
            "assistant_name": answers.get("agent_name"),
            "user_name": answers.get("user_name"),
            "memory_policy": answers.get("memory_policy"),
            "first_project": answers.get("first_project"),
        }
    if status == "skipped":
        payload["skip_reason"] = state["skip_reason"]
    return payload
