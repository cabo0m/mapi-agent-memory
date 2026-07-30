from __future__ import annotations

"""Read-only preview / safe writer for creating memory links.

Examples:
    python auto_link_memories.py --project demo-project --preview
    python auto_link_memories.py --project demo-project --run --limit 120

The script uses only Python standard library and writes only to memory_links.
"""

import argparse
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "agent_memory.db"

STOPWORDS = {
    "oraz", "jest", "jako", "przez", "który", "która", "ktore", "które", "jego", "jej", "dla",
    "the", "and", "with", "from", "this", "that", "into", "about", "memory", "memories",
    "projekt", "project", "owner", "owner", "agent", "memory", "api",
}


@dataclass(frozen=True)
class CandidateLink:
    from_memory_id: int
    to_memory_id: int
    relation_type: str
    weight: float
    origin: str
    reason: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_text(value: Any) -> str:
    text = clean(value).lower()
    text = re.sub(r"[^0-9a-ząćęłńóśźż]+", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def tokens(value: Any) -> set[str]:
    text = normalize_text(value)
    return {token for token in text.split() if len(token) >= 4 and token not in STOPWORDS}


def tags(value: Any) -> set[str]:
    return {normalize_text(part) for part in clean(value).split(",") if normalize_text(part)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def load_memories(conn: sqlite3.Connection, project: str | None, include_archived: bool) -> list[dict[str, Any]]:
    sql = "SELECT * FROM memories WHERE 1 = 1"
    params: list[Any] = []
    if project:
        sql += " AND project_key = ?"
        params.append(project)
    if not include_archived:
        sql += " AND COALESCE(activity_state, 'active') != 'archived'"
    sql += " ORDER BY id ASC"
    return [row_to_dict(row) for row in conn.execute(sql, params).fetchall()]


def load_existing_links(conn: sqlite3.Connection) -> set[tuple[int, int, str]]:
    rows = conn.execute(
        """
        SELECT from_memory_id, to_memory_id, relation_type
        FROM memory_links
        WHERE archived_at IS NULL
        """
    ).fetchall()
    return {(int(row["from_memory_id"]), int(row["to_memory_id"]), clean(row["relation_type"])) for row in rows}


def add_candidate(
    candidates: dict[tuple[int, int, str], CandidateLink],
    existing: set[tuple[int, int, str]],
    from_id: int,
    to_id: int,
    relation_type: str,
    weight: float,
    reason: str,
) -> None:
    if from_id == to_id:
        return
    relation = relation_type.strip()
    key = (int(from_id), int(to_id), relation)
    if key in existing:
        return
    old = candidates.get(key)
    weight = max(0.0, min(1.0, float(weight)))
    origin = "auto_link_memories_v1"
    candidate = CandidateLink(int(from_id), int(to_id), relation, weight, origin, reason)
    if old is None or candidate.weight > old.weight:
        candidates[key] = candidate


def explicit_id_candidates(
    memories: list[dict[str, Any]],
    memory_ids: set[int],
    candidates: dict[tuple[int, int, str], CandidateLink],
    existing: set[tuple[int, int, str]],
) -> None:
    pattern = re.compile(r"\[(\d+)\]")
    for memory in memories:
        source_id = int(memory["id"])
        text = f"{clean(memory.get('summary_short'))}\n{clean(memory.get('content'))}"
        for raw_id in pattern.findall(text):
            target_id = int(raw_id)
            if target_id in memory_ids and target_id != source_id:
                relation = "summarizes" if clean(memory.get("memory_type")) == "consolidated_summary" else "mentions"
                add_candidate(candidates, existing, source_id, target_id, relation, 0.95, "content contains explicit [id] reference")


def parent_candidates(
    memories: list[dict[str, Any]],
    memory_ids: set[int],
    candidates: dict[tuple[int, int, str], CandidateLink],
    existing: set[tuple[int, int, str]],
) -> None:
    for memory in memories:
        source_id = int(memory["id"])
        for field, relation in [
            ("parent_memory_id", "child_of"),
            ("supersedes_memory_id", "supersedes"),
            ("promoted_from_id", "promoted_from"),
            ("demoted_from_id", "demoted_from"),
        ]:
            target = memory.get(field)
            if target is not None and int(target) in memory_ids:
                add_candidate(candidates, existing, source_id, int(target), relation, 0.98, f"structured field {field}")


def duplicate_candidates(
    memories: list[dict[str, Any]],
    candidates: dict[tuple[int, int, str], CandidateLink],
    existing: set[tuple[int, int, str]],
) -> None:
    by_content: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for memory in memories:
        key = (clean(memory.get("memory_type")), normalize_text(memory.get("content")))
        if key[1]:
            by_content.setdefault(key, []).append(memory)
    for group in by_content.values():
        if len(group) < 2:
            continue
        canonical = min(group, key=lambda item: int(item["id"]))
        canonical_id = int(canonical["id"])
        for memory in group:
            memory_id = int(memory["id"])
            if memory_id != canonical_id:
                add_candidate(candidates, existing, memory_id, canonical_id, "duplicate_of", 0.99, "exact normalized content duplicate")


def semantic_candidates(
    memories: list[dict[str, Any]],
    candidates: dict[tuple[int, int, str], CandidateLink],
    existing: set[tuple[int, int, str]],
    pair_limit: int,
) -> None:
    prepared = []
    for memory in memories:
        tag_set = tags(memory.get("tags"))
        text_set = tokens(f"{clean(memory.get('summary_short'))} {clean(memory.get('content'))}")
        prepared.append((memory, tag_set, text_set))

    pair_count = 0
    for index, (a, a_tags, a_tokens) in enumerate(prepared):
        for b, b_tags, b_tokens in prepared[index + 1:]:
            if pair_count >= pair_limit:
                return
            pair_count += 1
            same_project = clean(a.get("project_key")) and clean(a.get("project_key")) == clean(b.get("project_key"))
            same_type = clean(a.get("memory_type")) == clean(b.get("memory_type"))
            tag_score = jaccard(a_tags, b_tags)
            text_score = jaccard(a_tokens, b_tokens)
            score = (0.55 * tag_score) + (0.35 * text_score) + (0.07 if same_project else 0.0) + (0.03 if same_type else 0.0)
            if tag_score >= 0.60 and score >= 0.48:
                relation = "related_to"
                weight = min(0.90, 0.45 + score)
                reason = f"shared tags/text: tag_score={tag_score:.2f}, text_score={text_score:.2f}"
                a_id = int(a["id"])
                b_id = int(b["id"])
                add_candidate(candidates, existing, a_id, b_id, relation, weight, reason)
                add_candidate(candidates, existing, b_id, a_id, relation, weight, reason)
            elif same_project and same_type and text_score >= 0.72:
                relation = "related_to"
                weight = min(0.86, 0.40 + text_score)
                reason = f"same project/type and close text: text_score={text_score:.2f}"
                a_id = int(a["id"])
                b_id = int(b["id"])
                add_candidate(candidates, existing, a_id, b_id, relation, weight, reason)
                add_candidate(candidates, existing, b_id, a_id, relation, weight, reason)


def build_candidates(project: str | None, include_archived: bool, pair_limit: int) -> list[CandidateLink]:
    conn = get_db()
    try:
        memories = load_memories(conn, project, include_archived)
        existing = load_existing_links(conn)
    finally:
        conn.close()

    memory_ids = {int(memory["id"]) for memory in memories}
    candidates: dict[tuple[int, int, str], CandidateLink] = {}
    explicit_id_candidates(memories, memory_ids, candidates, existing)
    parent_candidates(memories, memory_ids, candidates, existing)
    duplicate_candidates(memories, candidates, existing)
    semantic_candidates(memories, candidates, existing, pair_limit)
    return sorted(candidates.values(), key=lambda item: (-item.weight, item.from_memory_id, item.to_memory_id, item.relation_type))


def insert_candidates(candidates: list[CandidateLink], limit: int) -> int:
    selected = candidates[:limit]
    if not selected:
        return 0
    conn = get_db()
    try:
        now = utc_now_iso()
        for item in selected:
            conn.execute(
                """
                INSERT INTO memory_links
                    (from_memory_id, to_memory_id, relation_type, weight, origin, created_at, visibility_scope)
                VALUES (?, ?, ?, ?, ?, ?, 'inherited')
                """,
                (item.from_memory_id, item.to_memory_id, item.relation_type, item.weight, item.origin, now),
            )
        conn.commit()
        return len(selected)
    finally:
        conn.close()


def print_candidates(candidates: list[CandidateLink], limit: int) -> None:
    print(f"candidates: {len(candidates)}")
    for item in candidates[:limit]:
        print(
            f"{item.from_memory_id} -> {item.to_memory_id} | {item.relation_type} | "
            f"weight={item.weight:.2f} | {item.reason}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Create safe memory_links candidates.")
    parser.add_argument("--project", default=None, help="Limit to project_key, for example demo-project.")
    parser.add_argument("--include-archived", action="store_true", help="Include archived memories.")
    parser.add_argument("--limit", type=int, default=120, help="Maximum links to write or print.")
    parser.add_argument("--pair-limit", type=int, default=60000, help="Maximum semantic pairs to scan.")
    parser.add_argument("--run", action="store_true", help="Write links to DB. Without this flag only preview is printed.")
    args = parser.parse_args()

    candidates = build_candidates(args.project, args.include_archived, max(1, int(args.pair_limit)))
    print_candidates(candidates, max(1, int(args.limit)))
    if args.run:
        created = insert_candidates(candidates, max(1, int(args.limit)))
        print(f"created: {created}")
    else:
        print("preview_only: use --run to write links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
