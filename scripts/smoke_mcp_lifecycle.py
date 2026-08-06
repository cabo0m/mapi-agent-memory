from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def lifecycle_smoke(path: Path) -> dict[str, Any]:
    """Exercise guarded lifecycle contracts in a disposable database.

    This test intentionally keeps the runtime profile at maintainer and confirms that the
    admin workshop remains denied. The lifecycle calls use the same registered handlers as
    MCP workshop actions, directly, so no public server or private database is required.
    """
    os.environ["MCP_SURFACE_PROFILE"] = "maintainer"
    os.environ["MAPI_ADMIN_TOOLS_ENABLED"] = "false"

    from mapi.demo import run_demo_database

    result = run_demo_database(path)

    import server_core

    admin = server_core.open_workshop("admin")
    if admin.get("status") != "denied":
        raise RuntimeError(f"Admin workshop must remain denied: {admin}")

    connection = server_core.get_db_connection()
    try:
        foreign = server_core._insert_memory(
            connection,
            content="A decision in a different demo project.",
            summary_short="Foreign project decision",
            memory_type="decision",
            project_key="mapi-product-demo-other",
            scope_code="project",
            source="synthetic-lifecycle-smoke",
            source_event_ref="mapi-lifecycle-smoke:foreign",
            truth_kind="decision",
            ensure_embedding=False,
        )
        connection.commit()
    finally:
        connection.close()

    blocked = server_core.preview_memory_supersession(
        new_memory_id=int(foreign["id"]),
        old_memory_id=int(result["previous_memory_id"]),
        relation_kind="replacement",
        reason="Cross-project transitions must fail.",
    )
    reasons = set((blocked.get("guard") or {}).get("blockers") or [])
    if blocked.get("status") != "blocked" or not reasons.intersection(
        {"project_mismatch", "scope_mismatch", "cross_project_or_scope", "cross_project"}
    ):
        raise RuntimeError(f"Cross-project guard failed: {blocked}")

    current = server_core.get_memory_current_state(
        int(result["previous_memory_id"]), include_history=True
    )
    if int((current.get("current") or {}).get("id") or 0) != int(result["current_memory_id"]):
        raise RuntimeError(f"Current state failed after lifecycle apply: {current}")
    if [int(item["id"]) for item in current.get("history") or []] != [
        int(result["previous_memory_id"])
    ]:
        raise RuntimeError(f"History was not preserved: {current}")

    return {
        "status": "ok",
        "profile": "maintainer",
        "database": str(path),
        "current_memory_id": result["current_memory_id"],
        "previous_memory_id": result["previous_memory_id"],
        "preview_hash": result["preview_hash"],
        "cross_project_status": blocked["status"],
        "admin_status": admin["status"],
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="mapi-lifecycle-smoke-") as directory:
        print(json.dumps(lifecycle_smoke(Path(directory) / "lifecycle.db"), indent=2))


if __name__ == "__main__":
    main()
