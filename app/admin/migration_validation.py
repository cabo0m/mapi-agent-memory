from __future__ import annotations

"""Read-only migration validation payloads."""

from typing import Any


def validate_migration_0010_payload(conn: Any) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    row = conn.execute("SELECT COUNT(*) AS cnt FROM memories WHERE workspace_id IS NULL").fetchone()
    checks["memories_missing_workspace"] = int(row["cnt"])

    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM memories WHERE visibility_scope IS NULL OR TRIM(visibility_scope) = ''"
    ).fetchone()
    checks["memories_missing_scope"] = int(row["cnt"])

    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM memories WHERE visibility_scope = 'private' AND owner_user_id IS NULL"
    ).fetchone()
    checks["private_without_owner"] = int(row["cnt"])

    scope_rows = conn.execute(
        "SELECT visibility_scope, COUNT(*) AS cnt FROM memories GROUP BY visibility_scope ORDER BY cnt DESC"
    ).fetchall()
    checks["scope_distribution"] = {r["visibility_scope"]: r["cnt"] for r in scope_rows}

    row = conn.execute("SELECT COUNT(*) AS cnt FROM memory_links WHERE workspace_id IS NULL").fetchone()
    checks["links_missing_workspace"] = int(row["cnt"])

    row = conn.execute("SELECT COUNT(*) AS cnt FROM timeline_events WHERE workspace_id IS NULL").fetchone()
    checks["timeline_missing_workspace"] = int(row["cnt"])

    cross_rows = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM memory_links ml
        JOIN memories m1 ON m1.id = ml.from_memory_id
        JOIN memories m2 ON m2.id = ml.to_memory_id
        WHERE m1.visibility_scope <> m2.visibility_scope
          AND m1.visibility_scope NOT IN ('inherited', 'workspace')
          AND m2.visibility_scope NOT IN ('inherited', 'workspace')
        """
    ).fetchone()
    checks["cross_scope_links"] = int(cross_rows["cnt"])

    row = conn.execute("SELECT COUNT(*) AS cnt FROM workspaces").fetchone()
    checks["workspace_count"] = int(row["cnt"])
    row = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
    checks["user_count"] = int(row["cnt"])

    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM memories
        WHERE visibility_scope = 'project'
          AND (project_key IS NULL OR TRIM(project_key) = '')
        """
    ).fetchone()
    checks["project_scope_without_project_key"] = int(row["cnt"])

    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM memories
        WHERE visibility_scope = 'workspace' AND workspace_id IS NULL
        """
    ).fetchone()
    checks["workspace_scope_without_workspace_id"] = int(row["cnt"])

    flag_rows = conn.execute(
        "SELECT flag_key, is_enabled, rollout_mode FROM feature_flags WHERE flag_key LIKE 'multiuser_%'"
    ).fetchall()
    checks["multiuser_flags"] = {
        r["flag_key"]: {"is_enabled": bool(r["is_enabled"]), "rollout_mode": r["rollout_mode"]}
        for r in flag_rows
    }

    red_flags = [
        k
        for k in (
            "memories_missing_workspace",
            "memories_missing_scope",
            "private_without_owner",
            "links_missing_workspace",
            "project_scope_without_project_key",
            "workspace_scope_without_workspace_id",
        )
        if checks.get(k, 0) > 0
    ]

    checks["red_flags"] = red_flags
    checks["status"] = "clean" if not red_flags else "needs_attention"
    return checks
