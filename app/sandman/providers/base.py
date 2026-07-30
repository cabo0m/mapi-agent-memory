from __future__ import annotations

from typing import Any, Mapping, Protocol


class SandmanProvider(Protocol):
    name: str
    kind: str
    capabilities: Mapping[str, bool]

    def analyze(self, request: Mapping[str, Any]) -> dict[str, Any]: ...


PROPOSAL_ONLY_CAPABILITIES = {
    "proposal_only": True,
    "supports_tools": False,
    "supports_mutation": False,
    "supports_queue_routing": False,
    "supports_external_network": False,
}
