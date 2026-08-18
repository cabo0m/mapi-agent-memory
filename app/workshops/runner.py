from __future__ import annotations

"""Runtime dispatcher for compact MAPI workshop actions.

The public MCP tool stays registered in server_core.py, but the validation and
dispatch mechanics live here so the entrypoint is thinner.
"""

import inspect
import json
from collections.abc import Callable
from typing import Any

from app.runtime.freshness import mutation_freshness_guard
from app.workshops.runtime_registry import get_workshop_handler
from app.workshops.security_audit import record_security_audit, security_audit_path
from mcp_surface import current_surface_profile, lookup_workshop_action, profile_allows


def _schema_type_allows(value: Any, schema_type: str) -> bool:
    if value is None:
        return "null" in {part.strip() for part in schema_type.split("|")}
    allowed = {part.strip() for part in schema_type.split("|")}
    if "bool" in allowed and isinstance(value, bool):
        return True
    if "int" in allowed and isinstance(value, int) and not isinstance(value, bool):
        return True
    if "float" in allowed and isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    if "str" in allowed and isinstance(value, str):
        return True
    if "object" in allowed and isinstance(value, dict):
        return True
    if "array" in allowed and isinstance(value, list):
        return True
    return False


def validate_workshop_payload(handler: Any, action: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    signature = inspect.signature(handler)
    params = signature.parameters
    accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())
    accepted_keys = {
        name
        for name, param in params.items()
        if param.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
    }
    if not accepts_kwargs:
        for key in sorted(set(payload) - accepted_keys):
            errors.append({"field": key, "code": "unknown_field", "message": f"Unknown payload field '{key}'"})

    for name, param in params.items():
        if param.kind not in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}:
            continue
        if param.default is inspect.Parameter.empty and name not in payload:
            errors.append({"field": name, "code": "missing_required", "message": f"Missing required payload field '{name}'"})

    schema = action.payload_schema or {}
    for key, schema_type in schema.items():
        if key not in payload:
            continue
        if not _schema_type_allows(payload[key], str(schema_type)):
            errors.append({
                "field": key,
                "code": "invalid_type",
                "expected": schema_type,
                "actual": type(payload[key]).__name__,
                "message": f"Payload field '{key}' must match {schema_type}",
            })
    return errors


def validate_workshop_constraints(action: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for field, constraint in dict(getattr(action, "payload_constraints", None) or {}).items():
        if field not in payload or payload[field] is None:
            continue
        allowed_values = constraint.get("enum")
        if allowed_values is not None and payload[field] not in allowed_values:
            errors.append({
                "field": field,
                "code": "invalid_enum_value",
                "actual": payload[field],
                "allowed_values": list(allowed_values),
            })
    return errors


def run_workshop_action_payload(
    *,
    area: str,
    action: str,
    normalize_optional_text: Callable[[Any], str | None],
    payload: dict[str, Any] | None = None,
    payload_json: str | None = None,
) -> dict[str, Any]:
    """Validate and dispatch one compact workshop action."""
    profile = current_surface_profile()
    workshop, resolved = lookup_workshop_action(area, action)
    if workshop is None or resolved is None:
        return {
            "status": "error",
            "error": "unknown_workshop_action",
            "area": normalize_optional_text(area),
            "action": normalize_optional_text(action),
            "profile": profile,
        }
    if not profile_allows(profile, workshop.min_profile) or not profile_allows(profile, resolved.min_profile):
        record_security_audit(
            decision="denied",
            profile=profile,
            area=workshop.area,
            action=resolved.action,
            tool_name=resolved.tool_name,
            requirement=resolved.min_profile,
            risk_class=resolved.risk_class,
            outcome="insufficient_profile",
        )
        return {
            "status": "error",
            "error": "insufficient_profile",
            "area": workshop.area,
            "action": resolved.action,
            "profile": profile,
            "required": resolved.min_profile,
        }
    if payload is not None and not isinstance(payload, dict):
        return {"status": "error", "error": "payload_must_be_object"}
    call_payload: dict[str, Any] = dict(payload or {})
    if payload_json is not None and str(payload_json).strip():
        try:
            decoded = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            return {"status": "error", "error": "invalid_payload_json", "details": str(exc)}
        if not isinstance(decoded, dict):
            return {"status": "error", "error": "payload_json_must_decode_to_object"}
        call_payload.update(decoded)
    if resolved.tool_name == "get_quality_alerts" and not call_payload:
        call_payload["area_code"] = "projects"
    freshness = mutation_freshness_guard(
        area=workshop.area,
        action=resolved.action,
        risk_class=resolved.risk_class,
        payload=call_payload,
    )
    if not freshness.get("allowed"):
        record_security_audit(
            decision="denied",
            profile=profile,
            area=workshop.area,
            action=resolved.action,
            tool_name=resolved.tool_name,
            requirement=resolved.min_profile,
            risk_class=resolved.risk_class,
            outcome="runtime_not_ready",
        )
        return {
            "status": "error",
            "error": "runtime_not_ready",
            "area": workshop.area,
            "action": resolved.action,
            "profile": profile,
            "reason_codes": freshness.get("reason_codes") or [],
            "runtime_commit": freshness.get("runtime_commit"),
            "repository_head": freshness.get("repository_head"),
            "repository_details": freshness.get("repository_details") or {},
        }
    handler = get_workshop_handler(resolved.tool_name)
    if handler is None or not callable(handler):
        return {"status": "error", "error": "handler_not_found", "tool_name": resolved.tool_name}
    validation_errors = validate_workshop_payload(handler, resolved, call_payload)
    validation_errors.extend(validate_workshop_constraints(resolved, call_payload))
    if validation_errors:
        return {
            "status": "error",
            "error": "invalid_workshop_payload",
            "area": str(area).strip().lower(),
            "action": resolved.action,
            "tool_name": resolved.tool_name,
            "payload_schema": resolved.payload_schema or {},
            "payload_constraints": resolved.payload_constraints or {},
            "validation_errors": validation_errors,
            "details": validation_errors,
        }
    should_audit_execution = resolved.risk_class == "R3"
    operation_audit_path = security_audit_path() if should_audit_execution else None
    if should_audit_execution:
        record_security_audit(
            decision="allowed",
            profile=profile,
            area=workshop.area,
            action=resolved.action,
            tool_name=resolved.tool_name,
            requirement=resolved.min_profile,
            risk_class=resolved.risk_class,
            outcome="started",
            audit_path=operation_audit_path,
        )
    try:
        result = handler(**call_payload)
    except Exception:
        if should_audit_execution:
            record_security_audit(
                decision="allowed",
                profile=profile,
                area=workshop.area,
                action=resolved.action,
                tool_name=resolved.tool_name,
                requirement=resolved.min_profile,
                risk_class=resolved.risk_class,
                outcome="failed",
                audit_path=operation_audit_path,
            )
        raise
    if should_audit_execution:
        record_security_audit(
            decision="allowed",
            profile=profile,
            area=workshop.area,
            action=resolved.action,
            tool_name=resolved.tool_name,
            requirement=resolved.min_profile,
            risk_class=resolved.risk_class,
            outcome="completed",
            audit_path=operation_audit_path,
        )
    return {
        "status": "ok",
        "profile": profile,
        "area": workshop.area,
        "action": resolved.action,
        "tool_name": resolved.tool_name,
        "risk_class": resolved.risk_class,
        "result": result,
    }
