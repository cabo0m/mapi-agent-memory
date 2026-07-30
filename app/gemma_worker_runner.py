from __future__ import annotations

"""Staged Gemma Worker job runner."""

import json
from pathlib import Path
from typing import Any

from app import (
    gemma_worker_jobs,
    gemma_worker_policy,
    gemma_worker_reports,
    mapi_gemma_agent,
)

_PREPARE_PLAN_ALLOWED_STATUSES = {"created", "planned"}
_RUN_JOB_ALLOWED_STATUSES = {"approved"}
_REPO_SNAPSHOT_IGNORED_DIRS = {
    ".git",
    "node_modules",
    ".next",
    ".venv",
    "__pycache__",
    "dist",
    "build",
}
_REPO_SNAPSHOT_MAX_PATHS = 100
_GLOB_CHARS = {"*", "?", "["}
_FILE_READ_MAX_CHARS = 12000


def build_repo_snapshot(
    repo: str,
    *,
    max_paths: int = _REPO_SNAPSHOT_MAX_PATHS,
) -> dict[str, Any]:
    repo_root = gemma_worker_policy.validate_repo_root(repo)
    files: list[str] = []

    def add_file(path: Path) -> bool:
        relative = path.relative_to(repo_root).as_posix()
        files.append(relative)
        return len(files) >= max_paths

    for child in sorted(repo_root.iterdir(), key=lambda item: (item.is_file() is False, item.name.lower())):
        if child.name in _REPO_SNAPSHOT_IGNORED_DIRS:
            continue
        if child.is_file() and add_file(child):
            break
    if len(files) < max_paths:
        for child in sorted(repo_root.iterdir(), key=lambda item: item.name.lower()):
            if len(files) >= max_paths:
                break
            if not child.is_dir() or child.name in _REPO_SNAPSHOT_IGNORED_DIRS:
                continue
            for nested in sorted(child.iterdir(), key=lambda item: (item.is_file() is False, item.name.lower())):
                if nested.name in _REPO_SNAPSHOT_IGNORED_DIRS:
                    continue
                if nested.is_file() and add_file(nested):
                    break

    return {
        "repo": str(repo_root),
        "max_paths": max_paths,
        "ignored_dirs": sorted(_REPO_SNAPSHOT_IGNORED_DIRS),
        "files": files,
        "truncated": len(files) >= max_paths,
    }


def _looks_like_glob(target: str) -> bool:
    return any(char in target for char in _GLOB_CHARS)


def validate_plan_targets(
    repo: str,
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    repo_root = gemma_worker_policy.validate_repo_root(repo)
    errors: list[dict[str, Any]] = []
    for index, step in enumerate(plan.get("planned_steps") or []):
        target = step.get("target")
        if target is None:
            continue
        action = str(step.get("allowed_action") or "").strip()
        target_text = str(target).strip()
        if not target_text:
            continue
        if _looks_like_glob(target_text):
            try:
                candidate_path = gemma_worker_policy.validate_repo_path(
                    repo,
                    target_text,
                    allow_missing=True,
                )
            except gemma_worker_policy.GemmaWorkerPolicyError as exc:
                errors.append(
                    {
                        "field": "planned_steps.target",
                        "code": "invalid_path",
                        "index": index,
                        "target": target_text,
                        "message": str(exc),
                    }
                )
                continue
            matches = [path for path in repo_root.glob(target_text) if path.is_file()]
            if not matches:
                errors.append(
                    {
                        "field": "planned_steps.target",
                        "code": "glob_no_matches",
                        "index": index,
                        "target": target_text,
                    }
                )
            continue

        allow_missing = action == "propose_patch"
        try:
            resolved = gemma_worker_policy.validate_repo_path(
                repo,
                target_text,
                allow_missing=allow_missing,
            )
        except gemma_worker_policy.GemmaWorkerPolicyError as exc:
            errors.append(
                {
                    "field": "planned_steps.target",
                    "code": "invalid_path",
                    "index": index,
                    "target": target_text,
                    "message": str(exc),
                }
            )
            continue
        if not allow_missing and not resolved.exists():
            errors.append(
                {
                    "field": "planned_steps.target",
                    "code": "missing_path",
                    "index": index,
                    "target": target_text,
                }
            )
    return errors


def build_prepare_plan_prompt(
    job: dict[str, Any],
    policy: dict[str, Any],
    repo_snapshot: dict[str, Any],
) -> str:
    return (
        "Gemma Worker planning task for MAPI.\n"
        "You are in planning mode only.\n"
        "Do not modify files. Do not simulate execution. Do not invent completed work.\n"
        "Return exactly one raw JSON object matching the response schema.\n\n"
        f"TASK:\n{job['task']}\n\n"
        f"REPO_ROOT:\n{policy['repo_root']}\n\n"
        f"PROJECT_KEY:\n{job.get('project_key') or ''}\n\n"
        f"CONTEXT:\n{job.get('context') or ''}\n\n"
        f"ALLOWED_ACTIONS:\n{policy['allowed_actions']}\n\n"
        f"ACCEPTANCE_CRITERIA:\n{job.get('acceptance_criteria') or []}\n\n"
        f"REPO_SNAPSHOT:\n{json.dumps(repo_snapshot, ensure_ascii=False, sort_keys=True)}\n\n"
        "PLANNING_RULES:\n"
        "- use only allowed actions\n"
        "- use only file targets that exist in the repo snapshot unless you explicitly propose a new file via propose_patch\n"
        "- if a file target is uncertain, leave target as null\n"
        "- if you expect no file changes during planning, say so explicitly\n"
        "- approval is required before any execution step\n"
    )


def build_run_job_prompt(job: dict[str, Any], policy: dict[str, Any]) -> str:
    execution_context = json.dumps(
        job.get("execution_context") or {},
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        "Gemma Worker execution task for MAPI.\n"
        "You are in controlled execution mode.\n"
        "Return exactly one raw JSON object matching the response schema.\n"
        "Do not output chain-of-thought. Do not claim host-side actions you did not perform.\n"
        "On MVP prefer proposed patches over direct file mutation.\n\n"
        f"TASK:\n{job['task']}\n\n"
        f"REPO_ROOT:\n{policy['repo_root']}\n\n"
        f"PROJECT_KEY:\n{job.get('project_key') or ''}\n\n"
        f"CONTEXT:\n{job.get('context') or ''}\n\n"
        f"ALLOWED_ACTIONS:\n{policy['allowed_actions']}\n\n"
        f"ACCEPTANCE_CRITERIA:\n{job.get('acceptance_criteria') or []}\n\n"
        f"APPROVED_PLAN:\n{json.dumps(job.get('plan') or {}, ensure_ascii=False, sort_keys=True)}\n\n"
        f"EXECUTION_CONTEXT:\n{execution_context}\n\n"
        "EXECUTION_RULES:\n"
        "- use only allowed actions\n"
        "- if you describe a patch without host-side apply, use patch mode proposed_only\n"
        "- every changed file or patch path must stay inside repo root\n"
        "- include tests_run even if a test was skipped\n"
    )


def _read_file_excerpt(path: Path, *, limit: int = _FILE_READ_MAX_CHARS) -> tuple[str, bool]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= limit:
        return text, False
    omitted = len(text) - limit
    return text[:limit] + f"\n\n[TRUNCATED: omitted {omitted} chars]", True


def build_execution_context(
    job: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    repo_snapshot = build_repo_snapshot(job["repo"])
    action_trace: list[dict[str, Any]] = [
        {
            "action": "list_files",
            "target": policy["repo_root"],
            "status": "ok",
            "details": f"repo snapshot with {len(repo_snapshot['files'])} paths",
        }
    ]
    context: dict[str, Any] = {"repo_snapshot": repo_snapshot, "read_files": []}
    plan = job.get("plan") or {}
    repo_root = Path(policy["repo_root"])
    for step in plan.get("planned_steps") or []:
        action = str(step.get("allowed_action") or "").strip()
        target = step.get("target")
        if action != "read_file" or not isinstance(target, str) or not target.strip():
            continue
        try:
            resolved = gemma_worker_policy.validate_repo_path(
                job["repo"],
                target,
                allow_missing=False,
            )
        except gemma_worker_policy.GemmaWorkerPolicyError as exc:
            action_trace.append(
                {
                    "action": "read_file",
                    "target": str(target),
                    "status": "error",
                    "details": str(exc),
                }
            )
            continue
        excerpt, truncated = _read_file_excerpt(resolved)
        relative = resolved.relative_to(repo_root).as_posix()
        context["read_files"].append(
            {
                "path": relative,
                "truncated": truncated,
                "content_excerpt": excerpt,
            }
        )
        action_trace.append(
            {
                "action": "read_file",
                "target": relative,
                "status": "ok",
                "details": "loaded file excerpt for execution context",
            }
        )
    return context, action_trace


def prepare_gemma_worker_plan(
    conn: Any,
    *,
    job_id: int,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    ensure_loaded: bool = True,
    unload_after_call: bool | None = None,
) -> dict[str, Any]:
    job = gemma_worker_jobs.require_gemma_worker_job(conn, int(job_id))
    if job["status"] not in _PREPARE_PLAN_ALLOWED_STATUSES:
        return {
            "status": "error",
            "stage": "prepare_plan",
            "error": "job_not_plannable",
            "job": job,
        }

    try:
        policy = gemma_worker_policy.build_job_policy(
            job["repo"],
            list(job.get("allowed_actions") or []),
        )
    except gemma_worker_policy.GemmaWorkerPolicyError as exc:
        failed_job = gemma_worker_jobs.transition_gemma_worker_job_status(
            conn,
            int(job_id),
            next_status="failed",
            error=str(exc),
        )
        payload = gemma_worker_policy.policy_denied_payload(
            details=str(exc),
            stage="prepare_plan",
        )
        payload["job"] = failed_job
        return payload

    repo_snapshot = build_repo_snapshot(job["repo"])
    prompt = build_prepare_plan_prompt(job, policy, repo_snapshot)
    try:
        gemma_result = mapi_gemma_agent.gemma_ask_payload(
            prompt=prompt,
            system_prompt=(
                "You are Gemma Worker for MAPI. Planning mode only. "
                "Return exactly one raw JSON object matching the response schema."
            ),
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            ensure_loaded=ensure_loaded,
            unload_after_call=unload_after_call,
            response_format_json=json.dumps(
                gemma_worker_reports.gemma_worker_plan_response_format()
            ),
        )
    except Exception as exc:
        failed_job = gemma_worker_jobs.transition_gemma_worker_job_status(
            conn,
            int(job_id),
            next_status="failed",
            error=str(exc),
        )
        return {
            "status": "error",
            "stage": "prepare_plan",
            "error": "gemma_call_failed",
            "details": str(exc),
            "job": failed_job,
        }
    if gemma_result.get("status") != "ok":
        failed_job = gemma_worker_jobs.transition_gemma_worker_job_status(
            conn,
            int(job_id),
            next_status="failed",
            error=str(gemma_result.get("error") or "Gemma prepare_plan call failed"),
        )
        return {
            "status": "error",
            "stage": "prepare_plan",
            "error": "gemma_call_failed",
            "job": failed_job,
            "gemma_result": gemma_result,
        }

    plan, validation_errors = gemma_worker_reports.parse_gemma_worker_plan(
        str(gemma_result.get("answer") or "")
    )
    if plan is None:
        failed_job = gemma_worker_jobs.transition_gemma_worker_job_status(
            conn,
            int(job_id),
            next_status="failed",
            error=json.dumps(validation_errors, ensure_ascii=False, sort_keys=True),
        )
        return {
            "status": "error",
            "stage": "prepare_plan",
            "error": "invalid_plan_json",
            "validation_errors": validation_errors,
            "job": failed_job,
            "gemma_result": gemma_result,
        }

    validation_errors = list(validation_errors)
    for index, step in enumerate(plan.get("planned_steps") or []):
        allowed_action = str(step.get("allowed_action") or "").strip()
        if allowed_action and allowed_action not in set(policy["allowed_actions"]):
            validation_errors.append(
                {
                    "field": "planned_steps.allowed_action",
                    "code": "not_allowed",
                    "index": index,
                    "action": allowed_action,
                }
            )
    validation_errors.extend(validate_plan_targets(job["repo"], plan))
    if validation_errors:
        failed_job = gemma_worker_jobs.transition_gemma_worker_job_status(
            conn,
            int(job_id),
            next_status="failed",
            error=json.dumps(validation_errors, ensure_ascii=False, sort_keys=True),
        )
        return {
            "status": "error",
            "stage": "prepare_plan",
            "error": "invalid_plan_targets",
            "validation_errors": validation_errors,
            "job": failed_job,
            "gemma_result": gemma_result,
            "repo_snapshot": repo_snapshot,
        }

    updated_job = gemma_worker_jobs.transition_gemma_worker_job_status(
        conn,
        int(job_id),
        next_status="needs_approval",
        plan=plan,
        error=None,
    )
    return {
        "status": "ok",
        "stage": "prepare_plan",
        "job": updated_job,
        "plan": plan,
        "repo_snapshot": repo_snapshot,
        "gemma_result": {
            "status": gemma_result.get("status"),
            "model": gemma_result.get("model"),
            "mode": gemma_result.get("mode"),
        },
    }


def approve_gemma_worker_job(conn: Any, *, job_id: int) -> dict[str, Any]:
    try:
        job = gemma_worker_jobs.transition_gemma_worker_job_status(
            conn,
            int(job_id),
            next_status="approved",
            error=None,
        )
    except ValueError as exc:
        return {
            "status": "error",
            "stage": "approve_job",
            "error": "invalid_status_transition",
            "details": str(exc),
            "job": gemma_worker_jobs.require_gemma_worker_job(conn, int(job_id)),
        }
    return {"status": "ok", "stage": "approve_job", "job": job}


def reject_gemma_worker_job(
    conn: Any,
    *,
    job_id: int,
    reason: str | None = None,
) -> dict[str, Any]:
    try:
        job = gemma_worker_jobs.transition_gemma_worker_job_status(
            conn,
            int(job_id),
            next_status="rejected",
            error=reason,
        )
    except ValueError as exc:
        return {
            "status": "error",
            "stage": "reject_job",
            "error": "invalid_status_transition",
            "details": str(exc),
            "job": gemma_worker_jobs.require_gemma_worker_job(conn, int(job_id)),
        }
    return {"status": "ok", "stage": "reject_job", "job": job}


def cancel_gemma_worker_job(
    conn: Any,
    *,
    job_id: int,
    reason: str | None = None,
) -> dict[str, Any]:
    try:
        job = gemma_worker_jobs.transition_gemma_worker_job_status(
            conn,
            int(job_id),
            next_status="cancelled",
            error=reason,
        )
    except ValueError as exc:
        return {
            "status": "error",
            "stage": "cancel_job",
            "error": "invalid_status_transition",
            "details": str(exc),
            "job": gemma_worker_jobs.require_gemma_worker_job(conn, int(job_id)),
        }
    return {"status": "ok", "stage": "cancel_job", "job": job}


def run_gemma_worker_job(
    conn: Any,
    *,
    job_id: int,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    ensure_loaded: bool = True,
    unload_after_call: bool | None = None,
) -> dict[str, Any]:
    job = gemma_worker_jobs.require_gemma_worker_job(conn, int(job_id))
    if job["status"] not in _RUN_JOB_ALLOWED_STATUSES:
        return {
            "status": "error",
            "stage": "run_job",
            "error": "approval_required",
            "job": job,
        }
    try:
        policy = gemma_worker_policy.build_job_policy(
            job["repo"],
            list(job.get("allowed_actions") or []),
        )
    except gemma_worker_policy.GemmaWorkerPolicyError as exc:
        failed_job = gemma_worker_jobs.transition_gemma_worker_job_status(
            conn,
            int(job_id),
            next_status="failed",
            error=str(exc),
        )
        payload = gemma_worker_policy.policy_denied_payload(
            details=str(exc),
            stage="run_job",
        )
        payload["job"] = failed_job
        return payload

    execution_context, action_trace = build_execution_context(job, policy)
    running_job = gemma_worker_jobs.transition_gemma_worker_job_status(
        conn,
        int(job_id),
        next_status="running",
        result={"action_trace": action_trace},
    )
    running_job["execution_context"] = execution_context
    prompt = build_run_job_prompt(running_job, policy)
    try:
        gemma_result = mapi_gemma_agent.gemma_ask_payload(
            prompt=prompt,
            system_prompt=(
                "You are Gemma Worker for MAPI. Controlled execution mode. "
                "Return exactly one raw JSON object matching the response schema."
            ),
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            ensure_loaded=ensure_loaded,
            unload_after_call=unload_after_call,
            response_format_json=json.dumps(
                gemma_worker_reports.gemma_worker_execution_report_response_format()
            ),
        )
    except Exception as exc:
        failed_job = gemma_worker_jobs.transition_gemma_worker_job_status(
            conn,
            int(job_id),
            next_status="failed",
            error=str(exc),
        )
        return {
            "status": "error",
            "stage": "run_job",
            "error": "gemma_call_failed",
            "details": str(exc),
            "job": failed_job,
        }
    if gemma_result.get("status") != "ok":
        failed_job = gemma_worker_jobs.transition_gemma_worker_job_status(
            conn,
            int(job_id),
            next_status="failed",
            error=str(gemma_result.get("error") or "Gemma run_job call failed"),
        )
        return {
            "status": "error",
            "stage": "run_job",
            "error": "gemma_call_failed",
            "job": failed_job,
            "gemma_result": gemma_result,
        }

    report, validation_errors = gemma_worker_reports.parse_gemma_worker_execution_report(
        str(gemma_result.get("answer") or "")
    )
    if report is not None:
        allowed_action_set = set(policy["allowed_actions"])
        report["model_actions_used"] = list(report["actions_used"])
        for action in report["model_actions_used"]:
            if action not in allowed_action_set:
                validation_errors.append(
                    {
                        "field": "actions_used",
                        "code": "not_allowed",
                        "action": action,
                    }
                )
        for path in report["changed_files"]:
            try:
                gemma_worker_policy.validate_repo_path(
                    job["repo"],
                    path,
                    allow_missing=True,
                )
            except gemma_worker_policy.GemmaWorkerPolicyError as exc:
                validation_errors.append(
                    {
                        "field": "changed_files",
                        "code": "invalid_path",
                        "path": path,
                        "message": str(exc),
                    }
                )
        for patch in report["patches"]:
            try:
                gemma_worker_policy.validate_repo_path(
                    job["repo"],
                    patch["path"],
                    allow_missing=True,
                )
            except gemma_worker_policy.GemmaWorkerPolicyError as exc:
                validation_errors.append(
                    {
                        "field": "patches",
                        "code": "invalid_path",
                        "path": patch["path"],
                        "message": str(exc),
                    }
                )

    if report is None or validation_errors:
        failed_job = gemma_worker_jobs.transition_gemma_worker_job_status(
            conn,
            int(job_id),
            next_status="failed",
            error=json.dumps(validation_errors, ensure_ascii=False, sort_keys=True),
            result=report,
        )
        return {
            "status": "error",
            "stage": "run_job",
            "error": "invalid_execution_report",
            "validation_errors": validation_errors,
            "job": failed_job,
            "gemma_result": gemma_result,
        }

    report["action_trace"] = action_trace
    report["actions_used"] = sorted({entry["action"] for entry in action_trace})
    next_status = "completed" if report["status"] == "completed" else "failed"
    finished_job = gemma_worker_jobs.transition_gemma_worker_job_status(
        conn,
        int(job_id),
        next_status=next_status,
        result=report,
        error=None if next_status == "completed" else report["summary"],
    )
    return {
        "status": "ok" if next_status == "completed" else "error",
        "stage": "run_job",
        "job": finished_job,
        "report": report,
        "gemma_result": {
            "status": gemma_result.get("status"),
            "model": gemma_result.get("model"),
            "mode": gemma_result.get("mode"),
        },
    }
