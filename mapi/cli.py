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
    import server  # noqa: F401 - runtime import binds the authoritative handlers

    from app.workshops.catalog import WORKSHOPS
    from app.workshops.runtime_registry import validate_workshop_handler_registry
    from mcp_surface import normalize_surface_profile, surface_manifest

    path = _database_path()
    migration_tail: str | None = None
    database_ok = False
    if path.exists():
        try:
            with sqlite3.connect(path) as connection:
                row = connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                migration_tail = str(row[0]) if row else None
                database_ok = connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        except sqlite3.Error:
            database_ok = False

    profile = normalize_surface_profile(os.environ.get("MCP_SURFACE_PROFILE", "agent"))
    registry = validate_workshop_handler_registry()
    optional = {
        "semantic": _module_available("sentence_transformers") and _module_available("sqlite_vec"),
        "gemini": _module_available("google.genai"),
    }
    result: dict[str, Any] = {
        "status": "ready" if database_ok and registry["complete"] else "attention",
        "python": sys.version.split()[0],
        "database": str(path),
        "database_ok": database_ok,
        "migration_tail": migration_tail,
        "profile": profile,
        "visible_workshops": len(surface_manifest(profile)["workshops"]),
        "registered_workshops": len(WORKSHOPS),
        "registry_complete": bool(registry["complete"]),
        "optional_capabilities": optional,
    }
    print(json.dumps(result, indent=2))
    if result["status"] != "ready":
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
