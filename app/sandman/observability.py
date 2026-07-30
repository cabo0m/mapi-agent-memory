from __future__ import annotations

import math
from collections import Counter
from typing import Any, Mapping

from app.sandman import routing, shadow_repository


PROVIDER_OBSERVABILITY_SCHEMA_VERSION = "sandman_provider_observability.v1"


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def provider_observability_payload(
    conn: Any,
    *,
    project_key: str,
    limit: int,
    include_debug: bool,
    flag_evaluations: Mapping[str, Any],
) -> dict[str, Any]:
    if project_key not in routing.ALLOWED_PROJECT_KEYS:
        return {
            "schema_version": PROVIDER_OBSERVABILITY_SCHEMA_VERSION,
            "status": "project_not_supported",
            "reason_codes": ["project_not_allowlisted"],
            "safety": {"read_only": True, "raw_content_exposed": False},
        }
    if not 1 <= int(limit) <= 200:
        return {
            "schema_version": PROVIDER_OBSERVABILITY_SCHEMA_VERSION,
            "status": "invalid_limit",
            "reason_codes": ["limit_out_of_range"],
            "safety": {"read_only": True, "raw_content_exposed": False},
        }
    runs = shadow_repository.list_runs(
        conn, project_key=project_key, limit=int(limit)
    )
    status_counts = Counter(str(item["status"]) for item in runs)
    validation_counts = Counter(
        str(item["validation_status"] or "unavailable") for item in runs
    )
    failure_counts = Counter(
        str(item["error_category"])
        for item in runs
        if item.get("error_category")
    )
    proposal_counts: Counter[str] = Counter()
    latencies: list[float] = []
    token_totals = {"input": 0, "output": 0, "total": 0}
    token_available = {"input": True, "output": True, "total": True}
    cost_total = 0.0
    cost_available = True
    abstain_count = 0
    completed_with_abstain = 0
    last_failure = None
    pre_network_skipped_count = 0
    pre_network_skip_reasons: Counter[str] = Counter()
    deduped_existing_count = 0
    for item in runs:
        proposal_counts.update(item.get("proposal_counts") or {})
        if item.get("latency_ms") is not None:
            latencies.append(float(item["latency_ms"]))
        for source, target in (
            ("input_tokens", "input"),
            ("output_tokens", "output"),
            ("total_tokens", "total"),
        ):
            if item.get(source) is None:
                token_available[target] = False
            else:
                token_totals[target] += int(item[source])
        if item.get("estimated_cost_usd") is None:
            cost_available = False
        else:
            cost_total += float(item["estimated_cost_usd"])
        if item.get("abstain") is not None:
            completed_with_abstain += 1
            abstain_count += int(bool(item["abstain"]))
        if item.get("error_category") and (
            last_failure is None
            or str(item.get("completed_at") or item.get("updated_at") or "")
            > last_failure
        ):
            last_failure = str(
                item.get("completed_at") or item.get("updated_at") or ""
            )
        metadata = item.get("provider_metadata") or {}
        if isinstance(metadata, Mapping) and metadata.get("execution_mode") == "route_canary":
            pre_network_skipped_count += int(
                metadata.get("pre_network_skipped_count") or 0
            )
            pre_network_skip_reasons.update(
                metadata.get("pre_network_skip_reason_codes") or []
            )
            deduped_existing_count += int(
                metadata.get("deduped_against_existing_model_queue_count") or 0
            )

    origin_findings: list[dict[str, Any]] = []
    model_rows = conn.execute(
        """
        SELECT m.id, m.source_context, m.created_at,
               COALESCE(r.status, 'pending') AS review_status
        FROM memories m
        LEFT JOIN memory_consolidation_review_items r
          ON r.proposal_memory_id=m.id
        WHERE m.project_key=?
          AND m.memory_type='consolidation_proposal'
          AND m.source LIKE 'sandman_v3:gemini:queue_route:%'
        ORDER BY m.id ASC
        """,
        (project_key,),
    ).fetchall()
    queue_status_counts: Counter[str] = Counter()
    queue_type_counts: Counter[str] = Counter()
    queue_action_counts: Counter[str] = Counter()
    valid_model_count = 0
    last_routed_at = None
    for row in model_rows:
        try:
            origin = routing.parse_model_origin(str(row["source_context"] or ""))
        except routing.RoutingError as exc:
            origin_findings.append(
                {
                    "proposal_memory_id": int(row["id"]),
                    "integrity_code": exc.reason_codes[0],
                }
            )
            continue
        valid_model_count += 1
        queue_status_counts[str(row["review_status"])] += 1
        action = _action_from_origin(conn, int(row["id"]))
        if action:
            queue_action_counts[action] += 1
            queue_type_counts[routing.PROPOSAL_TYPES.get(action, "unknown")] += 1
        created_at = str(row["created_at"] or "")
        if last_routed_at is None or created_at > last_routed_at:
            last_routed_at = created_at

    approved = queue_status_counts["approved"]
    rejected = queue_status_counts["rejected"]
    reviewed = approved + rejected
    canary_state = routing.canary_state(conn, project_key=project_key)
    canary = {
        "current": canary_state["physical_routed_count"],
        "physical_routed_count": canary_state["physical_routed_count"],
        "valid_origin_count": canary_state["valid_origin_count"],
        "invalid_origin_count": canary_state["invalid_origin_count"],
        "missing_review_count": canary_state["missing_review_count"],
        "duplicate_queue_key_count": canary_state["duplicate_queue_key_count"],
        "integrity_status": canary_state["integrity_status"],
        "integrity_reason_codes": canary_state["integrity_reason_codes"],
        "remaining": canary_state["remaining_canary_budget"],
        "paused": canary_state["paused"],
        "max_per_run": routing.MAX_ROUTED_PROPOSALS_PER_RUN,
        "total_cap": routing.MAX_TOTAL_CANARY_PROPOSALS,
    }
    unsupported_metrics = [
        "historical_deterministic_dedupe_count_not_persisted"
    ]
    result = {
        "schema_version": PROVIDER_OBSERVABILITY_SCHEMA_VERSION,
        "status": "ok",
        "project_key": project_key,
        "feature_flags": dict(flag_evaluations),
        "shadow_status_counts": dict(sorted(status_counts.items())),
        "validation_status_counts": dict(sorted(validation_counts.items())),
        "provider_failure_categories": dict(sorted(failure_counts.items())),
        "abstain": {
            "count": abstain_count,
            "rate": (
                abstain_count / completed_with_abstain
                if completed_with_abstain
                else None
            ),
            "reason_code": (
                None if completed_with_abstain else "abstain_denominator_zero"
            ),
        },
        "proposal_counts_by_action": dict(sorted(proposal_counts.items())),
        "latency": {
            "p50_ms": _nearest_rank(latencies, 0.50),
            "p95_ms": _nearest_rank(latencies, 0.95),
            "percentile_method": "nearest_rank",
            "reason_code": None if latencies else "latency_unavailable",
        },
        "usage": {
            "input_tokens": token_totals["input"]
            if token_available["input"]
            else None,
            "output_tokens": token_totals["output"]
            if token_available["output"]
            else None,
            "total_tokens": token_totals["total"]
            if token_available["total"]
            else None,
            "reason_codes": [
                f"{key}_tokens_unavailable"
                for key, available in token_available.items()
                if not available
            ],
        },
        "estimated_cost_usd_total": cost_total if cost_available else None,
        "estimated_cost_reason_code": (
            None if cost_available else "cost_metadata_unavailable"
        ),
        "model_queue": {
            "routed_proposal_count": valid_model_count,
            "status_counts": dict(sorted(queue_status_counts.items())),
            "proposal_type_counts": dict(sorted(queue_type_counts.items())),
            "action_counts": dict(sorted(queue_action_counts.items())),
            "operator_reviewed_count": reviewed,
            "operator_approved_count": approved,
            "operator_rejected_count": rejected,
            "operator_acceptance_rate": approved / reviewed if reviewed else None,
            "operator_acceptance_reason_code": (
                None if reviewed else "operator_acceptance_denominator_zero"
            ),
        },
        "canary": canary,
        "routing_safety": {
            "routing_policy_version": routing.ROUTING_POLICY_VERSION,
            "route_preview_schema_version": routing.ROUTE_PREVIEW_SCHEMA_VERSION,
            "route_result_schema_version": routing.ROUTE_RESULT_SCHEMA_VERSION,
            "pre_network_skipped_count": pre_network_skipped_count,
            "pre_network_skip_reason_counts": dict(
                sorted(pre_network_skip_reasons.items())
            ),
            "deduped_against_existing_model_queue_count": deduped_existing_count,
        },
        "last_routed_proposal_timestamp": last_routed_at,
        "last_provider_failure_timestamp": last_failure,
        "origin_metadata_integrity": {
            "finding_count": len(origin_findings),
            "findings": origin_findings[: int(limit)],
        },
        "unsupported_metrics": unsupported_metrics,
        "safety": {
            "read_only": True,
            "candidate_content_exposed": False,
            "proposal_content_exposed": False,
            "raw_origin_metadata_exposed": False,
            "raw_secret_exposed": False,
        },
    }
    if include_debug:
        result["debug"] = {
            "run_sample_size": len(runs),
            "model_origin_row_count": len(model_rows),
        }
    return result


def _action_from_origin(conn: Any, proposal_memory_id: int) -> str | None:
    row = conn.execute(
        "SELECT content FROM memories WHERE id=?", (int(proposal_memory_id),)
    ).fetchone()
    if row is None:
        return None
    for line in str(row["content"] or "").splitlines():
        if line.startswith("Relacja modelu:"):
            action = line.split(":", 1)[1].strip()
            return action if action in routing.ROUTABLE_ACTIONS else None
    return None
