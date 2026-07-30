"""Canonical workshop manifests, handler ownership and runtime dispatch."""

from app.workshops.catalog import WORKSHOPS, WORKSHOP_PACKAGES, WORKSHOP_TOOL_OWNERS
from app.workshops.contracts import Workshop, WorkshopAction

__all__ = [
    "WORKSHOPS",
    "WORKSHOP_PACKAGES",
    "WORKSHOP_TOOL_OWNERS",
    "Workshop",
    "WorkshopAction",
]
