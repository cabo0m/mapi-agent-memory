from __future__ import annotations

import json

from app.sandman.router import preview_deterministic_provider_payload, preview_provider_request_payload, provider_status_payload
from tests.sandman_v3_helpers import add_link, flag_parts, insert_memory, make_conn


def kwargs(conn, ids, **values):
    flag, evaluation = flag_parts()
    base = dict(
        project_key="p", scope_code="project", memory_ids_json=json.dumps(ids),
        allowed_actions_json=json.dumps(["duplicate_of", "supersedes", "contradicts", "related_to"]),
        provider_name="deterministic", proposal_budget=8, include_debug=False,
        feature_flag=flag, feature_flag_evaluation=evaluation,
        request_id_factory=lambda: "fixture-request",
    )
    base.update(values); return base


def test_missing_off_and_enabled_flag_semantics() -> None:
    missing_flag, missing_eval = flag_parts(enabled=False, missing=True)
    status = provider_status_payload(feature_flag=missing_flag, feature_flag_evaluation=missing_eval)
    assert status["status"] == "feature_disabled" and status["feature_flag_evaluation"]["reason"] == "missing_flag"
    off_flag, off_eval = flag_parts(enabled=False)
    assert provider_status_payload(feature_flag=off_flag, feature_flag_evaluation=off_eval)["status"] == "feature_disabled"
    on_flag, on_eval = flag_parts()
    ready = provider_status_payload(feature_flag=on_flag, feature_flag_evaluation=on_eval)
    assert ready["status"] == "ready" and ready["registered_providers"][0]["provider_name"] == "deterministic"


def test_unavailable_and_unknown_providers_do_not_fallback() -> None:
    conn = make_conn(); ids = [insert_memory(conn, "one"), insert_memory(conn, "two")]
    for name, code in (("gemma", "legacy_separate"), ("other", "unknown_provider")):
        result = preview_provider_request_payload(conn, **kwargs(conn, ids, provider_name=name))
        assert result["status"] == "provider_unavailable" and code in result["reason_codes"]
    gemini = preview_provider_request_payload(conn, **kwargs(conn, ids, provider_name="gemini"))
    assert gemini["status"] == "request_ready"
    assert gemini["provider_name"] == "gemini"


def test_exact_boundary_and_public_global_are_blocked() -> None:
    conn = make_conn(); ids = [insert_memory(conn, "one"), insert_memory(conn, "two", project_key="other")]
    assert preview_provider_request_payload(conn, **kwargs(conn, ids))["status"] == "request_blocked"
    clean = make_conn(); clean_ids = [insert_memory(clean, "one"), insert_memory(clean, "two")]
    assert preview_provider_request_payload(clean, **kwargs(clean, clean_ids, scope_code="global"))["status"] == "request_blocked"


def test_fixture_duplicate_supersession_contradiction_and_partial_redaction() -> None:
    conn = make_conn()
    first = insert_memory(conn, "identical")
    second = insert_memory(conn, "identical", supersedes_memory_id=first)
    third = insert_memory(conn, "safe third")
    blocked = insert_memory(conn, "bank account balance", tags="financial")
    add_link(conn, third, first, "contradicts"); conn.commit()
    request_preview = preview_provider_request_payload(conn, **kwargs(conn, [first, second, blocked]))
    assert request_preview["status"] == "request_ready_partial"
    result = preview_deterministic_provider_payload(conn, **kwargs(conn, [first, second, third]))
    assert result["status"] == "preview_completed"
    assert {item["action"] for item in result["proposals"]} >= {"duplicate_of", "supersedes", "contradicts"}


def test_restricted_only_and_dream_fact_are_safely_blocked_or_rejected() -> None:
    conn = make_conn()
    restricted = [insert_memory(conn, "diagnosis", tags="health"), insert_memory(conn, "salary", tags="financial")]
    assert preview_provider_request_payload(conn, **kwargs(conn, restricted))["status"] == "request_blocked"
    dream = insert_memory(conn, "dream", entry_type="dream", truth_kind="dream", memory_type="dream")
    fact = insert_memory(conn, "fact", entry_type="fact", truth_kind="fact", superseded_by_memory_id=dream)
    result = preview_deterministic_provider_payload(conn, **kwargs(conn, [dream, fact], allowed_actions_json='["supersedes"]'))
    assert result["status"] == "response_rejected"
    assert "dream_fact_boundary_violation" in result["validation"]["reason_codes"]
