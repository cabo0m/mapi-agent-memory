from __future__ import annotations

import json
from typing import Any

import mcp_surface


def test_propose_memory_capture_classifies_project_decision(server: Any) -> None:
    result = server.propose_memory_capture(
        content="Zapamiętaj: ustalmy, że ProjectCard ma bazować na project_brief i nie tworzy nowego storage.",
        project_key="mapi",
        source_context="Rozmowa o wdrożeniu pamięci v2",
    )

    proposal = result["proposal"]
    assert result["status"] == "proposed"
    assert "decision" in result["signals"]
    assert proposal["memory_type"] == "project_decision"
    assert proposal["entry_type"] == "decision"
    assert proposal["truth_kind"] == "decision"
    assert proposal["memory_v2_status"] == "proposed"
    assert proposal["requires_user_confirmation"] is True
    assert proposal["project_key"] == "mapi"


def test_propose_memory_capture_skips_transient_trivia(server: Any) -> None:
    result = server.propose_memory_capture(content="ok")

    assert result["status"] == "skipped"
    assert result["skip_reason"] in {"transient_trivia", "too_little_signal"}


def test_create_memory_from_proposal_approves_and_persists_active_memory(server: Any) -> None:
    proposal = server.propose_memory_capture(
        content="Zapamiętaj: the owner woli krótkie podsumowania z jasnym następnym krokiem.",
        source_context="Preferencja użytkownika",
    )["proposal"]

    created = server.create_memory_from_proposal(
        proposal_json=json.dumps(proposal),
        summary_short="Preferencja krótkich podsumowań",
    )

    memory = created["memory"]
    assert created["status"] == "created"
    assert memory["summary_short"] == "Preferencja krótkich podsumowań"
    assert memory["entry_type"] == "user_profile"
    assert memory["truth_kind"] == "preference"
    assert memory["memory_v2_status"] == "active"
    assert memory["requires_user_confirmation"] is False
    audit = server.list_memory_audit(int(memory["id"]), event_type_prefix="memory_v2.")
    event_types = [item["event_type"] for item in audit["items"]]
    assert "memory_v2.created" in event_types
    assert "memory_v2.proposal_approved" in event_types


def test_memory_workshop_exposes_capture_proposal_actions() -> None:
    agent = mcp_surface.open_workshop_payload("memory", profile="agent")
    agent_actions = {item["action"] for item in agent["actions"]}
    assert {"capture_proposal", "propose"} <= agent_actions
    assert "capture_save" not in agent_actions
    assert "create_from_proposal" not in agent_actions

    maintainer = mcp_surface.open_workshop_payload("memory", profile="maintainer")
    maintainer_actions = {item["action"] for item in maintainer["actions"]}
    assert {"capture_save", "create_from_proposal"} <= maintainer_actions


def test_run_workshop_action_supports_capture_proposal(server: Any) -> None:
    result = server.run_workshop_action(
        "memory",
        "capture_proposal",
        payload={
            "content": "Zapamiętaj: błąd logowania po restarcie wymaga sprawdzenia rate limitu.",
            "project_key": "mapi",
        },
    )

    assert result["status"] == "ok"
    assert result["tool_name"] == "propose_memory_capture"
    assert result["result"]["status"] == "proposed"
