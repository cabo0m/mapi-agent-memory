from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.runtime.context import runtime_data_dir, runtime_root
from app.runtime.doctor import collect_doctor_report

RECOVERY_SCHEMA = "mapi_recovery.v1"


def _utc_now() -> str: return datetime.now(UTC).isoformat().replace("+00:00", "Z")

def _read_json(path: Path) -> dict[str, Any] | None:
    try: value=json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError,json.JSONDecodeError): return None
    return value if isinstance(value,dict) else None

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["tasklist.exe", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=5, check=False, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        output = completed.stdout.strip().casefold()
        return completed.returncode == 0 and str(pid) in output and "no tasks are running" not in output
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True

def writer_lock_path() -> Path:
    configured=str(os.environ.get("MAPI_WRITER_LOCK_PATH") or "").strip()
    return Path(configured).expanduser().resolve() if configured else (runtime_data_dir()/"runtime"/"mapi-writer.lock").resolve()

def build_recovery_plan(*, root: Path | None = None, doctor_report: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved_root=Path(root or runtime_root()).resolve()
    report=doctor_report or collect_doctor_report(root=resolved_root,deep=False)
    lock_path=writer_lock_path(); writer=_read_json(lock_path) or {}; pid=int(writer.get("pid") or 0); alive=_pid_alive(pid)
    steps=[]
    if writer and not alive: steps.append({"action":"remove_dead_writer_lease","path":str(lock_path),"safe":True})
    elif writer and alive: steps.append({"action":"preserve_live_writer_lease","pid":pid,"safe":True})
    if any(f.get("reason_code") in {"database_missing","database_quick_check_failed","database_foreign_key_findings"} for f in report.get("findings") or []):
        steps.append({"action":"restore_or_repair_database_before_restart","safe":False,"automatic":False})
    steps.extend([{"action":"run_mapi_migrate","safe":True},{"action":"restart_runtime_with_operator_command","safe":True,"requires":"MAPI_RECOVERY_COMMAND_JSON"},{"action":"run_mapi_doctor_and_smoke","safe":True}])
    return {"schema":RECOVERY_SCHEMA,"status":"planned","generated_at":_utc_now(),"root":str(resolved_root),"database_mutations":False,"initial_doctor_status":report.get("status"),"initial_reason_codes":[f.get("reason_code") for f in report.get("findings") or []],"writer_pid":pid or None,"writer_pid_alive":alive,"steps":steps}

def recover_runtime(*, execute: bool=False, root: Path | None=None, restart_command_json: str | None=None, timeout_seconds: int=120) -> dict[str, Any]:
    plan=build_recovery_plan(root=root)
    if not execute: return {**plan,"status":"dry_run"}
    lock_path=writer_lock_path(); writer=_read_json(lock_path) or {}; pid=int(writer.get("pid") or 0)
    if writer and _pid_alive(pid): return {**plan,"status":"error","error":"live_writer_lease_present","writer_pid":pid}
    if writer and lock_path.exists(): lock_path.unlink()
    raw=restart_command_json or str(os.environ.get("MAPI_RECOVERY_COMMAND_JSON") or "").strip()
    if not raw: return {**plan,"status":"manual_restart_required","error":"recovery_command_not_configured"}
    try: argv=json.loads(raw)
    except json.JSONDecodeError as exc: return {**plan,"status":"error","error":"recovery_command_json_invalid","detail":str(exc)}
    if not isinstance(argv,list) or not argv or not all(isinstance(x,str) and x for x in argv): return {**plan,"status":"error","error":"recovery_command_must_be_nonempty_json_argv"}
    try: completed=subprocess.run(argv,cwd=str(Path(root or runtime_root()).resolve()),capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=max(5,min(int(timeout_seconds),300)),check=False,shell=False)
    except (OSError,subprocess.TimeoutExpired) as exc: return {**plan,"status":"error","error":"recovery_command_failed_to_run","detail":exc.__class__.__name__}
    doctor=collect_doctor_report(root=Path(root or runtime_root()).resolve(),deep=False)
    return {**plan,"status":"recovered" if completed.returncode==0 and doctor.get("status")!="BLOCKED" else "attention","command_returncode":completed.returncode,"stdout_tail":completed.stdout[-2000:],"stderr_tail":completed.stderr[-2000:],"doctor":doctor}
