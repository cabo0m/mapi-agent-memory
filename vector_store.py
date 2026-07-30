"""
vector_store.py — semantic search dla MAPI
Model: all-MiniLM-L6-v2 (sentence-transformers, lokalnie)
Storage: sqlite-vec (rozszerzenie SQLite, tabela memory_embeddings)
"""
from __future__ import annotations

import json
import sqlite3
import struct
from typing import Any

# ---------------------------------------------------------------------------
# Model (lazy load — ładuje się przy pierwszym użyciu)
# ---------------------------------------------------------------------------

_model: Any | None = None
_MODEL_NAME = "all-MiniLM-L6-v2"
_EMBEDDING_DIM = 384  # wymiar wektora dla all-MiniLM-L6-v2


def _get_model() -> Any:
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                'Semantic search is unavailable. Install the optional extra with pip install -e ".[semantic]".'
            ) from exc
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


# ---------------------------------------------------------------------------
# Pomocnicze: serializacja wektora do/z blob SQLite
# ---------------------------------------------------------------------------

def _vec_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


# ---------------------------------------------------------------------------
# Setup połączenia z sqlite-vec
# ---------------------------------------------------------------------------

def _load_vec_extension(conn: sqlite3.Connection) -> None:
    try:
        import sqlite_vec
    except ImportError as exc:
        raise RuntimeError(
            'Semantic search is unavailable. Install the optional extra with pip install -e ".[semantic]".'
        ) from exc
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


# ---------------------------------------------------------------------------
# Migracja: tworzy tabelę memory_embeddings jeśli nie istnieje
# ---------------------------------------------------------------------------

def ensure_embeddings_table(conn: sqlite3.Connection) -> None:
    """Tworzy wirtualną tabelę vec i tabelę metadanych."""
    _load_vec_extension(conn)
    conn.executescript(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings_vec
        USING vec0(
            memory_id INTEGER PRIMARY KEY,
            embedding FLOAT[{_EMBEDDING_DIM}]
        );

        CREATE TABLE IF NOT EXISTS memory_embeddings_meta (
            memory_id   INTEGER PRIMARY KEY,
            model_name  TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Generowanie embeddingu dla jednego wspomnienia
# ---------------------------------------------------------------------------

def _text_for_memory(memory: dict[str, Any]) -> str:
    """Łączy pola tekstowe wspomnienia w jeden string do embeddingu."""
    parts = []
    if memory.get("summary_short"):
        parts.append(memory["summary_short"])
    if memory.get("content"):
        parts.append(memory["content"])
    if memory.get("tags"):
        parts.append(memory["tags"])
    return " | ".join(parts)


def embed_memory(conn: sqlite3.Connection, memory: dict[str, Any]) -> None:
    """Generuje i zapisuje embedding dla jednego wspomnienia."""
    _load_vec_extension(conn)
    model = _get_model()
    memory_id = memory["id"]
    text = _text_for_memory(memory)
    vec = model.encode(text, normalize_embeddings=True).tolist()
    blob = _vec_to_blob(vec)

    conn.execute(
        "DELETE FROM memory_embeddings_vec WHERE memory_id = ?",
        (memory_id,)
    )
    conn.execute(
        "INSERT INTO memory_embeddings_vec(memory_id, embedding) VALUES (?, ?)",
        (memory_id, blob)
    )
    conn.execute(
        """INSERT INTO memory_embeddings_meta(memory_id, model_name, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(memory_id) DO UPDATE SET
               model_name = excluded.model_name,
               updated_at = excluded.updated_at""",
        (memory_id, _MODEL_NAME)
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Backfill: generuje embeddingi dla wszystkich wspomnień bez embeddingu
# ---------------------------------------------------------------------------

def backfill_embeddings(conn: sqlite3.Connection, project_key: str | None = None) -> dict[str, Any]:
    """
    Generuje embeddingi dla wspomnień które jeszcze ich nie mają.
    Zwraca statystyki: ile przetworzono, ile pominięto, ile błędów.
    """
    _load_vec_extension(conn)
    ensure_embeddings_table(conn)

    where_project = "AND m.project_key = ?" if project_key else ""
    params: list[Any] = [project_key] if project_key else []

    rows = conn.execute(f"""
        SELECT m.id, m.content, m.summary_short, m.tags
        FROM memories m
        LEFT JOIN memory_embeddings_meta em ON m.id = em.memory_id
        WHERE m.archived_at IS NULL
          AND em.memory_id IS NULL
          {where_project}
        ORDER BY m.id
    """, params).fetchall()

    processed = 0
    errors = 0
    model = _get_model()

    for row in rows:
        memory = {
            "id": row[0],
            "content": row[1],
            "summary_short": row[2],
            "tags": row[3],
        }
        try:
            text = _text_for_memory(memory)
            vec = model.encode(text, normalize_embeddings=True).tolist()
            blob = _vec_to_blob(vec)
            conn.execute(
                "DELETE FROM memory_embeddings_vec WHERE memory_id = ?",
                (memory["id"],)
            )
            conn.execute(
                "INSERT INTO memory_embeddings_vec(memory_id, embedding) VALUES (?, ?)",
                (memory["id"], blob)
            )
            conn.execute(
                """INSERT INTO memory_embeddings_meta(memory_id, model_name, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(memory_id) DO UPDATE SET
                       model_name = excluded.model_name,
                       updated_at = excluded.updated_at""",
                (memory["id"], _MODEL_NAME)
            )
            processed += 1
            if processed % 50 == 0:
                conn.commit()
                print(f"  backfill: {processed}/{len(rows)}")
        except Exception as e:
            errors += 1
            print(f"  ERROR memory_id={memory['id']}: {e}")

    conn.commit()
    return {
        "total_candidates": len(rows),
        "processed": processed,
        "errors": errors,
        "model": _MODEL_NAME,
    }


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------

def search_semantic(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 10,
    project_key: str | None = None,
) -> list[dict[str, Any]]:
    """
    Wyszukuje wspomnienia semantycznie podobne do query.
    Zwraca listę wyników z distance i podstawowymi polami wspomnienia.
    """
    _load_vec_extension(conn)
    model = _get_model()
    vec = model.encode(query, normalize_embeddings=True).tolist()
    blob = _vec_to_blob(vec)

    # sqlite-vec: vec_distance_cosine, szukamy top_k * 3 żeby mieć margines na filtr project
    candidates_limit = top_k * 4 if project_key else top_k

    rows = conn.execute(
        """
        SELECT
            v.memory_id,
            v.distance,
            m.summary_short,
            m.memory_type,
            m.project_key,
            m.importance_score,
            m.tags,
            m.created_at
        FROM memory_embeddings_vec v
        JOIN memories m ON m.id = v.memory_id
        WHERE m.archived_at IS NULL
          AND v.embedding MATCH ?
          AND k = ?
        ORDER BY v.distance
        """,
        (blob, candidates_limit)
    ).fetchall()

    results = []
    for row in rows:
        if project_key and row[4] != project_key:
            continue
        results.append({
            "memory_id": row[0],
            "distance": round(row[1], 4),
            "similarity": round(max(0.0, 1.0 - (row[1] * row[1]) / 2.0), 4),
            "summary_short": row[2],
            "memory_type": row[3],
            "project_key": row[4],
            "importance_score": row[5],
            "tags": row[6],
            "created_at": row[7],
        })
        if len(results) >= top_k:
            break

    return results


# ---------------------------------------------------------------------------
# Statystyki pokrycia embeddingów
# ---------------------------------------------------------------------------

def embedding_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Zwraca ile wspomnień ma embeddingi, ile nie ma."""
    _load_vec_extension(conn)
    total_active = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE archived_at IS NULL"
    ).fetchone()[0]
    with_embedding = conn.execute(
        "SELECT COUNT(*) FROM memory_embeddings_meta"
    ).fetchone()[0]
    return {
        "total_active_memories": total_active,
        "with_embedding": with_embedding,
        "without_embedding": total_active - with_embedding,
        "coverage_pct": round(with_embedding / total_active * 100, 1) if total_active else 0,
        "model": _MODEL_NAME,
        "embedding_dim": _EMBEDDING_DIM,
    }
