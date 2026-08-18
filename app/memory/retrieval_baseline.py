from __future__ import annotations

"""Deterministic read-only retrieval baseline helpers for MAPI."""

from dataclasses import dataclass
import hashlib
import json
import statistics
import time
from typing import Any, Callable, Iterable


RETRIEVAL_BASELINE_SCHEMA = "mapi_retrieval_baseline.v1"
RETRIEVAL_ELIGIBLE_STATE_CODES = ("active", "validated")
RETRIEVAL_ELIGIBLE_V2_STATUS = "active"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _pct(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100.0, 1) if denominator else 0.0


def collect_embedding_coverage(conn: Any) -> dict[str, Any]:
    """Return storage coverage and the narrower retrieval-eligible coverage.

    Storage coverage intentionally preserves the historical metric used by MAPI:
    every non-archived memory counts. Retrieval-eligible coverage excludes review
    and superseded lifecycle states so the two concepts are no longer conflated.
    """
    storage = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN em.memory_id IS NOT NULL THEN 1 ELSE 0 END) AS with_embedding
        FROM memories m
        LEFT JOIN memory_embeddings_meta em ON em.memory_id = m.id
        WHERE m.archived_at IS NULL
        """
    ).fetchone()
    eligible = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN em.memory_id IS NOT NULL THEN 1 ELSE 0 END) AS with_embedding
        FROM memories m
        LEFT JOIN memory_embeddings_meta em ON em.memory_id = m.id
        WHERE m.archived_at IS NULL
          AND COALESCE(m.state_code, 'active') IN ('active', 'validated')
          AND COALESCE(m.memory_v2_status, 'active') = 'active'
        """
    ).fetchone()
    lifecycle_rows = conn.execute(
        """
        SELECT
            COALESCE(m.state_code, '<null>') AS state_code,
            COALESCE(m.memory_v2_status, '<null>') AS memory_v2_status,
            COUNT(*) AS total,
            SUM(CASE WHEN em.memory_id IS NULL THEN 1 ELSE 0 END) AS without_embedding
        FROM memories m
        LEFT JOIN memory_embeddings_meta em ON em.memory_id = m.id
        WHERE m.archived_at IS NULL
        GROUP BY COALESCE(m.state_code, '<null>'), COALESCE(m.memory_v2_status, '<null>')
        ORDER BY total DESC, state_code, memory_v2_status
        """
    ).fetchall()
    orphan_or_archived = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM memory_embeddings_meta em
            LEFT JOIN memories m ON m.id = em.memory_id
            WHERE m.id IS NULL OR m.archived_at IS NOT NULL
            """
        ).fetchone()[0]
    )

    storage_total = int(storage[0] or 0)
    storage_with = int(storage[1] or 0)
    eligible_total = int(eligible[0] or 0)
    eligible_with = int(eligible[1] or 0)
    return {
        "storage_coverage": {
            "definition": "memories.archived_at IS NULL",
            "total": storage_total,
            "with_embedding": storage_with,
            "without_embedding": storage_total - storage_with,
            "coverage_pct": _pct(storage_with, storage_total),
        },
        "retrieval_eligible_coverage": {
            "definition": "non-archived AND state_code in active|validated AND memory_v2_status=active",
            "total": eligible_total,
            "with_embedding": eligible_with,
            "without_embedding": eligible_total - eligible_with,
            "coverage_pct": _pct(eligible_with, eligible_total),
        },
        "lifecycle_breakdown": [
            {
                "state_code": str(row[0]),
                "memory_v2_status": str(row[1]),
                "total": int(row[2]),
                "without_embedding": int(row[3] or 0),
            }
            for row in lifecycle_rows
        ],
        "orphan_or_archived_embedding_rows": orphan_or_archived,
    }


def _latency_summary(samples_ms: list[float]) -> dict[str, Any]:
    ordered = sorted(samples_ms)
    if not ordered:
        return {"runs": 0, "min_ms": None, "median_ms": None, "max_ms": None, "samples_ms": []}
    return {
        "runs": len(ordered),
        "min_ms": round(ordered[0], 3),
        "median_ms": round(statistics.median(ordered), 3),
        "max_ms": round(ordered[-1], 3),
        "samples_ms": [round(value, 3) for value in samples_ms],
    }


def _call_timed(func: Callable[[], dict[str, Any]], runs: int) -> tuple[dict[str, Any], dict[str, Any]]:
    result: dict[str, Any] = {}
    samples: list[float] = []
    for _ in range(max(1, int(runs))):
        started = time.perf_counter()
        result = func()
        samples.append((time.perf_counter() - started) * 1000.0)
    return result, _latency_summary(samples)


def _ids_from_lexical(result: dict[str, Any]) -> list[int]:
    return [int(item["id"]) for item in result.get("items", []) if item.get("id") is not None]


def _ids_from_semantic(result: dict[str, Any]) -> list[int]:
    return [int(item["memory_id"]) for item in result.get("results", []) if item.get("memory_id") is not None]


def _channel_verdict(
    *,
    returned_ids: list[int],
    expected_ids: set[int],
    forbidden_ids: set[int],
    returned_project_keys: Iterable[str | None],
    expected_project_key: str | None,
    expect_empty: bool = False,
) -> dict[str, Any]:
    returned = set(returned_ids)
    missing_expected = sorted(expected_ids - returned)
    forbidden_returned = sorted(forbidden_ids & returned)
    unexpected_ids = sorted(returned) if expect_empty else []
    wrong_project = sorted({
        str(value)
        for value in returned_project_keys
        if expected_project_key is not None and value != expected_project_key
    })
    return {
        "passed": not missing_expected and not forbidden_returned and not unexpected_ids and not wrong_project,
        "returned_ids": returned_ids,
        "missing_expected_ids": missing_expected,
        "forbidden_returned_ids": forbidden_returned,
        "unexpected_ids": unexpected_ids,
        "wrong_project_keys": wrong_project,
    }


def evaluate_golden_cases(
    cases: list[dict[str, Any]],
    *,
    lexical_search: Callable[..., dict[str, Any]],
    semantic_search: Callable[..., dict[str, Any]],
    latency_runs: int = 3,
) -> dict[str, Any]:
    evaluated: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case["case_id"])
        query = str(case["query"])
        project_key = case.get("project_key")
        expected_project_key = case.get("expected_project_key", project_key)
        expected_ids = {int(value) for value in case.get("expected_ids", [])}
        forbidden_ids = {int(value) for value in case.get("forbidden_ids", [])}
        expect_empty = bool(case.get("expect_empty", False))
        top_k = int(case.get("top_k", 5))
        channels = set(case.get("channels", ["lexical", "semantic"]))
        channel_results: dict[str, Any] = {}

        if "lexical" in channels:
            lexical, latency = _call_timed(
                lambda: lexical_search(
                    text_query=query,
                    project_key=project_key,
                    project_key_mode="aliases",
                    limit=top_k,
                    include_history=False,
                    debug=True,
                ),
                latency_runs,
            )
            lexical_items = list(lexical.get("items", []))
            channel_results["lexical"] = {
                **_channel_verdict(
                    returned_ids=_ids_from_lexical(lexical),
                    expected_ids=expected_ids,
                    forbidden_ids=forbidden_ids,
                    returned_project_keys=[item.get("project_key") for item in lexical_items],
                    expected_project_key=expected_project_key,
                    expect_empty=expect_empty,
                ),
                "latency": latency,
                "count": int(lexical.get("count", len(lexical_items))),
                "response_characters": len(_canonical_json(lexical)),
                "retrieval_strategy": list((lexical.get("debug") or {}).get("retrieval_strategy", [])),
            }

        if "semantic" in channels:
            semantic, latency = _call_timed(
                lambda: semantic_search(query=query, project_key=project_key, top_k=top_k),
                latency_runs,
            )
            semantic_items = list(semantic.get("results", []))
            channel_results["semantic"] = {
                **_channel_verdict(
                    returned_ids=_ids_from_semantic(semantic),
                    expected_ids=expected_ids,
                    forbidden_ids=forbidden_ids,
                    returned_project_keys=[item.get("project_key") for item in semantic_items],
                    expected_project_key=expected_project_key,
                    expect_empty=expect_empty,
                ),
                "latency": latency,
                "count": int(semantic.get("results_count", len(semantic_items))),
                "response_characters": len(_canonical_json(semantic)),
                "status": semantic.get("status"),
            }

        case_passed = all(bool(value.get("passed")) for value in channel_results.values())
        evaluated.append({
            "case_id": case_id,
            "purpose": case.get("purpose"),
            "query": query,
            "project_key": project_key,
            "expected_ids": sorted(expected_ids),
            "forbidden_ids": sorted(forbidden_ids),
            "channels": channel_results,
            "passed": case_passed,
        })

    return {
        "case_count": len(evaluated),
        "passed_count": sum(1 for item in evaluated if item["passed"]),
        "failed_count": sum(1 for item in evaluated if not item["passed"]),
        "all_passed": all(item["passed"] for item in evaluated),
        "cases": evaluated,
    }
