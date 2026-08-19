from __future__ import annotations

import sqlite3
from pathlib import Path

from mapi.backup import ensure_initial_backup


def _source_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO sample(value) VALUES ('polaris')")
        conn.commit()
    finally:
        conn.close()


def test_initial_backup_is_sqlite_consistent_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    backups = tmp_path / "backups"
    _source_database(source)

    first = ensure_initial_backup(db_path=source, backup_dir=backups)
    second = ensure_initial_backup(db_path=source, backup_dir=backups)

    assert first["status"] == "created"
    assert first["quick_check"] == "ok"
    assert first["foreign_key_findings"] == 0
    assert len(first["sha256"]) == 64
    assert Path(first["path"]).is_file()
    assert second["status"] == "existing_verified"
    assert second["path"] == first["path"]
    assert len(list(backups.glob("mapi-initial-*.db"))) == 1


def test_initial_backup_rejects_missing_source(tmp_path: Path) -> None:
    try:
        ensure_initial_backup(db_path=tmp_path / "missing.db", backup_dir=tmp_path / "backups")
    except RuntimeError as exc:
        assert str(exc) == "initial_backup_source_missing"
    else:
        raise AssertionError("missing source unexpectedly accepted")
