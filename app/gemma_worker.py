from __future__ import annotations

"""Gemma Worker payloads for MAPI-supervised local work."""

import json
from dataclasses import dataclass
from typing import Any, Literal

GemmaWorkerStatus = Literal["planned", "done", "blocked", "failed"]

DEFAULT_ACTIONS = (
    "bootstrap_agent_context",
    "find_memories",
    "get_memory",
    "read_file_text",
    "search_text",
    "replace_once",
    "run_pytest",
    "git_status",
    "create_memory",
)


_WORKER_REPORT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["final_report", "status", "requested_actions", "risks", "needs_agent_review"],
    "properties": {
        "final_report": {"type": "string"},
        "status": {"type": "string", "enum": ["done", "blocked", "failed"]},
        "requested_actions": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "needs_agent_review": {"type": "boolean", "enum": [True]},
    },
}


def _worker_report_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "gemma_worker_report",
            "strict": False,
            "schema": _WORKER_REPORT_RESPONSE_SCHEMA,
        },
    }


def _string_list_field(value: Any, *, field: str, errors: list[dict[str, Any]]) -> list[str]:
    if not isinstance(value, list):
        errors.append({"field": field, "code": "not_array"})
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append({"field": field, "code": "item_not_string", "index": index})
            continue
        result.append(item)
    return result


def _parse_worker_json_report(value: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    text = (value or "").strip()
    errors: list[dict[str, Any]] = []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None, [{"field": "gemma_json", "code": "invalid_json", "message": str(exc)}]
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as inner_exc:
            return None, [{"field": "gemma_json", "code": "invalid_json", "message": str(inner_exc)}]
    if not isinstance(parsed, dict):
        return None, [{"field": "gemma_json", "code": "not_object"}]
    status = parsed.get("status")
    if status not in {"done", "blocked", "failed"}:
        errors.append({"field": "status", "code": "invalid", "allowed": ["done", "blocked", "failed"]})
    final_report = parsed.get("final_report")
    if not isinstance(final_report, str) or not final_report.strip():
        errors.append({"field": "final_report", "code": "missing"})
    requested_actions = _string_list_field(parsed.get("requested_actions"), field="requested_actions", errors=errors)
    risks = _string_list_field(parsed.get("risks"), field="risks", errors=errors)
    if parsed.get("needs_agent_review") is not True:
        errors.append({"field": "needs_agent_review", "code": "required_true"})
    if errors:
        return None, errors
    return {
        "status": status,
        "final_report": final_report.strip(),
        "requested_actions": requested_actions,
        "risks": risks,
        "needs_agent_review": True,
    }, []


@dataclass(frozen=True)
class GemmaWorkerTask:
    task: str
    repo: str
    allowed_actions: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    context: str | None = None
    supervisor: str = "agent"

    def validate(self) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        if not self.task.strip():
            errors.append({"field": "task", "code": "missing"})
        if not self.repo.strip():
            errors.append({"field": "repo", "code": "missing"})
        if not self.acceptance_criteria:
            errors.append({"field": "acceptance_criteria", "code": "missing"})
        unknown = sorted(set(self.allowed_actions) - set(DEFAULT_ACTIONS))
        for action in unknown:
            errors.append({"field": "allowed_actions", "code": "unknown_action", "action": action})
        return errors


@dataclass(frozen=True)
class GemmaWorkerReport:
    status: GemmaWorkerStatus
    summary: str
    changed_files: tuple[str, ...] = ()
    actions_used: tuple[str, ...] = ()
    tests_run: tuple[str, ...] = ()
    needs_agent_review: bool = True

    def validate(self) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        if self.status == "done" and not self.summary.strip():
            errors.append({"field": "summary", "code": "missing"})
        if not self.needs_agent_review:
            errors.append({"field": "needs_agent_review", "code": "required"})
        unknown = sorted(set(self.actions_used) - set(DEFAULT_ACTIONS))
        for action in unknown:
            errors.append({"field": "actions_used", "code": "unknown_action", "action": action})
        return errors


def gemma_worker_status_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "mode": "gemma_worker",
        "role": "local_worker_for_agent",
        "supervisor": "agent",
        "default_actions": list(DEFAULT_ACTIONS),
        "requires_report": True,
        "requires_agent_review": True,
    }


def gemma_worker_prepare_task_payload(
    *,
    task: str,
    repo: str,
    allowed_actions_json: str | None = None,
    acceptance_criteria_json: str | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    allowed_actions = tuple(json.loads(allowed_actions_json or "[]"))
    acceptance_criteria = tuple(json.loads(acceptance_criteria_json or "[]"))
    worker_task = GemmaWorkerTask(
        task=task,
        repo=repo,
        allowed_actions=allowed_actions,
        acceptance_criteria=acceptance_criteria,
        context=context,
    )
    errors = worker_task.validate()
    return {
        "status": "error" if errors else "ok",
        "mode": "gemma_worker",
        "task": worker_task.task,
        "repo": worker_task.repo,
        "allowed_actions": list(worker_task.allowed_actions),
        "acceptance_criteria": list(worker_task.acceptance_criteria),
        "supervisor": worker_task.supervisor,
        "validation_errors": errors,
    }


def gemma_worker_report_payload(
    *,
    status: str,
    summary: str,
    changed_files_json: str | None = None,
    actions_used_json: str | None = None,
    tests_run_json: str | None = None,
    needs_agent_review: bool = True,
) -> dict[str, Any]:
    report = GemmaWorkerReport(
        status=status,  # type: ignore[arg-type]
        summary=summary,
        changed_files=tuple(json.loads(changed_files_json or "[]")),
        actions_used=tuple(json.loads(actions_used_json or "[]")),
        tests_run=tuple(json.loads(tests_run_json or "[]")),
        needs_agent_review=needs_agent_review,
    )
    errors = report.validate()
    return {
        "status": "error" if errors else "ok",
        "mode": "gemma_worker",
        "worker_status": report.status,
        "summary": report.summary,
        "changed_files": list(report.changed_files),
        "actions_used": list(report.actions_used),
        "tests_run": list(report.tests_run),
        "needs_agent_review": report.needs_agent_review,
        "validation_errors": errors,
    }





def _final_report_text(value: str) -> str:
    text = (value or "").strip()
    if text.startswith("{"):
        parsed_report, json_errors = _parse_worker_json_report(text)
        if parsed_report is not None:
            return str(parsed_report["final_report"])
        return "Gemma Worker returned invalid JSON report."
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("FINAL_REPORT:"):
            return line.split("FINAL_REPORT:", 1)[1].strip() or "Gemma Worker returned empty final report."
    for marker in ("Final answer:", "Final:", "Raport:", "Report:"):
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith(marker):
                return line.split(marker, 1)[1].strip() or "Gemma Worker returned empty final report."
    return "Gemma Worker returned no clean FINAL_REPORT line."


def gemma_worker_run_task_payload(
    *,
    task: str,
    repo: str,
    allowed_actions_json: str | None = None,
    acceptance_criteria_json: str | None = None,
    context: str | None = None,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    ensure_loaded: bool = True,
    unload_after_call: bool | None = None,
) -> dict[str, Any]:
    prepared = gemma_worker_prepare_task_payload(
        task=task,
        repo=repo,
        allowed_actions_json=allowed_actions_json,
        acceptance_criteria_json=acceptance_criteria_json,
        context=context,
    )
    if prepared.get("status") != "ok":
        return {"status": "error", "mode": "gemma_worker", "stage": "prepare_task", "prepared": prepared}

    from app import mapi_gemma_agent

    prompt = (
        "Gemma Worker task for MAPI. Return exactly one raw JSON object matching the response schema. Do not include reasoning, thinking process, analysis steps, or scratchpad.\n\n"
        f"TASK:\n{task}\n\n"
        f"REPO:\n{repo}\n\n"
        f"ALLOWED_ACTIONS:\n{prepared.get('allowed_actions', [])}\n\n"
        f"ACCEPTANCE_CRITERIA:\n{prepared.get('acceptance_criteria', [])}\n\n"
        f"CONTEXT:\n{context or ''}\n"
    )
    gemma_result = mapi_gemma_agent.gemma_ask_payload(
        prompt=prompt,
        system_prompt="You are Gemma Worker for MAPI. Return exactly one raw JSON object matching the response schema.",
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        ensure_loaded=ensure_loaded,
        unload_after_call=unload_after_call,
        response_format_json=json.dumps(_worker_report_response_format()),
    )
    parsed_worker_report = None
    json_validation_errors: list[dict[str, Any]] = []
    if gemma_result.get("status") == "ok":
        parsed_worker_report, json_validation_errors = _parse_worker_json_report(str(gemma_result.get("answer") or ""))
    worker_status = str(parsed_worker_report["status"]) if parsed_worker_report else "failed"
    report = gemma_worker_report_payload(
        status=worker_status,
        summary=_final_report_text(str(gemma_result.get("answer") or gemma_result.get("error") or "No Gemma text returned.")),
        changed_files_json="[]",
        actions_used_json="[]",
        tests_run_json="[]",
        needs_agent_review=True,
    )
    if json_validation_errors:
        report["status"] = "error"
        report["validation_errors"] = list(report.get("validation_errors") or []) + json_validation_errors
    if parsed_worker_report is not None:
        allowed_action_set = set(prepared.get("allowed_actions") or [])
        unknown_requested_actions = sorted(set(parsed_worker_report["requested_actions"]) - allowed_action_set)
        for action in unknown_requested_actions:
            report["validation_errors"].append({
                "field": "requested_actions",
                "code": "not_allowed",
                "action": action,
            })
        if unknown_requested_actions:
            report["status"] = "error"
        report["requested_actions"] = parsed_worker_report["requested_actions"]
        report["risks"] = parsed_worker_report["risks"]
        report["json_contract"] = "gemma_worker_report_v1"
    return {"status": report.get("status"), "mode": "gemma_worker", "prepared": prepared, "gemma_result": gemma_result, "report": report}
