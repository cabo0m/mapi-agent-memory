from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from scripts.sandman import apply_snapshot_retention, preview_snapshot_retention


FIXED_GENERATED_AT = "2026-07-21T12:00:00+00:00"


def _snapshot_name(timestamp: str, token: str = "abcd1234") -> str:
    return f"{timestamp}-nightly-{token}-agent_memory.db"


def _write_snapshot(sandman_dir: Path, timestamp: str, *, token: str = "abcd1234", size: int = 8) -> Path:
    snapshot_dir = sandman_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / _snapshot_name(timestamp, token)
    path.write_bytes((token.encode("ascii") * (size + len(token) - 1))[:size])
    return path


def _build(sandman_dir: Path, **kwargs):
    return preview_snapshot_retention.build_retention_preview(
        sandman_dir,
        generated_at=FIXED_GENERATED_AT,
        **kwargs,
    )


def _by_name(payload: dict) -> dict[str, dict]:
    return {item["name"]: item for item in payload["snapshots"]}


def test_empty_snapshot_directory_has_zeroed_summary(tmp_path: Path) -> None:
    payload = _build(tmp_path / "sandman")

    assert payload["snapshots"] == []
    assert payload["policy"]["reference_snapshot_date"] is None
    assert payload["summary"]["total_snapshot_count"] == 0
    assert payload["summary"]["retained_snapshot_count"] == 0
    assert payload["summary"]["delete_candidate_reclaim_bytes"] == 0


def test_one_snapshot_is_always_keep_last(tmp_path: Path) -> None:
    sandman_dir = tmp_path / "sandman"
    snapshot = _write_snapshot(sandman_dir, "2026-07-20T220000")

    payload = _build(sandman_dir)

    item = _by_name(payload)[snapshot.name]
    assert item["category"] == preview_snapshot_retention.KEEP_LAST
    assert item["decision"] == "KEEP"
    assert payload["summary"]["retained_snapshot_count"] == 1


def test_multiple_snapshots_keep_last_and_six_additional_daily_dates(tmp_path: Path) -> None:
    sandman_dir = tmp_path / "sandman"
    for offset in range(9):
        timestamp = datetime(2026, 7, 20 - offset, 22, 0, 0).strftime("%Y-%m-%dT%H%M%S")
        _write_snapshot(sandman_dir, timestamp, token=f"day{offset:04d}")

    payload = _build(sandman_dir, weekly_weeks=1, monthly_months=1)
    counts = payload["summary"]["category_counts"]

    assert counts[preview_snapshot_retention.KEEP_LAST] == 1
    assert counts[preview_snapshot_retention.KEEP_DAILY] == 6
    assert counts[preview_snapshot_retention.DELETE_CANDIDATE] == 2


def test_required_history_overrides_all_other_categories(tmp_path: Path) -> None:
    sandman_dir = tmp_path / "sandman"
    snapshot = _write_snapshot(sandman_dir, "2026-07-20T220000")

    payload = _build(
        sandman_dir,
        required_history={snapshot.name: "Used by production rollback evidence."},
    )
    item = _by_name(payload)[snapshot.name]

    assert item["category"] == preview_snapshot_retention.KEEP_REQUIRED_HISTORY
    assert item["reason"] == "Used by production rollback evidence."
    assert payload["summary"]["required_history_count"] == 1


def test_weekly_retention_uses_current_and_three_previous_iso_weeks(tmp_path: Path) -> None:
    sandman_dir = tmp_path / "sandman"
    timestamps = (
        "2026-07-20T220000",
        "2026-07-19T220000",
        "2026-07-12T220000",
        "2026-07-05T220000",
        "2026-06-28T220000",
    )
    for index, timestamp in enumerate(timestamps):
        _write_snapshot(sandman_dir, timestamp, token=f"week{index:03d}")

    payload = _build(sandman_dir, daily_days=1, weekly_weeks=4, monthly_months=1)
    counts = payload["summary"]["category_counts"]

    assert counts[preview_snapshot_retention.KEEP_LAST] == 1
    assert counts[preview_snapshot_retention.KEEP_WEEKLY] == 3
    assert counts[preview_snapshot_retention.DELETE_CANDIDATE] == 1


def test_monthly_retention_uses_current_and_two_previous_calendar_months(tmp_path: Path) -> None:
    sandman_dir = tmp_path / "sandman"
    timestamps = (
        "2026-07-20T220000",
        "2026-06-30T220000",
        "2026-05-31T220000",
        "2026-04-30T220000",
    )
    for index, timestamp in enumerate(timestamps):
        _write_snapshot(sandman_dir, timestamp, token=f"month{index:02d}")

    payload = _build(sandman_dir, daily_days=1, weekly_weeks=1, monthly_months=3)
    counts = payload["summary"]["category_counts"]

    assert counts[preview_snapshot_retention.KEEP_LAST] == 1
    assert counts[preview_snapshot_retention.KEEP_MONTHLY] == 2
    assert counts[preview_snapshot_retention.DELETE_CANDIDATE] == 1


def test_unknown_snapshot_name_requires_decision(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "sandman" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    unknown = snapshot_dir / "legacy-copy.db"
    unknown.write_bytes(b"legacy")

    payload = _build(tmp_path / "sandman")
    item = _by_name(payload)[unknown.name]

    assert item["date"] is None
    assert item["category"] == preview_snapshot_retention.NEEDS_DECISION
    assert item["decision"] == "REVIEW"
    assert payload["summary"]["potential_reclaim_bytes"] == len(b"legacy")
    assert payload["summary"]["delete_candidate_reclaim_bytes"] == 0


def test_json_report_is_stable_and_hash_verifies(tmp_path: Path) -> None:
    sandman_dir = tmp_path / "sandman"
    _write_snapshot(sandman_dir, "2026-07-20T220000")

    first = _build(sandman_dir)
    second = _build(sandman_dir)
    rendered = preview_snapshot_retention.render_json(first)

    assert first == second
    assert json.loads(rendered) == first
    assert first["preview_hash"] == preview_snapshot_retention.calculate_preview_hash(first)
    assert rendered.endswith("\n")


def test_markdown_report_contains_summary_and_snapshot_table(tmp_path: Path) -> None:
    sandman_dir = tmp_path / "sandman"
    snapshot = _write_snapshot(sandman_dir, "2026-07-20T220000")

    markdown = preview_snapshot_retention.render_markdown(_build(sandman_dir))

    assert markdown.startswith("# Sandman snapshot retention preview\n")
    assert "| Name | Date | Size | Category | Decision | Reason |" in markdown
    assert snapshot.name in markdown
    assert "Reclaim from DELETE_CANDIDATE only" in markdown


def test_future_apply_validates_preview_and_returns_not_implemented(tmp_path: Path) -> None:
    sandman_dir = tmp_path / "sandman"
    _write_snapshot(sandman_dir, "2026-07-20T220000")
    payload = _build(sandman_dir)
    preview_path = tmp_path / "preview.json"
    preview_path.write_text(preview_snapshot_retention.render_json(payload), encoding="utf-8")

    result = apply_snapshot_retention.validate_preview_for_future_apply(preview_path)

    assert result["status"] == "NOT_IMPLEMENTED"
    assert result["preview_hash"] == payload["preview_hash"]
    assert result["snapshot_count"] == 1


def test_future_apply_rejects_tampered_preview_hash(tmp_path: Path) -> None:
    sandman_dir = tmp_path / "sandman"
    _write_snapshot(sandman_dir, "2026-07-20T220000")
    payload = _build(sandman_dir)
    payload["summary"]["delete_candidate_count"] = 99
    preview_path = tmp_path / "preview.json"
    preview_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(apply_snapshot_retention.PreviewValidationError, match="hash mismatch"):
        apply_snapshot_retention.validate_preview_for_future_apply(preview_path)
