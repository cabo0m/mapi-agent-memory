from __future__ import annotations

import sys

import pytest

from scripts import smoke_mcp


def test_main_explains_that_the_server_must_be_running(monkeypatch) -> None:
    async def fail_to_connect(url: str, project_key: str) -> dict[str, object]:
        raise smoke_mcp.MAPIConnectionError(
            "Client failed to connect: All connection attempts failed"
        )

    monkeypatch.setattr(smoke_mcp, "smoke", fail_to_connect)
    monkeypatch.setattr(
        sys,
        "argv",
        ["smoke_mcp.py", "--url", "http://127.0.0.1:65534/mcp/"],
    )

    with pytest.raises(SystemExit) as error:
        smoke_mcp.main()

    assert str(error.value) == (
        "Could not connect to MAPI at http://127.0.0.1:65534/mcp/.\n"
        "Start the server in another terminal with `mapi-server`, then rerun this command."
    )


def test_main_does_not_hide_unrelated_runtime_errors(monkeypatch) -> None:
    async def fail_during_smoke(url: str, project_key: str) -> dict[str, object]:
        raise RuntimeError("Database failed to connect while reading a memory")

    monkeypatch.setattr(smoke_mcp, "smoke", fail_during_smoke)
    monkeypatch.setattr(sys, "argv", ["smoke_mcp.py"])

    with pytest.raises(RuntimeError, match="Database failed to connect"):
        smoke_mcp.main()
