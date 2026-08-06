from __future__ import annotations

from mapi.demo import run_demo_database, run_isolated_demo
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
