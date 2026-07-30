from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import load_server_module


@pytest.fixture
def server_core_module(isolated_tmp_root: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    server_path = Path(__file__).resolve().parents[1] / "server_core.py"
    module_name = f"server_core_research_ingest_{uuid.uuid4().hex}"
    module = load_server_module(server_path, module_name)

    from app import memory_config as config

    config.ROOT = isolated_tmp_root
    config.DATA_DIR = config.ROOT / "data"
    config.DB_PATH = config.DATA_DIR / "agent_memory.db"
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    module.ROOT = config.ROOT
    module.DATA_DIR = config.DATA_DIR
    module.DB_PATH = config.DB_PATH

    # Research ingest tests should validate workflow, not download/load embedding models.
    monkeypatch.setattr(
        module,
        "_ensure_memory_embedding_best_effort",
        lambda conn, memory: {"status": "test_stub", "memory_id": int(memory["id"])},
    )

    return module


def _table_count(db_path: Path, table_name: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
    finally:
        conn.close()


def test_create_ingest_item_stays_in_quarantine_and_does_not_create_memory(server_core_module: Any) -> None:
    server = server_core_module

    result = server.create_ingest_item(
        raw_text="Quarantine regression marker: ingest_should_not_create_memory_before_promotion.",
        source_type="url",
        source_ref="https://example.invalid/research-ingest/quarantine-test",
        title="Quarantine regression test",
        project_key="demo-project",
        tags="test,research-ingest,quarantine",
        quality_score=0.8,
        source_reliability_score=0.7,
        ingest_status="candidate",
    )

    assert result["status"] == "created"
    assert result["quarantine"] is True
    assert result["normal_memory_created"] is False
    item = result["item"]
    assert item["ingest_status"] == "candidate"
    assert item["source_type"] == "url"
    assert item["promoted_memory_id"] is None

    assert _table_count(server.DB_PATH, "ingest_items") == 1
    assert _table_count(server.DB_PATH, "ingest_sources") == 1
    assert _table_count(server.DB_PATH, "memories") == 0


def test_invalid_claims_json_is_rejected_without_row(server_core_module: Any) -> None:
    server = server_core_module

    with pytest.raises(ValueError, match="extracted_claims_json is not valid JSON"):
        server.create_ingest_item(
            raw_text="This row should not be persisted because claims JSON is malformed.",
            extracted_claims_json="[not valid json",
            source_type="manual",
            source_ref="smoke://research-ingest/invalid-json",
        )

    # The DB may not exist if validation failed before opening it; both outcomes are OK.
    if server.DB_PATH.exists():
        assert _table_count(server.DB_PATH, "ingest_items") == 0


def test_unknown_source_type_is_normalized_to_other(server_core_module: Any) -> None:
    result = server_core_module.create_ingest_item(
        raw_text="Unsupported source type should be normalized rather than accepted as a fake enum.",
        source_type="strange_crystal_ball",
        source_ref="smoke://research-ingest/weird-source-type",
        title="Unknown source type test",
    )

    assert result["status"] == "created"
    assert result["item"]["source_type"] == "other"


def test_preview_research_ingest_review_classifies_candidates(server_core_module: Any) -> None:
    server = server_core_module
    high = server.create_ingest_item(
        raw_text="High quality research candidate with enough text to be considered for promotion into a concise verified memory.",
        source_type="manual",
        source_ref="smoke://research-ingest/high-quality",
        title="High quality candidate",
        project_key="demo-project",
        quality_score=0.9,
        source_reliability_score=0.85,
        ingest_status="candidate",
    )["item"]
    low = server.create_ingest_item(
        raw_text="x",
        source_type="manual",
        source_ref="smoke://research-ingest/low-quality",
        title="Low quality candidate",
        project_key="demo-project",
        quality_score=0.1,
        source_reliability_score=0.2,
        ingest_status="new",
    )["item"]
    middle = server.create_ingest_item(
        raw_text="Medium confidence candidate should wait for more evidence before promotion.",
        source_type="manual",
        source_ref="smoke://research-ingest/middle-quality",
        title="Medium quality candidate",
        project_key="demo-project",
        quality_score=0.45,
        source_reliability_score=0.45,
        ingest_status="new",
    )["item"]

    preview = server.preview_research_ingest_review(project_key="demo-project", limit=10)
    actions = {int(row["ingest_item_id"]): row["action"] for row in preview["decisions"]}

    assert preview["status"] == "ok"
    assert actions[int(high["id"])] == "promote_candidate"
    assert actions[int(low["id"])] == "reject_candidate"
    assert actions[int(middle["id"])] == "keep_in_quarantine"


def test_reject_and_archive_update_quarantine_status(server_core_module: Any) -> None:
    server = server_core_module
    reject_item = server.create_ingest_item(
        raw_text="Rejectable item.",
        source_type="manual",
        source_ref="smoke://research-ingest/rejectable",
    )["item"]
    archive_item = server.create_ingest_item(
        raw_text="Archivable item.",
        source_type="manual",
        source_ref="smoke://research-ingest/archivable",
    )["item"]

    rejected = server.reject_ingest_item(reject_item["id"], reason="bad source", reviewed_by="pytest")
    archived = server.archive_ingest_item(archive_item["id"], reason="not needed", reviewed_by="pytest")

    assert rejected["status"] == "rejected"
    assert rejected["item"]["ingest_status"] == "rejected"
    assert rejected["item"]["rejection_reason"] == "bad source"
    assert rejected["item"]["reviewed_by"] == "pytest"
    assert archived["status"] == "archived"
    assert archived["item"]["ingest_status"] == "archived"
    assert archived["item"]["rejection_reason"] == "not needed"


def test_promote_ingest_item_creates_one_memory_and_is_idempotent(server_core_module: Any) -> None:
    server = server_core_module
    item = server.create_ingest_item(
        raw_text="Promotion candidate: ingest item should become one normal memory only after explicit promotion.",
        source_type="url",
        source_ref="https://example.invalid/research-ingest/promote-test",
        title="Promotion candidate",
        project_key="demo-project",
        tags="research-ingest,promotion-test",
        quality_score=0.9,
        source_reliability_score=0.9,
        ingest_status="candidate",
    )["item"]

    promoted = server.promote_ingest_item(
        ingest_item_id=int(item["id"]),
        memory_content="Research ingest creates normal memory only through explicit promotion.",
        memory_type="research_note",
        summary_short="Explicit promotion creates memory",
        tags="research-ingest,evidence-backed",
        importance_score=0.4,
        confidence_score=0.8,
        reviewed_by="pytest",
    )

    assert promoted["status"] == "promoted"
    memory = promoted["memory"]
    assert memory["memory_type"] == "research_note"
    assert memory["source"] == "https://example.invalid/research-ingest/promote-test"
    assert memory["validation_source"] == "research_ingest"
    assert memory["layer_code"] == "working"
    assert memory["area_code"] == "knowledge"
    assert memory["scope_code"] == "project"
    assert memory["project_key"] == "demo-project"
    assert memory["embedding_hook"]["status"] == "test_stub"

    promoted_item = promoted["item"]
    assert promoted_item["ingest_status"] == "promoted"
    assert int(promoted_item["promoted_memory_id"]) == int(memory["id"])
    assert promoted_item["reviewed_by"] == "pytest"
    assert _table_count(server.DB_PATH, "memories") == 1

    second = server.promote_ingest_item(
        ingest_item_id=int(item["id"]),
        memory_content="A second promotion attempt must not create another memory.",
        memory_type="research_note",
    )
    assert second["status"] == "already_promoted"
    assert int(second["promoted_memory_id"]) == int(memory["id"])
    assert _table_count(server.DB_PATH, "memories") == 1


def test_promoted_ingest_item_cannot_be_rejected(server_core_module: Any) -> None:
    server = server_core_module
    item = server.create_ingest_item(
        raw_text="Promoted item should be protected from later rejection.",
        source_type="manual",
        source_ref="smoke://research-ingest/promoted-reject-guard",
        quality_score=0.8,
        source_reliability_score=0.8,
        ingest_status="candidate",
    )["item"]
    promoted = server.promote_ingest_item(
        ingest_item_id=int(item["id"]),
        memory_content="Promoted ingest items cannot be rejected afterwards.",
        memory_type="research_note",
    )

    rejection = server.reject_ingest_item(int(item["id"]), reason="late rejection")

    assert promoted["status"] == "promoted"
    assert rejection["status"] == "noop"
    assert "cannot be rejected" in rejection["message"]
    assert int(rejection["item"]["promoted_memory_id"]) == int(promoted["memory"]["id"])
    assert _table_count(server.DB_PATH, "memories") == 1
