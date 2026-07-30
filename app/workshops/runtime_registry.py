from __future__ import annotations

from threading import RLock
from types import MappingProxyType
from typing import Any

from app.workshops.catalog import WORKSHOP_PACKAGES, WORKSHOP_TOOL_OWNERS

_LOCK = RLock()
_HANDLERS: dict[str, Any] = {}
_HANDLER_SOURCES: dict[str, str] = {}


class WorkshopHandlerBindingError(RuntimeError):
    pass


def bind_workshop_handlers(
    provider: Any,
    *,
    replace: bool = False,
    strict: bool = False,
    local_only: bool = False,
) -> dict[str, Any]:
    """Bind explicitly declared workshop handlers from one provider module/object."""
    provider_name = getattr(provider, "__name__", provider.__class__.__name__)
    discovered: dict[str, Any] = {}
    missing: list[str] = []
    for package in WORKSHOP_PACKAGES:
        bound = package.bind_handlers(provider)
        if local_only:
            bound = {
                tool_name: handler
                for tool_name, handler in bound.items()
                if getattr(handler, "__module__", None) == provider_name
            }
        discovered.update(bound)
        for tool_name in package.TOOL_NAMES:
            if tool_name not in bound:
                missing.append(tool_name)

    with _LOCK:
        for tool_name, handler in discovered.items():
            existing = _HANDLERS.get(tool_name)
            if existing is not None and existing is not handler and not replace:
                raise WorkshopHandlerBindingError(
                    f"Handler '{tool_name}' already bound by {_HANDLER_SOURCES.get(tool_name, 'unknown')}"
                )
            _HANDLERS[tool_name] = handler
            _HANDLER_SOURCES[tool_name] = provider_name

    unresolved = sorted(set(WORKSHOP_TOOL_OWNERS) - set(_HANDLERS))
    if strict and unresolved:
        raise WorkshopHandlerBindingError(f"Missing workshop handlers: {', '.join(unresolved)}")
    return {
        "provider": provider_name,
        "bound_count": len(discovered),
        "missing_from_provider": sorted(set(missing)),
        "unresolved": unresolved,
        "complete": not unresolved,
    }


def get_workshop_handler(tool_name: str) -> Any | None:
    with _LOCK:
        return _HANDLERS.get(tool_name)


def workshop_handlers_snapshot() -> MappingProxyType[str, Any]:
    with _LOCK:
        return MappingProxyType(dict(_HANDLERS))


def workshop_handler_sources() -> MappingProxyType[str, str]:
    with _LOCK:
        return MappingProxyType(dict(_HANDLER_SOURCES))


def validate_workshop_handler_registry() -> dict[str, Any]:
    with _LOCK:
        unresolved = sorted(set(WORKSHOP_TOOL_OWNERS) - set(_HANDLERS))
        extra = sorted(set(_HANDLERS) - set(WORKSHOP_TOOL_OWNERS))
        return {
            "expected_count": len(WORKSHOP_TOOL_OWNERS),
            "bound_count": len(_HANDLERS),
            "unresolved": unresolved,
            "extra": extra,
            "complete": not unresolved and not extra,
        }


def clear_workshop_handlers_for_tests() -> None:
    with _LOCK:
        _HANDLERS.clear()
        _HANDLER_SOURCES.clear()
