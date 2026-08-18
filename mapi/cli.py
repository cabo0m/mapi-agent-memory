from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from app import db_migrations
from app.memory_config import DB_PATH


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _database_path() -> Path:
    path = Path(os.environ.get("MAPI_DB_PATH", DB_PATH)).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def migrate() -> None:
    path = _database_path()
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        applied = db_migrations.apply_all_migrations(connection)
        connection.commit()
        versions = sorted(db_migrations.applied_migration_versions(connection))
    print(
        json.dumps(
            {
                "status": "ok",
                "database": str(path),
                "applied_now": applied,
                "migration_tail": versions[-1] if versions else None,
            },
            indent=2,
        )
    )


def doctor() -> None:
    from app.runtime.doctor import collect_doctor_report

    deep = "--deep" in sys.argv[1:]
    result = collect_doctor_report(deep=deep)
    print(json.dumps(result, indent=2))
    if result["status"] == "BLOCKED":
        raise SystemExit(2)


def recover() -> None:
    from app.runtime.recovery import recover_runtime

    execute = "--execute" in sys.argv[1:]
    result = recover_runtime(execute=execute)
    print(json.dumps(result, indent=2))
    if result.get("status") in {"error", "attention"}:
        raise SystemExit(2)


def seed_demo() -> None:
    from mapi.seed import seed_demo_database

    print(json.dumps(seed_demo_database(_database_path()), indent=2))


def demo() -> None:
    from mapi.demo import run_isolated_demo

    result = run_isolated_demo()
    print(result["human_output"])


def server() -> None:
    os.environ.setdefault("MCP_SURFACE_PROFILE", "agent")
    os.environ.setdefault("MAPI_RUNTIME_HOST", "127.0.0.1")
    from app.runtime.server_runtime import run_server

    run_server()
