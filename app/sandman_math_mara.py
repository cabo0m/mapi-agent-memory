from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app import db_migrations
from app import memory_config as config
from app import sandman_profiles


RUN_TYPE_NIGHTLY = "nightly"
RUN_TYPE_MANUAL = "manual"
RUN_TYPE_DRY_RUN = "dry_run"
RUN_TYPE_MORNING = "morning_report"

DEFAULT_MEMORY_LIMIT = 80
MAX_MEMORY_LIMIT = 500
DREAM_LINK_RELATION = "dream"
DREAM_MEMORY_TYPE = "dream"
DREAM_LINK_MAX = 40
DREAM_SOURCE_LINK_MAX = 80
MARA_PROMPT_SNIPPET_LIMIT = 18
MEMORY_ID_REF_RE = re.compile(r"\[(\d+)\]")
MEMORY_ID_KEY_VALUE_RE = re.compile(
    r'\\?"(?:source_memory_id|target_memory_id|memory_id|from_memory_id|to_memory_id|memory_a_id|memory_b_id)\\?"\s*:\s*(\d+)'
)


ModelChecker = Callable[[], dict[str, Any]]
ModelLifecycle = Any


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SandmanPaths:
    root: Path
    base_dir: Path
    runs_dir: Path
    logs_dir: Path
    reports_dir: Path
    dreams_dir: Path
    candidates_dir: Path
    snapshots_dir: Path
    lock_path: Path
    db_path: Path

    @classmethod
    def for_root(cls, root_path: str | os.PathLike[str] | Path | None = None) -> "SandmanPaths":
        root = Path(root_path).resolve() if root_path is not None else Path(config.ROOT).resolve()
        base_dir = root / "data" / "sandman"
        return cls(
            root=root,
            base_dir=base_dir,
            runs_dir=base_dir / "runs",
            logs_dir=base_dir / "logs",
            reports_dir=base_dir / "reports",
            dreams_dir=base_dir / "dreams",
            candidates_dir=base_dir / "candidates",
            snapshots_dir=base_dir / "snapshots",
            lock_path=base_dir / "sandman.lock",
            db_path=root / "data" / "agent_memory.db",
        )

    def ensure_dirs(self) -> None:
        for path in (
            self.base_dir,
            self.runs_dir,
            self.logs_dir,
            self.reports_dir,
            self.dreams_dir,
            self.candidates_dir,
            self.snapshots_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


class MaraLmStudioClient:
    def __init__(self, *, timeout_seconds: int | None = None, max_tokens: int | None = None, model: str | None = None) -> None:
        self.timeout_seconds = int(timeout_seconds or os.environ.get("SANDMAN_MARA_TIMEOUT_SECONDS", "120"))
        self.max_tokens = int(max_tokens or os.environ.get("SANDMAN_MARA_MAX_TOKENS", "1800"))
        self.model = model or os.environ.get("SANDMAN_GEMMA_LMS_IDENTIFIER", "sandman-gemma")

    def complete_json(self, prompt: str, *, timeout_seconds: int | None = None) -> str:
        from app import lm_studio_client

        messages = [
            {"role": "system", "content": sandman_profiles.SANDMAN_MARA_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        return lm_studio_client.call_lm_studio(
            messages,
            sandman_profiles.sandman_mara_response_format(),
            max_tokens=self.max_tokens,
            timeout=int(timeout_seconds or self.timeout_seconds),
            model=self.model,
        )


class SandmanLmsModelLifecycle:
    """Owns the LM Studio CLI model lifecycle for one Sandman Mara pass."""

    def __init__(
        self,
        *,
        model_key: str | None = None,
        identifier: str | None = None,
        ttl_seconds: int | None = None,
        gpu: str | None = None,
        context_length: str | None = None,
        start_server: bool | None = None,
    ) -> None:
        self.model_key = model_key or os.environ.get("SANDMAN_GEMMA_MODEL", "google/gemma-4-e2b")
        self.identifier = identifier or os.environ.get("SANDMAN_GEMMA_LMS_IDENTIFIER", "sandman-gemma")
        self.ttl_seconds = int(ttl_seconds if ttl_seconds is not None else os.environ.get("SANDMAN_GEMMA_LMS_TTL_SECONDS", "300"))
        self.gpu = gpu if gpu is not None else os.environ.get("SANDMAN_GEMMA_LMS_GPU", "max")
        self.context_length = context_length if context_length is not None else os.environ.get("SANDMAN_GEMMA_LMS_CONTEXT_LENGTH")
        self.start_server = _env_bool("SANDMAN_MARA_LMS_START_SERVER", True) if start_server is None else bool(start_server)

    def load(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "unknown",
            "model_key": self.model_key,
            "identifier": self.identifier,
            "server_start": None,
            "load": None,
        }
        if self.start_server:
            server_start = _run_lms(["server", "start"], timeout=120, ok_markers=("already", "running"))
            result["server_start"] = server_start
            if server_start["status"] == "failed":
                result["status"] = "failed"
                result["error"] = server_start.get("error")
                return result

        args = ["load", self.model_key, "--identifier", self.identifier, "--ttl", str(self.ttl_seconds), "--yes"]
        if self.gpu:
            args.extend(["--gpu", self.gpu])
        if self.context_length:
            args.extend(["--context-length", str(self.context_length)])
        load_result = _run_lms(args, timeout=600, ok_markers=("already", "loaded"))
        result["load"] = load_result
        result["status"] = "loaded" if load_result["status"] == "ok" else "failed"
        if load_result["status"] == "failed":
            result["error"] = load_result.get("error")
        return result

    def unload(self) -> dict[str, Any]:
        result = _run_lms(["unload", self.identifier], timeout=120, ok_markers=("not loaded", "not found", "unloaded"))
        status = "unloaded" if result["status"] == "ok" else "failed"
        return {"status": status, "identifier": self.identifier, "unload": result}


def _run_lms(args: list[str], *, timeout: int, ok_markers: tuple[str, ...] = ()) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["lms", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return {"status": "failed", "command": ["lms", *args], "error": _safe_error(exc)}
    except subprocess.TimeoutExpired as exc:
        return {"status": "failed", "command": ["lms", *args], "error": _safe_error(exc)}

    stdout = _clean_lms_output(completed.stdout)
    stderr = _clean_lms_output(completed.stderr)
    combined = f"{stdout}\n{stderr}".lower()
    marker_ok = any(marker in combined for marker in ok_markers)
    status = "ok" if completed.returncode == 0 or marker_ok else "failed"
    payload: dict[str, Any] = {
        "status": status,
        "command": ["lms", *args],
        "returncode": completed.returncode,
    }
    if stdout:
        payload["stdout"] = stdout
    if stderr:
        payload["stderr"] = stderr
    if status == "failed":
        payload["error"] = stderr or stdout or f"lms {' '.join(args)} failed with exit code {completed.returncode}"
    return payload


def _clean_lms_output(value: str | None) -> str:
    text = (value or "").strip().replace("\r", "\n")
    redacted_tokens = ("token", "bearer", "authorization", "secret", "password", "apikey", "api_key")
    if any(marker in text.lower() for marker in redacted_tokens):
        return "<redacted lms output containing sensitive marker>"
    return text[:1200]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_now() -> datetime:
    return datetime.now().astimezone()


def file_timestamp(now: datetime | None = None) -> str:
    current = now or local_now()
    return current.strftime("%Y-%m-%dT%H%M%S")


def make_run_id(run_type: str, now: datetime | None = None) -> str:
    return f"{file_timestamp(now)}-{run_type}-{uuid.uuid4().hex[:8]}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_now_iso()} {message}\n")


def _safe_error(exc: BaseException) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    redacted_tokens = ("token", "bearer", "authorization", "secret", "password", "apikey", "api_key")
    lower = text.lower()
    if any(marker in lower for marker in redacted_tokens):
        return "<redacted error containing sensitive marker>"
    return text[:1200]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            return False
        output = (completed.stdout or "").lower()
        return str(pid) in output and "no tasks" not in output
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def inspect_lock(lock_path: Path) -> dict[str, Any]:
    lock = _read_json(lock_path)
    if not lock:
        return {"exists": False, "active": False, "stale": False, "lock": None}
    pid = _to_int(lock.get("pid"), default=0)
    active = _is_process_running(pid)
    return {"exists": True, "active": active, "stale": not active, "lock": lock}


def acquire_lock(paths: SandmanPaths, *, run_id: str, run_type: str) -> dict[str, Any]:
    paths.ensure_dirs()
    state = inspect_lock(paths.lock_path)
    if state["active"]:
        return {"acquired": False, "reason": "active_lock", "lock": state["lock"], "warnings": []}
    warnings: list[str] = []
    if state["stale"]:
        warnings.append("stale_lock_removed")
        try:
            paths.lock_path.unlink()
        except FileNotFoundError:
            pass
    payload = {
        "run_id": run_id,
        "run_type": run_type,
        "started_at": utc_now_iso(),
        "pid": os.getpid(),
    }
    write_json(paths.lock_path, payload)
    return {"acquired": True, "reason": "lock_acquired", "lock": payload, "warnings": warnings}


def release_lock(paths: SandmanPaths, *, run_id: str) -> None:
    lock = _read_json(paths.lock_path)
    if not lock or lock.get("run_id") != run_id:
        return
    try:
        paths.lock_path.unlink()
    except FileNotFoundError:
        return


def _db_connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _db_connect_write(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    db_migrations.apply_all_migrations(conn)
    conn.commit()
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in row.keys()} for row in rows]


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _memory_tags(memory: dict[str, Any]) -> set[str]:
    return {
        item.strip().lower()
        for item in str(memory.get("tags") or "").split(",")
        if item.strip()
    }


def _to_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _compact_memory(memory: dict[str, Any]) -> dict[str, Any]:
    """Build Mara's content-first view without retrieval labels or tag hints."""
    content = " ".join(str(memory.get("content") or "").split())
    return {
        "id": memory.get("id"),
        "summary_short": memory.get("summary_short"),
        "content_excerpt": content[:1200],
        "memory_type": memory.get("memory_type"),
        "project_key": memory.get("project_key"),
        "created_at": memory.get("created_at"),
        "importance_score": memory.get("importance_score"),
        "emotional_weight": memory.get("emotional_weight"),
    }


def _project_filter_sql(project_key: str | None) -> tuple[str, list[Any]]:
    if not project_key:
        return "", []
    return " AND LOWER(COALESCE(project_key, '')) = LOWER(?)", [project_key]


def _load_active_memories(db_path: Path, *, project_key: str | None, limit: int) -> tuple[list[dict[str, Any]], int, list[str]]:
    warnings: list[str] = []
    if not db_path.exists():
        return [], 0, [f"database_not_found: {db_path}"]
    try:
        conn = _db_connect_readonly(db_path)
    except sqlite3.Error as exc:
        return [], 0, [f"database_open_failed: {_safe_error(exc)}"]
    try:
        if not _table_exists(conn, "memories"):
            return [], 0, ["memories_table_not_found"]
        project_sql, params = _project_filter_sql(project_key)
        count_row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM memories
            WHERE archived_at IS NULL
              AND COALESCE(activity_state, 'active') = 'active'
              {project_sql}
            """,
            params,
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT *
            FROM memories
            WHERE archived_at IS NULL
              AND COALESCE(activity_state, 'active') = 'active'
              {project_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, max(1, min(int(limit), MAX_MEMORY_LIMIT))),
        ).fetchall()
        return _rows_to_dicts(rows), int(count_row["count"] or 0), warnings
    except sqlite3.Error as exc:
        return [], 0, [f"memory_query_failed: {_safe_error(exc)}"]
    finally:
        conn.close()


def create_db_snapshot(paths: SandmanPaths, *, run_id: str, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"status": "skipped", "reason": "dry_run_no_snapshot"}
    if not paths.db_path.exists():
        return {"status": "skipped", "reason": "database_not_found", "path": str(paths.db_path)}
    snapshot_path = paths.snapshots_dir / f"{run_id}-agent_memory.db"
    try:
        shutil.copy2(paths.db_path, snapshot_path)
    except OSError as exc:
        return {"status": "failed", "error": _safe_error(exc), "path": str(snapshot_path)}
    return {"status": "created", "path": str(snapshot_path)}


def build_math_output(
    *,
    run_id: str,
    run_type: str,
    db_path: Path,
    project_key: str | None = None,
    limit: int = DEFAULT_MEMORY_LIMIT,
    generated_at: str | None = None,
) -> dict[str, Any]:
    output = sandman_profiles.empty_math_output(run_id=run_id, run_type=run_type, generated_at=generated_at)
    memories, total_count, warnings = _load_active_memories(db_path, project_key=project_key, limit=limit)
    output["warnings"].extend(warnings)
    output["summary"]["memory_count_seen"] = total_count

    if warnings:
        output["status"] = "partial"
        output["summary"]["short"] = "Deterministic memory hygiene preview completed with warnings."

    findings: list[dict[str, Any]] = []
    duplicate_candidates: list[dict[str, Any]] = []
    conflict_candidates: list[dict[str, Any]] = []
    revalidation_candidates: list[dict[str, Any]] = []
    tag_candidates: list[dict[str, Any]] = []
    link_candidates: list[dict[str, Any]] = []

    by_summary: dict[str, list[dict[str, Any]]] = {}
    by_content: dict[str, list[dict[str, Any]]] = {}
    for memory in memories:
        memory_id = _to_int(memory.get("id"))
        summary_key = _normalize_text(memory.get("summary_short"))
        if summary_key:
            by_summary.setdefault(summary_key, []).append(memory)
        content_key = _normalize_text(memory.get("content"))[:240]
        if len(content_key) >= 60:
            by_content.setdefault(content_key, []).append(memory)

        tags = _memory_tags(memory)
        confidence = _to_float(memory.get("confidence_score"))
        importance = _to_float(memory.get("importance_score"))
        project = str(memory.get("project_key") or "").strip()
        scope = str(memory.get("scope_code") or "").strip()
        owner_role = str(memory.get("owner_role") or "").strip()
        owner_id = str(memory.get("owner_id") or "").strip()

        if not tags:
            tag_candidates.append(
                {
                    "memory_id": memory_id,
                    "suggested_tags": _suggest_tags(memory),
                    "reason": "missing tags on active memory",
                    "confidence": 0.55,
                    "requires_review": True,
                }
            )
            findings.append(
                _finding(
                    "weak_tagging",
                    "low",
                    [memory_id],
                    "Active memory has no tags.",
                    "Missing tags reduce retrieval and linking quality.",
                    0.65,
                )
            )
        if project and (not scope or scope == "global"):
            findings.append(
                _finding(
                    "project_key_issue",
                    "medium",
                    [memory_id],
                    f"project_key={project!r} with scope_code={scope or '<empty>'!r}.",
                    "Project-scoped memories should normally use project scope unless explicitly global.",
                    0.75,
                )
            )
        if not owner_role or not owner_id:
            findings.append(
                _finding(
                    "metadata_issue",
                    "medium",
                    [memory_id],
                    "owner_role or owner_id is missing.",
                    "Owner gaps make governance and review queues weaker.",
                    0.7,
                )
            )
        if confidence < 0.55 and importance >= 0.7:
            revalidation_candidates.append(
                {
                    "memory_id": memory_id,
                    "reason": "high importance memory has low confidence",
                    "suggested_question": "Czy to wspomnienie nadal jest prawdziwe i kto powinien je potwierdzic?",
                    "confidence": 0.74,
                    "requires_review": True,
                }
            )
        if _to_int(memory.get("contradiction_flag")) == 1:
            conflict_candidates.append(
                {
                    "memory_ids": [memory_id],
                    "conflict_summary": "Memory is already marked with contradiction_flag=1.",
                    "evidence": f"memory_id={memory_id}, summary={memory.get('summary_short')!r}",
                    "review_question": "Czy konflikt jest aktualny, czy zostal juz rozstrzygniety w nowszej decyzji?",
                    "confidence": 0.8,
                    "requires_review": True,
                }
            )

    for bucket in (by_summary, by_content):
        for group in bucket.values():
            if len(group) < 2:
                continue
            ids = sorted(_to_int(item.get("id")) for item in group)
            if not ids or any(item <= 0 for item in ids):
                continue
            duplicate_candidates.append(
                {
                    "memory_ids": ids,
                    "reason": "same normalized summary or near-identical content prefix",
                    "confidence": 0.76,
                    "requires_review": True,
                }
            )

    link_candidates = _build_tag_link_candidates(memories)

    candidates = output["candidates"]
    candidates["link_candidates"] = link_candidates[:40]
    candidates["duplicate_candidates"] = duplicate_candidates[:40]
    candidates["conflict_candidates"] = conflict_candidates[:40]
    candidates["revalidation_candidates"] = revalidation_candidates[:40]
    candidates["tag_candidates"] = tag_candidates[:40]

    output["findings"] = findings[:80]
    candidate_count = sum(len(items) for items in candidates.values())
    output["summary"]["candidate_count"] = candidate_count
    if candidate_count >= 50 or any(item.get("severity") == "high" for item in output["findings"]):
        output["summary"]["risk_level"] = "medium"
    output["summary"]["short"] = (
        f"Matematyk saw {total_count} active memories and produced {candidate_count} review candidates."
    )
    return output


def _finding(kind: str, severity: str, memory_ids: list[int], evidence: str, reasoning: str, confidence: float) -> dict[str, Any]:
    return {
        "type": kind,
        "severity": severity,
        "memory_ids": memory_ids,
        "evidence": evidence,
        "reasoning": reasoning,
        "confidence": confidence,
    }


def _suggest_tags(memory: dict[str, Any]) -> list[str]:
    suggestions: list[str] = []
    for key in ("project_key", "memory_type", "layer_code", "area_code"):
        value = str(memory.get(key) or "").strip().lower().replace(" ", "-")
        if value and value not in suggestions:
            suggestions.append(value)
    if not suggestions:
        suggestions.append("needs-tag-review")
    return suggestions[:4]


def _build_tag_link_candidates(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    limited = memories[:80]
    for index, left in enumerate(limited):
        left_id = _to_int(left.get("id"))
        left_tags = _memory_tags(left)
        generic_tags = _generic_link_tags(left)
        if not left_tags:
            continue
        for right in limited[index + 1 :]:
            right_id = _to_int(right.get("id"))
            if left_id == right_id:
                continue
            overlap = sorted(left_tags & _memory_tags(right))
            useful_overlap = [tag for tag in overlap if tag not in (generic_tags | _generic_link_tags(right))]
            if not useful_overlap:
                continue
            candidates.append(
                {
                    "source_memory_id": left_id,
                    "target_memory_id": right_id,
                    "reason": f"shared non-generic tags: {', '.join(useful_overlap[:4])}",
                    "confidence": round(min(0.82, 0.48 + 0.08 * len(useful_overlap)), 3),
                    "requires_review": True,
                }
            )
            if len(candidates) >= 40:
                return candidates
    return candidates


def _generic_link_tags(memory: dict[str, Any]) -> set[str]:
    tags = {
        "ai",
        "chat",
        "codex",
        "gemma",
        "agent",
        "mapi",
        "memory",
        "demo-project",
        "project",
        "sandman",
        "smoke",
        "test",
    }
    project_key = str(memory.get("project_key") or "").strip().lower()
    if project_key:
        tags.add(project_key)
    return tags


def _memory_ids_from_math(math_output: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    for finding in math_output.get("findings", []):
        for memory_id in finding.get("memory_ids", []):
            _append_unique_int(ids, memory_id)
    candidates = math_output.get("candidates") or {}
    for item in candidates.get("link_candidates", []):
        _append_unique_int(ids, item.get("source_memory_id"))
        _append_unique_int(ids, item.get("target_memory_id"))
    for key in ("duplicate_candidates", "conflict_candidates", "revalidation_candidates", "tag_candidates"):
        for item in candidates.get(key, []):
            if "memory_id" in item:
                _append_unique_int(ids, item.get("memory_id"))
            for memory_id in item.get("memory_ids", []):
                _append_unique_int(ids, memory_id)
    return ids[:30]


def _append_unique_int(items: list[int], value: Any) -> None:
    parsed = _to_int(value)
    if parsed > 0 and parsed not in items:
        items.append(parsed)


def load_memory_snippets(db_path: Path, memory_ids: list[int], *, project_key: str | None, fallback_limit: int = 12) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    try:
        conn = _db_connect_readonly(db_path)
    except sqlite3.Error:
        return []
    try:
        if not _table_exists(conn, "memories"):
            return []
        rows: list[sqlite3.Row] = []
        if memory_ids:
            placeholders = ",".join("?" for _ in memory_ids)
            rows = conn.execute(
                f"SELECT * FROM memories WHERE id IN ({placeholders}) ORDER BY id DESC",
                tuple(memory_ids),
            ).fetchall()
        if not rows:
            project_sql, params = _project_filter_sql(project_key)
            rows = conn.execute(
                f"""
                SELECT *
                FROM memories
                WHERE archived_at IS NULL
                  AND COALESCE(activity_state, 'active') = 'active'
                  {project_sql}
                ORDER BY id DESC
                LIMIT ?
                """,
                (*params, fallback_limit),
            ).fetchall()
        return [_compact_memory(item) for item in _rows_to_dicts(rows)]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def default_mara_model_checker() -> dict[str, Any]:
    try:
        from app import sandman_gemma_runtime

        return sandman_gemma_runtime.ensure_gemma_ready(required=False, fail_closed=False, autostart=False)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": _safe_error(exc)}


def _safe_model_lifecycle_call(action_name: str, callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        result = callback()
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "action": action_name, "error": _safe_error(exc)}
    if not isinstance(result, dict):
        return {"status": "failed", "action": action_name, "error": "model lifecycle callback did not return an object"}
    result.setdefault("action", action_name)
    return result


def _load_model_for_run(model_lifecycle: ModelLifecycle, lifecycle_log: dict[str, Any], warnings: list[str]) -> None:
    load_result = _safe_model_lifecycle_call("load", model_lifecycle.load)
    lifecycle_log["load"] = load_result
    if load_result.get("status") not in {"ok", "loaded", "already_loaded"}:
        warnings.append("sandman_model_load_failed")


def _unload_model_for_run(model_lifecycle: ModelLifecycle, lifecycle_log: dict[str, Any], warnings: list[str]) -> None:
    unload_result = _safe_model_lifecycle_call("unload", model_lifecycle.unload)
    lifecycle_log["unload"] = unload_result
    if unload_result.get("status") not in {"ok", "unloaded", "not_loaded"}:
        warnings.append("sandman_model_unload_failed")


def build_mara_prompt(
    *,
    run_id: str,
    run_type: str,
    project_key: str | None,
    math_output: dict[str, Any],
    memory_snippets: list[dict[str, Any]],
) -> str:
    payload = {
        "run_id": run_id,
        "run_type": run_type,
        "project_key": project_key,
        "input_kind": "sandman_mara_dream_pass_v2",
        "selection_context": (
            "The memories were selected upstream by a deterministic process. Do not inspect, repeat, "
            "or reconstruct its tags, scores, findings, or candidate reasons. Read the memory content itself."
        ),
        "memory_fragments": memory_snippets[:MARA_PROMPT_SNIPPET_LIMIT],
        "dream_direction": {
            "language": "Polish",
            "order": ["scene", "encounter", "tension", "transformation", "awakening"],
            "narrative_first": True,
            "interpretation_after_narrative_only": True,
            "use_archetypes_when_natural": True,
            "prefer_semantic_leaps_over_shared_labels": True,
            "avoid_style": [
                "corporate report",
                "research abstract",
                "tag summary",
                "project status recap",
                "phrases such as persistent thread or underlying tension",
            ],
        },
        "output_contract": {
            "profile_name": "sandman_mara",
            "run_id": run_id,
            "run_type": run_type,
            "status": "completed|partial|skipped|failed",
            "dream_report": {
                "title": "string",
                "narrative": "string",
                "dominant_motifs": [],
                "unresolved_loops": [],
                "morning_note": "string",
            },
            "association_candidates": [],
            "metaphor_links": [],
            "consolidation_proposals": [],
            "revalidation_questions": [],
        },
        "hard_rule": (
            "Dream artifacts are not facts. Write the dream_report.narrative as an actual symbolic story, not an analysis. "
            "Do not mention tags, similarity scores, candidate lists, pipeline mechanics, or memory metadata inside the narrative. "
            "Let objects, places, characters, weather, movement, absence, repetition, and transformation carry meaning. "
            "A memory id may appear outside the narrative in associations or questions, but the story should remain readable without ids. "
            "Put cautious interpretation only in morning_note, unresolved_loops, association_candidates, metaphor_links, and revalidation_questions. "
            "Every explicit association must name input memory ids or say evidence is weak. Keep JSON compact: max 6 association_candidates, "
            "max 3 metaphor_links, max 4 revalidation_questions, max 5 consolidation_proposals. consolidation_proposals are review-only proposals, "
            "never direct fact mutations."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def run_mara_profile(
    *,
    run_id: str,
    run_type: str,
    paths: SandmanPaths,
    project_key: str | None,
    math_output: dict[str, Any],
    model_checker: ModelChecker | None = None,
    mara_client: Any | None = None,
) -> dict[str, Any]:
    checker = model_checker or default_mara_model_checker
    model_status = checker()
    if model_status.get("status") != "ok":
        detail = str(model_status.get("error") or model_status.get("status") or "unknown")
        return sandman_profiles.mara_skipped_output(
            run_id=run_id,
            run_type=run_type,
            reason="model_unavailable",
            detail=detail,
        )

    memory_ids = _memory_ids_from_math(math_output)
    snippets = load_memory_snippets(paths.db_path, memory_ids, project_key=project_key)
    prompt = build_mara_prompt(
        run_id=run_id,
        run_type=run_type,
        project_key=project_key,
        math_output=math_output,
        memory_snippets=snippets,
    )
    client = mara_client or MaraLmStudioClient()
    raw = ""
    try:
        raw = client.complete_json(prompt)
        parsed = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        fallback = _mara_raw_fragment_output(raw, run_id=run_id, run_type=run_type, error=_safe_error(exc))
        if fallback is not None:
            return fallback
        failed = sandman_profiles.mara_skipped_output(
            run_id=run_id,
            run_type=run_type,
            reason="model_response_failed",
            detail=_safe_error(exc),
        )
        failed["status"] = "failed"
        failed["errors"].append(_safe_error(exc))
        return failed

    if not isinstance(parsed, dict):
        failed = sandman_profiles.mara_skipped_output(run_id=run_id, run_type=run_type, reason="invalid_model_payload")
        failed["status"] = "failed"
        failed["errors"].append("Mara model output was not a JSON object.")
        return failed

    return normalize_mara_output(parsed, run_id=run_id, run_type=run_type)


def _mara_raw_fragment_output(raw: str, *, run_id: str, run_type: str, error: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if len(text) < 80:
        return None
    excerpt = text[:6000]
    touched_ids: list[int] = []
    _append_ids_from_value(touched_ids, excerpt)
    return normalize_mara_output(
        {
            "profile_name": "sandman_mara",
            "run_id": run_id,
            "run_type": run_type,
            "generated_at": utc_now_iso(),
            "status": "partial",
            "summary": {
                "short": "Mara produced a raw dream fragment, but JSON parsing failed.",
                "dream_count": 1,
                "association_count": 0,
                "risk_level": "low",
            },
            "dream_report": {
                "title": "Surowy sen Mary",
                "narrative": excerpt,
                "dominant_motifs": [],
                "unresolved_loops": ["Mara returned malformed JSON; treat this as a raw dream fragment."],
                "morning_note": "Sen częściowy: przeczytać ostrożnie, bo struktura JSON była uszkodzona.",
            },
            "association_candidates": [],
            "metaphor_links": [],
            "revalidation_questions": [
                {
                    "memory_ids": touched_ids[:12],
                    "question": "Czy surowy fragment snu Mary zawiera użyteczne skojarzenie?",
                    "reason": "model_response_failed_raw_fragment",
                    "priority": "low",
                }
            ]
            if touched_ids
            else [],
            "warnings": ["model_response_failed_raw_fragment", error],
            "errors": [],
            "requires_human_review": True,
        },
        run_id=run_id,
        run_type=run_type,
    )


def normalize_mara_output(payload: dict[str, Any], *, run_id: str, run_type: str) -> dict[str, Any]:
    output = dict(payload)
    output["profile_name"] = "sandman_mara"
    output["run_id"] = str(output.get("run_id") or run_id)
    output["run_type"] = str(output.get("run_type") or run_type)
    output["generated_at"] = str(output.get("generated_at") or utc_now_iso())
    output["status"] = str(output.get("status") or "partial")
    if output["status"] not in {"completed", "partial", "skipped", "failed"}:
        output["status"] = "partial"
    output.setdefault("summary", {"short": "Mara produced a dream artifact.", "dream_count": 1, "association_count": 0, "risk_level": "low"})
    output.setdefault("dream_report", {"title": "Mara dream", "narrative": "", "dominant_motifs": [], "unresolved_loops": [], "morning_note": ""})
    output.setdefault("association_candidates", [])
    output.setdefault("metaphor_links", [])
    output.setdefault("consolidation_proposals", [])
    output.setdefault("revalidation_questions", [])
    output.setdefault("warnings", [])
    output.setdefault("errors", [])
    output["requires_human_review"] = True
    missing = sandman_profiles.validate_required_keys(
        output,
        [
            "summary",
            "dream_report",
            "association_candidates",
            "metaphor_links",
            "consolidation_proposals",
            "revalidation_questions",
            "warnings",
            "errors",
        ],
    )
    if missing:
        output["warnings"].append(f"mara_missing_keys_normalized: {missing}")
        output["status"] = "partial"
    return output


def persist_mara_dream_memory(
    *,
    paths: SandmanPaths,
    run_id: str,
    run_type: str,
    project_key: str | None,
    mara_output: dict[str, Any],
    artifact_path: Path | None,
    snapshot: dict[str, Any] | None,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return {"status": "skipped", "reason": "dry_run", "warnings": [], "errors": []}
    if str(mara_output.get("status") or "") not in {"completed", "partial"}:
        return {"status": "skipped", "reason": "mara_not_completed", "warnings": [], "errors": []}
    if not paths.db_path.exists():
        return {"status": "skipped", "reason": "database_not_found", "warnings": [f"database_not_found: {paths.db_path}"], "errors": []}

    snapshot_status = str((snapshot or {}).get("status") or "")
    if snapshot_status != "created":
        return {
            "status": "skipped",
            "reason": "snapshot_not_created",
            "warnings": [f"mara_memory_persistence_skipped_snapshot_status={snapshot_status or 'unknown'}"],
            "errors": [],
        }

    origin = f"sandman_mara:{run_id}"
    conn: sqlite3.Connection | None = None
    try:
        conn = _db_connect_write(paths.db_path)
        if not _table_exists(conn, "memories") or not _table_exists(conn, "memory_links"):
            return {"status": "skipped", "reason": "memory_tables_not_found", "warnings": ["memory_tables_not_found"], "errors": []}

        touched_ids = _existing_memory_ids(conn, _extract_mara_touched_memory_ids(mara_output))
        dream_memory_id, dream_created = _get_or_create_mara_dream_memory(
            conn,
            run_id=run_id,
            run_type=run_type,
            project_key=project_key,
            mara_output=mara_output,
            touched_memory_ids=touched_ids,
            artifact_path=artifact_path,
            origin=origin,
        )

        source_link_ids: list[int] = []
        source_links_created = 0
        for memory_id in touched_ids[:DREAM_SOURCE_LINK_MAX]:
            link = _insert_dream_link_if_missing(
                conn,
                from_memory_id=dream_memory_id,
                to_memory_id=memory_id,
                weight=0.55,
                origin=f"{origin}:source",
            )
            if link:
                source_link_ids.append(int(link["id"]))
                if link["created"]:
                    source_links_created += 1

        association_link_ids: list[int] = []
        association_links_created = 0
        valid_ids = set(touched_ids)
        for pair in _extract_mara_association_pairs(mara_output)[:DREAM_LINK_MAX]:
            source_id = int(pair["source_memory_id"])
            target_id = int(pair["target_memory_id"])
            if source_id not in valid_ids or target_id not in valid_ids:
                continue
            link = _insert_dream_link_if_missing(
                conn,
                from_memory_id=source_id,
                to_memory_id=target_id,
                weight=pair["weight"],
                origin=f"{origin}:association",
            )
            if link:
                association_link_ids.append(int(link["id"]))
                if link["created"]:
                    association_links_created += 1

        consolidation_proposal_memory_ids: list[int] = []
        consolidation_links_created = 0
        for proposal in _extract_mara_consolidation_proposals(mara_output)[:5]:
            proposal_memory_id = _insert_mara_consolidation_proposal_memory(
                conn,
                run_id=run_id,
                run_type=run_type,
                project_key=project_key,
                proposal=proposal,
                artifact_path=artifact_path,
                origin=origin,
            )
            consolidation_proposal_memory_ids.append(proposal_memory_id)
            for memory_id in proposal["memory_ids"][:DREAM_SOURCE_LINK_MAX]:
                link = _insert_relation_link_if_missing(
                    conn,
                    from_memory_id=proposal_memory_id,
                    to_memory_id=memory_id,
                    relation_type="relates_to",
                    weight=0.72,
                    origin=f"{origin}:proposal",
                )
                if link and link["created"]:
                    consolidation_links_created += 1

        conn.commit()
        warnings: list[str] = []
        if not touched_ids:
            warnings.append("mara_dream_memory_created_without_touched_memories")
        return {
            "status": "created" if dream_created else "already_exists",
            "dream_memory_id": dream_memory_id,
            "dream_memory_created": dream_created,
            "touched_memory_ids": touched_ids,
            "source_link_ids": source_link_ids,
            "source_links_created": source_links_created,
            "association_link_ids": association_link_ids,
            "association_links_created": association_links_created,
            "consolidation_proposal_memory_ids": consolidation_proposal_memory_ids,
            "consolidation_proposals_created": len(consolidation_proposal_memory_ids),
            "consolidation_links_created": consolidation_links_created,
            "relation_type": DREAM_LINK_RELATION,
            "origin": origin,
            "warnings": warnings,
            "errors": [],
        }
    except sqlite3.Error as exc:
        if conn is not None:
            conn.rollback()
        return {"status": "failed", "reason": "database_write_failed", "warnings": [], "errors": [_safe_error(exc)]}
    finally:
        if conn is not None:
            conn.close()


def _get_or_create_mara_dream_memory(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    run_type: str,
    project_key: str | None,
    mara_output: dict[str, Any],
    touched_memory_ids: list[int],
    artifact_path: Path | None,
    origin: str,
) -> tuple[int, bool]:
    row = conn.execute(
        "SELECT id FROM memories WHERE memory_type = ? AND source = ? AND archived_at IS NULL LIMIT 1",
        (DREAM_MEMORY_TYPE, origin),
    ).fetchone()
    if row:
        return int(row["id"]), False

    now = utc_now_iso()
    dream_report = mara_output.get("dream_report") if isinstance(mara_output.get("dream_report"), dict) else {}
    title = str(dream_report.get("title") or "Sen Mary").strip() or "Sen Mary"
    summary = _truncate_for_summary(f"Mara: {title}")
    content = _build_mara_dream_memory_content(
        run_id=run_id,
        run_type=run_type,
        mara_output=mara_output,
        touched_memory_ids=touched_memory_ids,
        artifact_path=artifact_path,
    )
    workspace_id = _first_memory_workspace_id(conn, touched_memory_ids)
    tags = ",".join(item for item in ["sandman", "mara", "dream", "dream-link", "requires-review", project_key] if item)
    cursor = conn.execute(
        """
        INSERT INTO memories (
            content, summary_short, memory_type, source, importance_score, confidence_score, tags,
            recall_count, created_at, last_accessed_at, activity_state, evidence_count, contradiction_flag,
            layer_code, area_code, state_code, scope_code, decay_score, emotional_weight, identity_weight,
            project_key, owner_role, owner_id, workspace_id, visibility_scope, sharing_policy, priority
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'active', 1, 0, ?, ?, ?, ?, 0.0, ?, 0.0, ?, ?, ?, ?, 'project', 'explicit', 'medium')
        """,
        (
            content,
            summary,
            DREAM_MEMORY_TYPE,
            origin,
            0.58,
            0.45,
            tags,
            now,
            now,
            "buffer",
            "sandman",
            "review",
            "project",
            0.35,
            project_key,
            "dream_reviewer",
            "agent",
            workspace_id,
        ),
    )
    return int(cursor.lastrowid), True


def _build_mara_dream_memory_content(
    *,
    run_id: str,
    run_type: str,
    mara_output: dict[str, Any],
    touched_memory_ids: list[int],
    artifact_path: Path | None,
) -> str:
    dream_report = mara_output.get("dream_report") if isinstance(mara_output.get("dream_report"), dict) else {}
    summary = mara_output.get("summary") if isinstance(mara_output.get("summary"), dict) else {}
    association_lines = _format_mara_items(mara_output.get("association_candidates"), ("source_memory_id", "target_memory_id", "reason", "confidence"))
    metaphor_lines = _format_mara_items(mara_output.get("metaphor_links"), ("source_memory_id", "target_memory_id", "link", "type"))
    question_lines = _format_mara_items(mara_output.get("revalidation_questions"), ("memory_ids", "question", "reason", "priority"))
    motifs = _format_plain_list(dream_report.get("dominant_motifs"))
    loops = _format_plain_list(dream_report.get("unresolved_loops"))
    touched = ", ".join(str(item) for item in touched_memory_ids) if touched_memory_ids else "brak jawnych id"
    artifact = str(artifact_path) if artifact_path else "brak pliku artefaktu"
    return (
        "To jest sen Mary, czyli hipoteza i materiał do późniejszego czytania przez Jagodę. "
        "Nie traktować jako faktu bez review.\n\n"
        f"Run: {run_id} ({run_type})\n"
        f"Status Mary: {mara_output.get('status')}\n"
        f"Artefakt: {artifact}\n"
        f"Poruszone wspomnienia: {touched}\n\n"
        f"Tytuł snu: {dream_report.get('title') or 'Sen Mary'}\n\n"
        f"Narracja:\n{dream_report.get('narrative') or ''}\n\n"
        f"Motywy: {motifs}\n"
        f"Nierozwiązane pętle: {loops}\n"
        f"Notatka poranna: {dream_report.get('morning_note') or summary.get('short') or ''}\n\n"
        f"Skojarzenia dream:\n{association_lines}\n\n"
        f"Linki metaforyczne:\n{metaphor_lines}\n\n"
        f"Pytania do rewalidacji:\n{question_lines}\n"
    )


def _format_plain_list(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "brak"
    return ", ".join(str(item) for item in value[:12])


def _format_mara_items(value: Any, preferred_keys: tuple[str, ...]) -> str:
    if not isinstance(value, list) or not value:
        return "- brak"
    lines: list[str] = []
    for item in value[:12]:
        if isinstance(item, dict):
            parts = []
            for key in preferred_keys:
                if key in item and item[key] not in (None, "", []):
                    parts.append(f"{key}={item[key]}")
            if not parts:
                parts.append(json.dumps(item, ensure_ascii=False)[:500])
            lines.append(f"- {'; '.join(parts)}")
        else:
            lines.append(f"- {item}")
    return "\n".join(lines)


def _truncate_for_summary(value: str, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _first_memory_workspace_id(conn: sqlite3.Connection, memory_ids: list[int]) -> int | None:
    if not memory_ids:
        row = conn.execute("SELECT id FROM workspaces WHERE workspace_key = 'default' LIMIT 1").fetchone()
        return int(row["id"]) if row else None
    placeholders = ",".join("?" for _ in memory_ids[:DREAM_SOURCE_LINK_MAX])
    row = conn.execute(
        f"SELECT workspace_id FROM memories WHERE id IN ({placeholders}) AND workspace_id IS NOT NULL LIMIT 1",
        tuple(memory_ids[:DREAM_SOURCE_LINK_MAX]),
    ).fetchone()
    if row and row["workspace_id"] is not None:
        return int(row["workspace_id"])
    return None


def _insert_dream_link_if_missing(
    conn: sqlite3.Connection,
    *,
    from_memory_id: int,
    to_memory_id: int,
    weight: float,
    origin: str,
) -> dict[str, Any] | None:
    if from_memory_id <= 0 or to_memory_id <= 0 or from_memory_id == to_memory_id:
        return None
    existing = conn.execute(
        """
        SELECT id FROM memory_links
        WHERE archived_at IS NULL
          AND from_memory_id = ?
          AND to_memory_id = ?
          AND relation_type = ?
          AND origin = ?
        LIMIT 1
        """,
        (from_memory_id, to_memory_id, DREAM_LINK_RELATION, origin),
    ).fetchone()
    if existing:
        return {"id": int(existing["id"]), "created": False}
    workspace_id = _first_memory_workspace_id(conn, [from_memory_id])
    cursor = conn.execute(
        """
        INSERT INTO memory_links
            (from_memory_id, to_memory_id, relation_type, weight, origin, created_at, workspace_id, visibility_scope)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'inherited')
        """,
        (
            from_memory_id,
            to_memory_id,
            DREAM_LINK_RELATION,
            max(0.1, min(float(weight), 0.95)),
            origin,
            utc_now_iso(),
            workspace_id,
        ),
    )
    return {"id": int(cursor.lastrowid), "created": True}


def _insert_relation_link_if_missing(
    conn: sqlite3.Connection,
    *,
    from_memory_id: int,
    to_memory_id: int,
    relation_type: str,
    weight: float,
    origin: str,
) -> dict[str, Any] | None:
    if from_memory_id <= 0 or to_memory_id <= 0 or from_memory_id == to_memory_id:
        return None
    existing = conn.execute(
        """
        SELECT id FROM memory_links
        WHERE archived_at IS NULL
          AND from_memory_id = ?
          AND to_memory_id = ?
          AND relation_type = ?
          AND origin = ?
        LIMIT 1
        """,
        (from_memory_id, to_memory_id, relation_type, origin),
    ).fetchone()
    if existing:
        return {"id": int(existing["id"]), "created": False}
    workspace_id = _first_memory_workspace_id(conn, [from_memory_id])
    cursor = conn.execute(
        """
        INSERT INTO memory_links
            (from_memory_id, to_memory_id, relation_type, weight, origin, created_at, workspace_id, visibility_scope)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'inherited')
        """,
        (
            from_memory_id,
            to_memory_id,
            relation_type,
            max(0.1, min(float(weight), 0.95)),
            origin,
            utc_now_iso(),
            workspace_id,
        ),
    )
    return {"id": int(cursor.lastrowid), "created": True}


def _existing_memory_ids(conn: sqlite3.Connection, memory_ids: list[int]) -> list[int]:
    unique = [item for item in dict.fromkeys(memory_ids) if item > 0]
    if not unique:
        return []
    placeholders = ",".join("?" for _ in unique[:DREAM_SOURCE_LINK_MAX])
    rows = conn.execute(
        f"SELECT id FROM memories WHERE id IN ({placeholders}) AND archived_at IS NULL ORDER BY id ASC",
        tuple(unique[:DREAM_SOURCE_LINK_MAX]),
    ).fetchall()
    existing = {int(row["id"]) for row in rows}
    return [item for item in unique if item in existing]


def _extract_mara_touched_memory_ids(mara_output: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    for key in ("association_candidates", "metaphor_links", "consolidation_proposals", "revalidation_questions", "dream_report", "summary"):
        _append_ids_from_value(ids, mara_output.get(key))
    return ids


def _extract_mara_consolidation_proposals(mara_output: dict[str, Any]) -> list[dict[str, Any]]:
    raw = mara_output.get("consolidation_proposals")
    if not isinstance(raw, list):
        return []
    proposals: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        memory_ids: list[int] = []
        _append_ids_from_value(memory_ids, item.get("memory_ids"), key_hint="memory_ids")
        _append_ids_from_value(memory_ids, item.get("source_memory_ids"), key_hint="source_memory_ids")
        unique_memory_ids = [memory_id for memory_id in dict.fromkeys(memory_ids) if memory_id > 0]
        if not unique_memory_ids:
            continue
        action = str(item.get("action") or item.get("proposal_type") or "review_candidate").strip() or "review_candidate"
        reason = str(item.get("reason") or item.get("justification") or "").strip()
        title = str(item.get("title") or action).strip() or action
        proposals.append(
            {
                "title": title,
                "action": action,
                "reason": reason,
                "memory_ids": unique_memory_ids[:12],
                "requires_user_confirmation": True if item.get("requires_user_confirmation") is not False else True,
            }
        )
    return proposals


def _insert_mara_consolidation_proposal_memory(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    run_type: str,
    project_key: str | None,
    proposal: dict[str, Any],
    artifact_path: Path | None,
    origin: str,
) -> int:
    proposal_origin = f"{origin}:consolidation:{proposal['action']}:{'-'.join(str(item) for item in proposal['memory_ids'])}"
    row = conn.execute(
        "SELECT id FROM memories WHERE memory_type = ? AND source = ? AND archived_at IS NULL LIMIT 1",
        ("consolidation_proposal", proposal_origin),
    ).fetchone()
    if row:
        return int(row["id"])

    now = utc_now_iso()
    artifact = str(artifact_path) if artifact_path else "brak pliku artefaktu"
    title = _truncate_for_summary(f"Mara proposal: {proposal['title']}", limit=120)
    summary = title
    content = (
        "To jest propozycja konsolidacji Mary, nie fakt ani automatyczna mutacja.\n\n"
        f"Run: {run_id} ({run_type})\n"
        f"Artefakt: {artifact}\n"
        f"Akcja: {proposal['action']}\n"
        f"Dotknięte memory_id: {', '.join(str(item) for item in proposal['memory_ids'])}\n"
        f"Uzasadnienie: {proposal['reason'] or 'brak dodatkowego uzasadnienia'}\n"
        "Wymaga review i jawnej decyzji operatora/użytkownika przed zmianą faktów lub decyzji.\n"
    )
    workspace_id = _first_memory_workspace_id(conn, proposal["memory_ids"])
    tags = ",".join(item for item in ["sandman", "mara", "consolidation-proposal", "requires-review", project_key] if item)
    cursor = conn.execute(
        """
        INSERT INTO memories (
            content, summary_short, memory_type, source, importance_score, confidence_score, tags,
            recall_count, created_at, last_accessed_at, activity_state, evidence_count, contradiction_flag,
            layer_code, area_code, state_code, scope_code, decay_score, emotional_weight, identity_weight,
            project_key, owner_role, owner_id, workspace_id, visibility_scope, sharing_policy, priority,
            schema_version, entry_type, truth_kind, title, source_context, updated_at, last_confirmed_at,
            memory_v2_status, importance_level, requires_user_confirmation, should_resurface_when_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'active', 1, 0, ?, ?, ?, ?, 0.0, ?, 0.0, ?, ?, ?, ?, 'project', 'explicit', 'high',
                2, 'raw_note', 'proposal', ?, ?, ?, NULL, 'proposed', 'high', 1, ?)
        """,
        (
            content,
            summary,
            "consolidation_proposal",
            proposal_origin,
            0.7,
            0.52,
            tags,
            now,
            now,
            "working",
            "sandman",
            "candidate",
            "project",
            0.2,
            project_key,
            "dream_reviewer",
            "agent",
            workspace_id,
            title,
            f"Mara consolidation proposal for action={proposal['action']}",
            now,
            json.dumps(
                [
                    "operator przegląda wyniki Sandmana",
                    "trzeba zdecydować czy propozycja konsolidacji jest sensowna",
                ],
                ensure_ascii=False,
            ),
        ),
    )
    return int(cursor.lastrowid)


def _append_ids_from_value(ids: list[int], value: Any, *, key_hint: str | None = None) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if _is_memory_id_key(lowered):
                _append_ids_from_value(ids, item, key_hint=lowered)
            elif isinstance(item, str):
                for match in MEMORY_ID_REF_RE.findall(item):
                    _append_unique_int(ids, match)
            elif isinstance(item, (dict, list, tuple)):
                _append_ids_from_value(ids, item, key_hint=lowered)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _append_ids_from_value(ids, item, key_hint=key_hint)
    elif isinstance(value, int):
        if key_hint and _is_memory_id_key(key_hint):
            _append_unique_int(ids, value)
    elif isinstance(value, str):
        if key_hint and _is_memory_id_key(key_hint):
            _append_unique_int(ids, value)
        for match in MEMORY_ID_REF_RE.findall(value):
            _append_unique_int(ids, match)
        for match in MEMORY_ID_KEY_VALUE_RE.findall(value):
            _append_unique_int(ids, match)


def _is_memory_id_key(key: str) -> bool:
    return key in {
        "memory_id",
        "memory_ids",
        "source_memory_id",
        "source_memory_ids",
        "target_memory_id",
        "target_memory_ids",
        "from_memory_id",
        "to_memory_id",
        "memory_a_id",
        "memory_b_id",
        "left_memory_id",
        "right_memory_id",
    } or key.endswith("_memory_id") or key.endswith("_memory_ids")


def _extract_mara_association_pairs(mara_output: dict[str, Any]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for section_name in ("association_candidates", "metaphor_links"):
        section = mara_output.get(section_name)
        if not isinstance(section, list):
            continue
        for item in section:
            if not isinstance(item, dict):
                continue
            source_id = _first_int_from_keys(item, ("source_memory_id", "from_memory_id", "memory_a_id", "left_memory_id"))
            target_id = _first_int_from_keys(item, ("target_memory_id", "to_memory_id", "memory_b_id", "right_memory_id"))
            if source_id <= 0 or target_id <= 0:
                ids: list[int] = []
                _append_ids_from_value(ids, item)
                if len(ids) >= 2:
                    source_id, target_id = ids[0], ids[1]
            if source_id <= 0 or target_id <= 0 or source_id == target_id:
                continue
            key = (source_id, target_id)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(
                {
                    "source_memory_id": source_id,
                    "target_memory_id": target_id,
                    "weight": _to_float(item.get("confidence"), default=0.5),
                }
            )
    return pairs


def _first_int_from_keys(item: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        if key in item:
            parsed = _to_int(item.get(key))
            if parsed > 0:
                return parsed
    return 0


def run_nightly(
    *,
    root_path: str | os.PathLike[str] | Path | None = None,
    project_key: str | None = "demo-project",
    dry_run: bool = False,
    math_only: bool = False,
    mara_only: bool = False,
    no_mara: bool = False,
    limit: int = DEFAULT_MEMORY_LIMIT,
    model_checker: ModelChecker | None = None,
    model_lifecycle: ModelLifecycle | None = None,
    unload_model: bool = True,
    mara_client: Any | None = None,
) -> dict[str, Any]:
    paths = SandmanPaths.for_root(root_path)
    paths.ensure_dirs()
    run_type = RUN_TYPE_DRY_RUN if dry_run else RUN_TYPE_NIGHTLY
    if mara_only:
        run_type = RUN_TYPE_MANUAL
    run_id = make_run_id(run_type)
    log_path = paths.logs_dir / f"{run_id}.log"
    run_path = paths.runs_dir / f"{run_id}.json"
    report_path = paths.reports_dir / f"{run_id}.md"
    append_log(log_path, f"run_started run_id={run_id} run_type={run_type}")

    if math_only and mara_only:
        raise ValueError("--math-only and --mara-only cannot be used together")
    if no_mara and mara_only:
        raise ValueError("--no-mara and --mara-only cannot be used together")

    lock_state = inspect_lock(paths.lock_path)
    if lock_state["active"]:
        result = _skipped_existing_run(
            run_id=run_id,
            run_type=run_type,
            paths=paths,
            active_lock=lock_state["lock"],
            log_path=log_path,
            run_path=run_path,
        )
        return result

    lock_info: dict[str, Any] = {"acquired": False, "warnings": []}
    if not dry_run:
        lock_info = acquire_lock(paths, run_id=run_id, run_type=run_type)
        if not lock_info.get("acquired"):
            result = _skipped_existing_run(
                run_id=run_id,
                run_type=run_type,
                paths=paths,
                active_lock=lock_info.get("lock"),
                log_path=log_path,
                run_path=run_path,
            )
            return result

    started_at = utc_now_iso()
    warnings = list(lock_info.get("warnings") or [])
    errors: list[str] = []
    resolved_model_lifecycle = model_lifecycle or SandmanLmsModelLifecycle()
    model_lifecycle_log: dict[str, Any] = {
        "scope": "sandman_run",
        "load_requested": True,
        "unload_requested": bool(unload_model),
        "load": None,
        "unload": None,
    }
    math_output = sandman_profiles.empty_math_output(run_id=run_id, run_type=run_type, generated_at=started_at)
    mara_output = sandman_profiles.mara_skipped_output(run_id=run_id, run_type=run_type, reason="not_requested", generated_at=started_at)
    mara_persistence: dict[str, Any] = {"status": "not_requested", "warnings": [], "errors": []}
    snapshot = {"status": "skipped", "reason": "not_started"}
    math_path: Path | None = None
    mara_path: Path | None = None

    try:
        _load_model_for_run(resolved_model_lifecycle, model_lifecycle_log, warnings)
        if not mara_only:
            snapshot = create_db_snapshot(paths, run_id=run_id, dry_run=dry_run)
            if snapshot.get("status") == "failed":
                warnings.append("snapshot_failed")
            math_output = build_math_output(
                run_id=run_id,
                run_type=run_type,
                db_path=paths.db_path,
                project_key=project_key,
                limit=limit,
                generated_at=utc_now_iso(),
            )
            math_path = paths.candidates_dir / f"{run_id}-math-candidates.json"
            write_json(math_path, math_output)
            append_log(log_path, f"math_completed status={math_output.get('status')} candidates={math_output.get('summary', {}).get('candidate_count')}")
        else:
            snapshot = create_db_snapshot(paths, run_id=run_id, dry_run=dry_run)
            if snapshot.get("status") == "failed":
                warnings.append("snapshot_failed")
            latest_math = find_latest_artifact(paths.candidates_dir, "*-math-candidates.json")
            loaded_math = _read_json(latest_math) if latest_math else None
            if loaded_math:
                math_output = loaded_math
                warnings.append(f"mara_only_using_latest_math: {latest_math}")
            else:
                math_output["status"] = "skipped"
                warnings.append("mara_only_no_previous_math_artifact")

        if math_only:
            mara_output = sandman_profiles.mara_skipped_output(run_id=run_id, run_type=run_type, reason="math_only_requested")
        elif no_mara:
            mara_output = sandman_profiles.mara_skipped_output(run_id=run_id, run_type=run_type, reason="no_mara_requested")
        else:
            mara_output = run_mara_profile(
                run_id=run_id,
                run_type=run_type,
                paths=paths,
                project_key=project_key,
                math_output=math_output,
                model_checker=model_checker,
                mara_client=mara_client,
            )
            if mara_output.get("status") != "skipped":
                mara_path = paths.dreams_dir / f"{run_id}-mara.json"
                write_json(mara_path, mara_output)
                mara_persistence = persist_mara_dream_memory(
                    paths=paths,
                    run_id=run_id,
                    run_type=run_type,
                    project_key=project_key,
                    mara_output=mara_output,
                    artifact_path=mara_path,
                    snapshot=snapshot,
                    dry_run=dry_run,
                )
                mara_output["memory_persistence"] = mara_persistence
                write_json(mara_path, mara_output)
                warnings.extend(mara_persistence.get("warnings") or [])
                if mara_persistence.get("status") == "failed":
                    errors.extend(str(item) for item in (mara_persistence.get("errors") or ["mara_memory_persistence_failed"]))
            append_log(log_path, f"mara_completed status={mara_output.get('status')}")

        status = _overall_status(math_output, mara_output, errors)
        finished_at = utc_now_iso()
        result = {
            "run_id": run_id,
            "run_type": run_type,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": status,
            "project_key": project_key,
            "dry_run": dry_run,
            "profiles": _profile_names(math_output, mara_output),
            "snapshot": snapshot,
            "model_lifecycle": model_lifecycle_log,
            "lock": {
                "used": not dry_run,
                "warnings": lock_info.get("warnings") or [],
            },
            "math_status": math_output.get("status"),
            "mara_status": mara_output.get("status"),
            "mara_skip_reason": _mara_skip_reason(mara_output),
            "mara_persistence": mara_persistence,
            "math": math_output,
            "mara": mara_output,
            "artifact_paths": {
                "run": str(run_path),
                "log": str(log_path),
                "report": str(report_path),
                "math_candidates": str(math_path) if math_path else None,
                "mara_dream": str(mara_path) if mara_path else None,
            },
            "warnings": warnings + list(math_output.get("warnings") or []) + list(mara_output.get("warnings") or []),
            "errors": errors + list(math_output.get("errors") or []) + list(mara_output.get("errors") or []),
        }
        if result["mara_status"] == "skipped" and result["mara_skip_reason"] == "model_unavailable":
            result["warnings"].append("mara_skipped_model_unavailable")
        if unload_model:
            _unload_model_for_run(resolved_model_lifecycle, model_lifecycle_log, result["warnings"])
        write_text(report_path, build_run_report_markdown(result))
        write_json(run_path, result)
        append_log(log_path, f"run_finished status={status}")
        return result
    except Exception as exc:  # noqa: BLE001
        errors.append(_safe_error(exc))
        failed = {
            "run_id": run_id,
            "run_type": run_type,
            "started_at": started_at,
            "finished_at": utc_now_iso(),
            "status": "failed",
            "project_key": project_key,
            "profiles": _profile_names(math_output, mara_output),
            "snapshot": snapshot,
            "model_lifecycle": model_lifecycle_log,
            "math": math_output,
            "mara": mara_output,
            "mara_persistence": mara_persistence,
            "warnings": warnings,
            "errors": errors,
            "artifact_paths": {"run": str(run_path), "log": str(log_path), "report": str(report_path)},
        }
        if unload_model:
            _unload_model_for_run(resolved_model_lifecycle, model_lifecycle_log, failed["warnings"])
        write_text(report_path, build_run_report_markdown(failed))
        write_json(run_path, failed)
        append_log(log_path, f"run_failed error={errors[-1]}")
        return failed
    finally:
        if lock_info.get("acquired"):
            release_lock(paths, run_id=run_id)


def _skipped_existing_run(
    *,
    run_id: str,
    run_type: str,
    paths: SandmanPaths,
    active_lock: dict[str, Any] | None,
    log_path: Path,
    run_path: Path,
) -> dict[str, Any]:
    report_path = paths.reports_dir / f"{run_id}.md"
    result = {
        "run_id": run_id,
        "run_type": run_type,
        "started_at": utc_now_iso(),
        "finished_at": utc_now_iso(),
        "status": "skipped_existing_run",
        "profiles": [],
        "active_lock": active_lock,
        "warnings": ["active_sandman_lock"],
        "errors": [],
        "artifact_paths": {"run": str(run_path), "log": str(log_path), "report": str(report_path)},
    }
    write_text(report_path, build_run_report_markdown(result))
    write_json(run_path, result)
    append_log(log_path, "skipped_existing_run active_sandman_lock")
    return result


def _overall_status(math_output: dict[str, Any], mara_output: dict[str, Any], errors: list[str]) -> str:
    if errors:
        return "failed"
    statuses = {str(math_output.get("status")), str(mara_output.get("status"))}
    if "failed" in statuses:
        return "partial"
    if "partial" in statuses or ("skipped" in statuses and str(mara_output.get("status")) == "skipped"):
        return "partial"
    return "completed"


def _profile_names(math_output: dict[str, Any], mara_output: dict[str, Any]) -> list[str]:
    names: list[str] = []
    if math_output.get("status") != "skipped":
        names.append("sandman_math")
    if mara_output.get("status") != "skipped":
        names.append("sandman_mara")
    return names


def _mara_skip_reason(mara_output: dict[str, Any]) -> str | None:
    if mara_output.get("status") != "skipped":
        return None
    summary = mara_output.get("summary") or {}
    short = str(summary.get("short") or "")
    if "model_unavailable" in short:
        return "model_unavailable"
    warnings = [str(item) for item in mara_output.get("warnings") or []]
    if warnings:
        return warnings[0]
    return "skipped"


def find_latest_artifact(directory: Path, pattern: str) -> Path | None:
    items = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return items[0] if items else None


def find_latest_run(paths: SandmanPaths) -> dict[str, Any] | None:
    for path in sorted(paths.runs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        payload = _read_json(path)
        if not payload:
            continue
        if payload.get("run_type") == RUN_TYPE_MORNING:
            continue
        payload.setdefault("artifact_paths", {})
        payload["artifact_paths"]["run"] = str(path)
        return payload
    return None


def run_morning_report(*, root_path: str | os.PathLike[str] | Path | None = None) -> dict[str, Any]:
    paths = SandmanPaths.for_root(root_path)
    paths.ensure_dirs()
    run_id = make_run_id(RUN_TYPE_MORNING)
    report_path = paths.reports_dir / f"{local_now().strftime('%Y-%m-%d')}-morning.md"
    log_path = paths.logs_dir / f"{run_id}.log"
    lock_state = inspect_lock(paths.lock_path)
    if lock_state["active"]:
        markdown = build_nightly_still_running_report(lock_state["lock"])
        write_text(report_path, markdown)
        result = {
            "run_id": run_id,
            "run_type": RUN_TYPE_MORNING,
            "status": "nightly_still_running",
            "report_path": str(report_path),
            "active_lock": lock_state["lock"],
            "warnings": ["nightly still running"],
            "errors": [],
        }
        append_log(log_path, "morning_report nightly_still_running")
        return result

    latest = find_latest_run(paths)
    if not latest:
        markdown = build_no_nightly_report()
        write_text(report_path, markdown)
        result = {
            "run_id": run_id,
            "run_type": RUN_TYPE_MORNING,
            "status": "no_nightly_report",
            "report_path": str(report_path),
            "warnings": ["no nightly run artifacts found"],
            "errors": [],
        }
        append_log(log_path, "morning_report no_nightly_report")
        return result

    markdown = build_morning_report_markdown(latest)
    write_text(report_path, markdown)
    result = {
        "run_id": run_id,
        "run_type": RUN_TYPE_MORNING,
        "status": "completed",
        "source_run_id": latest.get("run_id"),
        "report_path": str(report_path),
        "warnings": [],
        "errors": [],
    }
    append_log(log_path, f"morning_report completed source_run_id={latest.get('run_id')}")
    return result


def build_nightly_still_running_report(lock: dict[str, Any] | None) -> str:
    lock = lock or {}
    return (
        "# Sandman Morning Report\n\n"
        "## Status\n"
        "- Status: nightly still running\n"
        f"- Active run id: {lock.get('run_id', '<unknown>')}\n"
        f"- Started: {lock.get('started_at', '<unknown>')}\n"
        f"- PID: {lock.get('pid', '<unknown>')}\n\n"
        "## Suggested next action\n"
        "Wait for the nightly run to finish, then run the morning report again.\n"
    )


def build_no_nightly_report() -> str:
    return (
        "# Sandman Morning Report\n\n"
        "## Status\n"
        "- Status: no nightly report exists\n\n"
        "## Suggested next action\n"
        "Run the nightly Sandman pass once, then generate the morning report again.\n"
    )


def build_morning_report_markdown(run_record: dict[str, Any]) -> str:
    math = run_record.get("math") or {}
    mara = run_record.get("mara") or {}
    math_summary = math.get("summary") or {}
    mara_summary = mara.get("summary") or {}
    mara_report = mara.get("dream_report") or {}
    warnings = run_record.get("warnings") or []
    errors = run_record.get("errors") or []
    candidates = math.get("candidates") or {}
    candidate_count = sum(len(items) for items in candidates.values() if isinstance(items, list))
    association_count = len(mara.get("association_candidates") or [])
    question_count = len(mara.get("revalidation_questions") or [])
    next_action = _suggest_next_action(run_record, candidate_count, association_count, question_count)
    return (
        "# Sandman Morning Report\n\n"
        "## Status\n"
        f"- Run id: {run_record.get('run_id', '<unknown>')}\n"
        f"- Started: {run_record.get('started_at', '<unknown>')}\n"
        f"- Finished: {run_record.get('finished_at', '<unknown>')}\n"
        f"- Profiles used: {', '.join(run_record.get('profiles') or []) or '<none>'}\n"
        f"- Status: {run_record.get('status', '<unknown>')}\n\n"
        "## Matematyk\n"
        f"- What was checked: {math_summary.get('memory_count_seen', 0)} active memories.\n"
        f"- Most important structural findings: {len(math.get('findings') or [])} findings.\n"
        f"- Candidates needing review: {candidate_count} candidates.\n\n"
        "## Mara\n"
        f"- Dream status: {mara.get('status', 'skipped')}\n"
        f"- Dream summary: {mara_report.get('morning_note') or mara_summary.get('short') or 'No dream artifact.'}\n"
        f"- Strange but potentially useful associations: {association_count}\n"
        f"- Questions worth asking: {question_count}\n\n"
        "## Risks and errors\n"
        f"- Errors: {_join_list(errors)}\n"
        f"- Warnings: {_join_list(warnings)}\n"
        f"- Skipped steps: {_skipped_steps(run_record)}\n\n"
        "## Suggested next action\n"
        f"{next_action}\n"
    )


def build_run_report_markdown(run_record: dict[str, Any]) -> str:
    math = run_record.get("math") or {}
    mara = run_record.get("mara") or {}
    math_summary = math.get("summary") or {}
    mara_summary = mara.get("summary") or {}
    lifecycle = run_record.get("model_lifecycle") or {}
    load_status = (lifecycle.get("load") or {}).get("status") or "not_recorded"
    unload_status = (lifecycle.get("unload") or {}).get("status") or "not_recorded"
    persistence = run_record.get("mara_persistence") or {}
    warnings = run_record.get("warnings") or []
    errors = run_record.get("errors") or []
    return (
        "# Sandman Run Report\n\n"
        "## Status\n"
        f"- Run id: {run_record.get('run_id', '<unknown>')}\n"
        f"- Run type: {run_record.get('run_type', '<unknown>')}\n"
        f"- Started: {run_record.get('started_at', '<unknown>')}\n"
        f"- Finished: {run_record.get('finished_at', '<unknown>')}\n"
        f"- Status: {run_record.get('status', '<unknown>')}\n"
        f"- Profiles: {', '.join(run_record.get('profiles') or []) or '<none>'}\n\n"
        "## Matematyk\n"
        f"- Status: {math.get('status', 'skipped')}\n"
        f"- Memories seen: {math_summary.get('memory_count_seen', 0)}\n"
        f"- Candidates: {math_summary.get('candidate_count', 0)}\n"
        f"- Risk: {math_summary.get('risk_level', 'unknown')}\n\n"
        "## Mara\n"
        f"- Status: {mara.get('status', 'skipped')}\n"
        f"- Summary: {mara_summary.get('short', 'No dream artifact.')}\n"
        f"- Artifact note: Mara output is a dream artifact, not a fact.\n\n"
        "## Mara Memory Writes\n"
        f"- Persistence status: {persistence.get('status', 'not_recorded')}\n"
        f"- Dream memory id: {persistence.get('dream_memory_id', 'none')}\n"
        f"- Touched memories: {len(persistence.get('touched_memory_ids') or [])}\n"
        f"- Source dream links created: {persistence.get('source_links_created', 0)}\n"
        f"- Association dream links created: {persistence.get('association_links_created', 0)}\n\n"
        "## Snapshot\n"
        f"- {json.dumps(run_record.get('snapshot') or {}, ensure_ascii=False)}\n\n"
        "## Gemma / LMS\n"
        f"- Load requested: {lifecycle.get('load_requested', False)}\n"
        f"- Load status: {load_status}\n"
        f"- Unload requested: {lifecycle.get('unload_requested', False)}\n"
        f"- Unload status: {unload_status}\n\n"
        "## Risks and errors\n"
        f"- Warnings: {_join_list(warnings)}\n"
        f"- Errors: {_join_list(errors)}\n"
    )


def _join_list(items: list[Any]) -> str:
    if not items:
        return "none"
    return "; ".join(str(item) for item in items[:8])


def _skipped_steps(run_record: dict[str, Any]) -> str:
    skipped: list[str] = []
    if run_record.get("mara_status") == "skipped":
        skipped.append(f"Mara ({run_record.get('mara_skip_reason') or 'skipped'})")
    snapshot = run_record.get("snapshot") or {}
    if snapshot.get("status") == "skipped":
        skipped.append(f"snapshot ({snapshot.get('reason')})")
    return ", ".join(skipped) if skipped else "none"


def _suggest_next_action(run_record: dict[str, Any], candidate_count: int, association_count: int, question_count: int) -> str:
    if run_record.get("mara_status") == "skipped" and run_record.get("mara_skip_reason") == "model_unavailable":
        return "Review Matematyk candidates first; Mara was skipped because the local model was unavailable."
    if question_count:
        return "Start with Mara's revalidation questions, then approve or reject Matematyk candidates."
    if candidate_count:
        return "Review Matematyk candidates and approve only low-risk, well-grounded changes."
    if association_count:
        return "Read Mara associations as hypotheses only and mark useful ones for later review."
    return "No action required beyond checking warnings."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sandman Matematyk/Mara MVP runner.")
    parser.add_argument("--project-key", default="demo-project")
    parser.add_argument("--root", default=None, help="Repository root. Defaults to app memory_config.ROOT.")
    parser.add_argument("--limit", type=int, default=DEFAULT_MEMORY_LIMIT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--math-only", action="store_true")
    parser.add_argument("--mara-only", action="store_true")
    parser.add_argument("--no-mara", action="store_true")
    parser.add_argument("--morning-only", action="store_true")
    parser.add_argument("--keep-model-loaded", action="store_true", help="Do not run lms unload after the Sandman analysis run.")
    args = parser.parse_args(argv)

    if args.morning_only:
        result = run_morning_report(root_path=args.root)
    else:
        result = run_nightly(
            root_path=args.root,
            project_key=args.project_key,
            dry_run=args.dry_run,
            math_only=args.math_only,
            mara_only=args.mara_only,
            no_mara=args.no_mara,
            limit=args.limit,
            unload_model=not args.keep_model_loaded,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"failed"} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
