from __future__ import annotations

import os
from types import MethodType
from typing import Any

try:
    from fastmcp.exceptions import NotFoundError
except Exception:  # pragma: no cover - keeps the repository test stub lightweight
    class NotFoundError(Exception):
        pass


from app.runtime.remote_actor import remote_surface_profile

from app.workshops.access_policy import (
    ACCESS_REQUIREMENTS,
    PROFILE_ALLOWED_REQUIREMENTS as PROFILE_ALLOWED_REQUIREMENTS,
    SURFACE_PROFILE_ALIASES as SURFACE_PROFILE_ALIASES,
    SURFACE_PROFILES,
    canonical_profile_token,
    canonical_requirement_token,
    profile_allows_requirement,
)
from app.workshops.catalog import WORKSHOPS
from app.workshops.contracts import Workshop, WorkshopAction
from app.workshops.security_audit import record_security_audit


def normalize_surface_profile(value: str | None) -> str:
    """Return a canonical role, failing closed to reader."""
    normalized = canonical_profile_token(value)
    if normalized == "admin":
        admin_enabled = os.environ.get("MAPI_ADMIN_TOOLS_ENABLED", "false").strip().lower()
        if admin_enabled not in {"1", "true", "yes", "on"}:
            return "maintainer"
    return normalized if normalized in SURFACE_PROFILES else "reader"


READER_TOOLS = (
    "bootstrap_agent_context",
    "open_workshop",
    "run_workshop_action",
    "find_memories",
    "get_memory",
    "get_memory_links",
)

AGENT_TOOLS = READER_TOOLS + (
    "save_memory",
    "propose_memory",
    "recall_memory",
)

MAINTAINER_TOOLS = AGENT_TOOLS
ADMIN_TOOLS = MAINTAINER_TOOLS

# Compatibility constants for older callers and checks.
PUBLIC_TOOLS = READER_TOOLS
CLEAN_OPERATOR_TOOLS = AGENT_TOOLS

def current_surface_profile() -> str:
    remote_profile = remote_surface_profile()
    if remote_profile is not None:
        return normalize_surface_profile(remote_profile)
    return normalize_surface_profile(os.environ.get("MCP_SURFACE_PROFILE"))


def _effective_surface_profile(profile: str | None) -> str:
    return current_surface_profile() if profile is None else normalize_surface_profile(profile)


def profile_allows(profile: str | None, required: str | None) -> bool:
    resolved = normalize_surface_profile(profile)
    required_token = canonical_requirement_token(required)
    if required_token not in ACCESS_REQUIREMENTS:
        return False
    return profile_allows_requirement(resolved, required_token)


def visible_tool_order(profile: str | None = None) -> list[str]:
    resolved = _effective_surface_profile(profile)
    by_profile = {
        "reader": READER_TOOLS,
        "agent": AGENT_TOOLS,
        "maintainer": MAINTAINER_TOOLS,
        "admin": ADMIN_TOOLS,
    }
    return list(by_profile[resolved])


def visible_tool_names(profile: str | None = None) -> set[str]:
    return set(visible_tool_order(profile))


def is_tool_visible(tool_name: str, profile: str | None = None) -> bool:
    visible = visible_tool_names(profile)
    return tool_name in visible


def workshop_index(profile: str | None = None) -> list[dict[str, Any]]:
    resolved = _effective_surface_profile(profile)
    index: list[dict[str, Any]] = []
    for workshop in WORKSHOPS.values():
        if not profile_allows(resolved, workshop.min_profile):
            continue
        visible_actions = [action for action in workshop.actions if profile_allows(resolved, action.min_profile)]
        if not visible_actions:
            continue
        index.append(
            {
                "area": workshop.area,
                "purpose": workshop.purpose,
                "risk": workshop.risk,
                "recommended_first_action": workshop.recommended_first_action,
                "action_count": len(visible_actions),
                "total_action_count": len(workshop.actions),
            }
        )
    return index


def surface_manifest(profile: str | None = None) -> dict[str, Any]:
    resolved = _effective_surface_profile(profile)
    visible = visible_tool_order(resolved)
    return {
        "profile": resolved,
        "visible_tool_count": len(visible),
        "visible_tools": visible,
        "workshops": workshop_index(resolved),
    }


def open_workshop_payload(area: str, profile: str | None = None) -> dict[str, Any]:
    resolved = _effective_surface_profile(profile)
    key = str(area or "").strip().lower()
    workshop = WORKSHOPS.get(key)
    if workshop is None:
        return {
            "status": "error",
            "error": "unknown_workshop",
            "available_workshops": [item["area"] for item in workshop_index(resolved)],
        }
    if not profile_allows(resolved, workshop.min_profile):
        return {"status": "denied", "error": "workshop_not_available_for_profile", "area": key, "profile": resolved}
    return {
        "status": "ok",
        "profile": resolved,
        "area": workshop.area,
        "purpose": workshop.purpose,
        "risk": workshop.risk,
        "recommended_first_action": workshop.recommended_first_action,
        "guardrails": list(workshop.guardrails),
        "actions": [
            {
                "action": action.action,
                "tool_name": action.tool_name,
                "purpose": action.purpose,
                "risk": action.risk,
                "risk_class": action.risk_class,
                "access_requirement": action.min_profile,
                "min_profile": action.min_profile,
                "backup_required": action.backup_required,
                "payload_schema": action.payload_schema or {},
            }
            for action in workshop.actions
            if profile_allows(resolved, action.min_profile)
        ],
    }


def lookup_workshop_action(area: str, action: str) -> tuple[Workshop | None, WorkshopAction | None]:
    workshop = WORKSHOPS.get(str(area or "").strip().lower())
    if workshop is None:
        return None, None
    action_key = str(action or "").strip().lower()
    for candidate in workshop.actions:
        if candidate.action == action_key:
            return workshop, candidate
    return workshop, None


def resolve_workshop_action(area: str, action: str, profile: str | None = None) -> WorkshopAction | None:
    resolved = _effective_surface_profile(profile)
    workshop, candidate = lookup_workshop_action(area, action)
    if workshop is None or candidate is None:
        return None
    if not profile_allows(resolved, workshop.min_profile):
        return None
    if not profile_allows(resolved, candidate.min_profile):
        return None
    return candidate


def install_mcp_surface_filter(mcp: Any) -> None:
    if getattr(mcp, "_agent_surface_filter_installed", False):
        return
    if not hasattr(mcp, "list_tools") or not hasattr(mcp, "call_tool"):
        return

    original_list_tools = mcp.list_tools
    original_call_tool = mcp.call_tool

    async def list_tools_with_surface(self: Any, *args: Any, **kwargs: Any) -> Any:
        tools = list(await original_list_tools(*args, **kwargs))
        profile = current_surface_profile()
        visible = visible_tool_names(profile)
        order = {name: index for index, name in enumerate(visible_tool_order(profile))}
        return sorted(
            [tool for tool in tools if getattr(tool, "name", "") in visible],
            key=lambda tool: order.get(getattr(tool, "name", ""), 9999),
        )

    async def call_tool_with_surface(self: Any, name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        profile = current_surface_profile()
        if not is_tool_visible(name, profile):
            record_security_audit(
                decision="denied",
                profile=profile,
                area=None,
                action=None,
                tool_name=name,
                requirement=None,
                risk_class=None,
                outcome="hidden_top_level_tool",
            )
            raise NotFoundError(f"Unknown tool: {name!r}")
        if name in {"create_memory", "save_memory", "propose_memory", "recall_memory"}:
            from app.runtime.freshness import mutation_freshness_guard

            action_by_tool = {
                "create_memory": "create",
                "save_memory": "save",
                "propose_memory": "propose",
                "recall_memory": "recall",
            }
            freshness = mutation_freshness_guard(
                area="memory",
                action=action_by_tool[name],
                risk_class="R1",
                payload=arguments or {},
            )
            if not freshness.get("allowed"):
                record_security_audit(
                    decision="denied",
                    profile=profile,
                    area="memory",
                    action=action_by_tool[name],
                    tool_name=name,
                    requirement="agent" if name in {"create_memory", "save_memory"} else "operator",
                    risk_class="R1",
                    outcome="runtime_not_ready",
                )
                reason_codes = list(freshness.get("reason_codes") or [])
                message = "runtime_not_ready: " + ",".join(reason_codes)
                if "repository_dirty" in reason_codes:
                    details = freshness.get("repository_details") or {}
                    tracked_paths = list(details.get("tracked_paths") or [])[:20]
                    path_text = ",".join(tracked_paths) if tracked_paths else "unknown"
                    message += (
                        f"; tracked_paths={path_text}; "
                        "guidance=move_wip_to_dedicated_worktree_do_not_disable_freshness"
                    )
                raise RuntimeError(message)
        return await original_call_tool(name, arguments, **kwargs)

    mcp.list_tools = MethodType(list_tools_with_surface, mcp)
    mcp.call_tool = MethodType(call_tool_with_surface, mcp)
    mcp._agent_surface_filter_installed = True
