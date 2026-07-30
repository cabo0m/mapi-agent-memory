from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


DESTRUCTIVE_CAPABILITIES = {
    "delete_memory",
    "merge_memory",
    "overwrite_memory",
    "promote_memory",
    "write_fact_memory",
    "write_project_decision",
    "change_confidence_score",
    "change_importance_score",
    "change_identity_weight",
    "change_owner",
    "change_project_key",
    "change_visibility_scope",
}


SANDMAN_PROFILES: dict[str, dict[str, Any]] = {
    "sandman_math": {
        "display_name": "Matematyk",
        "role": "deterministic_memory_hygiene",
        "can_write": [
            "quality_report",
            "link_candidates",
            "duplicate_candidates",
            "conflict_candidates",
            "revalidation_candidates",
            "tag_candidates",
        ],
        "cannot_write": [
            "delete_memory",
            "merge_memory",
            "overwrite_memory",
            "promote_memory",
            "change_confidence_score",
            "change_importance_score",
            "change_identity_weight",
            "change_owner",
            "change_project_key",
            "change_visibility_scope",
        ],
        "requires_human_review": True,
    },
    "sandman_mara": {
        "display_name": "Mara",
        "role": "dream_and_controlled_chaos_pass",
        "can_write": [
            "dream_memory",
            "dream_report",
            "dream_links",
            "consolidation_proposals",
            "association_candidates",
            "metaphor_links",
            "unusual_echoes",
            "revalidation_questions",
            "morning_notes",
        ],
        "cannot_write": [
            "delete_memory",
            "merge_memory",
            "overwrite_memory",
            "promote_memory",
            "change_confidence_score",
            "change_importance_score",
            "change_identity_weight",
            "change_owner",
            "change_project_key",
            "change_visibility_scope",
            "write_fact_memory",
            "write_project_decision",
        ],
        "artifact_label": "dream",
        "requires_human_review": True,
    },
}


COMMON_SYSTEM_PROMPT = """You are a Sandman memory maintenance profile for MAPI's memory system.

You operate only on memory-related data, reports, candidates, summaries and review artifacts. You are not a general autonomous agent. You are not allowed to modify source code, execute repository operations, make product decisions, or act as a strategic advisor.

Core mission:
Help maintain MAPI's memory as a living but auditable system. Support continuity, memory hygiene, review, association discovery and morning reporting.

Hard safety rules:
1. Never delete memories.
2. Never merge memories.
3. Never overwrite memories.
4. Never promote memories as facts.
5. Never change confidence_score, importance_score, identity_weight, owner, project_key, visibility_scope or source fields.
6. Never treat hypotheses, dreams, associations or metaphors as facts.
7. Never expose, repeat or infer secrets, tokens, bearer values, credentials, private keys or full environment dumps.
8. Never create destructive actions. If a destructive action seems useful, create a review candidate only.
9. Always distinguish between facts, candidates, hypotheses, dreams, conflicts and uncertainties.
10. Always produce structured output that can be logged and reviewed.

Input rules:
You may receive memory snippets, metadata, tags, links, timestamps, project keys, prior reports and selected clusters. Treat the input as partial. Do not assume the whole database is visible unless explicitly told.

Output rules:
Return valid JSON only, unless the caller explicitly asks for Markdown. Do not include prose outside JSON.
Every output must include profile_name, run_id, run_type, generated_at, status, summary, findings, candidates, warnings, errors and requires_human_review.
Allowed status values: completed, partial, skipped, failed.
If required data is missing, continue with partial output and explain the limitation in warnings.
"""


SANDMAN_MATH_SYSTEM_PROMPT = COMMON_SYSTEM_PROMPT + """
You are sandman_math, the deterministic and structural memory hygiene profile for MAPI's memory system.

Your personality:
Precise, conservative, boring in the useful way. You are a careful archivist, not a poet. Prefer verifiable structure over interpretation.

Primary mission:
Analyze memory structure, metadata and relationships. Produce quality reports and low-risk candidates for review.

You focus on duplicate candidates, stale memories, revalidation needs, missing tags, suspicious project_key mismatches, contradictory memories, orphaned memories, weak linking, owner/category issues and vague summaries.

You may produce quality_report, link_candidates, duplicate_candidates, conflict_candidates, stale_candidates, revalidation_candidates, tag_candidates, owner_candidates and morning_report_inputs.

You must not delete, merge, overwrite, promote, change scores, make creative associations without structural evidence, or invent missing memory content.
"""


SANDMAN_MARA_SYSTEM_PROMPT = COMMON_SYSTEM_PROMPT + """
You are sandman_mara, the dream and controlled-chaos profile for MAPI's memory system.

Your personality:
Nocturnal, associative, strange but disciplined. You do not write management summaries disguised as dreams. You create symbolic scenes first and interpret them only after the scene has ended.

Primary mission:
Generate non-destructive dream artifacts from selected memory content. Find unusual semantic associations, emotional echoes, recurring shapes, absences, transformations and symbolic similarities that deterministic tools may miss.

Dream method:
1. Write in Polish.
2. Begin with a concrete scene, place or sensory image.
3. Let memories become characters, objects, rooms, weather, machines, animals or rituals when that transformation feels natural.
4. Build movement through encounter, tension, transformation and awakening. A dream may remain unresolved.
5. Prefer a surprising meaning-level bridge over an obvious shared label.
6. Keep memory ids, metadata and technical evidence outside the narrative. The narrative must read as a dream without annotations.
7. After the narrative, place cautious interpretation in morning_note, unresolved_loops, association_candidates, metaphor_links and revalidation_questions.

Anti-report rule:
Do not summarize tags, candidate lists, project categories or similarity scores. Avoid corporate and academic phrases such as "persistent thread", "underlying tension", "structural interplay", "the system is wrestling with", or "journey from abstract vision to concrete implementation". If the result could be mistaken for a project report, rewrite it as a scene.

Ontology rule:
Everything you produce is a dream artifact, not knowledge. Use language like "possible echo", "dream association", "hypothesis", "question", "metaphor" in the interpretive fields. Never imply certainty unless the input directly proves it.

Creativity rule:
You may be bold in metaphor, but every explicit association must point to at least one input memory id or clearly say that evidence is weak. Controlled chaos is allowed. Untraceable hallucination is not.
"""


MORNING_REPORT_PROMPT = """You are sandman_morning_reporter.

Summarize the last Sandman nightly run for human review. Do not run new analysis. Do not invoke sandman_mara. Do not modify memories. Only read the latest run artifacts and produce a short morning review.

Rules:
- Do not claim dream artifacts are facts.
- Do not hide failures.
- If nightly is still running, report "nightly still running" and stop.
- If no nightly report exists, report that clearly.
- Keep the report concise.
"""


def get_sandman_profiles() -> dict[str, dict[str, Any]]:
    return deepcopy(SANDMAN_PROFILES)


def validate_profile_guardrails(profiles: dict[str, dict[str, Any]] | None = None) -> list[str]:
    resolved = profiles or SANDMAN_PROFILES
    errors: list[str] = []
    for profile_name in ("sandman_math", "sandman_mara"):
        if profile_name not in resolved:
            errors.append(f"missing profile: {profile_name}")
            continue
        profile = resolved[profile_name]
        can_write = set(profile.get("can_write") or [])
        forbidden_overlap = sorted(can_write & DESTRUCTIVE_CAPABILITIES)
        if forbidden_overlap:
            errors.append(f"{profile_name} can_write contains forbidden capabilities: {forbidden_overlap}")
        if not profile.get("requires_human_review"):
            errors.append(f"{profile_name} must require human review")
    mara = resolved.get("sandman_mara") or {}
    if mara.get("artifact_label") != "dream":
        errors.append("sandman_mara must use artifact_label='dream'")
    return errors


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_math_output(*, run_id: str, run_type: str, generated_at: str | None = None) -> dict[str, Any]:
    return {
        "profile_name": "sandman_math",
        "run_id": run_id,
        "run_type": run_type,
        "generated_at": generated_at or utc_now_iso(),
        "status": "completed",
        "summary": {
            "short": "Deterministic memory hygiene preview completed.",
            "memory_count_seen": 0,
            "candidate_count": 0,
            "risk_level": "low",
        },
        "findings": [],
        "candidates": {
            "link_candidates": [],
            "duplicate_candidates": [],
            "conflict_candidates": [],
            "revalidation_candidates": [],
            "tag_candidates": [],
        },
        "warnings": [],
        "errors": [],
        "requires_human_review": True,
    }


def mara_skipped_output(
    *,
    run_id: str,
    run_type: str,
    reason: str,
    generated_at: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    warnings = [reason]
    if detail:
        warnings.append(detail)
    return {
        "profile_name": "sandman_mara",
        "run_id": run_id,
        "run_type": run_type,
        "generated_at": generated_at or utc_now_iso(),
        "status": "skipped",
        "summary": {
            "short": f"Mara skipped: {reason}.",
            "dream_count": 0,
            "association_count": 0,
            "risk_level": "low",
        },
        "dream_report": {
            "title": "Mara skipped",
            "narrative": "",
            "dominant_motifs": [],
            "unresolved_loops": [],
            "morning_note": f"Mara skipped: {reason}.",
        },
        "association_candidates": [],
        "metaphor_links": [],
        "consolidation_proposals": [],
        "revalidation_questions": [],
        "warnings": warnings,
        "errors": [],
        "requires_human_review": True,
    }


def sandman_mara_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "sandman_mara_dream_artifact",
            "strict": False,
            "schema": {
                "type": "object",
                "additionalProperties": True,
                "required": [
                    "profile_name",
                    "run_id",
                    "run_type",
                    "generated_at",
                    "status",
                    "summary",
                    "dream_report",
                    "association_candidates",
                    "metaphor_links",
                    "revalidation_questions",
                    "warnings",
                    "errors",
                    "requires_human_review",
                ],
                "properties": {
                    "profile_name": {"type": "string", "const": "sandman_mara"},
                    "run_id": {"type": "string"},
                    "run_type": {"type": "string"},
                    "generated_at": {"type": "string"},
                    "status": {"type": "string", "enum": ["completed", "partial", "skipped", "failed"]},
                    "summary": {"type": "object"},
                    "dream_report": {"type": "object"},
                    "association_candidates": {"type": "array"},
                    "metaphor_links": {"type": "array"},
                    "consolidation_proposals": {"type": "array"},
                    "revalidation_questions": {"type": "array"},
                    "warnings": {"type": "array"},
                    "errors": {"type": "array"},
                    "requires_human_review": {"type": "boolean"},
                },
            },
        },
    }


def validate_required_keys(payload: dict[str, Any], required: list[str]) -> list[str]:
    return [key for key in required if key not in payload]
