from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

ALLOWED_ISSUE_KINDS = {
    "owner_gap",
    "project_scope_mismatch",
    "test_probe_noise",
    "duplicate_candidate",
    "consolidation_candidate",
    "stale_memory",
    "conflict_candidate",
    "ingest_candidate",
    "no_action",
    "needs_human_review",
}

ALLOWED_ACTIONS = {
    "set_owner",
    "normalize_project_scope",
    "archive_as_noise",
    "keep_as_evidence",
    "consolidate_then_archive",
    "consolidate",
    "link_memories",
    "promote_ingest",
    "reject_ingest",
    "keep",
    "needs_human_review",
}

LOW_RISK_AUTO_APPLY_ACTIONS = {"set_owner", "normalize_project_scope"}
ALLOWED_RISKS = {"low", "medium", "high"}
PROTECTED_LAYERS = {"core", "identity"}


@dataclass(frozen=True)
class ParsedDecisionBatch:
    status: str
    decisions: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    raw_text: str


class FakeGemmaClient:
    """Deterministic fake model for Sandman/Gemma flow tests and dry plumbing."""

    def __init__(self, mode: str = "valid_low_risk_json") -> None:
        self.mode = mode

    def complete_json(self, prompt: str, *, timeout_seconds: int = 30) -> str:
        if self.mode == "invalid_json":
            return "this is not json"
        if self.mode == "prose_instead_of_json":
            return "Moim zdaniem trzeba to naprawic, ale nie dam JSON-a."
        if self.mode == "hallucinated_memory_id":
            return json.dumps({"decisions": [{"memory_id": 999999999, "issue_kind": "owner_gap", "proposed_action": "set_owner", "confidence": 0.96, "risk": "low", "rationale": "Fake hallucinated id.", "required_checks": ["memory_is_active"]}]}, ensure_ascii=False)
        if self.mode == "forbidden_action":
            return json.dumps({"decisions": [{"memory_id": 1, "issue_kind": "owner_gap", "proposed_action": "delete_database", "confidence": 0.99, "risk": "low", "rationale": "Bad fake action.", "required_checks": []}]}, ensure_ascii=False)
        if self.mode == "mixed_risk_batch":
            return json.dumps({"decisions": [{"memory_id": 1, "issue_kind": "owner_gap", "proposed_action": "set_owner", "confidence": 0.95, "risk": "low", "rationale": "Low risk owner repair.", "required_checks": ["memory_is_active"]}, {"memory_id": 2, "issue_kind": "test_probe_noise", "proposed_action": "archive_as_noise", "confidence": 0.72, "risk": "medium", "rationale": "Needs operator review before archive.", "required_checks": ["memory_is_active"]}]}, ensure_ascii=False)
        if self.mode == "from_prompt_first_noise_review":
            return json.dumps({"decisions": [_decision_from_first_prompt_candidate(prompt, "test_probe_noise", "archive_as_noise", 0.72, "medium")]}, ensure_ascii=False)
        if self.mode == "from_prompt_first_owner_low":
            return json.dumps({"decisions": [_decision_from_first_prompt_candidate(prompt, "owner_gap", "set_owner", 0.95, "low")]}, ensure_ascii=False)
        if self.mode == "from_prompt_first_scope_low":
            return json.dumps({"decisions": [_decision_from_first_prompt_candidate(prompt, "project_scope_mismatch", "normalize_project_scope", 0.95, "low")]}, ensure_ascii=False)
        return json.dumps({"decisions": []}, ensure_ascii=False)


def _candidates_from_prompt(prompt: str) -> dict[str, list[dict[str, Any]]]:
    marker = "Candidates:\n"
    if marker not in prompt:
        return {}
    raw = prompt.split(marker, 1)[1].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for kind, items in parsed.items():
        if isinstance(items, list):
            result[str(kind)] = [item for item in items if isinstance(item, dict)]
    return result


def _decision_from_first_prompt_candidate(prompt: str, issue_kind: str, action: str, confidence: float, risk: str) -> dict[str, Any]:
    candidates = _candidates_from_prompt(prompt)
    items = candidates.get(issue_kind) or []
    if not items:
        return {
            "memory_id": 999999998,
            "issue_kind": issue_kind,
            "proposed_action": action,
            "confidence": confidence,
            "risk": risk,
            "rationale": f"No prompt candidate found for {issue_kind}; deliberate fake fallback for validator rejection.",
            "required_checks": ["memory_is_active"],
        }
    memory_id = int(items[0].get("memory_id") or items[0].get("id"))
    return {
        "memory_id": memory_id,
        "issue_kind": issue_kind,
        "proposed_action": action,
        "confidence": confidence,
        "risk": risk,
        "rationale": f"Fake Gemma selected first {issue_kind} candidate from prompt for positive plumbing smoke.",
        "required_checks": ["memory_is_active", "predicate_still_true"],
    }


def _json_error(reason_code: str, message: str) -> dict[str, Any]:
    return {"reason_code": reason_code, "message": message}


def parse_gemma_decisions(raw_text: str) -> ParsedDecisionBatch:
    """Parse a strict Gemma decision batch: raw JSON object with decisions array only."""
    text = (raw_text or "").strip()
    if not text:
        return ParsedDecisionBatch("error", [], [_json_error("empty_response", "Gemma returned an empty response.")], raw_text)
    if text.startswith("```") or not text.startswith("{"):
        return ParsedDecisionBatch("error", [], [_json_error("invalid_json", "Gemma response is not a raw JSON object.")], raw_text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return ParsedDecisionBatch("error", [], [_json_error("invalid_json", f"JSON decode failed: {exc}")], raw_text)
    if not isinstance(data, dict):
        return ParsedDecisionBatch("error", [], [_json_error("schema_error", "Top-level JSON must be an object.")], raw_text)
    decisions = data.get("decisions")
    if not isinstance(decisions, list):
        return ParsedDecisionBatch("error", [], [_json_error("schema_error", "Top-level object must contain decisions array.")], raw_text)

    errors: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            errors.append({"index": index, "reason_code": "schema_error", "message": "Decision must be an object."})
            continue
        required = ["memory_id", "issue_kind", "proposed_action", "confidence", "risk", "rationale", "required_checks"]
        missing = [field for field in required if field not in decision]
        if missing:
            errors.append({"index": index, "reason_code": "schema_error", "message": f"Missing required fields: {missing}"})
            continue
        try:
            memory_id = int(decision["memory_id"])
            confidence = float(decision["confidence"])
        except (TypeError, ValueError):
            errors.append({"index": index, "reason_code": "schema_error", "message": "memory_id must be int-like and confidence numeric."})
            continue
        issue_kind = str(decision["issue_kind"])
        proposed_action = str(decision["proposed_action"])
        risk = str(decision["risk"])
        required_checks = decision["required_checks"]
        if issue_kind not in ALLOWED_ISSUE_KINDS:
            errors.append({"index": index, "memory_id": memory_id, "reason_code": "unknown_issue_kind", "message": issue_kind})
            continue
        if proposed_action not in ALLOWED_ACTIONS:
            errors.append({"index": index, "memory_id": memory_id, "reason_code": "forbidden_action", "message": proposed_action})
            continue
        if risk not in ALLOWED_RISKS:
            errors.append({"index": index, "memory_id": memory_id, "reason_code": "schema_error", "message": f"Unknown risk: {risk}"})
            continue
        if not 0 <= confidence <= 1:
            errors.append({"index": index, "memory_id": memory_id, "reason_code": "schema_error", "message": "confidence must be 0..1"})
            continue
        if not isinstance(required_checks, list):
            errors.append({"index": index, "memory_id": memory_id, "reason_code": "schema_error", "message": "required_checks must be a list"})
            continue
        normalized.append({**decision, "memory_id": memory_id, "confidence": confidence, "issue_kind": issue_kind, "proposed_action": proposed_action, "risk": risk, "required_checks": [str(item) for item in required_checks], "rationale": str(decision.get("rationale") or "")})
    status = "ok" if not errors else "partial" if normalized else "error"
    return ParsedDecisionBatch(status, normalized, errors, raw_text)


def build_sandman_gemma_hygiene_prompt(candidates: dict[str, list[dict[str, Any]]], *, project_key: str | None = None) -> str:
    compact_candidates = {
        kind: [{"memory_id": item.get("id") or item.get("memory_id"), "summary_short": item.get("summary_short"), "memory_type": item.get("memory_type"), "project_key": item.get("project_key"), "scope_code": item.get("scope_code"), "layer_code": item.get("layer_code"), "area_code": item.get("area_code"), "owner_role": item.get("owner_role"), "owner_id": item.get("owner_id"), "tags": item.get("tags")} for item in items]
        for kind, items in candidates.items()
    }
    policy = {"allowed_issue_kinds": sorted(ALLOWED_ISSUE_KINDS), "allowed_actions": sorted(ALLOWED_ACTIONS), "auto_apply_hint": "low risk and confidence >= 0.90 only for set_owner/normalize_project_scope", "output_contract": {"decisions": [{"memory_id": 123, "issue_kind": "owner_gap", "proposed_action": "set_owner", "confidence": 0.93, "risk": "low", "rationale": "Short reason.", "required_checks": ["memory_is_active"]}]}}
    return (
        "You are Gemma acting as Sandman reviewer. You classify memory hygiene candidates and propose actions only. "
        "You do not execute actions. Return exactly one raw JSON object, no markdown, no prose.\n\n"
        f"Project filter: {project_key or '<all>'}\n"
        f"Policy:\n{json.dumps(policy, ensure_ascii=False, indent=2)}\n\n"
        f"Candidates:\n{json.dumps(compact_candidates, ensure_ascii=False, indent=2)}"
    )


def validate_sandman_decisions(decisions: Iterable[dict[str, Any]], current_memories: dict[int, dict[str, Any]], *, auto_apply_confidence: float = 0.90) -> dict[str, list[dict[str, Any]]]:
    accepted_auto_apply: list[dict[str, Any]] = []
    needs_human_review: list[dict[str, Any]] = []
    rejected_by_validator: list[dict[str, Any]] = []
    for decision in decisions:
        memory_id = int(decision["memory_id"])
        memory = current_memories.get(memory_id)
        if memory is None:
            rejected_by_validator.append({**decision, "validator_status": "rejected", "reason_code": "unknown_memory_id"})
            continue
        if memory.get("archived_at") is not None:
            rejected_by_validator.append({**decision, "validator_status": "rejected", "reason_code": "archived_memory"})
            continue
        layer = str(memory.get("layer_code") or "").lower()
        if layer in PROTECTED_LAYERS:
            needs_human_review.append({**decision, "validator_status": "needs_human_review", "reason_code": "protected_layer"})
            continue
        if not _predicate_still_true(decision, memory):
            rejected_by_validator.append({**decision, "validator_status": "rejected", "reason_code": "stale_predicate"})
            continue
        if decision["risk"] != "low" or float(decision["confidence"]) < auto_apply_confidence:
            needs_human_review.append({**decision, "validator_status": "needs_human_review", "reason_code": "risk_or_confidence"})
            continue
        if decision["proposed_action"] not in LOW_RISK_AUTO_APPLY_ACTIONS:
            needs_human_review.append({**decision, "validator_status": "needs_human_review", "reason_code": "action_not_auto_apply"})
            continue
        accepted_auto_apply.append({**decision, "validator_status": "accepted_auto_apply", "reason_code": "low_risk_validated"})
    return {"accepted_auto_apply": accepted_auto_apply, "needs_human_review": needs_human_review, "rejected_by_validator": rejected_by_validator}


def _tags_allow_global_project_scope(tags: Any) -> bool:
    return "allow-global-project-scope" in str(tags or "").lower()


def _predicate_still_true(decision: dict[str, Any], memory: dict[str, Any]) -> bool:
    issue = decision.get("issue_kind")
    if issue == "owner_gap":
        return not str(memory.get("owner_role") or "").strip() or not str(memory.get("owner_id") or "").strip()
    if issue == "project_scope_mismatch":
        project_key = str(memory.get("project_key") or "").strip()
        scope_code = str(memory.get("scope_code") or "").strip()
        return bool(project_key) and (not scope_code or scope_code == "global") and not _tags_allow_global_project_scope(memory.get("tags"))
    if issue == "test_probe_noise":
        haystack = " ".join(str(memory.get(key) or "") for key in ("tags", "summary_short", "source")).lower()
        return any(token in haystack for token in ("test", "smoke", "probe", "temporary-probe"))
    return True


def build_shadow_comparison(
    candidates: dict[str, list[dict[str, Any]]],
    validation: dict[str, list[dict[str, Any]]],
    *,
    issue_kinds: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Compare validated Gemma decisions with deterministic candidate collectors.

    This is shadow-mode only. It does not judge semantic quality; it measures whether
    Gemma proposed a decision for IDs that deterministic collectors still consider
    candidates for each issue kind.
    """
    requested = {str(item) for item in issue_kinds} if issue_kinds is not None else set(candidates.keys())
    if not requested:
        requested = set(candidates.keys())

    candidate_ids_by_kind: dict[str, set[int]] = {}
    for kind in requested:
        candidate_ids_by_kind[kind] = {
            int(item.get("memory_id") or item.get("id"))
            for item in candidates.get(kind, [])
            if item.get("memory_id") is not None or item.get("id") is not None
        }

    accepted_buckets = ("accepted_auto_apply", "needs_human_review")
    rejected_ids = {
        int(item["memory_id"])
        for item in validation.get("rejected_by_validator", [])
        if item.get("memory_id") is not None
    }
    model_ids_by_kind: dict[str, set[int]] = {kind: set() for kind in requested}
    for bucket in accepted_buckets:
        for decision in validation.get(bucket, []):
            kind = str(decision.get("issue_kind"))
            if kind not in requested or decision.get("memory_id") is None:
                continue
            model_ids_by_kind.setdefault(kind, set()).add(int(decision["memory_id"]))

    per_issue_kind: dict[str, dict[str, Any]] = {}
    totals = {
        "agreement_count": 0,
        "model_only_count": 0,
        "oracle_only_count": 0,
        "rejected_decision_count": len(rejected_ids),
    }
    for kind in sorted(requested):
        oracle_ids = candidate_ids_by_kind.get(kind, set())
        model_ids = model_ids_by_kind.get(kind, set())
        agreement = sorted(oracle_ids & model_ids)
        model_only = sorted(model_ids - oracle_ids)
        oracle_only = sorted(oracle_ids - model_ids)
        item = {
            "oracle_candidate_count": len(oracle_ids),
            "model_validated_decision_count": len(model_ids),
            "agreement_count": len(agreement),
            "model_only_count": len(model_only),
            "oracle_only_count": len(oracle_only),
            "agreement_memory_ids": agreement,
            "model_only_memory_ids": model_only,
            "oracle_only_memory_ids": oracle_only,
        }
        per_issue_kind[kind] = item
        totals["agreement_count"] += item["agreement_count"]
        totals["model_only_count"] += item["model_only_count"]
        totals["oracle_only_count"] += item["oracle_only_count"]

    comparable_decision_count = totals["agreement_count"] + totals["model_only_count"]
    totals["agreement_rate"] = round(totals["agreement_count"] / comparable_decision_count, 4) if comparable_decision_count else None
    status = "clean" if totals["model_only_count"] == 0 and totals["rejected_decision_count"] == 0 else "attention"
    if totals["agreement_count"] == 0 and any(candidate_ids_by_kind.values()):
        status = "no_model_agreement"
    return {
        "status": status,
        "totals": totals,
        "per_issue_kind": per_issue_kind,
    }
