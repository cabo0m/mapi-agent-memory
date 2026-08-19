from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

import mcp_surface

from app.runtime.doctor import backup_snapshot, database_snapshot, evaluate_components
from app.runtime.recovery import build_recovery_plan, recover_runtime


def test_database_snapshot_checks_integrity_and_counts(tmp_path: Path) -> None:
    path = tmp_path / "mapi.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
    INSERT INTO schema_migrations VALUES ('0034_test','2026-08-18T00:00:00Z');
    CREATE TABLE memories(id INTEGER PRIMARY KEY);
    CREATE TABLE memory_links(id INTEGER PRIMARY KEY);
    CREATE TABLE memory_events(id INTEGER PRIMARY KEY);
    INSERT INTO memories VALUES (1);
    """)
    conn.commit(); conn.close()
    result = database_snapshot(path)
    assert result["available"] is True
    assert result["quick_check"] == "ok"
    assert result["foreign_key_findings"] == 0
    assert result["schema_tail"] == "0034_test"
    assert result["memories"] == 1


def test_backup_snapshot_is_portable_and_age_aware(tmp_path: Path, monkeypatch) -> None:
    backup_dir = tmp_path / "external-backups"
    backup_dir.mkdir()
    artifact = backup_dir / "mapi-20260818.db"
    artifact.write_bytes(b"backup")
    monkeypatch.setenv("MAPI_BACKUP_DIR", str(backup_dir))
    result = backup_snapshot(tmp_path, now=datetime.now(UTC))
    assert result["available"] is True
    assert result["latest_path"] == str(artifact)
    assert result["artifact_count"] == 1


def _components(*, host="127.0.0.1", remote=False):
    return {
        "database": {"available": True, "quick_check": "ok", "foreign_key_findings": 0},
        "repository": {"dirty": False, "non_allowlisted_untracked_paths": []},
        "network": {"host": host, "port": 8015, "loopback": host == "127.0.0.1", "listener_reachable": True},
        "backup": {"available": True, "age_hours": 1.0},
        "remote_auth": {"enabled": remote},
        "runtime_readiness": {"status": "ready", "reason_codes": []},
        "deep": {},
    }


def test_doctor_blocks_public_bind_without_auth() -> None:
    status, findings = evaluate_components(_components(host="0.0.0.0", remote=False))
    assert status == "BLOCKED"
    assert "public_bind_without_remote_auth" in {item["reason_code"] for item in findings}


def test_doctor_allows_nonloopback_when_remote_auth_boundary_is_enabled() -> None:
    status, findings = evaluate_components(_components(host="0.0.0.0", remote=True))
    assert status == "READY"
    assert findings == []


def test_recovery_plan_is_preview_first_and_preserves_live_writer_policy(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / "writer.lock"
    lock.write_text(json.dumps({"pid": 99999999}), encoding="utf-8")
    monkeypatch.setenv("MAPI_WRITER_LOCK_PATH", str(lock))
    report = {"status": "ATTENTION", "findings": [{"reason_code": "backup_missing"}]}
    plan = build_recovery_plan(root=tmp_path, doctor_report=report)
    assert plan["database_mutations"] is False
    assert plan["steps"][0]["action"] == "remove_dead_writer_lease"
    assert any(step["action"] == "restart_runtime_with_operator_command" for step in plan["steps"])


def test_execute_recovery_requires_explicit_json_argv(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / "writer.lock"
    lock.write_text(json.dumps({"pid": 99999999}), encoding="utf-8")
    monkeypatch.setenv("MAPI_WRITER_LOCK_PATH", str(lock))
    monkeypatch.delenv("MAPI_RECOVERY_COMMAND_JSON", raising=False)
    result = recover_runtime(execute=True, root=tmp_path)
    assert result["status"] == "manual_restart_required"
    assert result["error"] == "recovery_command_not_configured"
    assert not lock.exists()


def test_governance_exposes_read_only_doctor_and_recovery_plan() -> None:
    workshop = mcp_surface.open_workshop_payload("governance", profile="reader")
    actions = {item["action"]: item for item in workshop["actions"]}
    assert actions["doctor"]["risk_class"] == "R0"
    assert actions["doctor"]["tool_name"] == "get_mapi_doctor_report"
    assert actions["recovery_plan"]["risk_class"] == "R0"
    assert actions["recovery_plan"]["tool_name"] == "get_mapi_recovery_plan"


def test_repository_state_uses_configured_repository_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.runtime.freshness as freshness

    configured = tmp_path / "source-checkout"
    configured.mkdir()
    seen: list[Path] = []

    def fake_run_git(root: Path, *args: str):
        seen.append(root)
        command = tuple(args)
        if command == ("rev-parse", "HEAD"):
            return 0, "abc123"
        if command[:2] == ("status", "--porcelain=v1"):
            return 0, ""
        if command == ("worktree", "list", "--porcelain"):
            return 0, ""
        return 1, ""

    monkeypatch.setenv("MAPI_REPOSITORY_ROOT", str(configured))
    monkeypatch.setattr(freshness, "_run_git", fake_run_git)
    state = freshness.repository_state()

    assert state["root"] == str(configured.resolve())
    assert state["head"] == "abc123"
    assert state["git_available"] is True
    assert seen and all(root == configured.resolve() for root in seen)
