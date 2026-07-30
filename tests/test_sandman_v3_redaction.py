from __future__ import annotations

from tests.sandman_v3_helpers import insert_memory, make_conn, redaction_for


def test_restricted_classes_are_excluded_without_raw_echo() -> None:
    conn = make_conn()
    values = [
        "-----BEGIN PRIVATE KEY----- TOPSECRET",
        "api_key=abcdefghijklmnop",
        "diagnosis ICD-10 patient id",
        "bank account balance private",
    ]
    ids = [insert_memory(conn, value, tags=tag) for value, tag in zip(values, [None, None, "health", "financial"])]
    result = redaction_for(conn, ids)
    assert result["status"] == "blocked"
    assert result["redaction_manifest"]["candidate_count_excluded"] == 4
    assert all(value not in repr(result) for value in values)


def test_personal_identifiers_are_redacted_and_counted() -> None:
    conn = make_conn()
    personal = insert_memory(conn, "personal email a@example.com phone +48 500 600 700 PESEL 44051401458", tags="personal")
    safe = insert_memory(conn, "harmless internal security policy discussion")
    result = redaction_for(conn, [personal, safe])
    text = result["candidates"][0]["content_redacted"]
    assert "a@example.com" not in text and "44051401458" not in text
    assert "[REDACTED_EMAIL]" in text and "[REDACTED_PERSON_ID]" in text
    assert result["redaction_manifest"]["replacement_counts"]


def test_unproven_personal_and_residual_secret_are_blocked() -> None:
    conn = make_conn()
    unproven = insert_memory(conn, "private note without recognized identifier", tags="personal")
    public_secret = insert_memory(conn, "public docs authorization: bearer abcdefghijklmnop", tags="public")
    safe = insert_memory(conn, "safe one")
    result = redaction_for(conn, [unproven, public_secret, safe])
    reasons = {item["memory_id"]: item["reason_codes"] for item in result["redaction_manifest"]["excluded_candidates"]}
    assert reasons[unproven] == ["personal_redaction_not_proven"]
    assert public_secret in reasons
    assert result["status"] == "blocked"


def test_partial_request_manifest_is_explicit() -> None:
    conn = make_conn()
    safe1 = insert_memory(conn, "safe first")
    safe2 = insert_memory(conn, "safe second")
    blocked = insert_memory(conn, "salary account balance", tags="financial")
    result = redaction_for(conn, [safe1, safe2, blocked])
    assert result["status"] == "request_ready_partial"
    assert result["redaction_manifest"]["included_memory_ids"] == [safe1, safe2]
    assert result["redaction_manifest"]["raw_secret_exposed"] is False
