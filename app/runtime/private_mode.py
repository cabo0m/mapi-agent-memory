from __future__ import annotations

"""Fail-closed single-instance runtime contract for public MAPI."""

import os
import sqlite3
from pathlib import Path
from typing import Any

from app.runtime.context import runtime_db_path

PRIVATE_MODE_SCHEMA = "mapi_single_instance.v1"
PRIVATE_RUNTIME_MODE = "single_instance"
LEGACY_PUBLIC_MODE = "unsupported_multi_user"
DEFAULT_OWNER_KEY = "owner"

BLOCKED_MULTIUSER_FLAGS = frozenset({
    "multiuser_identity_enabled",
    "multiuser_scope_retrieval_enabled",
    "multiuser_timeline_actor_enabled",
    "multiuser_scope_maintenance_enabled",
    "multiuser_scope_promotion_enabled",
})


def runtime_mode() -> str:
    return str(os.environ.get("MAPI_RUNTIME_MODE", PRIVATE_RUNTIME_MODE)).strip().lower() or PRIVATE_RUNTIME_MODE


def private_single_user_enabled() -> bool:
    return runtime_mode() != LEGACY_PUBLIC_MODE


def private_owner_key() -> str:
    return str(os.environ.get("MAPI_OWNER_KEY", DEFAULT_OWNER_KEY)).strip().lower() or DEFAULT_OWNER_KEY


def private_identity_allowed(identity_key: str | None) -> bool:
    if not private_single_user_enabled():
        return True
    return str(identity_key or "").strip().lower() == private_owner_key()


def require_private_owner(identity_key: str | None) -> str:
    if not private_identity_allowed(identity_key):
        raise PermissionError("private_single_user_identity_denied")
    return private_owner_key()


def assert_public_mpbm_available() -> None:
    if private_single_user_enabled():
        raise PermissionError("public_mpbm_disabled_private_single_user")


def effective_multiuser_flag_enabled(flag_key: str, database_enabled: bool) -> bool:
    if private_single_user_enabled() and str(flag_key) in BLOCKED_MULTIUSER_FLAGS:
        return False
    return bool(database_enabled)


def public_product_disabled_payload(component: str) -> dict[str, Any]:
    return {
        "status": "disabled",
        "error": "public_mpbm_disabled",
        "component": str(component),
        "runtime_mode": runtime_mode(),
        "owner_key": private_owner_key(),
        "self_registration_enabled": False,
        "onboarding_enabled": False,
        "invite_flows_enabled": False,
        "uninvited_oauth_enabled": False,
    }


def _count(conn: sqlite3.Connection, table: str) -> int | None:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return None


def get_private_runtime_status(include_debug: bool = False) -> dict[str, Any]:
    enabled = private_single_user_enabled()
    flag_rows: list[dict[str, Any]] = []
    historical_counts: dict[str, int | None] = {
        "users": None,
        "workspaces": None,
        "workspace_memberships": None,
        "onboarding_rows": None,
        "scope_promotion_rows": None,
    }
    path = Path(runtime_db_path()).resolve()
    if path.exists():
        try:
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                placeholders = ",".join("?" for _ in BLOCKED_MULTIUSER_FLAGS)
                rows = conn.execute(
                    f"SELECT flag_key,is_enabled,rollout_mode FROM feature_flags WHERE flag_key IN ({placeholders}) ORDER BY flag_key",
                    sorted(BLOCKED_MULTIUSER_FLAGS),
                ).fetchall()
                database_flags = {str(row["flag_key"]): row for row in rows}
                flag_rows = []
                for flag_key in sorted(BLOCKED_MULTIUSER_FLAGS):
                    row = database_flags.get(flag_key)
                    database_enabled = bool(row["is_enabled"]) if row is not None else False
                    flag_rows.append(
                        {
                            "flag_key": flag_key,
                            "database_enabled": database_enabled,
                            "effective_enabled": effective_multiuser_flag_enabled(flag_key, database_enabled),
                            "rollout_mode": row["rollout_mode"] if row is not None else None,
                            "database_row_present": row is not None,
                        }
                    )
                historical_counts = {
                    "users": _count(conn, "users"),
                    "workspaces": _count(conn, "workspaces"),
                    "workspace_memberships": _count(conn, "workspace_memberships"),
                    "onboarding_rows": _count(conn, "mpbm_user_onboarding"),
                    "scope_promotion_rows": _count(conn, "scope_promotion_proposals"),
                }
        except sqlite3.Error:
            flag_rows = [
                {
                    "flag_key": flag_key,
                    "database_enabled": False,
                    "effective_enabled": False,
                    "rollout_mode": None,
                    "database_row_present": False,
                }
                for flag_key in sorted(BLOCKED_MULTIUSER_FLAGS)
            ]
    if not flag_rows:
        flag_rows = [
            {
                "flag_key": flag_key,
                "database_enabled": False,
                "effective_enabled": False,
                "rollout_mode": None,
                "database_row_present": False,
            }
            for flag_key in sorted(BLOCKED_MULTIUSER_FLAGS)
        ]
    effective_multiuser_enabled = any(bool(item["effective_enabled"]) for item in flag_rows)
    ready = enabled and bool(private_owner_key()) and not effective_multiuser_enabled
    result: dict[str, Any] = {
        "status": "ready" if ready else "attention",
        "schema": PRIVATE_MODE_SCHEMA,
        "runtime_mode": runtime_mode(),
        "private_single_user_enabled": enabled,
        "owner_key": private_owner_key(),
        "allowed_human_identity_count": 1 if enabled else None,
        "public_mpbm_enabled": not enabled,
        "onboarding_enabled": not enabled,
        "invite_flows_enabled": not enabled,
        "self_registration_enabled": not enabled,
        "uninvited_oauth_enabled": False,
        "multiuser_flags": flag_rows,
        "effective_multiuser_flag_count": sum(1 for item in flag_rows if item["effective_enabled"]),
        "historical_data_preserved": True,
        "historical_counts": historical_counts,
        "project_boundaries_preserved": True,
        "technical_actor_audit_preserved": ["owner", "agent", "maintainer", "sandman", "admin"],
    }
    if include_debug:
        result["database_path"] = str(path)
        result["blocked_multiuser_flags"] = sorted(BLOCKED_MULTIUSER_FLAGS)
    return result
