from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    from scripts.maintenance.preview_backup_retention import SCHEMA_VERSION, calculate_preview_hash
except ModuleNotFoundError:  # Direct execution from scripts/maintenance.
    from preview_backup_retention import SCHEMA_VERSION, calculate_preview_hash


class PreviewValidationError(ValueError):
    """Raised when a backup retention preview is stale or malformed."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_preview(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreviewValidationError(f"preview cannot be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise PreviewValidationError("preview root must be an object")
    return payload


def verify_preview_hash(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise PreviewValidationError(f"unsupported preview schema: {payload.get('schema_version')!r}")
    expected = payload.get("preview_hash")
    if not isinstance(expected, str) or not expected:
        raise PreviewValidationError("preview_hash is missing")
    actual = calculate_preview_hash(payload)
    if actual != expected:
        raise PreviewValidationError("preview hash mismatch")


def _safe_relative_path(value: Any, *, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise PreviewValidationError(f"{field} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise PreviewValidationError(f"{field} must stay inside the repository: {value!r}")
    return path


def verify_backup_set(payload: Mapping[str, Any], repository_root: Path | None = None) -> None:
    raw_files = payload.get("files")
    raw_roots = payload.get("backup_roots")
    if not isinstance(raw_files, list):
        raise PreviewValidationError("preview files must be a list")
    if not isinstance(raw_roots, list):
        raise PreviewValidationError("preview backup_roots must be a list")

    root = (repository_root or Path(str(payload.get("repository_root") or ""))).resolve()
    if not root.is_dir():
        raise PreviewValidationError(f"repository root is unavailable: {root}")

    expected: dict[str, tuple[int, str]] = {}
    for item in raw_files:
        if not isinstance(item, dict):
            raise PreviewValidationError("preview file entries must be objects")
        relative = _safe_relative_path(item.get("location"), field="file location").as_posix()
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if relative in expected:
            raise PreviewValidationError(f"duplicate backup entry: {relative}")
        if not isinstance(size, int) or size < 0:
            raise PreviewValidationError(f"invalid backup size: {relative}")
        if not isinstance(digest, str) or len(digest) != 64:
            raise PreviewValidationError(f"invalid backup sha256: {relative}")
        expected[relative] = (size, digest)

    actual: dict[str, Path] = {}
    for raw_backup_root in raw_roots:
        relative_root = _safe_relative_path(raw_backup_root, field="backup root")
        absolute_root = (root / relative_root).resolve()
        try:
            absolute_root.relative_to(root)
        except ValueError as exc:
            raise PreviewValidationError(f"backup root escapes repository: {relative_root}") from exc
        if absolute_root.exists() and not absolute_root.is_dir():
            raise PreviewValidationError(f"backup root is not a directory: {relative_root}")
        if absolute_root.is_dir():
            for path in absolute_root.rglob("*"):
                if not path.is_file():
                    continue
                relative = path.resolve().relative_to(root).as_posix()
                if relative in actual:
                    raise PreviewValidationError(f"backup roots overlap at: {relative}")
                actual[relative] = path

    if set(actual) != set(expected):
        added = sorted(set(actual) - set(expected))
        missing = sorted(set(expected) - set(actual))
        details = []
        if added:
            details.append("added=" + ",".join(added))
        if missing:
            details.append("missing=" + ",".join(missing))
        raise PreviewValidationError("backup set mismatch: " + "; ".join(details))

    for relative, path in actual.items():
        expected_size, expected_digest = expected[relative]
        if path.stat().st_size != expected_size:
            raise PreviewValidationError(f"backup size mismatch: {relative}")
        if _sha256_file(path) != expected_digest:
            raise PreviewValidationError(f"backup sha256 mismatch: {relative}")


def validate_preview_for_future_apply(
    preview_path: Path,
    *,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    payload = load_preview(preview_path)
    verify_preview_hash(payload)
    verify_backup_set(payload, repository_root=repository_root)
    return {
        "status": "NOT_IMPLEMENTED",
        "preview_hash": payload["preview_hash"],
        "file_count": len(payload["files"]),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a MAPI backup retention preview; deletion is intentionally not implemented"
    )
    parser.add_argument("preview", type=Path, help="JSON preview produced by preview_backup_retention.py")
    parser.add_argument(
        "--repository-root",
        type=Path,
        help="Optional repository override for relocated previews or tests",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        validate_preview_for_future_apply(args.preview, repository_root=args.repository_root)
    except PreviewValidationError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print("NOT_IMPLEMENTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
