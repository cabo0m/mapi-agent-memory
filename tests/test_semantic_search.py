"""
tests/test_semantic_search.py
Testy regresyjne dla vector_store i narzędzi MAPI search_semantic/backfill.
"""
import sys
import os
import sqlite3
import hashlib
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytestmark = pytest.mark.semantic


class _DeterministicVector(list[float]):
    def tolist(self) -> list[float]:
        return list(self)


class _DeterministicEmbeddingModel:
    def encode(self, text: str, *, normalize_embeddings: bool) -> _DeterministicVector:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values = [float(digest[index % len(digest)] + 1) for index in range(384)]
        if normalize_embeddings:
            magnitude = sum(value * value for value in values) ** 0.5
            values = [value / magnitude for value in values]
        return _DeterministicVector(values)


@pytest.fixture(autouse=True)
def _use_deterministic_local_embeddings(monkeypatch):
    import vector_store

    monkeypatch.setattr(vector_store, "_model", _DeterministicEmbeddingModel())


# ---------------------------------------------------------------------------
# Helpers: fake DB z kilkoma wspomnieniami
# ---------------------------------------------------------------------------

def _make_fake_db():
    """Tworzy in-memory DB z minimalnym schematem."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            content TEXT,
            summary_short TEXT,
            tags TEXT,
            archived_at TEXT,
            memory_type TEXT DEFAULT 'project_note',
            project_key TEXT,
            importance_score REAL DEFAULT 0.5,
            created_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO memories (id, content, summary_short, tags, project_key)
        VALUES
            (1, 'Caddy reverse proxy dziala na VPS', 'VPS Caddy reverse proxy', 'caddy,vps,proxy', 'demo-project'),
            (2, 'OAuth token bearer security', 'Bearer token security', 'oauth,security,token', 'demo-project'),
            (3, 'Pytest regresyjne testy sandman', 'Testy sandman', 'pytest,sandman,testy', NULL);
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Test: ensure_embeddings_table tworzy tabele
# ---------------------------------------------------------------------------

def test_ensure_embeddings_table_creates_tables():
    from vector_store import ensure_embeddings_table
    conn = _make_fake_db()
    ensure_embeddings_table(conn)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "memory_embeddings_meta" in tables
    conn.close()


# ---------------------------------------------------------------------------
# Test: backfill generuje embeddingi dla wszystkich wspomnień
# ---------------------------------------------------------------------------

def test_backfill_generates_embeddings():
    from vector_store import ensure_embeddings_table, backfill_embeddings, embedding_stats
    conn = _make_fake_db()
    ensure_embeddings_table(conn)
    result = backfill_embeddings(conn)
    assert result["processed"] == 3
    assert result["errors"] == 0
    stats = embedding_stats(conn)
    assert stats["with_embedding"] == 3
    assert stats["coverage_pct"] == 100.0
    conn.close()


# ---------------------------------------------------------------------------
# Test: backfill pomija już przetworzone wspomnienia
# ---------------------------------------------------------------------------

def test_backfill_skips_already_embedded():
    from vector_store import ensure_embeddings_table, backfill_embeddings
    conn = _make_fake_db()
    ensure_embeddings_table(conn)
    r1 = backfill_embeddings(conn)
    assert r1["processed"] == 3
    r2 = backfill_embeddings(conn)
    assert r2["processed"] == 0  # nic nowego
    conn.close()


# ---------------------------------------------------------------------------
# Test: search_semantic zwraca wyniki posortowane malejąco po similarity
# ---------------------------------------------------------------------------

def test_search_semantic_returns_sorted_results():
    from vector_store import ensure_embeddings_table, backfill_embeddings, search_semantic
    conn = _make_fake_db()
    ensure_embeddings_table(conn)
    backfill_embeddings(conn)
    results = search_semantic(conn, "reverse proxy caddy", top_k=3)
    assert len(results) > 0
    sims = [r["similarity"] for r in results]
    assert sims == sorted(sims, reverse=True), "Wyniki muszą być posortowane malejąco"
    conn.close()


# ---------------------------------------------------------------------------
# Test: search_semantic filtruje po project_key
# ---------------------------------------------------------------------------

def test_search_semantic_filters_by_project_key():
    from vector_store import ensure_embeddings_table, backfill_embeddings, search_semantic
    conn = _make_fake_db()
    ensure_embeddings_table(conn)
    backfill_embeddings(conn)
    results = search_semantic(conn, "testy sandman", top_k=10, project_key="demo-project")
    # memory_id=3 ma project_key=NULL, nie powinno być w wynikach
    ids = [r["memory_id"] for r in results]
    assert 3 not in ids, "Memory bez project_key demo-project nie powinna być w wynikach"
    conn.close()


# ---------------------------------------------------------------------------
# Test: similarity jest w zakresie 0.0-1.0
# ---------------------------------------------------------------------------

def test_search_semantic_similarity_range():
    from vector_store import ensure_embeddings_table, backfill_embeddings, search_semantic
    conn = _make_fake_db()
    ensure_embeddings_table(conn)
    backfill_embeddings(conn)
    results = search_semantic(conn, "oauth security token", top_k=5)
    for r in results:
        assert 0.0 <= r["similarity"] <= 1.0, f"similarity poza zakresem: {r['similarity']}"
    conn.close()


# ---------------------------------------------------------------------------
# Test: server_core.search_semantic zwraca status ok
# ---------------------------------------------------------------------------

def test_server_core_search_semantic_returns_ok(monkeypatch):
    import server_core
    from vector_store import backfill_embeddings, ensure_embeddings_table

    fixture_db = _make_fake_db()
    ensure_embeddings_table(fixture_db)
    backfill_embeddings(fixture_db)

    monkeypatch.setattr(server_core, "get_db_connection", lambda: fixture_db)
    result = server_core.search_semantic("reverse proxy caddy", top_k=3)

    assert result["status"] == "ok"
    assert "results" in result
    assert isinstance(result["results"], list)
    assert result["results"]
