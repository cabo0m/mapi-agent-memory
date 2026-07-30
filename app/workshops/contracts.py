from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkshopAction:
    action: str
    tool_name: str
    purpose: str
    min_profile: str = "reader"
    risk: str = "low"
    risk_class: str = "R0"
    backup_required: bool = False
    payload_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class Workshop:
    area: str
    purpose: str
    min_profile: str
    risk: str
    recommended_first_action: str
    actions: tuple[WorkshopAction, ...]
    guardrails: tuple[str, ...] = ()
