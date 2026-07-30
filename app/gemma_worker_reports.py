from __future__ import annotations

"""Contracts and parsers for Gemma Worker staged outputs."""

import json
from typing import Any

_PLAN_STEP_REQUIRED_FIELDS = {"step", "allowed_action", "target", "reason"}

GEMMA_WORKER_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "status",
        "summary",
        "assumptions",
        "risks",
        "planned_steps",
        "files_expected_to_change",
        "tests_to_run",
        "needs_approval_before_execution",
    ],
    "properties": {
        "status": {"type": "string", "enum": ["ok"]},
        "summary": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "planned_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["step", "allowed_action", "target", "reason"],
                "properties": {
                    "step": {"type": "string"},
                    "allowed_action": {"type": "string"},
                    "target": {"type": ["string", "null"]},
                    "reason": {"type": "string"},
                },
            },
        },
        "files_expected_to_change": {"type": "array", "items": {"type": "string"}},
        "tests_to_run": {"type": "array", "items": {"type": "string"}},
        "needs_approval_before_execution": {"type": "boolean", "enum": [True]},
    },
}

GEMMA_WORKER_EXECUTION_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": [
        "status",
        "summary",
        "changed_files",
        "actions_used",
        "tests_run",
        "patches",
        "risks",
        "needs_agent_review",
    ],
    "properties": {
        "status": {"type": "string", "enum": ["completed", "failed"]},
        "summary": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "actions_used": {"type": "array", "items": {"type": "string"}},
        "tests_run": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["command", "status", "notes"],
                "properties": {
                    "command": {"type": "string"},
                    "status": {"type": "string", "enum": ["passed", "failed", "skipped"]},
                    "notes": {"type": "string"},
                },
            },
        },
        "patches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "mode", "summary"],
                "properties": {
                    "path": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["created", "updated", "deleted", "proposed_only"],
                    },
                    "summary": {"type": "string"},
                },
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "needs_agent_review": {"type": "boolean", "enum": [True]},
        "action_trace": {"type": "array"},
    },
}


def gemma_worker_plan_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "gemma_worker_plan_v1",
            "strict": False,
            "schema": GEMMA_WORKER_PLAN_SCHEMA,
        },
    }


def gemma_worker_execution_report_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "gemma_worker_execution_report_v1",
            "strict": False,
            "schema": GEMMA_WORKER_EXECUTION_REPORT_SCHEMA,
        },
    }


def _string_list_field(
    value: Any,
    *,
    field: str,
    errors: list[dict[str, Any]],
) -> list[str]:
    if not isinstance(value, list):
        errors.append({"field": field, "code": "not_array"})
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append({"field": field, "code": "item_not_string", "index": index})
            continue
        result.append(item.strip())
    return result


def _loads_json_object(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
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


def parse_gemma_worker_plan(
    raw_text: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    try:
        parsed = _loads_json_object(raw_text)
    except Exception as exc:
        return None, [{"field": "gemma_json", "code": "invalid_json", "message": str(exc)}]

    if parsed.get("status") != "ok":
        errors.append({"field": "status", "code": "invalid", "allowed": ["ok"]})
    summary = parsed.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append({"field": "summary", "code": "missing"})
    assumptions = _string_list_field(parsed.get("assumptions"), field="assumptions", errors=errors)
    risks = _string_list_field(parsed.get("risks"), field="risks", errors=errors)
    files_expected_to_change = _string_list_field(
        parsed.get("files_expected_to_change"),
        field="files_expected_to_change",
        errors=errors,
    )
    tests_to_run = _string_list_field(
        parsed.get("tests_to_run"),
        field="tests_to_run",
        errors=errors,
    )

    raw_steps = parsed.get("planned_steps")
    normalized_steps: list[dict[str, Any]] = []
    if not isinstance(raw_steps, list):
        errors.append({"field": "planned_steps", "code": "not_array"})
    else:
        for index, item in enumerate(raw_steps):
            if not isinstance(item, dict):
                errors.append({"field": "planned_steps", "code": "item_not_object", "index": index})
                continue
            missing_fields = sorted(_PLAN_STEP_REQUIRED_FIELDS - set(item))
            if missing_fields:
                errors.append(
                    {
                        "field": "planned_steps",
                        "code": "missing_fields",
                        "index": index,
                        "missing": missing_fields,
                    }
                )
                continue
            step = item.get("step")
            allowed_action = item.get("allowed_action")
            reason = item.get("reason")
            target = item.get("target")
            if not isinstance(step, str) or not step.strip():
                errors.append({"field": "planned_steps.step", "code": "invalid", "index": index})
                continue
            if not isinstance(allowed_action, str) or not allowed_action.strip():
                errors.append(
                    {"field": "planned_steps.allowed_action", "code": "invalid", "index": index}
                )
                continue
            if target is not None and not isinstance(target, str):
                errors.append({"field": "planned_steps.target", "code": "invalid", "index": index})
                continue
            if not isinstance(reason, str) or not reason.strip():
                errors.append({"field": "planned_steps.reason", "code": "invalid", "index": index})
                continue
            normalized_steps.append(
                {
                    "step": step.strip(),
                    "allowed_action": allowed_action.strip(),
                    "target": target.strip() if isinstance(target, str) else None,
                    "reason": reason.strip(),
                }
            )

    if parsed.get("needs_approval_before_execution") is not True:
        errors.append(
            {
                "field": "needs_approval_before_execution",
                "code": "required_true",
            }
        )

    if errors:
        return None, errors
    return {
        "status": "ok",
        "summary": summary.strip(),
        "assumptions": assumptions,
        "risks": risks,
        "planned_steps": normalized_steps,
        "files_expected_to_change": files_expected_to_change,
        "tests_to_run": tests_to_run,
        "needs_approval_before_execution": True,
    }, []


def parse_gemma_worker_execution_report(
    raw_text: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    try:
        parsed = _loads_json_object(raw_text)
    except Exception as exc:
        return None, [{"field": "gemma_json", "code": "invalid_json", "message": str(exc)}]

    status = parsed.get("status")
    if status not in {"completed", "failed"}:
        errors.append(
            {
                "field": "status",
                "code": "invalid",
                "allowed": ["completed", "failed"],
            }
        )
    summary = parsed.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append({"field": "summary", "code": "missing"})

    changed_files = _string_list_field(
        parsed.get("changed_files"),
        field="changed_files",
        errors=errors,
    )
    actions_used = _string_list_field(
        parsed.get("actions_used"),
        field="actions_used",
        errors=errors,
    )
    risks = _string_list_field(
        parsed.get("risks"),
        field="risks",
        errors=errors,
    )

    normalized_tests: list[dict[str, str]] = []
    raw_tests = parsed.get("tests_run")
    if not isinstance(raw_tests, list):
        errors.append({"field": "tests_run", "code": "not_array"})
    else:
        for index, item in enumerate(raw_tests):
            if not isinstance(item, dict):
                errors.append({"field": "tests_run", "code": "item_not_object", "index": index})
                continue
            command = item.get("command")
            test_status = item.get("status")
            notes = item.get("notes")
            if not isinstance(command, str) or not command.strip():
                errors.append({"field": "tests_run.command", "code": "invalid", "index": index})
                continue
            if test_status not in {"passed", "failed", "skipped"}:
                errors.append({"field": "tests_run.status", "code": "invalid", "index": index})
                continue
            if not isinstance(notes, str):
                errors.append({"field": "tests_run.notes", "code": "invalid", "index": index})
                continue
            normalized_tests.append(
                {
                    "command": command.strip(),
                    "status": test_status,
                    "notes": notes.strip(),
                }
            )

    normalized_patches: list[dict[str, str]] = []
    raw_patches = parsed.get("patches")
    if not isinstance(raw_patches, list):
        errors.append({"field": "patches", "code": "not_array"})
    else:
        for index, item in enumerate(raw_patches):
            if not isinstance(item, dict):
                errors.append({"field": "patches", "code": "item_not_object", "index": index})
                continue
            path = item.get("path")
            mode = item.get("mode")
            patch_summary = item.get("summary")
            if not isinstance(path, str) or not path.strip():
                errors.append({"field": "patches.path", "code": "invalid", "index": index})
                continue
            if mode not in {"created", "updated", "deleted", "proposed_only"}:
                errors.append({"field": "patches.mode", "code": "invalid", "index": index})
                continue
            if not isinstance(patch_summary, str) or not patch_summary.strip():
                errors.append({"field": "patches.summary", "code": "invalid", "index": index})
                continue
            normalized_patches.append(
                {
                    "path": path.strip(),
                    "mode": mode,
                    "summary": patch_summary.strip(),
                }
            )

    if parsed.get("needs_agent_review") is not True:
        errors.append({"field": "needs_agent_review", "code": "required_true"})
    action_trace = parsed.get("action_trace")
    if action_trace is not None and not isinstance(action_trace, list):
        errors.append({"field": "action_trace", "code": "not_array"})

    if errors:
        return None, errors
    return {
        "status": status,
        "summary": summary.strip(),
        "changed_files": changed_files,
        "actions_used": actions_used,
        "tests_run": normalized_tests,
        "patches": normalized_patches,
        "risks": risks,
        "needs_agent_review": True,
        "action_trace": list(action_trace or []),
    }, []
