from __future__ import annotations

from app import db_migrations, memory_config as config
import sqlite3


def main() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        ran = db_migrations.apply_all_migrations(conn)
        conn.commit()
        versions = sorted(db_migrations.applied_migration_versions(conn))
    finally:
        conn.close()

    print(f"DB: {config.DB_PATH}")
    print(f"Applied now: {ran}")
    print(f"All versions: {versions}")


if __name__ == "__main__":
    main()
