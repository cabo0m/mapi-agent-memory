from __future__ import annotations

"""MAPI-facing Gemma Worker adapter via LM Studio/LMS.

Gemma is the local worker for MAPI. The host still validates tool access and
reviews reports, but Gemma is no longer described as a read-only consultant.
"""

import json
import os
from typing import Any

from app import lm_studio_client
from app.sandman_gemma_client import LmsModelManager, check_lms_status

MAPI_GEMMA_AGENT_TIMEOUT_SECONDS: int = int(os.environ.get("MAPI_GEMMA_AGENT_TIMEOUT_SECONDS", "300"))
MAPI_GEMMA_AGENT_MAX_TOKENS: int = int(os.environ.get("MAPI_GEMMA_AGENT_MAX_TOKENS", "4096"))
MAPI_GEMMA_AGENT_TEMPERATURE: float = float(os.environ.get("MAPI_GEMMA_AGENT_TEMPERATURE", "0.0"))
MAPI_GEMMA_AGENT_UNLOAD_AFTER_CALL: bool = os.environ.get(
    "MAPI_GEMMA_AGENT_UNLOAD_AFTER_CALL", "true"
).strip().lower() in {"1", "true", "yes", "on"}
MAPI_GEMMA_CONTEXT_MAX_CHARS: int = int(os.environ.get("MAPI_GEMMA_CONTEXT_MAX_CHARS", "60000"))

_TEXT_RESPONSE_FORMAT: dict[str, Any] = {"type": "text"}

_CODING_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "risk_level",
        "assumptions",
        "plan",
        "files_to_inspect",
        "patch_suggestions",
        "tests_to_run",
        "commands_to_run",
        "open_questions",
    ],
    "properties": {
        "summary": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "plan": {"type": "array", "items": {"type": "string"}},
        "files_to_inspect": {"type": "array", "items": {"type": "string"}},
        "patch_suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "intent", "details"],
                "properties": {
                    "path": {"type": "string"},
                    "intent": {"type": "string"},
                    "details": {"type": "string"},
                },
            },
        },
        "tests_to_run": {"type": "array", "items": {"type": "string"}},
        "commands_to_run": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}


def _coding_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "mapi_gemma_coding_task",
            "strict": False,
            "schema": _CODING_RESPONSE_SCHEMA,
        },
    }


def _truncate_text(value: str, *, limit: int) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + f"\n\n[TRUNCATED: omitted {omitted} chars]"


def _loads_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Gemma response did not decode to a JSON object")
    return parsed


def _call_gemma(
    messages: list[dict[str, str]],
    *,
    response_format: dict[str, Any],
    model: str | None,
    max_tokens: int,
    timeout_seconds: int,
) -> str:
    return lm_studio_client.call_lm_studio(
        messages,
        response_format,
        max_tokens=max_tokens,
        timeout=timeout_seconds,
        model=model,
    )


def gemma_lms_status_payload() -> dict[str, Any]:
    """Return non-mutating LM Studio/LMS diagnostics for the MAPI Gemma agent."""
    status = check_lms_status()
    status["mapi_gemma_agent"] = {
        "timeout_seconds": MAPI_GEMMA_AGENT_TIMEOUT_SECONDS,
        "max_tokens": MAPI_GEMMA_AGENT_MAX_TOKENS,
        "temperature": MAPI_GEMMA_AGENT_TEMPERATURE,
        "context_max_chars": MAPI_GEMMA_CONTEXT_MAX_CHARS,
        "unload_after_call": MAPI_GEMMA_AGENT_UNLOAD_AFTER_CALL,
        "mode": "gemma_worker",
    }
    return status


def gemma_lms_load_payload() -> dict[str, Any]:
    """Load configured Gemma model through the LM Studio CLI."""
    manager = LmsModelManager()
    return manager.ensure_loaded()


def gemma_lms_unload_payload() -> dict[str, Any]:
    """Unload configured Gemma model through the LM Studio CLI."""
    manager = LmsModelManager()
    return manager.unload()


def gemma_ask_payload(
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    ensure_loaded: bool = True,
    unload_after_call: bool | None = None,
    response_format_json: str | None = None,
) -> dict[str, Any]:
    """Ask Gemma a general worker question through LM Studio."""
    normalized_prompt = (prompt or "").strip()
    if not normalized_prompt:
        return {"status": "error", "error": "prompt cannot be empty"}

    messages = [
        {
            "role": "system",
            "content": system_prompt
            or (
                "You are Gemma connected to MAPI through MAPI. Answer as a careful, "
                "local Gemma Worker for MAPI. Be precise about what you actually did. "
                "If you only planned or analyzed, say so. If a host-side action is needed, request it clearly."
            ),
        },
        {"role": "user", "content": normalized_prompt},
    ]
    response_format = _TEXT_RESPONSE_FORMAT
    if response_format_json:
        try:
            parsed_response_format = json.loads(response_format_json)
        except json.JSONDecodeError as exc:
            return {"status": "error", "error": f"invalid response_format_json: {exc}"}
        if not isinstance(parsed_response_format, dict):
            return {"status": "error", "error": "response_format_json must decode to a JSON object"}
        response_format = parsed_response_format

    manager = LmsModelManager()
    load_result: dict[str, Any] | None = None
    should_unload = MAPI_GEMMA_AGENT_UNLOAD_AFTER_CALL if unload_after_call is None else bool(unload_after_call)
    if ensure_loaded:
        load_result = manager.ensure_loaded()

    try:
        answer = _call_gemma(
            messages,
            response_format=response_format,
            model=manager.identifier,
            max_tokens=int(max_tokens or MAPI_GEMMA_AGENT_MAX_TOKENS),
            timeout_seconds=int(timeout_seconds or MAPI_GEMMA_AGENT_TIMEOUT_SECONDS),
        )
        return {
            "status": "ok",
            "model": manager.identifier,
            "load_result": load_result,
            "answer": answer,
            "mode": "gemma_worker",
        }
    finally:
        if should_unload:
            manager.unload()


def gemma_coding_task_payload(
    task: str,
    context: str | None = None,
    repository_hint: str | None = None,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    ensure_loaded: bool = True,
    unload_after_call: bool | None = None,
) -> dict[str, Any]:
    """Ask Gemma Worker for structured coding-task analysis or task preparation."""
    normalized_task = (task or "").strip()
    if not normalized_task:
        return {"status": "error", "error": "task cannot be empty"}

    manager = LmsModelManager()
    load_result: dict[str, Any] | None = None
    should_unload = MAPI_GEMMA_AGENT_UNLOAD_AFTER_CALL if unload_after_call is None else bool(unload_after_call)
    if ensure_loaded:
        load_result = manager.ensure_loaded()

    bounded_context = _truncate_text(context or "", limit=MAPI_GEMMA_CONTEXT_MAX_CHARS)
    user_prompt = (
        f"TASK:\n{normalized_task}\n\n"
        f"REPOSITORY_HINT:\n{repository_hint or ''}\n\n"
        f"CONTEXT:\n{bounded_context}\n"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are Gemma Worker used by MAPI via MAPI. Return one raw JSON object "
                "matching the schema. Be explicit about whether work is planned, blocked, "
                "or ready for host-side execution. Prefer small, local, easily reviewable changes. "
                "Mention risk and tests."
            ),
        },
        {"role": "user", "content": user_prompt},
    ]
    try:
        raw = _call_gemma(
            messages,
            response_format=_coding_response_format(),
            model=manager.identifier,
            max_tokens=int(max_tokens or MAPI_GEMMA_AGENT_MAX_TOKENS),
            timeout_seconds=int(timeout_seconds or MAPI_GEMMA_AGENT_TIMEOUT_SECONDS),
        )
        parsed = _loads_json_object(raw)
        return {
            "status": "ok",
            "model": manager.identifier,
            "load_result": load_result,
            "mode": "gemma_worker",
            "context_truncated": len(context or "") > len(bounded_context),
            "result": parsed,
        }
    finally:
        if should_unload:
            manager.unload()
