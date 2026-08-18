from __future__ import annotations

import time
from typing import Any, Callable

from app.workshops.reporting import ReportBudget, normalize_timeout_budget_ms


OPERATIONS_OBSERVABILITY_SCHEMA = "mapi_operations_observability.v1"


def _runtime_view(value: dict[str, Any]) -> dict[str, Any]:
    runtime = dict(value.get("runtime") or {})
    repository = dict(value.get("repository") or {})
    return {
        "status": value.get("status"),
        "mutations_allowed": value.get("mutations_allowed"),
        "reason_codes": list(value.get("reason_codes") or []),
        "commit_sha": runtime.get("commit_sha"),
        "profile": runtime.get("profile"),
        "runtime_mode": runtime.get("runtime_mode"),
        "schema_tail": runtime.get("schema_tail"),
        "pid": runtime.get("pid"),
        "repository_dirty": repository.get("dirty"),
        "worktree_count": len(repository.get("worktrees") or []),
    }


def _transport_view(value: dict[str, Any]) -> dict[str, Any]:
    backpressure = dict(value.get("backpressure") or {})
    connection_reuse = dict(value.get("connection_reuse") or {})
    overload = dict(value.get("overload_contract") or {})
    return {
        "status": value.get("status"),
        "transport": value.get("transport"),
        "stateful_session": value.get("stateful_session"),
        "max_in_flight_posts": backpressure.get("max_in_flight_posts"),
        "keepalive_seconds": connection_reuse.get("server_keepalive_seconds", backpressure.get("keepalive_seconds")),
        "accepted_total": backpressure.get("accepted_total"),
        "rejected_total": backpressure.get("rejected_total"),
        "current_in_flight": backpressure.get("active_posts"),
        "max_observed_posts": backpressure.get("max_observed_posts"),
        "overload_status_code": overload.get("status_code"),
        "retry_after_seconds": overload.get("retry_after_seconds", backpressure.get("retry_after_seconds")),
    }


def _embedding_view(value: dict[str, Any]) -> dict[str, Any]:
    storage = dict(value.get("storage_coverage") or {})
    eligible = dict(value.get("retrieval_eligible_coverage") or {})
    return {
        "status": value.get("status"),
        "model": value.get("model"),
        "embedding_dim": value.get("embedding_dim"),
        "storage": {
            "total": storage.get("total", value.get("total_active_memories")),
            "with_embedding": storage.get("with_embedding", value.get("with_embedding")),
            "without_embedding": storage.get("without_embedding", value.get("without_embedding")),
            "coverage_pct": storage.get("coverage_pct", value.get("coverage_pct")),
        },
        "retrieval_eligible": {
            "total": eligible.get("total"),
            "with_embedding": eligible.get("with_embedding"),
            "without_embedding": eligible.get("without_embedding"),
            "coverage_pct": eligible.get("coverage_pct"),
        },
        "orphan_or_archived_embedding_rows": value.get("orphan_or_archived_embedding_rows"),
    }


def _retrieval_quality_view(value: dict[str, Any]) -> dict[str, Any]:
    cases_run = int(value.get("cases_run") or 0)
    passed = int(value.get("passed") or 0)
    failures = list(value.get("failures") or [])
    return {
        "status": value.get("status"),
        "cases_run": cases_run,
        "passed": passed,
        "pass_rate": round(passed / cases_run, 4) if cases_run else None,
        "warning_count": len(value.get("warnings") or []),
        "warnings": list(value.get("warnings") or []),
        "failure_count": len(failures),
        "failed_cases": [
            {
                "case_id": item.get("case_id"),
                "reasons": list(item.get("reasons") or []),
            }
            for item in failures
        ],
    }


def _graph_debt_view(value: dict[str, Any]) -> dict[str, Any]:
    summary = dict(value.get("summary") or {})
    remediation = dict(value.get("remediation") or {})
    return {
        "status": value.get("status"),
        "schema": value.get("schema"),
        "active_links_scanned": summary.get("active_links_scanned"),
        "trusted_count": summary.get("trusted_count"),
        "legacy_unverified_count": summary.get("legacy_unverified_count"),
        "invalid_count": summary.get("invalid_count"),
        "redundant_count": summary.get("redundant_count"),
        "canonical_truth_review_count": summary.get("canonical_truth_review_count"),
        "heuristic_association_review_count": summary.get("heuristic_association_review_count"),
        "priority_debt_count": summary.get("priority_debt_count"),
        "legacy_graph_debt_count": summary.get("legacy_graph_debt_count"),
        "debt_source_codes": [str(item.get("code")) for item in remediation.get("debt_sources") or []],
        "auto_apply_allowed": remediation.get("auto_apply_allowed"),
    }


def _provider_view(value: dict[str, Any]) -> dict[str, Any]:
    feature_flags = {}
    for key, item in dict(value.get("feature_flags") or {}).items():
        feature_flags[str(key)] = {
            "enabled": item.get("enabled"),
            "reason": item.get("reason"),
            "read_only_mode": item.get("read_only_mode"),
        }
    model_queue = dict(value.get("model_queue") or {})
    usage = dict(value.get("usage") or {})
    shadow_status_counts = dict(value.get("shadow_status_counts") or {})
    cost_reason = value.get("estimated_cost_reason_code")
    total_tokens = int(usage.get("total_tokens") or 0)
    provider_activity_count = sum(int(item or 0) for item in shadow_status_counts.values())
    if cost_reason:
        cost_observation_status = "unavailable"
    elif total_tokens > 0 or provider_activity_count > 0:
        cost_observation_status = "observed"
    else:
        cost_observation_status = "no_observed_provider_usage"
    return {
        "status": value.get("status"),
        "project_key": value.get("project_key"),
        "feature_flags": feature_flags,
        "latency": dict(value.get("latency") or {}),
        "usage": usage,
        "provider_activity_count": provider_activity_count,
        "estimated_cost_usd_total": value.get("estimated_cost_usd_total"),
        "estimated_cost_reason_code": cost_reason,
        "cost_observation_status": cost_observation_status,
        "provider_failure_categories": dict(value.get("provider_failure_categories") or {}),
        "last_provider_failure_timestamp": value.get("last_provider_failure_timestamp"),
        "abstain": dict(value.get("abstain") or {}),
        "model_queue": {
            "routed_proposal_count": model_queue.get("routed_proposal_count"),
            "operator_reviewed_count": model_queue.get("operator_reviewed_count"),
            "operator_approved_count": model_queue.get("operator_approved_count"),
            "operator_rejected_count": model_queue.get("operator_rejected_count"),
            "operator_acceptance_rate": model_queue.get("operator_acceptance_rate"),
        },
        "canary": dict(value.get("canary") or {}),
        "unsupported_metrics": list(value.get("unsupported_metrics") or []),
    }


def operations_observability_payload(
    *,
    project_key: str,
    timeout_budget_ms: int,
    include_debug: bool,
    get_runtime_readiness: Callable[..., dict[str, Any]],
    get_transport_status: Callable[[], dict[str, Any]],
    get_embedding_stats: Callable[[], dict[str, Any]],
    get_retrieval_qa: Callable[..., dict[str, Any]],
    get_provider_observability: Callable[..., dict[str, Any]],
    get_legacy_graph_audit: Callable[..., dict[str, Any]],
    monotonic: Callable[[], float] | None = None,
) -> dict[str, Any]:
    normalized_budget = normalize_timeout_budget_ms(timeout_budget_ms)
    budget = ReportBudget(normalized_budget, monotonic=monotonic or time.monotonic)
    data: dict[str, Any] = {}

    _, runtime = budget.run(
        "runtime",
        lambda: get_runtime_readiness(include_debug=bool(include_debug)),
        minimum_ms=40,
        reserve_ms=400,
    )
    if isinstance(runtime, dict):
        data["runtime"] = _runtime_view(runtime)

    _, transport = budget.run(
        "transport",
        get_transport_status,
        minimum_ms=5,
        reserve_ms=360,
    )
    if isinstance(transport, dict):
        data["transport"] = _transport_view(transport)

    _, embeddings = budget.run(
        "embeddings",
        get_embedding_stats,
        minimum_ms=20,
        reserve_ms=330,
    )
    if isinstance(embeddings, dict):
        data["embeddings"] = _embedding_view(embeddings)

    _, graph_debt = budget.run(
        "graph_debt",
        lambda: get_legacy_graph_audit(
            project_key=None,
            include_trusted=False,
            include_candidates=False,
            sample_limit=1,
        ),
        minimum_ms=20,
        reserve_ms=320,
    )
    if isinstance(graph_debt, dict):
        data["graph_debt"] = _graph_debt_view(graph_debt)

    _, provider = budget.run(
        "provider",
        lambda: get_provider_observability(
            project_key=project_key,
            limit=50,
            include_debug=bool(include_debug),
        ),
        minimum_ms=10,
        reserve_ms=300,
    )
    if isinstance(provider, dict):
        data["provider"] = _provider_view(provider)

    _, retrieval = budget.run(
        "retrieval_quality",
        lambda: get_retrieval_qa(project_keys=None, limit_per_case=5),
        minimum_ms=300,
        reserve_ms=0,
    )
    if isinstance(retrieval, dict):
        data["retrieval_quality"] = _retrieval_quality_view(retrieval)

    report = budget.summary()
    warnings: list[str] = []
    runtime_view = data.get("runtime") or {}
    embedding_view = data.get("embeddings") or {}
    retrieval_view = data.get("retrieval_quality") or {}
    provider_view = data.get("provider") or {}
    graph_debt_view = data.get("graph_debt") or {}

    if runtime_view and runtime_view.get("status") != "ready":
        warnings.append("runtime_not_ready")
    retrieval_coverage = (embedding_view.get("retrieval_eligible") or {}).get("coverage_pct")
    if retrieval_coverage is not None and float(retrieval_coverage) < 100.0:
        warnings.append("embedding_coverage_below_100")
    if retrieval_view and int(retrieval_view.get("failure_count") or 0) > 0:
        warnings.append("retrieval_qa_failures")
    if provider_view and dict(provider_view.get("provider_failure_categories") or {}):
        warnings.append("provider_failures_observed")
    if provider_view and int((provider_view.get("model_queue") or {}).get("operator_rejected_count") or 0) > 0:
        warnings.append("provider_proposals_rejected_by_operator")
    if graph_debt_view and int(graph_debt_view.get("invalid_count") or 0) > 0:
        warnings.append("legacy_graph_invalid_edges")
    if graph_debt_view and int(graph_debt_view.get("redundant_count") or 0) > 0:
        warnings.append("legacy_graph_redundant_edges")

    health_status = "attention" if warnings else "healthy"
    top_status = "partial" if report.get("partial") else ("attention" if warnings else "ok")
    payload: dict[str, Any] = {
        "status": top_status,
        "schema": OPERATIONS_OBSERVABILITY_SCHEMA,
        "project_key": project_key,
        "report": report,
        "health": {
            "status": health_status,
            "warnings": warnings,
        },
        "cost_scope": "sandman_provider_observability_only",
        "sections": data,
        "safety": {
            "read_only": True,
            "mutations_performed": 0,
            "model_calls_performed": 0,
            "raw_memory_content_exposed": False,
            "raw_secrets_exposed": False,
        },
    }
    if include_debug:
        payload["debug"] = {
            "section_order": ["runtime", "transport", "embeddings", "graph_debt", "provider", "retrieval_quality"],
            "retrieval_qa_scope": "frozen_global_smoke_cases",
        }
    return payload
