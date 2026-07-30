from __future__ import annotations

import json

import pytest

from app.timeline import (
    TIMELINE_INDEX_NAMES,
    TIMELINE_LINK_TIMESTAMP_FIELDS,
    TIMELINE_OPTIONAL_FIELDS,
    TIMELINE_PAYLOAD_PREFERRED_KEYS,
    TIMELINE_REQUIRED_FIELDS,
    TimelineValidationError,
    ensure_utc_iso8601,
    new_operation_id,
    run_operation_id,
    timeline_contract,
    timeline_payload_json,
    utc_now_iso,
    validate_event_type,
    validate_origin,
    validate_reconstructed,
)


def test_utc_now_iso_returns_utc_z_format() -> None:
    value = utc_now_iso()
    assert value.endswith("Z")
    assert len(value) == 20
    assert ensure_utc_iso8601(value) == value


def test_ensure_utc_iso8601_rejects_non_utc_formats() -> None:
    with pytest.raises(TimelineValidationError):
        ensure_utc_iso8601("2026-04-17 12:30:00")

    with pytest.raises(TimelineValidationError):
        ensure_utc_iso8601("2026-04-17T12:30:00+00:00")


def test_validate_event_type_accepts_whitelisted_values_and_sleep_actions() -> None:
    assert validate_event_type("memory.created") == "memory.created"
    assert validate_event_type("sleep_action.archived") == "sleep_action.archived"


@pytest.mark.parametrize("value", ["", "memory_created", "sleep.finished"])
def test_validate_event_type_rejects_unknown_values(value: str) -> None:
    with pytest.raises(TimelineValidationError):
        validate_event_type(value)


def test_validate_origin_accepts_known_values_only() -> None:
    assert validate_origin(None) is None
    assert validate_origin("api") == "api"
    assert validate_origin("consolidation_v1_auto") == "consolidation_v1_auto"

    with pytest.raises(TimelineValidationError):
        validate_origin("scheduler")


def test_validate_reconstructed_accepts_binary_values_only() -> None:
    assert validate_reconstructed(0) == 0
    assert validate_reconstructed(1) == 1
    assert validate_reconstructed(False) == 0
    assert validate_reconstructed(True) == 1

    with pytest.raises(TimelineValidationError):
        validate_reconstructed(2)


def test_timeline_payload_json_accepts_dicts_and_lists() -> None:
    payload = {"reason": "test", "changed_fields": ["importance_score"]}
    encoded = timeline_payload_json(payload)
    assert encoded is not None
    assert json.loads(encoded) == payload

    list_encoded = timeline_payload_json([{"event": 1}])
    assert list_encoded is not None
    assert json.loads(list_encoded) == [{"event": 1}]


def test_timeline_payload_json_rejects_plain_string() -> None:
    with pytest.raises(TimelineValidationError):
        timeline_payload_json("not-json-object")


def test_operation_ids_have_stable_shape() -> None:
    generated = new_operation_id("recall")
    assert generated.startswith("recall:")
    assert len(generated.split(":", 1)[1]) == 32
    assert run_operation_id(17) == "run:17"

    with pytest.raises(TimelineValidationError):
        run_operation_id(0)


def test_timeline_contract_exposes_frozen_stage_1_decisions() -> None:
    contract = timeline_contract()

    assert contract["required_fields"] == list(TIMELINE_REQUIRED_FIELDS)
    assert contract["optional_fields"] == list(TIMELINE_OPTIONAL_FIELDS)
    assert contract["index_names"] == list(TIMELINE_INDEX_NAMES)
    assert contract["link_timestamp_fields"] == list(TIMELINE_LINK_TIMESTAMP_FIELDS)
    assert contract["payload_rules"]["preferred_keys"] == list(TIMELINE_PAYLOAD_PREFERRED_KEYS)

    assert "operation_id" in contract["optional_fields"]
    assert "idx_timeline_events_operation" in contract["index_names"]
    assert contract["payload_rules"]["accepted_types"] == ["dict", "list", "null"]
    assert contract["payload_rules"]["allow_full_snapshots"] is False
