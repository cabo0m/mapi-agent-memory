from __future__ import annotations

from typing import Any, Mapping


WORKSHOP_PAYLOAD_SCHEMA_VERSION = "mapi_workshop_payload_schema.v2"

MEMORY_FIND_SORT_VALUES = (
    "active",
    "recent",
    "created_at_desc",
    "created_at_asc",
    "recalled",
    "validated",
)
PROJECT_KEY_MODE_VALUES = ("exact", "aliases")


def enum_field(type_name: str, values: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": str(type_name),
        "enum": list(values),
    }


def normalize_field_spec(spec: Any) -> dict[str, Any]:
    if isinstance(spec, str):
        return {"type": spec, "enum": None}
    if isinstance(spec, Mapping):
        type_name = str(spec.get("type") or "any")
        enum_values = spec.get("enum")
        if enum_values is None:
            normalized_enum = None
        elif isinstance(enum_values, (list, tuple)):
            normalized_enum = list(enum_values)
        else:
            raise TypeError("workshop payload enum must be a list or tuple")
        return {"type": type_name, "enum": normalized_enum}
    raise TypeError("workshop payload schema field must be a string or mapping")


def invalid_choice_payload(*, field: str, actual: Any, allowed_values: tuple[str, ...] | list[str]) -> dict[str, Any]:
    return {
        "status": "error",
        "error": "invalid_enum_value",
        "field": field,
        "actual": actual,
        "allowed_values": list(allowed_values),
    }
