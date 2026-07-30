from __future__ import annotations

"""Content-free security audit for workshop authorization decisions."""

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from app.runtime.context import runtime_root

_LOCK = RLock()


def security_audit_path() -> Path:
    return runtime_root() / "logs" / "mapi-security-audit.jsonl"


def record_security_audit(
    *,
    decision: str,
    profile: str,
    area: str | None,
    action: str | None,
    tool_name: str | None,
    requirement: str | None,
    risk_class: str | None,
    outcome: str | None = None,
    audit_path: Path | None = None,
) -> None:
    """Append metadata only. Never persist payloads, arguments or result content."""
    event: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "decision": str(decision),
        "profile": str(profile),
        "area": area,
        "action": action,
        "tool_name": tool_name,
        "requirement": requirement,
        "risk_class": risk_class,
        "outcome": outcome,
    }
    try:
        path = audit_path or security_audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        with _LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError:
        # Authorization must fail closed independently from audit storage health.
        return
