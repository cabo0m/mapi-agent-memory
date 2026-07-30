from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    from scripts.sandman.preview_snapshot_retention import (
        SCHEMA_VERSION,
        calculate_preview_hash,
    )
except ModuleNotFoundError:  # Direct execution from scripts/sandman.
    from preview_snapshot_retention import SCHEMA_VERSION, calculate_preview_hash


class PreviewValidationError(ValueError):
    """Raised when a retention preview is stale or malformed."""


def _sha256_file(path: Path) -> str:
    import hashlib

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


def verify_snapshot_set(payload: Mapping[str, Any], snapshot_dir: Path | None = None) -> None:
    raw_snapshots = payload.get("snapshots")
    if not isinstance(raw_snapshots, list):
        raise PreviewValidationError("preview snapshots must be a list")

    expected: dict[str, tuple[int, str]] = {}
    for item in raw_snapshots:
        if not isinstance(item, dict):
            raise PreviewValidationError("preview snapshot entries must be objects")
        name = item.get("name")
        size = item.get("size_bytes")
        digest = item.get("file_sha256")
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise PreviewValidationError("snapshot name must be a plain file name")
        if name in expected:
            raise PreviewValidationError(f"duplicate snapshot entry: {name}")
        if not isinstance(size, int) or size < 0:
            raise PreviewValidationError(f"invalid snapshot size: {name}")
        if not isinstance(digest, str) or len(digest) != 64:
            raise PreviewValidationError(f"invalid snapshot sha256: {name}")
        expected[name] = (size, digest)

    directory = snapshot_dir or Path(str(payload.get("snapshot_directory") or ""))
    if not directory.is_dir():
        raise PreviewValidationError(f"snapshot directory is unavailable: {directory}")
    actual_paths = {path.name: path for path in directory.iterdir() if path.is_file()}
    if set(actual_paths) != set(expected):
        added = sorted(set(actual_paths) - set(expected))
        missing = sorted(set(expected) - set(actual_paths))
        details = []
        if added:
            details.append("added=" + ",".join(added))
        if missing:
            details.append("missing=" + ",".join(missing))
        raise PreviewValidationError("snapshot set mismatch: " + "; ".join(details))

    for name, path in actual_paths.items():
        expected_size, expected_digest = expected[name]
        if path.stat().st_size != expected_size:
            raise PreviewValidationError(f"snapshot size mismatch: {name}")
        if _sha256_file(path) != expected_digest:
            raise PreviewValidationError(f"snapshot sha256 mismatch: {name}")


def validate_preview_for_future_apply(
    preview_path: Path,
    *,
    snapshot_dir: Path | None = None,
) -> dict[str, Any]:
    payload = load_preview(preview_path)
    verify_preview_hash(payload)
    verify_snapshot_set(payload, snapshot_dir=snapshot_dir)
    return {
        "status": "NOT_IMPLEMENTED",
        "preview_hash": payload["preview_hash"],
        "snapshot_count": len(payload["snapshots"]),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a Sandman retention preview; deletion is intentionally not implemented"
    )
    parser.add_argument("preview", type=Path, help="JSON preview produced by preview_snapshot_retention.py")
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        help="Optional snapshot directory override for relocated previews or tests",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        validate_preview_for_future_apply(args.preview, snapshot_dir=args.snapshot_dir)
    except PreviewValidationError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print("NOT_IMPLEMENTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
