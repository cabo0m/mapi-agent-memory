from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any


class MAPIConnectionError(RuntimeError):
    """Raised only when the MCP client cannot establish its initial connection."""


def _data(result: Any) -> dict[str, Any]:
    value = getattr(result, "data", None)
    if isinstance(value, dict):
        return value
    content = getattr(result, "content", None) or []
    if content and hasattr(content[0], "text"):
        parsed = json.loads(content[0].text)
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError(f"MCP tool did not return an object: {result!r}")


async def smoke(url: str, project_key: str) -> dict[str, Any]:
    from contextlib import AsyncExitStack

    from fastmcp import Client

    async with AsyncExitStack() as stack:
        try:
            client = await stack.enter_async_context(Client(url))
        except RuntimeError as exc:
            raise MAPIConnectionError(str(exc)) from exc

        tools = await client.list_tools()
        names = [tool.name for tool in tools]
        required = {
            "bootstrap_agent_context",
            "open_workshop",
            "run_workshop_action",
            "save_memory",
            "find_memories",
            "get_memory",
            "get_memory_links",
        }
        missing = sorted(required - set(names))
        if missing:
            raise RuntimeError(f"Required tools are missing: {missing}")
        if "query_sql" in names:
            raise RuntimeError("Admin SQL tool is visible on the default surface")

        bootstrap = _data(
            await client.call_tool(
                "bootstrap_agent_context",
                {"project_key": project_key, "limit": 3},
            )
        )
        if bootstrap.get("status") != "ok":
            raise RuntimeError(f"Bootstrap failed: {bootstrap}")

        created = _data(
            await client.call_tool(
                "save_memory",
                {
                    "content": "The public MCP smoke stores a fictional verification note.",
                    "project_key": project_key,
                    "source_event_ref": "public:mcp-smoke:v1",
                },
            )
        )
        if created.get("status") not in {"created", "duplicate_existing"}:
            raise RuntimeError(f"Memory write failed: {created}")
        memory_id = int(created["memory_id"])

        found = _data(
            await client.call_tool(
                "find_memories",
                {"text_query": "public MCP smoke", "project_key": project_key},
            )
        )
        if not any(int(item["id"]) == memory_id for item in found.get("items", [])):
            raise RuntimeError("Written memory was not found")
        loaded = _data(await client.call_tool("get_memory", {"memory_id": memory_id}))
        if int(loaded["memory"]["id"]) != memory_id:
            raise RuntimeError("Read returned the wrong memory")
        links = _data(await client.call_tool("get_memory_links", {"memory_id": memory_id}))
        if "links" not in links:
            raise RuntimeError("Links payload is missing")

        timeline = _data(await client.call_tool("open_workshop", {"area": "timeline"}))
        timeline_result = _data(
            await client.call_tool(
                "run_workshop_action",
                {
                    "area": "timeline",
                    "action": "search_verbatim",
                    "payload": {
                        "query": "public MCP smoke",
                        "scope": "memories",
                        "project_key": project_key,
                        "limit": 5,
                    },
                },
            )
        )
        denied = _data(await client.call_tool("open_workshop", {"area": "admin"}))
        if (
            timeline.get("status") != "ok"
            or timeline_result.get("status") != "ok"
            or denied.get("status") != "denied"
        ):
            raise RuntimeError("Surface permission smoke failed")

        return {
            "status": "ok",
            "tool_count": len(names),
            "memory_id": memory_id,
            "timeline_actions": len(timeline.get("actions", [])),
            "timeline_search_status": timeline_result["status"],
            "admin_status": denied["status"],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the model-free public MAPI MCP smoke test.")
    parser.add_argument("--url", default="http://127.0.0.1:8015/mcp/")
    parser.add_argument("--project-key", default="demo-project")
    args = parser.parse_args()
    try:
        result = asyncio.run(smoke(args.url, args.project_key))
    except MAPIConnectionError:
        raise SystemExit(
            f"Could not connect to MAPI at {args.url}.\n"
            "Start the server in another terminal with `mapi-server`, then rerun this command."
        ) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
