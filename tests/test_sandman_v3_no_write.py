from __future__ import annotations

import json
import socket
import urllib.request

from app import lm_studio_client, sandman_gemma_client
from app.sandman.router import preview_deterministic_provider_payload, preview_provider_request_payload, provider_status_payload
from tests.sandman_v3_helpers import flag_parts, insert_memory, make_conn


TABLES = (
    "memories", "memory_links", "memory_events", "sleep_runs", "sleep_run_actions",
    "timeline_events", "memory_capture_review_items", "memory_retention_review_items",
    "memory_consolidation_review_items", "feature_flags",
)


def counts(conn):
    return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in TABLES}


def test_status_request_and_deterministic_preview_make_no_calls_or_writes(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("network or model call attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(lm_studio_client, "call_lm_studio", forbidden)
    monkeypatch.setattr(sandman_gemma_client.LocalGemmaClient, "complete_json", forbidden)
    monkeypatch.setattr(sandman_gemma_client.ManagedLmsGemmaClient, "complete_json", forbidden)

    conn = make_conn()
    ids = [insert_memory(conn, "same"), insert_memory(conn, "same")]
    conn.commit(); before = counts(conn)
    flag, evaluation = flag_parts()
    provider_status_payload(feature_flag=flag, feature_flag_evaluation=evaluation)
    common = dict(
        project_key="p", scope_code="project", memory_ids_json=json.dumps(ids),
        allowed_actions_json='["duplicate_of"]', provider_name="deterministic",
        proposal_budget=8, include_debug=False, feature_flag=flag,
        feature_flag_evaluation=evaluation, request_id_factory=lambda: "no-write",
    )
    preview_provider_request_payload(conn, **common)
    preview_deterministic_provider_payload(conn, **common)
    assert counts(conn) == before
