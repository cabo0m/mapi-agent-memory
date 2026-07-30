from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "sandman_snapshot_retention_preview.v1"
POLICY_VERSION = "sandman_snapshot_retention_policy.v1"
REQUIRED_HISTORY_SCHEMA_VERSION = "sandman_snapshot_required_history.v1"

KEEP_DAILY = "KEEP_DAILY"
KEEP_WEEKLY = "KEEP_WEEKLY"
KEEP_MONTHLY = "KEEP_MONTHLY"
KEEP_REQUIRED_HISTORY = "KEEP_REQUIRED_HISTORY"
KEEP_LAST = "KEEP_LAST"
DELETE_CANDIDATE = "DELETE_CANDIDATE"
NEEDS_DECISION = "NEEDS_DECISION"

CATEGORIES = (
    KEEP_DAILY,
    KEEP_WEEKLY,
    KEEP_MONTHLY,
    KEEP_REQUIRED_HISTORY,
    KEEP_LAST,
    DELETE_CANDIDATE,
    NEEDS_DECISION,
)
KEEP_CATEGORIES = {
    KEEP_DAILY,
    KEEP_WEEKLY,
    KEEP_MONTHLY,
    KEEP_REQUIRED_HISTORY,
    KEEP_LAST,
}

SNAPSHOT_NAME_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{6})-"
    r"(?P<run_type>[A-Za-z0-9_-]+)-(?P<run_token>[A-Za-z0-9]+)-agent_memory\.db$"
)


class RetentionPreviewError(ValueError):
    """Raised when a preview input cannot be classified safely."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_snapshot_datetime(name: str) -> datetime | None:
    match = SNAPSHOT_NAME_RE.fullmatch(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group("timestamp"), "%Y-%m-%dT%H%M%S")
    except ValueError:
        return None


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def calculate_preview_hash(payload: Mapping[str, Any]) -> str:
    hash_input = dict(payload)
    hash_input.pop("preview_hash", None)
    return hashlib.sha256(_canonical_json(hash_input).encode("utf-8")).hexdigest()


def load_required_history_manifest(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetentionPreviewError(f"required history manifest cannot be read: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != REQUIRED_HISTORY_SCHEMA_VERSION:
        raise RetentionPreviewError(
            f"required history manifest must use schema_version={REQUIRED_HISTORY_SCHEMA_VERSION}"
        )
    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, list):
        raise RetentionPreviewError("required history manifest snapshots must be a list")

    result: dict[str, str] = {}
    for item in snapshots:
        if not isinstance(item, dict):
            raise RetentionPreviewError("each required history entry must be an object")
        name = item.get("name")
        reason = item.get("reason")
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise RetentionPreviewError("required history entry name must be a plain file name")
        if not isinstance(reason, str) or not reason.strip():
            raise RetentionPreviewError(f"required history entry {name!r} needs a non-empty reason")
        if name in result:
            raise RetentionPreviewError(f"duplicate required history entry: {name}")
        result[name] = reason.strip()
    return result


def _month_key(value: date) -> tuple[int, int]:
    return value.year, value.month


def _previous_months(value: date, count: int) -> list[tuple[int, int]]:
    year = value.year
    month = value.month
    result: list[tuple[int, int]] = []
    for _ in range(count):
        result.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return result


def _iso_week_key(value: date) -> tuple[int, int]:
    iso = value.isocalendar()
    return iso.year, iso.week


def _latest_by_bucket(
    records: Sequence[dict[str, Any]],
    bucket_keys: Iterable[Any],
    key_function,
) -> dict[Any, dict[str, Any]]:
    wanted = set(bucket_keys)
    selected: dict[Any, dict[str, Any]] = {}
    for record in records:
        snapshot_datetime = record["_datetime"]
        bucket = key_function(snapshot_datetime.date())
        if bucket in wanted and bucket not in selected:
            selected[bucket] = record
    return selected


def _assign_if_unclassified(record: dict[str, Any], category: str, reason: str) -> None:
    if record["category"] is None:
        record["category"] = category
        record["reason"] = reason


def build_retention_preview(
    sandman_dir: Path,
    *,
    required_history: Mapping[str, str] | None = None,
    generated_at: str | None = None,
    daily_days: int = 7,
    weekly_weeks: int = 4,
    monthly_months: int = 3,
) -> dict[str, Any]:
    if daily_days < 1 or weekly_weeks < 1 or monthly_months < 1:
        raise RetentionPreviewError("retention windows must be positive")

    sandman_dir = sandman_dir.resolve()
    snapshot_dir = sandman_dir / "snapshots"
    if not snapshot_dir.exists():
        snapshot_files: list[Path] = []
    elif not snapshot_dir.is_dir():
        raise RetentionPreviewError(f"snapshot path is not a directory: {snapshot_dir}")
    else:
        snapshot_files = sorted(path for path in snapshot_dir.iterdir() if path.is_file())

    required = dict(required_history or {})
    missing_required = sorted(set(required) - {path.name for path in snapshot_files})
    if missing_required:
        raise RetentionPreviewError(
            "required history manifest references missing snapshots: " + ", ".join(missing_required)
        )

    records: list[dict[str, Any]] = []
    for path in snapshot_files:
        parsed_datetime = _parse_snapshot_datetime(path.name)
        record = {
            "name": path.name,
            "date": parsed_datetime.isoformat(timespec="seconds") if parsed_datetime else None,
            "size_bytes": path.stat().st_size,
            "file_sha256": _sha256_file(path),
            "category": None,
            "decision": None,
            "reason": None,
            "_datetime": parsed_datetime,
        }
        if path.name in required:
            record["category"] = KEEP_REQUIRED_HISTORY
            record["reason"] = required[path.name]
        elif parsed_datetime is None:
            record["category"] = NEEDS_DECISION
            record["reason"] = "File name does not match the supported Sandman snapshot convention."
        records.append(record)

    parseable = sorted(
        (record for record in records if record["_datetime"] is not None),
        key=lambda record: (record["_datetime"], record["name"]),
        reverse=True,
    )

    reference_datetime = parseable[0]["_datetime"] if parseable else None
    if parseable:
        _assign_if_unclassified(
            parseable[0],
            KEEP_LAST,
            "Newest parseable snapshot is always retained.",
        )

        reference_date = reference_datetime.date()
        daily_keys = [reference_date - timedelta(days=offset) for offset in range(daily_days)]
        daily_selected = _latest_by_bucket(parseable, daily_keys, lambda value: value)
        for bucket, record in daily_selected.items():
            _assign_if_unclassified(
                record,
                KEEP_DAILY,
                f"Latest snapshot for daily retention date {bucket.isoformat()}.",
            )

        weekly_keys = [_iso_week_key(reference_date - timedelta(weeks=offset)) for offset in range(weekly_weeks)]
        weekly_selected = _latest_by_bucket(parseable, weekly_keys, _iso_week_key)
        for bucket, record in weekly_selected.items():
            _assign_if_unclassified(
                record,
                KEEP_WEEKLY,
                f"Latest snapshot for ISO week {bucket[0]}-W{bucket[1]:02d}.",
            )

        monthly_keys = _previous_months(reference_date, monthly_months)
        monthly_selected = _latest_by_bucket(parseable, monthly_keys, _month_key)
        for bucket, record in monthly_selected.items():
            _assign_if_unclassified(
                record,
                KEEP_MONTHLY,
                f"Latest snapshot for calendar month {bucket[0]}-{bucket[1]:02d}.",
            )

    for record in records:
        if record["category"] is None:
            record["category"] = DELETE_CANDIDATE
            record["reason"] = "Outside daily, weekly, monthly, last, and required-history retention sets."
        if record["category"] in KEEP_CATEGORIES:
            record["decision"] = "KEEP"
        elif record["category"] == DELETE_CANDIDATE:
            record["decision"] = "DELETE_CANDIDATE"
        else:
            record["decision"] = "REVIEW"

    records.sort(key=lambda record: record["name"])
    category_counts = {category: 0 for category in CATEGORIES}
    for record in records:
        category_counts[record["category"]] += 1

    retained_count = sum(category_counts[category] for category in KEEP_CATEGORIES)
    delete_candidate_bytes = sum(
        record["size_bytes"] for record in records if record["category"] == DELETE_CANDIDATE
    )
    needs_decision_bytes = sum(
        record["size_bytes"] for record in records if record["category"] == NEEDS_DECISION
    )

    public_records = [
        {key: value for key, value in record.items() if not key.startswith("_")}
        for record in records
    ]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "version": POLICY_VERSION,
            "daily_days": daily_days,
            "weekly_weeks": weekly_weeks,
            "monthly_months": monthly_months,
            "reference_snapshot_date": (
                reference_datetime.isoformat(timespec="seconds") if reference_datetime else None
            ),
        },
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshot_directory": str(snapshot_dir),
        "summary": {
            "total_snapshot_count": len(public_records),
            "retained_snapshot_count": retained_count,
            "delete_candidate_count": category_counts[DELETE_CANDIDATE],
            "required_history_count": category_counts[KEEP_REQUIRED_HISTORY],
            "needs_decision_count": category_counts[NEEDS_DECISION],
            "category_counts": category_counts,
            "total_snapshot_bytes": sum(record["size_bytes"] for record in public_records),
            "potential_reclaim_bytes": delete_candidate_bytes + needs_decision_bytes,
            "delete_candidate_reclaim_bytes": delete_candidate_bytes,
        },
        "snapshots": public_records,
    }
    payload["preview_hash"] = calculate_preview_hash(payload)
    return payload


def render_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def _markdown_cell(value: Any) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(payload: Mapping[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Sandman snapshot retention preview",
        "",
        f"- Preview hash: `{payload['preview_hash']}`",
        f"- Snapshot directory: `{payload['snapshot_directory']}`",
        f"- Reference snapshot date: `{payload['policy']['reference_snapshot_date'] or 'none'}`",
        f"- Retained snapshots: **{summary['retained_snapshot_count']}**",
        f"- Delete candidates: **{summary['delete_candidate_count']}**",
        f"- Required history: **{summary['required_history_count']}**",
        f"- Needs decision: **{summary['needs_decision_count']}**",
        f"- Potential reclaim including NEEDS_DECISION: **{_format_bytes(summary['potential_reclaim_bytes'])}**",
        f"- Reclaim from DELETE_CANDIDATE only: **{_format_bytes(summary['delete_candidate_reclaim_bytes'])}**",
        "",
        "`DELETE_CANDIDATE` is a preview classification, not deletion authorization.",
        "",
        "| Name | Date | Size | Category | Decision | Reason |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for snapshot in payload["snapshots"]:
        lines.append(
            "| "
            + " | ".join(
                (
                    _markdown_cell(snapshot["name"]),
                    _markdown_cell(snapshot["date"]),
                    _format_bytes(snapshot["size_bytes"]),
                    _markdown_cell(snapshot["category"]),
                    _markdown_cell(snapshot["decision"]),
                    _markdown_cell(snapshot["reason"]),
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Sandman snapshot retention preview")
    parser.add_argument(
        "--sandman-dir",
        type=Path,
        default=Path("data/sandman"),
        help="Sandman data directory containing snapshots/ (default: data/sandman)",
    )
    parser.add_argument(
        "--required-history-manifest",
        type=Path,
        help="Optional explicit required-history manifest; missing or malformed entries fail closed",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Print stable JSON")
    output.add_argument("--markdown", action="store_true", help="Print operator Markdown (default)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        required = load_required_history_manifest(args.required_history_manifest)
        payload = build_retention_preview(args.sandman_dir, required_history=required)
    except RetentionPreviewError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(render_json(payload) if args.json else render_markdown(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
