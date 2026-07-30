from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

VALID_SCOPES = frozenset({"memories", "conversations", "all"})
VALID_SOURCES = frozenset({"claude", "chatgpt", "slack", "manual", "other"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count_words(text: str) -> int:
    return len(text.split()) if text else 0


def _fts5_phrase(query: str) -> str:
    """Wrap query as an FTS5 phrase so multi-word searches match exact sequences."""
    return '"' + query.replace('"', '""') + '"'


def _is_fts5_missing_table_error(e: sqlite3.OperationalError) -> bool:
    msg = str(e).lower()
    return "no such table" in msg or "no such module" in msg


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def store_conversation(
    conn: sqlite3.Connection,
    content: str,
    title: str | None = None,
    source: str = "manual",
    project_key: str | None = None,
    workspace_key: str = "default",
    user_key: str = "owner",
    tags: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    if source not in VALID_SOURCES:
        return {"status": "error", "error": f"source musi być jednym z: {sorted(VALID_SOURCES)}"}
    if not conversation_id:
        conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    now = _now_iso()
    word_count = _count_words(content)
    cursor = conn.execute(
        """
        INSERT INTO conversation_archives
            (conversation_id, title, source, content, project_key, workspace_key,
             user_key, tags, word_count, created_at, archived_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (conversation_id, title, source, content, project_key, workspace_key,
         user_key, tags, word_count, now, now),
    )
    conn.commit()
    return {"id": cursor.lastrowid, "conversation_id": conversation_id, "status": "stored"}


def get_conversation_by_id(conn: sqlite3.Connection, conversation_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM conversation_archives WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_conversations(
    conn: sqlite3.Connection,
    project_key: str | None = None,
    workspace_key: str | None = None,
    user_key: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    clauses: list[str] = []
    params: list = []
    if project_key is not None:
        clauses.append("project_key = ?")
        params.append(project_key)
    if workspace_key is not None:
        clauses.append("workspace_key = ?")
        params.append(workspace_key)
    if user_key is not None:
        clauses.append("user_key = ?")
        params.append(user_key)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = int(conn.execute(
        f"SELECT COUNT(*) FROM conversation_archives {where}", params
    ).fetchone()[0])
    rows = conn.execute(
        f"""
        SELECT id, conversation_id, title, source, project_key, workspace_key,
               user_key, tags, word_count, created_at, archived_at
        FROM conversation_archives
        {where}
        ORDER BY archived_at DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()
    return {"total": total, "items": [dict(r) for r in rows]}


def search_verbatim_memories(
    conn: sqlite3.Connection,
    query: str,
    project_key: str | None = None,
    limit: int = 20,
) -> list[dict]:
    fts_query = _fts5_phrase(query)
    try:
        if project_key:
            rows = conn.execute(
                """
                SELECT m.id, m.summary_short, m.memory_type, m.tags, m.importance_score,
                       snippet(memories_fts, 0, '[', ']', '…', 20) AS snippet
                FROM memories_fts
                JOIN memories m ON memories_fts.rowid = m.id
                WHERE memories_fts MATCH ? AND m.project_key = ? AND m.archived_at IS NULL
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, project_key, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT m.id, m.summary_short, m.memory_type, m.tags, m.importance_score,
                       snippet(memories_fts, 0, '[', ']', '…', 20) AS snippet
                FROM memories_fts
                JOIN memories m ON memories_fts.rowid = m.id
                WHERE memories_fts MATCH ? AND m.archived_at IS NULL
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
    except sqlite3.OperationalError as e:
        if not _is_fts5_missing_table_error(e):
            raise
        # FTS5 table missing — fallback to LIKE
        like = f"%{_like_escape(query)}%"
        rows = conn.execute(
            """
            SELECT id, summary_short, memory_type, tags, importance_score,
                   SUBSTR(content, 1, 120) AS snippet
            FROM memories
            WHERE (content LIKE ? OR summary_short LIKE ?)
              AND archived_at IS NULL
            ORDER BY importance_score DESC
            LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def search_verbatim_conversations(
    conn: sqlite3.Connection,
    query: str,
    project_key: str | None = None,
    limit: int = 20,
) -> list[dict]:
    fts_query = _fts5_phrase(query)
    try:
        if project_key:
            rows = conn.execute(
                """
                SELECT ca.id, ca.conversation_id, ca.title, ca.source, ca.tags, ca.archived_at,
                       snippet(conversations_fts, 1, '[', ']', '…', 20) AS snippet
                FROM conversations_fts
                JOIN conversation_archives ca ON conversations_fts.rowid = ca.id
                WHERE conversations_fts MATCH ? AND ca.project_key = ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, project_key, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT ca.id, ca.conversation_id, ca.title, ca.source, ca.tags, ca.archived_at,
                       snippet(conversations_fts, 1, '[', ']', '…', 20) AS snippet
                FROM conversations_fts
                JOIN conversation_archives ca ON conversations_fts.rowid = ca.id
                WHERE conversations_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
    except sqlite3.OperationalError as e:
        if not _is_fts5_missing_table_error(e):
            raise
        like = f"%{_like_escape(query)}%"
        rows = conn.execute(
            """
            SELECT id, conversation_id, title, source, tags, archived_at,
                   SUBSTR(content, 1, 120) AS snippet
            FROM conversation_archives
            WHERE content LIKE ? OR title LIKE ?
            ORDER BY archived_at DESC
            LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
    return [dict(r) for r in rows]
