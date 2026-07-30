from __future__ import annotations

"""Explicit disposition of mutating MCP tools outside the compact workshops."""

from dataclasses import asdict, dataclass
from typing import Final


@dataclass(frozen=True)
class ToolDisposition:
    status: str
    allowed_path: str
    rationale: str


ACTIVE_LOCAL_ADMIN: Final[frozenset[str]] = frozenset({
    "apply_schema_migrations",
    "backfill_semantic_embeddings",
    "make_dir",
    "move_path",
    "run_python_file",
    "write_file_base64",
})

ACTIVE_INTERNAL: Final[frozenset[str]] = frozenset({
    "apply_conflict_resolution",
    "apply_escalation_reactions",
    "archive_ingest_item",
    "approve_scope_promotion",
    "bulk_repair_owner_mappings",
    "bulk_set_duplicate_candidate_sla",
    "bulk_set_memory_owner",
    "bulk_set_memory_sla",
    "link_memories",
    "record_project_timeline_event",
    "reject_scope_promotion",
    "run_escalation_check",
    "set_duplicate_candidate_sla",
    "set_memory_owner",
    "set_memory_priority",
    "set_memory_sla",
    "set_owner_target_active",
    "upsert_feature_flag",
    "upsert_owner_directory_item",
    "upsert_owner_role_mapping",
    "upsert_sla_policy",
})

ACTIVE_DISPATCH: Final[frozenset[str]] = frozenset({"run_workshop_action"})

LEGACY_COMPATIBILITY: Final[frozenset[str]] = frozenset({
    "add_review_note",
    "add_validation_event",
    "approve_memory",
    "archive_memory_v2",
    "backfill_timeline",
    "confirm_memory_v2",
    "create_memory_draft",
    "create_memory_version",
    "create_private_memory",
    "create_project_memory",
    "create_workspace_memory",
    "demote_memory",
    "deprecate_memory",
    "mark_memory_stale",
    "promote_memory",
    "reject_memory",
    "return_memory_to_review",
    "run_conflicts_v1",
    "run_consolidation_v1",
    "run_memory_backfill_v1",
    "run_sandman_ai",
    "run_sandman_gemma_hygiene",
    "run_sandman_quality_hygiene",
    "set_feature_flag",
    "supersede_memory_v2",
    "undo_run",
})

REMOVE_CANDIDATES: Final[frozenset[str]] = frozenset()


def classify_tool(tool_name: str) -> ToolDisposition | None:
    if tool_name in ACTIVE_DISPATCH:
        return ToolDisposition(
            status="active",
            allowed_path="top_level_dispatch",
            rationale="Canonical compact workshop dispatcher.",
        )
    if tool_name in ACTIVE_LOCAL_ADMIN:
        return ToolDisposition(
            status="internal",
            allowed_path="local_admin_only",
            rationale="Required local operator capability; never part of the remote default surface.",
        )
    if tool_name in ACTIVE_INTERNAL:
        return ToolDisposition(
            status="internal",
            allowed_path="python_internal_only",
            rationale="Current domain or governance helper retained for internal composition and tests.",
        )
    if tool_name in LEGACY_COMPATIBILITY:
        return ToolDisposition(
            status="legacy",
            allowed_path="compatibility_only",
            rationale="Historical direct tool retained temporarily while callers migrate to owned workshops.",
        )
    if tool_name in REMOVE_CANDIDATES:
        return ToolDisposition(
            status="remove",
            allowed_path="none",
            rationale="No supported runtime caller remains.",
        )
    return None


def classified_tool_names() -> frozenset[str]:
    return frozenset().union(
        ACTIVE_DISPATCH,
        ACTIVE_LOCAL_ADMIN,
        ACTIVE_INTERNAL,
        LEGACY_COMPATIBILITY,
        REMOVE_CANDIDATES,
    )


def classification_manifest(tool_names: list[str] | tuple[str, ...]) -> dict[str, object]:
    items: dict[str, dict[str, str]] = {}
    missing: list[str] = []
    for tool_name in sorted(set(tool_names)):
        disposition = classify_tool(tool_name)
        if disposition is None:
            missing.append(tool_name)
            continue
        items[tool_name] = asdict(disposition)
    return {
        "schema": "mapi_mutating_tool_classification.v1",
        "classified_count": len(items),
        "missing": missing,
        "complete": not missing,
        "items": items,
    }
