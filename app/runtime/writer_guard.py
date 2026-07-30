from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

WRITER_GUARD_SCHEMA = "mapi_single_writer_guard.v1"
WRITER_MODE_ACTIVE = "active"
WRITER_MODE_READ_ONLY = "read_only"
WRITER_MODES = frozenset({WRITER_MODE_ACTIVE, WRITER_MODE_READ_ONLY})

_STATE_LOCK = threading.RLock()
_STOP_EVENT = threading.Event()
_HEARTBEAT_THREAD: threading.Thread | None = None
_STATE: dict[str, Any] = {
    "configured": False,
    "enabled": False,
    "mode": WRITER_MODE_ACTIVE,
    "lease_held": False,
    "instance_key": None,
    "lock_path": None,
    "db_path": None,
    "reason_codes": [],
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _epoch() -> float:
    return time.time()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def writer_guard_enabled() -> bool:
    return _env_bool("MAPI_WRITER_GUARD_ENABLED", False)


def writer_mode() -> str:
    value = str(os.environ.get("MAPI_WRITER_MODE", WRITER_MODE_ACTIVE)).strip().lower()
    return value if value in WRITER_MODES else WRITER_MODE_READ_ONLY


def _heartbeat_seconds() -> float:
    return max(1.0, float(os.environ.get("MAPI_WRITER_HEARTBEAT_SECONDS", "5")))


def _stale_seconds() -> float:
    return max(_heartbeat_seconds() * 3, float(os.environ.get("MAPI_WRITER_STALE_SECONDS", "30")))


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist.exe", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        text = str(result.stdout or "").strip().lower()
        return result.returncode == 0 and str(pid) in text and "no tasks" not in text
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_record(path: Path, record: dict[str, Any], *, exclusive: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if exclusive:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            os.write(descriptor, payload.encode("utf-8"))
        finally:
            os.close(descriptor)
        return
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def _record_is_active(record: dict[str, Any] | None, *, stale_seconds: float) -> bool:
    if not record:
        return False
    host = str(record.get("host") or "").strip().lower()
    local_host = socket.gethostname().strip().lower()
    try:
        pid = int(record.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if host and host == local_host:
        return _pid_alive(pid)
    try:
        heartbeat_epoch = float(record.get("heartbeat_epoch") or 0.0)
    except (TypeError, ValueError):
        heartbeat_epoch = 0.0
    return heartbeat_epoch > 0 and (_epoch() - heartbeat_epoch) < stale_seconds


def _lease_record(*, instance_key: str, db_path: Path) -> dict[str, Any]:
    now = _utc_now()
    return {
        "schema": WRITER_GUARD_SCHEMA,
        "instance_key": instance_key,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "db_path": str(db_path),
        "mode": WRITER_MODE_ACTIVE,
        "started_at": now,
        "heartbeat_at": now,
        "heartbeat_epoch": _epoch(),
        "commit_sha": str(os.environ.get("MAPI_EXPECTED_COMMIT") or "").strip() or None,
    }


def _lock_path(db_path: Path) -> Path:
    configured = str(os.environ.get("MAPI_WRITER_LOCK_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (db_path.parent / "runtime" / "mapi-writer.lock").resolve()


def _heartbeat_loop() -> None:
    while not _STOP_EVENT.wait(_heartbeat_seconds()):
        with _STATE_LOCK:
            if not _STATE.get("lease_held"):
                return
            path = Path(str(_STATE["lock_path"]))
            instance_key = str(_STATE["instance_key"])
            db_path = Path(str(_STATE["db_path"]))
        current = _read_record(path)
        if not current or str(current.get("instance_key")) != instance_key:
            with _STATE_LOCK:
                _STATE["lease_held"] = False
                _STATE["reason_codes"] = ["writer_lease_lost"]
            return
        record = _lease_record(instance_key=instance_key, db_path=db_path)
        record["started_at"] = current.get("started_at") or record["started_at"]
        try:
            _write_record(path, record, exclusive=False)
        except OSError:
            with _STATE_LOCK:
                _STATE["lease_held"] = False
                _STATE["reason_codes"] = ["writer_heartbeat_failed"]
            return


def release_writer_guard() -> bool:
    global _HEARTBEAT_THREAD
    _STOP_EVENT.set()
    thread = _HEARTBEAT_THREAD
    if thread and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=2)
    removed = False
    with _STATE_LOCK:
        path_value = _STATE.get("lock_path")
        instance_key = _STATE.get("instance_key")
        if path_value and instance_key:
            path = Path(str(path_value))
            current = _read_record(path)
            if current and str(current.get("instance_key")) == str(instance_key):
                try:
                    path.unlink()
                    removed = True
                except FileNotFoundError:
                    removed = True
                except OSError:
                    removed = False
        _STATE.update(
            {
                "configured": False,
                "enabled": False,
                "mode": WRITER_MODE_ACTIVE,
                "lease_held": False,
                "instance_key": None,
                "lock_path": None,
                "db_path": None,
                "reason_codes": [],
            }
        )
        _HEARTBEAT_THREAD = None
    return removed


def configure_writer_guard(*, db_path: str | Path) -> dict[str, Any]:
    global _HEARTBEAT_THREAD
    release_writer_guard()
    resolved_db = Path(db_path).expanduser().resolve()
    enabled = writer_guard_enabled()
    mode = writer_mode()
    with _STATE_LOCK:
        _STATE.update(
            {
                "configured": True,
                "enabled": enabled,
                "mode": mode,
                "lease_held": False,
                "instance_key": None,
                "lock_path": None,
                "db_path": str(resolved_db),
                "reason_codes": [],
            }
        )
    if not enabled:
        return writer_guard_status()
    if mode != WRITER_MODE_ACTIVE:
        with _STATE_LOCK:
            _STATE["reason_codes"] = ["writer_read_only_mode"]
        return writer_guard_status()

    path = _lock_path(resolved_db)
    instance_key = str(os.environ.get("MAPI_WRITER_INSTANCE_KEY") or "").strip()
    if not instance_key:
        instance_key = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
    stale_seconds = _stale_seconds()
    record = _lease_record(instance_key=instance_key, db_path=resolved_db)

    for _attempt in range(2):
        try:
            _write_record(path, record, exclusive=True)
            break
        except FileExistsError:
            current = _read_record(path)
            if _record_is_active(current, stale_seconds=stale_seconds):
                owner = None if current is None else current.get("instance_key")
                raise RuntimeError(f"single_writer_lease_held:{owner}")
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    else:
        raise RuntimeError("single_writer_lease_acquire_failed")

    _STOP_EVENT.clear()
    with _STATE_LOCK:
        _STATE.update(
            {
                "lease_held": True,
                "instance_key": instance_key,
                "lock_path": str(path),
                "reason_codes": [],
            }
        )
        _HEARTBEAT_THREAD = threading.Thread(
            target=_heartbeat_loop,
            name="mapi-writer-heartbeat",
            daemon=True,
        )
        _HEARTBEAT_THREAD.start()
    return writer_guard_status()


def writer_guard_status() -> dict[str, Any]:
    with _STATE_LOCK:
        payload = dict(_STATE)
    payload.update(
        {
            "schema": WRITER_GUARD_SCHEMA,
            "heartbeat_seconds": _heartbeat_seconds(),
            "stale_seconds": _stale_seconds(),
            "mutations_allowed": bool(
                not payload.get("enabled")
                or (
                    payload.get("mode") == WRITER_MODE_ACTIVE
                    and payload.get("lease_held") is True
                )
            ),
        }
    )
    return payload


def mutation_writer_guard(*, required: bool) -> dict[str, Any]:
    if not required:
        return {"allowed": True, "required": False, "reason_codes": []}
    status = writer_guard_status()
    if not status.get("enabled"):
        return {
            "allowed": True,
            "required": True,
            "enforcement_enabled": False,
            "reason_codes": [],
        }
    reasons: list[str] = []
    if status.get("mode") != WRITER_MODE_ACTIVE:
        reasons.append("writer_read_only_mode")
    if status.get("mode") == WRITER_MODE_ACTIVE and status.get("lease_held") is not True:
        reasons.extend(status.get("reason_codes") or ["writer_lease_not_held"])
    return {
        "allowed": not reasons,
        "required": True,
        "enforcement_enabled": True,
        "reason_codes": sorted(set(str(item) for item in reasons)),
        "writer_mode": status.get("mode"),
        "writer_instance_key": status.get("instance_key"),
    }


atexit.register(release_writer_guard)
