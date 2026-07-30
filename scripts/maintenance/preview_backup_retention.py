from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "mapi_backup_retention_preview.v1"
POLICY_VERSION = "mapi_backup_retention_policy.v1"

KEEP_LAST = "KEEP_LAST"
KEEP_MONTHLY = "KEEP_MONTHLY"
KEEP_REQUIRED_HISTORY = "KEEP_REQUIRED_HISTORY"
KEEP_MIGRATION = "KEEP_MIGRATION"
KEEP_ROLLBACK = "KEEP_ROLLBACK"
DELETE_CANDIDATE = "DELETE_CANDIDATE"
NEEDS_DECISION = "NEEDS_DECISION"

CATEGORIES = (
    KEEP_LAST,
    KEEP_MONTHLY,
    KEEP_REQUIRED_HISTORY,
    KEEP_MIGRATION,
    KEEP_ROLLBACK,
    DELETE_CANDIDATE,
    NEEDS_DECISION,
)
KEEP_CATEGORIES = {
    KEEP_LAST,
    KEEP_MONTHLY,
    KEEP_REQUIRED_HISTORY,
    KEEP_MIGRATION,
    KEEP_ROLLBACK,
}

DEFAULT_BACKUP_ROOTS = (Path("data/backups"), Path("backups"))
OPERATIONAL_ARCHIVE_RE = re.compile(r"^agent-db-(?P<stamp>\d{8}_\d{6})\.zip$")
PATH_TIMESTAMP_RE = re.compile(r"(?P<day>20\d{6})(?:T|_|-)?(?P<time>\d{6})(?:Z)?")
MIGRATION_RE = re.compile(r"(?:^|[-_])(?:pre|post)[-_]00\d+(?:[-_.]|$)", re.IGNORECASE)
ROLLBACK_RE = re.compile(r"(?:^|[-_])rollback(?:[-_.]|$)", re.IGNORECASE)


class BackupPreviewError(ValueError):
    """Raised when the backup inventory cannot be classified safely."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def calculate_preview_hash(payload: Mapping[str, Any]) -> str:
    hash_input = dict(payload)
    hash_input.pop("preview_hash", None)
    return hashlib.sha256(_canonical_json(hash_input).encode("utf-8")).hexdigest()


def _relative_path(repository_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repository_root).as_posix()


def _load_git_tracked_paths(repository_root: Path) -> set[str] | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return {
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in result.stdout.split(b"\0")
        if item
    }


def _parse_path_datetime(relative_path: str, path: Path) -> tuple[datetime, str]:
    matches = list(PATH_TIMESTAMP_RE.finditer(relative_path))
    if matches:
        match = matches[-1]
        try:
            parsed = datetime.strptime(match.group("day") + match.group("time"), "%Y%m%d%H%M%S")
            return parsed, "path_timestamp"
        except ValueError:
            pass
    stat = path.stat()
    return datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc), "filesystem_creation_time_utc"


def _format_name(path: Path) -> str:
    lower = path.name.lower()
    if lower.endswith(".tar.gz"):
        return "tar.gz"
    if lower.endswith(".db-shm"):
        return "sqlite-shm"
    if lower.endswith(".db-wal"):
        return "sqlite-wal"
    extension = path.suffix.lower().lstrip(".")
    return extension or "no-extension"


def _backup_type(relative_path: str) -> str:
    lower = relative_path.lower()
    if lower.startswith("data/backups/"):
        if lower.endswith(".db-shm") or lower.endswith(".db-wal"):
            return "production_database_backup_sidecar"
        if lower.endswith(".db"):
            return "production_database_backup"
        return "production_backup_artifact"
    if lower.startswith("backups/agent-db/archives/"):
        return "scheduled_database_archive"
    if lower.startswith("backups/agent-db/logs/"):
        return "backup_operation_log"
    if lower.startswith("backups/local_memory_daily/"):
        return "local_memory_backup_bundle"
    if lower.startswith("backups/pre_user_onboarding_"):
        return "onboarding_checkpoint_bundle"
    if lower.startswith("backups/vps_runtime_"):
        return "vps_runtime_backup"
    return "backup_artifact"


def _matching_archive_log(repository_root: Path, archive_name: str) -> Path | None:
    match = OPERATIONAL_ARCHIVE_RE.fullmatch(archive_name)
    if not match:
        return None
    candidate = repository_root / "backups" / "agent-db" / "logs" / f"backup-{match.group('stamp')}.log"
    return candidate if candidate.is_file() else None


def _production_run_evidence(repository_root: Path, relative_path: str, path: Path) -> tuple[bool | None, str]:
    lower = relative_path.lower()
    if lower.startswith("backups/agent-db/archives/"):
        log_path = _matching_archive_log(repository_root, path.name)
        if log_path:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if path.name in text and "Backup completed:" in text:
                return True, f"Confirmed by {log_path.relative_to(repository_root).as_posix()}."
        return None, "No matching successful local backup-run log was found."
    if lower.startswith("backups/agent-db/logs/"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "Start backup" in text:
            return True, "This file is the backup-run log."
        return None, "The log does not contain a confirmed backup-run start."
    if lower.startswith("data/backups/"):
        operation_markers = (
            "capture-reconciliation",
            "create-routing",
            "0026",
            "northstar",
            "test-artifact-repair",
            "sprint",
            "v2xx",
            "v3-",
            "v3xx",
        )
        if any(marker in path.name.lower() for marker in operation_markers):
            return True, "Operation-scoped production backup name under data/backups/."
        return None, "No verified production-run evidence was found."
    if lower.startswith("backups/local_memory_daily/"):
        parts = Path(relative_path).parts
        if len(parts) >= 3:
            bundle_name = parts[2]
            if bundle_name.endswith(".zip"):
                bundle_name = bundle_name[:-4]
            manifest = repository_root / "backups" / "local_memory_daily" / bundle_name / "manifest.json"
            if manifest.is_file():
                return True, f"Confirmed by {manifest.relative_to(repository_root).as_posix()}."
    if lower.startswith("backups/pre_user_onboarding_"):
        bundle_name = Path(relative_path).parts[1]
        if bundle_name.endswith(".zip"):
            bundle_name = bundle_name[:-4]
        manifest = repository_root / "backups" / bundle_name / "manifest.json"
        if manifest.is_file():
            return True, f"Confirmed by {manifest.relative_to(repository_root).as_posix()}."
    return None, "Production-run relationship is unverified."


def _only_copy_status(
    repository_root: Path,
    relative_path: str,
    path: Path,
    digest_count: int,
) -> tuple[bool | None, str]:
    if digest_count > 1:
        return False, "At least one byte-identical copy exists in the scanned backup inventory."

    lower = relative_path.lower()
    if lower.startswith("backups/agent-db/archives/"):
        log_path = _matching_archive_log(repository_root, path.name)
        if log_path:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if path.name in text and "Copied (new)" in text:
                return False, "Matching log confirms upload to the configured remote backup target."
    if lower.startswith("backups/local_memory_daily/"):
        parts = Path(relative_path).parts
        if len(parts) >= 3:
            bundle_name = parts[2]
            if bundle_name.endswith(".zip"):
                bundle_name = bundle_name[:-4]
            bundle_dir = repository_root / "backups" / "local_memory_daily" / bundle_name
            bundle_zip = repository_root / "backups" / "local_memory_daily" / f"{bundle_name}.zip"
            if bundle_dir.is_dir() and bundle_zip.is_file():
                return False, "Both expanded and ZIP forms of this logical backup are present."
    if lower.startswith("backups/pre_user_onboarding_"):
        bundle_name = Path(relative_path).parts[1]
        if bundle_name.endswith(".zip"):
            bundle_name = bundle_name[:-4]
        bundle_dir = repository_root / "backups" / bundle_name
        bundle_zip = repository_root / "backups" / f"{bundle_name}.zip"
        if bundle_dir.is_dir() and bundle_zip.is_file():
            return False, "Both expanded and ZIP forms of this logical backup are present."
    return None, "No second copy was proven; external copies and archive contents were not assumed."


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


def _initial_classification(record: dict[str, Any]) -> tuple[str | None, str | None]:
    relative_path = record["location"]
    name = record["name"]
    lower = relative_path.lower()

    if ROLLBACK_RE.search(name):
        return KEEP_ROLLBACK, "Explicit rollback backup; rollback evidence is never a deletion candidate."
    if MIGRATION_RE.search(name):
        return KEEP_MIGRATION, "Explicit pre/post schema migration 00xx backup or companion file."
    if lower.startswith("data/backups/"):
        return KEEP_REQUIRED_HISTORY, "Operation-scoped production backup retained as required history."
    if record["git_tracked"] is True:
        return KEEP_REQUIRED_HISTORY, "Tracked historical backup artifact; preserve until separately de-versioned."
    if record["backup_type"] == "backup_operation_log":
        return NEEDS_DECISION, "Backup log is evidence, not an operational backup payload covered by this policy."
    if record["backup_type"] == "scheduled_database_archive":
        if OPERATIONAL_ARCHIVE_RE.fullmatch(name):
            return None, None
        return NEEDS_DECISION, "Scheduled archive name does not match the supported timestamp convention."
    return NEEDS_DECISION, "Backup meaning or retention role is not unambiguous."


def build_backup_retention_preview(
    repository_root: Path,
    *,
    backup_roots: Sequence[Path] = DEFAULT_BACKUP_ROOTS,
    tracked_paths: set[str] | None = None,
    generated_at: str | None = None,
    keep_last: int = 5,
    monthly_months: int = 6,
) -> dict[str, Any]:
    if keep_last < 1 or monthly_months < 1:
        raise BackupPreviewError("retention windows must be positive")
    repository_root = repository_root.resolve()
    normalized_roots: list[str] = []
    paths: list[Path] = []
    for root in backup_roots:
        absolute = root if root.is_absolute() else repository_root / root
        absolute = absolute.resolve()
        try:
            relative_root = absolute.relative_to(repository_root).as_posix()
        except ValueError as exc:
            raise BackupPreviewError(f"backup root is outside repository: {absolute}") from exc
        normalized_roots.append(relative_root)
        if absolute.exists() and not absolute.is_dir():
            raise BackupPreviewError(f"backup root is not a directory: {absolute}")
        if absolute.is_dir():
            paths.extend(path for path in absolute.rglob("*") if path.is_file())
    paths = sorted(set(paths), key=lambda item: _relative_path(repository_root, item))

    resolved_tracked = tracked_paths if tracked_paths is not None else _load_git_tracked_paths(repository_root)
    base_records: list[dict[str, Any]] = []
    for path in paths:
        relative_path = _relative_path(repository_root, path)
        created_at, date_source = _parse_path_datetime(relative_path, path)
        digest = _sha256_file(path)
        production_link, production_evidence = _production_run_evidence(
            repository_root, relative_path, path
        )
        base_records.append(
            {
                "name": path.name,
                "location": relative_path,
                "size_bytes": path.stat().st_size,
                "sha256": digest,
                "created_at": created_at.isoformat(timespec="seconds"),
                "date_source": date_source,
                "backup_type": _backup_type(relative_path),
                "format": _format_name(path),
                "git_tracked": relative_path in resolved_tracked if resolved_tracked is not None else None,
                "only_copy": None,
                "only_copy_reason": None,
                "production_run_link": production_link,
                "production_run_evidence": production_evidence,
                "category": None,
                "decision": None,
                "reason": None,
                "_path": path,
                "_datetime": created_at,
            }
        )

    digest_counts = Counter(record["sha256"] for record in base_records)
    for record in base_records:
        only_copy, only_copy_reason = _only_copy_status(
            repository_root,
            record["location"],
            record["_path"],
            digest_counts[record["sha256"]],
        )
        record["only_copy"] = only_copy
        record["only_copy_reason"] = only_copy_reason
        category, reason = _initial_classification(record)
        record["category"] = category
        record["reason"] = reason

    operational = sorted(
        (
            record
            for record in base_records
            if record["backup_type"] == "scheduled_database_archive" and record["category"] is None
        ),
        key=lambda record: (record["_datetime"], record["location"]),
        reverse=True,
    )
    for record in operational[:keep_last]:
        record["category"] = KEEP_LAST
        record["reason"] = f"One of the {keep_last} newest operational database archives."

    if operational:
        allowed_months = _previous_months(operational[0]["_datetime"].date(), monthly_months)
        latest_by_month: dict[tuple[int, int], dict[str, Any]] = {}
        for record in operational:
            key = _month_key(record["_datetime"].date())
            if key in allowed_months and key not in latest_by_month:
                latest_by_month[key] = record
        for key, record in latest_by_month.items():
            if record["category"] is None:
                record["category"] = KEEP_MONTHLY
                record["reason"] = f"Latest operational archive for calendar month {key[0]}-{key[1]:02d}."

    for record in operational:
        if record["category"] is None:
            record["category"] = DELETE_CANDIDATE
            record["reason"] = "Outside last-five and six-month operational archive retention sets."

    for record in base_records:
        if record["category"] in KEEP_CATEGORIES:
            record["decision"] = "KEEP"
        elif record["category"] == DELETE_CANDIDATE:
            record["decision"] = "DELETE_CANDIDATE"
        else:
            record["decision"] = "REVIEW"

    base_records.sort(key=lambda record: record["location"])
    public_records = [
        {key: value for key, value in record.items() if not key.startswith("_")}
        for record in base_records
    ]
    category_counts = {category: 0 for category in CATEGORIES}
    for record in public_records:
        category_counts[record["category"]] += 1

    delete_candidate_bytes = sum(
        record["size_bytes"] for record in public_records if record["category"] == DELETE_CANDIDATE
    )
    needs_decision_bytes = sum(
        record["size_bytes"] for record in public_records if record["category"] == NEEDS_DECISION
    )
    tracked_count = sum(record["git_tracked"] is True for record in public_records)
    tracked_backup_payload_count = sum(
        record["git_tracked"] is True and record["backup_type"] != "backup_operation_log"
        for record in public_records
    )

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "version": POLICY_VERSION,
            "keep_last_operational": keep_last,
            "monthly_months": monthly_months,
            "reference_operational_backup_date": (
                operational[0]["_datetime"].isoformat(timespec="seconds") if operational else None
            ),
        },
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repository_root": str(repository_root),
        "backup_roots": normalized_roots,
        "summary": {
            "total_file_count": len(public_records),
            "retained_file_count": sum(category_counts[category] for category in KEEP_CATEGORIES),
            "delete_candidate_count": category_counts[DELETE_CANDIDATE],
            "needs_decision_count": category_counts[NEEDS_DECISION],
            "git_tracked_count": tracked_count,
            "git_tracked_backup_payload_count": tracked_backup_payload_count,
            "category_counts": category_counts,
            "total_bytes": sum(record["size_bytes"] for record in public_records),
            "potential_reclaim_bytes": delete_candidate_bytes + needs_decision_bytes,
            "delete_candidate_reclaim_bytes": delete_candidate_bytes,
        },
        "files": public_records,
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


def _cell(value: Any) -> str:
    if value is None:
        return "UNVERIFIED"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(payload: Mapping[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# MAPI backup retention preview",
        "",
        f"- Preview hash: `{payload['preview_hash']}`",
        f"- Files inventoried: **{summary['total_file_count']}**",
        f"- Retained files: **{summary['retained_file_count']}**",
        f"- Delete candidates: **{summary['delete_candidate_count']}**",
        f"- Needs decision: **{summary['needs_decision_count']}**",
        f"- Git-tracked backup payloads: **{summary['git_tracked_backup_payload_count']}**",
        f"- Potential reclaim including NEEDS_DECISION: **{_format_bytes(summary['potential_reclaim_bytes'])}**",
        f"- Reclaim from DELETE_CANDIDATE only: **{_format_bytes(summary['delete_candidate_reclaim_bytes'])}**",
        "",
        "`DELETE_CANDIDATE` is a preview classification, not deletion authorization.",
        "",
        "| Location | Created | Type | Format | Tracked | Only copy | Production run | Size | Category | Decision | Reason | SHA-256 |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for item in payload["files"]:
        lines.append(
            "| "
            + " | ".join(
                (
                    _cell(item["location"]),
                    _cell(item["created_at"]),
                    _cell(item["backup_type"]),
                    _cell(item["format"]),
                    _cell(item["git_tracked"]),
                    _cell(item["only_copy"]),
                    _cell(item["production_run_link"]),
                    _format_bytes(item["size_bytes"]),
                    _cell(item["category"]),
                    _cell(item["decision"]),
                    _cell(item["reason"]),
                    _cell(item["sha256"]),
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only MAPI backup retention preview")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Print stable JSON")
    output.add_argument("--markdown", action="store_true", help="Print operator Markdown (default)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = build_backup_retention_preview(args.repository_root)
    except BackupPreviewError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(render_json(payload) if args.json else render_markdown(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
