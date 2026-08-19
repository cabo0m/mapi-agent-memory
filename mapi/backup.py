from __future__ import annotations

"""Verified first-run SQLite backup helpers."""

import hashlib
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _verify_sqlite(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        quick = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()
    if quick != ["ok"]:
        raise RuntimeError("backup_quick_check_failed")
    if foreign_keys:
        raise RuntimeError("backup_foreign_key_check_failed")
    return {"quick_check": "ok", "foreign_key_findings": 0}


def ensure_initial_backup(*, db_path: str | Path, backup_dir: str | Path) -> dict[str, Any]:
    source = Path(db_path).expanduser().resolve()
    target_dir = Path(backup_dir).expanduser().resolve()
    if not source.is_file():
        raise RuntimeError("initial_backup_source_missing")
    target_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(
        (path for path in target_dir.glob("mapi-initial-*.db") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if existing:
        candidate = existing[0]
        verification = _verify_sqlite(candidate)
        return {
            "status": "existing_verified",
            "path": str(candidate),
            "size_bytes": candidate.stat().st_size,
            "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            **verification,
        }

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    destination = target_dir / f"mapi-initial-{stamp}.db"
    source_conn = sqlite3.connect(source, timeout=30)
    destination_conn = sqlite3.connect(destination, timeout=30)
    try:
        source_conn.backup(destination_conn)
        destination_conn.commit()
    finally:
        destination_conn.close()
        source_conn.close()
    if os.name != "nt":
        destination.chmod(0o600)
    verification = _verify_sqlite(destination)
    return {
        "status": "created",
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        **verification,
    }
