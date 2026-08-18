from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


BOUNDED_REPORT_SCHEMA = "mapi_bounded_report.v1"
DEFAULT_REPORT_TIMEOUT_MS = 1500
MIN_REPORT_TIMEOUT_MS = 50
MAX_REPORT_TIMEOUT_MS = 60_000


def normalize_timeout_budget_ms(value: int | None) -> int:
    if value is None:
        return DEFAULT_REPORT_TIMEOUT_MS
    normalized = int(value)
    if normalized < MIN_REPORT_TIMEOUT_MS or normalized > MAX_REPORT_TIMEOUT_MS:
        raise ValueError("timeout_budget_ms_out_of_range")
    return normalized


@dataclass
class ReportBudget:
    timeout_budget_ms: int
    monotonic: Callable[[], float] = time.monotonic
    started_at: float = field(init=False)
    sections: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.timeout_budget_ms = normalize_timeout_budget_ms(self.timeout_budget_ms)
        self.started_at = self.monotonic()

    def elapsed_ms(self) -> float:
        return max(0.0, (self.monotonic() - self.started_at) * 1000.0)

    def remaining_ms(self, *, reserve_ms: int = 0) -> int:
        return max(0, int(self.timeout_budget_ms - self.elapsed_ms() - max(0, int(reserve_ms))))

    def can_start(self, *, minimum_ms: int = 1, reserve_ms: int = 0) -> bool:
        return self.remaining_ms(reserve_ms=reserve_ms) >= max(1, int(minimum_ms))

    def skipped(self, name: str, *, reason: str = "budget_exhausted") -> dict[str, Any]:
        section = {
            "status": "skipped_budget",
            "elapsed_ms": 0.0,
            "reason": reason,
        }
        self.sections[name] = section
        return section

    def run(
        self,
        name: str,
        callback: Callable[[], Any],
        *,
        minimum_ms: int = 1,
        reserve_ms: int = 0,
    ) -> tuple[dict[str, Any], Any | None]:
        if not self.can_start(minimum_ms=minimum_ms, reserve_ms=reserve_ms):
            return self.skipped(name), None
        section_started = self.monotonic()
        try:
            value = callback()
        except TimeoutError as exc:
            section = {
                "status": "timed_out",
                "elapsed_ms": round((self.monotonic() - section_started) * 1000.0, 3),
                "error_type": exc.__class__.__name__,
            }
            self.sections[name] = section
            return section, None
        except Exception as exc:
            section = {
                "status": "error",
                "elapsed_ms": round((self.monotonic() - section_started) * 1000.0, 3),
                "error_type": exc.__class__.__name__,
            }
            self.sections[name] = section
            return section, None
        elapsed = round((self.monotonic() - section_started) * 1000.0, 3)
        value_status = str(value.get("status") or "") if isinstance(value, dict) else ""
        if value_status == "timed_out":
            status = "timed_out"
        elif value_status in {"error", "failed"}:
            status = "error"
        else:
            status = "ok"
        section = {
            "status": status,
            "elapsed_ms": elapsed,
        }
        if isinstance(value, dict) and value.get("count") is not None:
            section["count"] = int(value.get("count") or 0)
        self.sections[name] = section
        return section, value

    def summary(self) -> dict[str, Any]:
        elapsed = round(self.elapsed_ms(), 3)
        partial_statuses = {"timed_out", "error", "skipped_budget", "skipped_dependency"}
        partial = any(str(item.get("status")) in partial_statuses for item in self.sections.values())
        return {
            "schema": BOUNDED_REPORT_SCHEMA,
            "timeout_budget_ms": int(self.timeout_budget_ms),
            "elapsed_ms": elapsed,
            "remaining_ms": max(0, int(self.timeout_budget_ms - elapsed)),
            "partial": partial,
            "status": "partial" if partial else "ok",
            "sections": dict(self.sections),
        }
