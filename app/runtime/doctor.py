from __future__ import annotations

import importlib.util
import json
import os
import socket
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.runtime.context import runtime_db_path, runtime_root
from app.runtime.freshness import get_runtime_readiness, repository_state
from app.runtime.remote_auth_config import RemoteAuthConfig

DOCTOR_SCHEMA = "mapi_doctor.v1"
BACKUP_MAX_AGE_HOURS = 48.0


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,)).fetchone() is not None


def database_snapshot(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.exists():
        return {"available": False, "path": str(path)}
    try:
        conn = sqlite3.connect(path, timeout=10)
        try:
            quick = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
            fk = len(conn.execute("PRAGMA foreign_key_check").fetchall())
            tail = None
            if _table_exists(conn, "schema_migrations"):
                row = conn.execute("SELECT version FROM schema_migrations ORDER BY applied_at DESC, version DESC LIMIT 1").fetchone()
                tail = str(row[0]) if row else None
            def count(table: str) -> int | None:
                return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) if _table_exists(conn, table) else None
            return {"available": True, "path": str(path), "size_bytes": path.stat().st_size, "quick_check": "ok" if quick == ["ok"] else "; ".join(quick), "foreign_key_findings": fk, "schema_tail": tail, "memories": count("memories"), "links": count("memory_links"), "events": count("memory_events")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"available": True, "path": str(path), "quick_check": "error", "error": exc.__class__.__name__}


def backup_snapshot(root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    configured = str(os.environ.get("MAPI_BACKUP_DIR") or "").strip()
    base = Path(configured).expanduser().resolve() if configured else (root / "backups").resolve()
    if not base.exists():
        return {"available": False, "base": str(base), "artifact_count": 0}
    candidates = [p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".zip", ".bak"}]
    if not candidates:
        return {"available": False, "base": str(base), "artifact_count": 0}
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    current = now or datetime.now(UTC)
    modified = datetime.fromtimestamp(newest.stat().st_mtime, UTC)
    return {"available": True, "base": str(base), "artifact_count": len(candidates), "latest_path": str(newest), "latest_size_bytes": newest.stat().st_size, "age_hours": round((current-modified).total_seconds()/3600.0, 3)}


def optional_capabilities_snapshot() -> dict[str, bool]:
    def available(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False
    return {
        "semantic": available("sentence_transformers") and available("sqlite_vec"),
        "gemini": available("google.genai"),
    }


def network_snapshot() -> dict[str, Any]:
    host = str(os.environ.get("MAPI_RUNTIME_HOST") or "127.0.0.1").strip()
    port = int(os.environ.get("MAPI_RUNTIME_PORT") or 8015)
    listening = False
    try:
        with socket.create_connection((host, port), timeout=0.25):
            listening = True
    except OSError:
        listening = False
    return {"host": host, "port": port, "listener_reachable": listening, "loopback": host in {"127.0.0.1", "localhost", "::1"}}


def _finding(code: str, severity: str, message: str, action: str, **details: Any) -> dict[str, Any]:
    return {"reason_code": code, "severity": severity, "message": message, "recommended_action": action, "details": details}


def evaluate_components(components: dict[str, Any], *, backup_max_age_hours: float = BACKUP_MAX_AGE_HOURS) -> tuple[str, list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    db = components["database"]
    repo = components["repository"]
    network = components["network"]
    backup = components["backup"]
    remote = components["remote_auth"]
    readiness = components["runtime_readiness"]
    deep = components.get("deep") or {}
    if not db.get("available"):
        findings.append(_finding("database_missing", "blocked", "Primary SQLite database is missing.", "Restore a verified backup or run migrations for a new instance."))
    else:
        if db.get("quick_check") != "ok": findings.append(_finding("database_quick_check_failed", "blocked", "SQLite quick_check failed.", "Freeze writes and restore/investigate the database.", value=db.get("quick_check")))
        if int(db.get("foreign_key_findings") or 0): findings.append(_finding("database_foreign_key_findings", "blocked", "SQLite foreign-key findings exist.", "Freeze writes and repair before restart.", count=db.get("foreign_key_findings")))
    if repo.get("dirty"):
        findings.append(_finding("repository_tracked_dirty", "attention", "Repository has tracked changes.", "Commit or move work in progress before production restart.", paths=repo.get("tracked_paths") or []))
    if repo.get("non_allowlisted_untracked_paths"):
        findings.append(_finding("repository_untracked_attention", "attention", "Repository has non-allowlisted untracked files.", "Classify or move runtime/recovery artifacts.", paths=repo.get("non_allowlisted_untracked_paths") or []))
    if not backup.get("available"):
        findings.append(_finding("backup_missing", "attention", "No backup artifact was found in MAPI_BACKUP_DIR.", "Create and verify a SQLite-consistent backup."))
    elif float(backup.get("age_hours") or 0) > backup_max_age_hours:
        findings.append(_finding("backup_stale", "attention", "Latest backup is older than the policy window.", "Create and verify a fresh backup.", age_hours=backup.get("age_hours"), threshold=backup_max_age_hours))
    if not network.get("loopback") and not remote.get("enabled"):
        findings.append(_finding("public_bind_without_remote_auth", "blocked", "Runtime is configured on a non-loopback address without remote authentication.", "Bind loopback or enable the authenticated TLS proxy boundary."))
    if not network.get("listener_reachable"):
        findings.append(_finding("listener_not_reachable", "attention", "The configured MCP listener is not reachable from the local host.", "Start/restart the runtime or verify host/port configuration.", host=network.get("host"), port=network.get("port")))
    if readiness.get("status") not in {"ready", "ok"}:
        findings.append(_finding("runtime_readiness_attention", "attention", "Runtime readiness is not fully ready.", "Inspect readiness reason codes before restart.", status=readiness.get("status"), reason_codes=readiness.get("reason_codes") or []))
    qa = deep.get("search_qa") or {}
    if qa.get("status") == "ok" and qa.get("failures"):
        findings.append(_finding("search_qa_failures", "attention", "Retrieval QA reports failures.", "Repair retrieval before rollout.", failures=qa.get("failures")))
    if any(item["severity"] == "blocked" for item in findings): return "BLOCKED", findings
    if findings: return "ATTENTION", findings
    return "READY", findings


def collect_doctor_report(*, root: Path | None = None, db_path: Path | None = None, deep: bool = False, now: datetime | None = None, readiness_provider: Callable[..., dict[str, Any]] = get_runtime_readiness, qa_provider: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
    resolved_root = Path(root or runtime_root()).resolve()
    resolved_db = Path(db_path or runtime_db_path()).resolve()
    try:
        readiness = readiness_provider(include_debug=False)
    except Exception as exc:
        readiness = {"status": "error", "reason_codes": [f"readiness_error:{exc.__class__.__name__}"]}
    remote = RemoteAuthConfig.from_env()
    deep_payload: dict[str, Any] = {}
    if deep and qa_provider is not None:
        try: deep_payload["search_qa"] = qa_provider()
        except Exception as exc: deep_payload["search_qa"] = {"status": "error", "error": exc.__class__.__name__}
    components = {"repository": repository_state(), "database": database_snapshot(resolved_db), "backup": backup_snapshot(resolved_root, now=now), "network": network_snapshot(), "remote_auth": {"enabled": remote.enabled, "base_url": remote.base_url}, "runtime_readiness": readiness, "optional_capabilities": optional_capabilities_snapshot(), "deep": deep_payload}
    status, findings = evaluate_components(components)
    return {"schema": DOCTOR_SCHEMA, "status": status, "generated_at": (now or datetime.now(UTC)).isoformat().replace("+00:00", "Z"), "root": str(resolved_root), "database": str(resolved_db), "deep": bool(deep), "findings": findings, "components": components}
