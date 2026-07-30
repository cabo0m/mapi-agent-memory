from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

CORE_TERMS = (
    "agent_core",
    "session_bootstrap",
    "bootstrap_agent_context",
    "continuity",
    "project_context",
)

DEFAULT_PROJECT_ANCHOR_TAGS = (
    "session-closure",
    "next-step",
    "mapi",
    "memory",
    "governance",
    "bootstrap_agent_context",
)

def project_anchor_tags_for(project_key: str | None) -> tuple[str, ...]:
    normalized = (project_key or "demo-project").strip().lower()
    return tuple(dict.fromkeys((*DEFAULT_PROJECT_ANCHOR_TAGS, normalized)))


def project_anchor_exclude_tags_for(project_key: str | None) -> tuple[str, ...]:
    return ()


@dataclass(frozen=True)
class ActorContext:
    user_key: str | None = None
    workspace_key: str | None = None
    project_key: str | None = None
    scopes: tuple[str, ...] = ()
    is_admin: bool = False
    surface: str = "unknown"


@dataclass(frozen=True)
class BootstrapPolicy:
    project_key: str = "demo-project"
    project_key_values: tuple[str, ...] = ()
    limit: int = 24
    core_terms: tuple[str, ...] = CORE_TERMS
    project_anchor_tags: tuple[str, ...] = ()
    project_anchor_exclude_tags: tuple[str, ...] = ()
    recent_limit: int = 8

    def __post_init__(self) -> None:
        if not self.project_key_values:
            object.__setattr__(self, "project_key_values", (self.project_key,))
        if not self.project_anchor_tags:
            object.__setattr__(self, "project_anchor_tags", project_anchor_tags_for(self.project_key))
        if not self.project_anchor_exclude_tags:
            object.__setattr__(
                self,
                "project_anchor_exclude_tags",
                project_anchor_exclude_tags_for(self.project_key),
            )

    @property
    def safe_limit(self) -> int:
        return max(6, min(int(self.limit or 24), 50))

    @property
    def safe_recent_limit(self) -> int:
        return max(1, min(int(self.recent_limit or 8), 50))


def compact_bootstrap_memory(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "summary_short": item.get("summary_short"),
        "memory_type": item.get("memory_type"),
        "content": item.get("content"),
        "tags": item.get("tags"),
        "importance_score": item.get("importance_score"),
        "confidence_score": item.get("confidence_score"),
        "identity_weight": item.get("identity_weight"),
        "project_key": item.get("project_key"),
        "scope_code": item.get("scope_code"),
        "visibility_scope": item.get("visibility_scope"),
        "owner_user_id": item.get("owner_user_id"),
    }


def make_bootstrap_response(
    *,
    policy: BootstrapPolicy,
    core_rows: Iterable[dict[str, Any]],
    project_rows: Iterable[dict[str, Any]],
    recent_rows: Iterable[dict[str, Any]],
    actor: ActorContext | None = None,
) -> dict[str, Any]:
    seen: set[int] = set()

    def uniq(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            memory_id = int(row.get("id") or 0)
            if memory_id and memory_id not in seen:
                seen.add(memory_id)
                result.append(compact_bootstrap_memory(row))
        return result

    return {
        "status": "ok",
        "policy": {
            "name": "shared_memory_bootstrap_policy_v1",
            "project_key": policy.project_key,
            "limit": policy.safe_limit,
            "recent_limit": policy.safe_recent_limit,
            "core_terms": list(policy.core_terms),
            "project_anchor_tags": list(policy.project_anchor_tags),
            "project_anchor_exclude_tags": list(policy.project_anchor_exclude_tags),
        },
        "actor": {
            "user_key": actor.user_key if actor else None,
            "workspace_key": actor.workspace_key if actor else None,
            "project_key": actor.project_key if actor else None,
            "scopes": list(actor.scopes) if actor else [],
            "is_admin": bool(actor.is_admin) if actor else False,
            "surface": actor.surface if actor else "unknown",
        },
        "core_identity": uniq(core_rows),
        "project_anchors": uniq(project_rows),
        "recent_project_context": uniq(recent_rows),
        "rule": "shared policy: same memory semantics for MAPI and MPbM; auth/scope gates differ outside this policy",
    }


def build_core_identity_sql(policy: BootstrapPolicy) -> tuple[str, list[Any]]:
    like_sql = " OR ".join(["COALESCE(tags, '') LIKE ?" for _ in policy.core_terms])
    sql = f"""
        SELECT * FROM memories
        WHERE activity_state = 'active'
          AND ({like_sql} OR identity_weight >= 0.75)
        ORDER BY identity_weight DESC, importance_score DESC, confidence_score DESC, id DESC
        LIMIT ?
    """
    params: list[Any] = [f"%{term}%" for term in policy.core_terms] + [policy.safe_limit]
    return sql, params


def build_project_anchors_sql(policy: BootstrapPolicy) -> tuple[str, list[Any]]:
    like_sql = " OR ".join(["COALESCE(tags, '') LIKE ?" for _ in policy.project_anchor_tags])
    project_key_values = tuple(dict.fromkeys(policy.project_key_values or (policy.project_key,)))
    project_key_sql = ", ".join(["?"] * len(project_key_values))
    exclude_sql = ""
    if policy.project_anchor_exclude_tags:
        exclude_like_sql = " OR ".join(["COALESCE(tags, '') LIKE ?" for _ in policy.project_anchor_exclude_tags])
        exclude_sql = f"\n          AND NOT ({exclude_like_sql})"
    sql = f"""
        SELECT * FROM memories
        WHERE activity_state = 'active'
          AND project_key IN ({project_key_sql})
          AND ({like_sql}){exclude_sql}
          /* anchors are project-specific; demo-project excludes MAPI/MPbM/Sandman/governance infrastructure */
        ORDER BY importance_score DESC, confidence_score DESC, id DESC
        LIMIT ?
    """
    params: list[Any] = list(project_key_values)
    params += [f"%{tag}%" for tag in policy.project_anchor_tags]
    params += [f"%{tag}%" for tag in policy.project_anchor_exclude_tags]
    params += [policy.safe_limit]
    return sql, params


def build_recent_project_sql(policy: BootstrapPolicy) -> tuple[str, list[Any]]:
    project_key_values = tuple(dict.fromkeys(policy.project_key_values or (policy.project_key,)))
    project_key_sql = ", ".join(["?"] * len(project_key_values))
    sql = f"""
        SELECT * FROM memories
        WHERE activity_state = 'active' AND project_key IN ({project_key_sql})
        ORDER BY id DESC
        LIMIT {policy.safe_recent_limit}
    """
    return sql, list(project_key_values)
