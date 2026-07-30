from __future__ import annotations

"""SQLite admin helper functions for MAPI tools."""

from pathlib import Path
from typing import Any, Callable


def get_db_info_payload(*, db_path: Path, get_db_connection: Callable[[], Any]) -> dict[str, Any]:
    conn = get_db_connection()
    try:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
        memory_count = conn.execute("SELECT COUNT(*) AS count FROM memories").fetchone()["count"]
        link_count = conn.execute("SELECT COUNT(*) AS count FROM memory_links").fetchone()["count"]
    finally:
        conn.close()
    return {"db_path": str(db_path), "exists": db_path.exists(), "size": db_path.stat().st_size if db_path.exists() else 0, "tables": [row["name"] for row in tables], "memory_count": memory_count, "link_count": link_count}


def query_sql_payload(
    *,
    get_db_connection: Callable[[], Any],
    parse_params_json: Callable[[str], Any],
    is_read_only_sql: Callable[[str], bool],
    row_to_dict: Callable[[Any], dict[str, Any]],
    query: str,
    params_json: str = "[]",
    allow_write: bool = False,
    max_rows: int = 100,
) -> dict[str, Any]:
    sql = (query or "").strip()
    if not sql:
        return {"status": "error", "error": 'query nie może być puste'}
    if max_rows < 1:
        return {"status": "error", "error": 'max_rows musi być >= 1'}
    params = parse_params_json(params_json)
    if not allow_write and not is_read_only_sql(sql):
        return {"status": "error", "error": 'To zapytanie wygląda na modyfikujące dane. Ustaw allow_write=True, jeśli chcesz je wykonać.'}
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        if cursor.description is None:
            conn.commit()
            return {"query": sql, "params": params, "allow_write": allow_write, "rowcount": cursor.rowcount, "lastrowid": cursor.lastrowid, "returned_rows": 0, "rows": []}
        fetched = cursor.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = fetched[:max_rows]
        return {"query": sql, "params": params, "allow_write": allow_write, "columns": [column[0] for column in cursor.description], "returned_rows": len(rows), "truncated": truncated, "rows": [row_to_dict(row) for row in rows]}
    finally:
        conn.close()
