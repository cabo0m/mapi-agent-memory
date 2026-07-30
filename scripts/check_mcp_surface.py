from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import types
from pathlib import Path


def _install_fastmcp_introspection_stub() -> None:
    if "fastmcp" in sys.modules:
        return
    try:
        __import__("fastmcp")
        return
    except ModuleNotFoundError:
        pass

    module = types.ModuleType("fastmcp")

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    class FastMCP:
        def __init__(self, name: str) -> None:
            self.name = name
            self._tools: list[_Tool] = []

        def tool(self, fn=None):
            def decorate(func):
                self._tools.append(_Tool(func.__name__))
                return func

            if fn is None:
                return decorate
            return decorate(fn)

        async def list_tools(self):
            return list(self._tools)

        def run(self, *args, **kwargs) -> None:
            return None

    module.FastMCP = FastMCP
    module.__agent_introspection_stub__ = True
    sys.modules["fastmcp"] = module


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Print visible MAPI MCP tools for a surface profile.")
    parser.add_argument("--profile", default=None, help="public, clean_operator, admin or debug")
    parser.add_argument("--workshops", action="store_true", help="Include workshop index")
    args = parser.parse_args()

    if args.profile:
        os.environ["MCP_SURFACE_PROFILE"] = args.profile

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))

    _install_fastmcp_introspection_stub()

    import server_core
    import mcp_surface

    payload = mcp_surface.surface_manifest()
    if getattr(sys.modules.get("fastmcp"), "__agent_introspection_stub__", False):
        tool_names = mcp_surface.visible_tool_order(payload["profile"])
    else:
        tools = await server_core.mcp.list_tools()
        tool_names = [tool.name for tool in tools]
    payload["tool_count"] = len(tool_names)
    payload["tools"] = tool_names
    if args.workshops:
        payload["workshops"] = mcp_surface.workshop_index()
    else:
        payload.pop("workshops", None)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
