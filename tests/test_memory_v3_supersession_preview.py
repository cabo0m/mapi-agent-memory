from __future__ import annotations

from typing import Any

import mcp_surface


def _create_memory(server: Any, **overrides: Any) -> int:
    payload = {
        "content": "Memory payload.",
        "memory_type": "project_note",
        "summary_short": "Memory payload",
        "project_key": "mapi",
        "scope_code": "project",
        "state_code": "validated",
        "memory_v2_status": "active",
        "truth_kind": "fact",
        "entry_type": "project",
        "confidence_score": 0.9,
        "importance_score": 0.75,
    }
    payload.update(overrides)
    return int(server.create_memory(**payload)["memory"]["id"])


def _counts(server: Any) -> dict[str, int]:
    conn = server.get_db_connection()
    try:
        return {
            "memories": int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]),
            "memory_links": int(conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]),
            "memory_events": int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]),
        }
    finally:
        conn.close()


def test_supersession_preview_happy_path_replacement(server: Any) -> None:
    old_id = _create_memory(server, summary_short="Old")
    new_id = _create_memory(server, summary_short="New")

    result = server.preview_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason="Nowa wersja zastępuje starą.",
        include_debug=True,
    )

    assert result["status"] == "preview_ready"
    assert result["schema_version"] == "memory_v3_supersession_preview.v1"
    assert result["guard"]["allowed"] is True
    assert result["planned_changes"]["new_memory"]["supersedes_memory_id"] == old_id
    assert result["planned_changes"]["old_memory"]["superseded_by_memory_id"] == new_id
    assert result["planned_changes"]["old_memory"]["state_code"] == "superseded"
    assert result["planned_changes"]["old_memory"]["memory_v2_status"] == "superseded"
    assert result["planned_changes"]["link"]["relation_type"] == "supersedes"
    assert len(result["preview_hash"]) == 64
    assert len(result["input_fingerprint"]) == 64
    assert len(result["candidate_set_fingerprint"]) == 64
    assert result["operator_next_action"] == "wait_for_v3_2"
    assert result["safety"]["read_only"] is True


def test_supersession_preview_happy_path_correction_and_refinement(server: Any) -> None:
    old_id = _create_memory(server, summary_short="Old")
    correction_id = _create_memory(server, summary_short="Correction")
    refinement_id = _create_memory(server, summary_short="Refinement")

    correction = server.preview_memory_supersession(
        new_memory_id=correction_id,
        old_memory_id=old_id,
        relation_kind="correction",
        reason="Poprawa błędu.",
    )
    refinement = server.preview_memory_supersession(
        new_memory_id=refinement_id,
        old_memory_id=old_id,
        relation_kind="refinement",
        reason="Doprecyzowanie treści.",
    )

    assert correction["status"] == "preview_ready"
    assert refinement["status"] == "preview_ready"


def test_supersession_preview_blocks_unsupported_relation_and_same_id(server: Any) -> None:
    memory_id = _create_memory(server)

    unsupported = server.preview_memory_supersession(
        new_memory_id=memory_id,
        old_memory_id=memory_id + 1,
        relation_kind="association",
        reason="To nie jest supersession.",
    )
    same_id = server.preview_memory_supersession(
        new_memory_id=memory_id,
        old_memory_id=memory_id,
        relation_kind="replacement",
        reason="To samo ID.",
    )

    assert unsupported["status"] == "blocked"
    assert unsupported["guard"]["blockers"] == ["unsupported_relation_kind"]
    assert same_id["status"] == "blocked"
    assert same_id["guard"]["blockers"] == ["same_memory_id"]


def test_supersession_preview_blocks_missing_memories(server: Any) -> None:
    existing_id = _create_memory(server)

    missing_new = server.preview_memory_supersession(
        new_memory_id=999999,
        old_memory_id=existing_id,
        relation_kind="replacement",
        reason="Brak nowej memory.",
    )
    missing_old = server.preview_memory_supersession(
        new_memory_id=existing_id,
        old_memory_id=999998,
        relation_kind="replacement",
        reason="Brak starej memory.",
    )

    assert missing_new["status"] == "blocked"
    assert "new_memory_missing" in missing_new["guard"]["blockers"]
    assert missing_old["status"] == "blocked"
    assert "old_memory_missing" in missing_old["guard"]["blockers"]


def test_supersession_preview_blocks_cross_project_and_cross_scope(server: Any) -> None:
    old_id = _create_memory(server, project_key="mapi", scope_code="project")
    new_id = _create_memory(server, project_key="other-project", scope_code="global")

    result = server.preview_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason="Cross project replacement.",
    )

    assert result["status"] == "blocked"
    assert set(result["guard"]["blockers"]) >= {"cross_project", "cross_scope"}
    assert result["operator_next_action"] == "resolve_integrity_issue"


def test_supersession_preview_blocks_cycle_and_branch_conflicts(server: Any) -> None:
    old_id = _create_memory(server, summary_short="Old")
    mid_id = _create_memory(server, summary_short="Mid", supersedes_memory_id=old_id)
    new_id = _create_memory(server, summary_short="New", supersedes_memory_id=mid_id)
    other_head_id = _create_memory(server, summary_short="Other head")
    conn = server.get_db_connection()
    try:
        conn.execute(
            "UPDATE memories SET superseded_by_memory_id = ? WHERE id = ?",
            (other_head_id, old_id),
        )
        conn.execute(
            "INSERT INTO memory_links (from_memory_id, to_memory_id, relation_type, weight, origin) VALUES (?, ?, 'supersedes', 1.0, 'pytest')",
            (mid_id, old_id),
        )
        conn.execute(
            "INSERT INTO memory_links (from_memory_id, to_memory_id, relation_type, weight, origin) VALUES (?, ?, 'supersedes', 1.0, 'pytest')",
            (new_id, mid_id),
        )
        conn.commit()
    finally:
        conn.close()

    cycle = server.preview_memory_supersession(
        new_memory_id=old_id,
        old_memory_id=new_id,
        relation_kind="replacement",
        reason="To zrobi cykl.",
    )
    branch = server.preview_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason="Old ma już inny head.",
    )

    assert cycle["status"] == "blocked"
    assert "proposed_supersession_would_create_cycle" in cycle["guard"]["blockers"]
    assert branch["status"] == "blocked"
    assert "old_memory_already_replaced_by_different_head" in branch["guard"]["blockers"]


def test_supersession_preview_blocks_reverse_pointer_mismatch(server: Any) -> None:
    old_id = _create_memory(server)
    new_id = _create_memory(server)
    wrong_id = _create_memory(server)
    conn = server.get_db_connection()
    try:
        conn.execute("UPDATE memories SET superseded_by_memory_id = ? WHERE id = ?", (wrong_id, old_id))
        conn.commit()
    finally:
        conn.close()

    result = server.preview_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason="Reverse pointer mismatch.",
    )

    assert result["status"] == "blocked"
    assert "old_memory_already_replaced_by_different_head" in result["guard"]["blockers"]


def test_supersession_preview_blocks_archived_new_memory_and_candidate_old(server: Any) -> None:
    old_candidate_id = _create_memory(server, state_code="candidate", memory_v2_status="proposed")
    archived_new_id = _create_memory(server, state_code="archived", memory_v2_status="archived")

    result = server.preview_memory_supersession(
        new_memory_id=archived_new_id,
        old_memory_id=old_candidate_id,
        relation_kind="replacement",
        reason="Archived cannot replace candidate.",
    )

    assert result["status"] == "blocked"
    assert set(result["guard"]["blockers"]) >= {
        "new_memory_not_active_head_candidate",
        "old_memory_candidate_requires_review",
    }


def test_supersession_preview_blocks_proposal_or_dream_replacing_confirmed_fact(server: Any) -> None:
    old_id = _create_memory(server, truth_kind="decision", requires_user_confirmation=False)
    proposal_id = _create_memory(server, truth_kind="proposal")
    dream_id = _create_memory(server, truth_kind="dream")

    proposal = server.preview_memory_supersession(
        new_memory_id=proposal_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason="Proposal wants to replace decision.",
    )
    dream = server.preview_memory_supersession(
        new_memory_id=dream_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason="Dream wants to replace decision.",
    )

    assert "proposal_or_dream_cannot_replace_confirmed_fact" in proposal["guard"]["blockers"]
    assert "proposal_or_dream_cannot_replace_confirmed_fact" in dream["guard"]["blockers"]


def test_supersession_preview_returns_already_satisfied(server: Any) -> None:
    old_id = _create_memory(server, state_code="superseded", memory_v2_status="superseded")
    new_id = _create_memory(server, supersedes_memory_id=old_id)
    conn = server.get_db_connection()
    try:
        conn.execute("UPDATE memories SET superseded_by_memory_id = ? WHERE id = ?", (new_id, old_id))
        conn.execute(
            "INSERT INTO memory_links (from_memory_id, to_memory_id, relation_type, weight, origin) VALUES (?, ?, 'supersedes', 1.0, 'pytest')",
            (new_id, old_id),
        )
        conn.commit()
    finally:
        conn.close()

    result = server.preview_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason="Already in place.",
    )

    assert result["status"] == "already_satisfied"
    assert result["guard"]["allowed"] is True
    assert result["operator_next_action"] == "inspect"


def test_supersession_preview_fingerprints_are_stable_and_semantic(server: Any) -> None:
    old_id = _create_memory(server)
    new_id = _create_memory(server)

    first = server.preview_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason="Stable preview.",
    )
    second = server.preview_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason="Stable preview.",
    )

    conn = server.get_db_connection()
    try:
        conn.execute("UPDATE memories SET last_accessed_at = '2099-01-01T00:00:00Z' WHERE id = ?", (new_id,))
        conn.commit()
    finally:
        conn.close()
    after_irrelevant = server.preview_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason="Stable preview.",
    )

    conn = server.get_db_connection()
    try:
        conn.execute("UPDATE memories SET updated_at = '2099-01-02T00:00:00Z' WHERE id = ?", (new_id,))
        conn.commit()
    finally:
        conn.close()
    after_semantic = server.preview_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason="Stable preview.",
    )

    assert first["input_fingerprint"] == second["input_fingerprint"]
    assert first["candidate_set_fingerprint"] == second["candidate_set_fingerprint"]
    assert first["preview_hash"] == second["preview_hash"]
    assert after_irrelevant["candidate_set_fingerprint"] == first["candidate_set_fingerprint"]
    assert after_semantic["candidate_set_fingerprint"] != first["candidate_set_fingerprint"]


def test_supersession_preview_is_read_only(server: Any) -> None:
    old_id = _create_memory(server)
    new_id = _create_memory(server)
    before = _counts(server)

    result = server.preview_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason="Read only preview.",
    )

    after = _counts(server)
    assert result["safety"]["read_only"] is True
    assert result["safety"]["mutations_performed"] == 0
    assert before == after


def test_supersession_preview_workshop_action_is_exposed(server: Any) -> None:
    old_id = _create_memory(server)
    new_id = _create_memory(server)
    payload = mcp_surface.open_workshop_payload("memory")
    action = next(item for item in payload["actions"] if item["action"] == "supersession_preview")

    result = server.run_workshop_action(
        "memory",
        "supersession_preview",
        payload={
            "new_memory_id": new_id,
            "old_memory_id": old_id,
            "relation_kind": "replacement",
            "reason": "Workshop preview.",
            "include_debug": True,
        },
    )

    assert action["tool_name"] == "preview_memory_supersession"
    assert result["status"] == "ok"
    assert result["result"]["schema_version"] == "memory_v3_supersession_preview.v1"
