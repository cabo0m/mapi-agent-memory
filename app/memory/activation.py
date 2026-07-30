from __future__ import annotations

"""Memory activation mutation payloads."""

from typing import Any, Callable


def recall_memory_payload(
    conn: Any,
    *,
    memory_id: int,
    strength: float = 0.1,
    recall_type: str = "manual",
    require_memory_row: Callable[[Any, int], Any],
    normalize_score: Callable[[float], float],
    utc_now_iso: Callable[[], str],
    row_to_dict: Callable[[Any], dict[str, Any]],
    enrich_memory_dict: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    memory = require_memory_row(conn, int(memory_id))
    current_importance = float(memory["importance_score"] or 0.0)
    new_importance = normalize_score(current_importance + float(strength))
    recalled_at = utc_now_iso()
    conn.execute(
        "UPDATE memories SET importance_score = ?, recall_count = recall_count + 1, last_recalled_at = ?, last_accessed_at = ? WHERE id = ?",
        (new_importance, recalled_at, recalled_at, int(memory_id)),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
    return {
        "status": "recalled",
        "recall_type": recall_type,
        "updated_memory": enrich_memory_dict(row_to_dict(updated)),
        "activation_changes": [
            {
                "memory_id": int(memory_id),
                "old_importance_score": current_importance,
                "new_importance_score": new_importance,
            }
        ],
    }
