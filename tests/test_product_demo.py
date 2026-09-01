from __future__ import annotations

from mapi.demo import (
    _require_truthful_assertions,
    run_all_product_proofs,
    run_conflict_provenance_proof,
    run_decision_supersession_proof,
    run_demo_database,
    run_isolated_demo,
    run_product_proof_cli,
)
from scripts.smoke_mcp_lifecycle import lifecycle_smoke


def test_product_demo_resolves_current_state_and_history(tmp_path) -> None:
    result = run_demo_database(tmp_path / "demo.db")
    assert result["status"] == "ok"
    assert result["relation"] == "supersedes"
    assert result["current_memory_id"] != result["previous_memory_id"]
    assert len(result["preview_hash"]) == 64
    assert "Current decision: PostgreSQL" in result["human_output"]
    assert "Previous decision: SQLite" in result["human_output"]


def test_product_demo_default_database_is_temporary() -> None:
    result = run_isolated_demo()
    assert result["status"] == "ok"
    assert "mapi-project-memory-demo-" in result["database"]


def test_lifecycle_smoke_preserves_profile_and_project_boundaries(tmp_path) -> None:
    result = lifecycle_smoke(tmp_path / "lifecycle.db")
    assert result["status"] == "ok"
    assert result["profile"] == "maintainer"
    assert result["cross_project_status"] == "blocked"
    assert result["admin_status"] == "denied"


def test_decision_supersession_proof_is_stable_across_two_runs() -> None:
    first = run_decision_supersession_proof()
    second = run_decision_supersession_proof()

    assert first == second
    assert first["status"] == "passed"
    assert first["assertions"]["single_current_head"] is True
    assert first["assertions"]["history_preserved"] is True
    assert first["assertions"]["supersedes_relation_present"] is True


def test_conflict_provenance_proof_is_stable_across_two_runs() -> None:
    first = run_conflict_provenance_proof()
    second = run_conflict_provenance_proof()

    assert first == second
    assert first["status"] == "passed"
    assert first["resolution"]["automatic_winner_selected"] is False
    assert all(value is True for name, value in first["assertions"].items() if name != "external_calls")
    assert first["assertions"]["external_calls"] == 0


def test_product_proof_suite_reports_both_scenarios() -> None:
    result = run_all_product_proofs()

    assert result["status"] == "passed"
    assert [proof["scenario"] for proof in result["proofs"]] == [
        "decision_supersession",
        "conflict_provenance",
    ]


def test_truthful_assertion_guard_does_not_treat_false_as_zero() -> None:
    import pytest

    with pytest.raises(RuntimeError, match="conflict_review_outcome"):
        _require_truthful_assertions(
            {
                "conflict_review_outcome": False,
                "external_calls": 0,
            }
        )


def test_product_proof_cli_returns_nonzero_and_json_on_error(capsys) -> None:
    import json

    def broken_proof():
        raise RuntimeError("synthetic proof failure")

    exit_code = run_product_proof_cli(broken_proof)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["error_type"] == "RuntimeError"
    assert "FAILED" in captured.err
