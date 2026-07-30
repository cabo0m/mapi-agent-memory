from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app import db_migrations
from app.sandman import canonical_runtime


class AbstainingProvider:
    def analyze(self, request, *, model_role="primary"):
        return {
            "validation": {
                "status": "accepted",
                "normalized_proposals": [],
                "reason_codes": [],
                "abstain": True,
                "response_fingerprint": "sha256:" + "a" * 64,
            },
            "model_name": "gemini-3.1-flash-lite",
            "model_role": model_role,
            "latency_ms": 7,
            "retry_count": 0,
            "usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
            "estimated_cost_usd": 0.0001,
            "pricing_reason": "pytest",
            "pricing": {"schema_version": "pytest", "source_date": "2026-07-30", "currency": "USD"},
            "provider_metadata": {"api_mode": "interactions", "status": "completed"},
        }


class TimeoutProvider:
    def analyze(self, request, *, model_role="primary"):
        raise TimeoutError("pytest timeout")


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    conn = sqlite3.connect(root / "data" / "agent_memory.db")
    conn.row_factory = sqlite3.Row
    db_migrations.apply_all_migrations(conn)
    conn.commit()
    conn.close()
    return root


def _insert_memory(root: Path, content: str, *, project_key: str = "demo-project", created_at: str) -> int:
    conn = sqlite3.connect(root / "data" / "agent_memory.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """
        INSERT INTO memories (
            content, summary_short, memory_type, source,
            importance_score, confidence_score, tags,
            created_at, updated_at, last_accessed_at,
            activity_state, state_code, memory_v2_status,
            project_key, scope_code, workspace_id,
            visibility_scope, sharing_policy,
            entry_type, truth_kind
        ) VALUES (?, ?, 'project_fact', 'pytest', 0.8, 0.9, 'sandman,canonical',
                  ?, ?, ?, 'active', 'validated', 'active', ?, 'project', 1,
                  'project', 'explicit', 'fact', 'fact')
        """,
        (content, content[:80], created_at, created_at, created_at, project_key),
    )
    conn.commit()
    memory_id = int(cursor.lastrowid)
    conn.close()
    return memory_id


def _counts(root: Path) -> dict[str, int]:
    conn = sqlite3.connect(root / "data" / "agent_memory.db")
    result = {
        "memories": int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]),
        "links": int(conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]),
        "queue": int(conn.execute("SELECT COUNT(*) FROM memory_consolidation_review_items").fetchone()[0]),
        "scheduler": int(conn.execute("SELECT COUNT(*) FROM sandman_scheduler_runs").fetchone()[0]),
        "shadow": int(conn.execute("SELECT COUNT(*) FROM sandman_semantic_shadow_runs").fetchone()[0]),
    }
    conn.close()
    return result


def _provider_factory(_config):
    return AbstainingProvider()


def _timeout_factory(_config):
    return TimeoutProvider()


def test_migration_0028_creates_proposal_only_ledger_and_final_flags(tmp_path: Path) -> None:
    root = _root(tmp_path)
    conn = sqlite3.connect(root / "data" / "agent_memory.db")
    conn.row_factory = sqlite3.Row
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sandman_scheduler_runs)").fetchall()}
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sandman_scheduler_runs'"
    ).fetchone()[0]
    flags = {
        row["flag_key"]: dict(row)
        for row in conn.execute(
            "SELECT flag_key,is_enabled,rollout_mode,allowed_project_keys,allowed_scope_codes,read_only_mode "
            "FROM feature_flags WHERE flag_key IN (?,?,?,?,?)",
            (
                canonical_runtime.CANONICAL_FLAG,
                canonical_runtime.PROVIDER_FLAG,
                canonical_runtime.SHADOW_FLAG,
                canonical_runtime.ROUTING_FLAG,
                canonical_runtime.LEGACY_GEMMA_FLAG,
            ),
        ).fetchall()
    }
    tail = conn.execute("SELECT version FROM schema_migrations ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    conn.close()

    assert tail == db_migrations.MIGRATION_SEQUENCE[-1][0]
    assert {"changed_count", "auto_apply", "shadow_run_id", "estimated_cost_usd"} <= columns
    assert "CHECK (changed_count = 0)" in sql
    assert "CHECK (auto_apply = 0)" in sql
    assert flags[canonical_runtime.CANONICAL_FLAG]["is_enabled"] == 1
    assert flags[canonical_runtime.PROVIDER_FLAG]["is_enabled"] == 1
    assert flags[canonical_runtime.SHADOW_FLAG]["is_enabled"] == 1
    assert flags[canonical_runtime.ROUTING_FLAG]["is_enabled"] == 0
    assert flags[canonical_runtime.LEGACY_GEMMA_FLAG]["is_enabled"] == 0
    assert all(flags[key]["read_only_mode"] == 1 for key in flags)


def test_preview_is_redacted_read_only_and_uses_one_provider_path(tmp_path: Path, monkeypatch) -> None:
    root = _root(tmp_path)
    left = _insert_memory(root, "Canonical feature is enabled for this project.", created_at="2026-07-29T20:00:00Z")
    right = _insert_memory(root, "Canonical feature is disabled for this project.", created_at="2026-07-29T20:01:00Z")
    monkeypatch.setenv("MAPI_GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "configured-for-preview-only")
    before = _counts(root)

    result = canonical_runtime.preview_canonical(
        root_path=root,
        project_key="demo-project",
        memory_ids=[left, right],
        proposal_budget=3,
        include_debug=True,
    )
    after = _counts(root)

    assert result["status"] == "preview_ready"
    assert result["provider_path"] == "deterministic_core+gemini_shadow"
    assert result["memory_writes"] == 0
    assert result["queue_writes"] == 0
    assert result["auto_apply"] is False
    assert result["redaction_manifest"]["raw_secret_exposed"] is False
    assert result["redaction_manifest"]["full_project_dump"] is False
    assert result["shadow_preview"]["status"] == "preview_ready"
    assert result["shadow_preview"]["api_mode"] == "interactions"
    assert result["shadow_preview"]["safety"]["store_requested"] is False
    assert before == after


def test_canonical_run_writes_only_ledgers_and_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    root = _root(tmp_path)
    left = _insert_memory(root, "The scheduler contract is enabled.", created_at="2026-07-29T20:00:00Z")
    right = _insert_memory(root, "The scheduler contract is disabled.", created_at="2026-07-29T20:01:00Z")
    monkeypatch.setenv("MAPI_GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "configured")
    before = _counts(root)
    now = datetime(2026, 7, 29, 22, 5, tzinfo=ZoneInfo("Europe/Warsaw"))

    first = canonical_runtime.run_canonical(
        root_path=root,
        project_key="demo-project",
        memory_ids=[left, right],
        run_key="nightly_preview:demo-project:2026-07-29",
        now=now,
        provider_factory=_provider_factory,
    )
    second = canonical_runtime.run_canonical(
        root_path=root,
        project_key="demo-project",
        memory_ids=[left, right],
        run_key="nightly_preview:demo-project:2026-07-29",
        now=now,
        provider_factory=_provider_factory,
    )
    after = _counts(root)

    assert first["status"] in {"completed", "no_op"}
    assert first["memory_writes"] == 0
    assert first["queue_writes"] == 0
    assert first["auto_apply"] is False
    assert first["run"]["changed_count"] == 0
    assert first["run"]["auto_apply"] is False
    assert first["run"]["provider_path"] == canonical_runtime.PROVIDER_PATH
    assert first["run"]["shadow_status"] == "completed"
    assert second["status"] == "existing_result"
    assert second["run"]["id"] == first["run"]["id"]
    assert after["memories"] == before["memories"]
    assert after["links"] == before["links"]
    assert after["queue"] == before["queue"]
    assert after["scheduler"] == before["scheduler"] + 1
    assert after["shadow"] == before["shadow"] + 1


def test_active_lock_blocks_second_run_without_ledger_write(tmp_path: Path) -> None:
    root = _root(tmp_path)
    paths = canonical_runtime.CanonicalPaths.for_root(root)
    paths.ensure_dirs()
    paths.lock_path.write_text(
        json.dumps({"run_key": "active", "run_type": "nightly_preview", "pid": os.getpid()}),
        encoding="utf-8",
    )
    before = _counts(root)

    result = canonical_runtime.run_canonical(root_path=root, project_key="demo-project", run_key="blocked")

    assert result["status"] == "already_running"
    assert result["reason_codes"] == ["active_lock"]
    assert _counts(root) == before


def test_provider_timeout_is_distinct_and_never_mutates_memory(tmp_path: Path, monkeypatch) -> None:
    root = _root(tmp_path)
    memory_id = _insert_memory(root, "Timeout candidate remains untouched.", created_at="2026-07-29T20:00:00Z")
    peer_id = _insert_memory(root, "Timeout peer remains untouched.", created_at="2026-07-29T20:01:00Z")
    monkeypatch.setenv("MAPI_GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "configured")
    before = _counts(root)

    result = canonical_runtime.run_canonical(
        root_path=root,
        project_key="demo-project",
        memory_ids=[memory_id, peer_id],
        run_key="canary:demo-project:timeout",
        run_type="canary",
        provider_factory=_timeout_factory,
    )
    after = _counts(root)

    assert result["status"] == "timed_out"
    assert result["run"]["status"] == "timed_out"
    assert "provider_timeout" in result["run"]["reason_codes"]
    assert result["run"]["changed_count"] == 0
    assert after["memories"] == before["memories"]
    assert after["links"] == before["links"]
    assert after["queue"] == before["queue"]


def test_no_candidates_is_no_op(tmp_path: Path, monkeypatch) -> None:
    root = _root(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "configured")
    result = canonical_runtime.run_canonical(
        root_path=root,
        project_key="demo-project",
        run_key="nightly_preview:demo-project:empty-2026-07-29",
        execute_shadow=False,
    )
    assert result["status"] == "no_op"
    assert result["run"]["candidate_count"] == 0
    assert result["run"]["changed_count"] == 0


def test_morning_report_marks_missing_nightly_as_missed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    now = datetime(2026, 7, 30, 9, 5, tzinfo=ZoneInfo("Europe/Warsaw"))

    result = canonical_runtime.run_morning_report(root_path=root, project_key="demo-project", now=now)
    report = Path(result["report_path"]).read_text(encoding="utf-8")

    assert result["status"] == "missed"
    assert result["run"]["status"] == "missed"
    assert result["source_run"] is None
    assert "Morning status: missed" in report
    assert "Memory writes: 0" in report


def test_status_becomes_ready_after_expected_nightly_run(tmp_path: Path, monkeypatch) -> None:
    root = _root(tmp_path)
    memory_id = _insert_memory(root, "Freshness probe for canonical scheduler.", created_at="2026-07-29T20:00:00Z")
    peer_id = _insert_memory(root, "Freshness peer for canonical scheduler.", created_at="2026-07-29T20:01:00Z")
    monkeypatch.setenv("GEMINI_API_KEY", "configured")
    night = datetime(2026, 7, 29, 22, 5, tzinfo=ZoneInfo("Europe/Warsaw"))
    morning = datetime(2026, 7, 30, 9, 5, tzinfo=ZoneInfo("Europe/Warsaw"))

    before = canonical_runtime.get_canonical_status(root_path=root, project_key="demo-project", now=morning)
    canonical_runtime.run_canonical(
        root_path=root,
        project_key="demo-project",
        memory_ids=[memory_id, peer_id],
        now=night,
        provider_factory=_provider_factory,
    )
    after = canonical_runtime.get_canonical_status(root_path=root, project_key="demo-project", now=morning)

    assert before["status"] == "attention"
    assert "expected_nightly_run_missing" in before["reason_codes"]
    assert after["status"] == "ready"
    assert after["reason_codes"] == []
    assert after["provider_path"] == canonical_runtime.PROVIDER_PATH
    assert after["model_auto_apply"] is False
    assert after["legacy_runtime"]["math_mara_scheduler_active"] is False


def test_sandman_workshop_has_no_competing_legacy_runs() -> None:
    from app.workshops.catalog import WORKSHOPS

    actions = {item.action for item in WORKSHOPS["sandman"].actions}
    assert {"canonical_status", "canonical_preview", "canonical_runs", "canonical_run"} <= actions
    assert not {
        "preview",
        "run",
        "preview_ai",
        "preview_gemma",
        "list_runs",
        "get_run",
        "get_actions",
        "semantic_route_preview",
        "semantic_route_canary",
    } & actions


def test_canonical_prompt_requires_complete_evidence_ids() -> None:
    from app.sandman.providers import gemini

    assert canonical_runtime.PROMPT_VERSION == "sandman_provider_prompt.v2"
    assert "evidence_memory_ids must include every" in gemini.INSTRUCTION
    assert "source_memory_id and the target_memory_id" in gemini.INSTRUCTION
