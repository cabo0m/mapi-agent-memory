from __future__ import annotations

import importlib

import mcp_surface


def test_unknown_profile_fails_closed_to_reader() -> None:
    assert mcp_surface.normalize_surface_profile("unknown") == "reader"


def test_admin_requires_explicit_enablement(monkeypatch) -> None:
    monkeypatch.delenv("MAPI_ADMIN_TOOLS_ENABLED", raising=False)
    assert mcp_surface.normalize_surface_profile("admin") == "maintainer"
    monkeypatch.setenv("MAPI_ADMIN_TOOLS_ENABLED", "true")
    assert mcp_surface.normalize_surface_profile("admin") == "admin"


def test_public_profiles_are_vendor_neutral() -> None:
    assert tuple(mcp_surface.SURFACE_PROFILES) == ("reader", "agent", "maintainer", "admin")


def test_agent_surface_hides_admin_workshop(monkeypatch) -> None:
    monkeypatch.setenv("MCP_SURFACE_PROFILE", "agent")
    areas = {item["area"] for item in mcp_surface.workshop_index()}
    assert "admin" not in areas


def test_core_imports_without_optional_model_packages(monkeypatch) -> None:
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args, **kwargs):
        if name in {"google.genai", "sentence_transformers", "sqlite_vec"}:
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    import server

    assert server.mcp is not None
