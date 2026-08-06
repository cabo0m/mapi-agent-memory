# MCP integration

## Endpoint and verified transport

- HTTP MCP transport;
- local endpoint: `http://127.0.0.1:8015/mcp/`;
- safe default profile: `agent`;
- protocol check: `python scripts/smoke_mcp.py`.

The smoke performs an MCP initialize/list/call flow through the FastMCP client. It verifies the required public tools, bootstrap, explicitly authorized fictional write, search, read, links, timeline access and admin denial.

## Codex

The current official Codex manual documents Streamable HTTP servers in
`~/.codex/config.toml` or a trusted project's `.codex/config.toml`. The verified
configuration shape is:

```toml
[mcp_servers.mapi]
url = "http://127.0.0.1:8015/mcp/"
```

1. Install, migrate, seed and start `mapi-server`.
2. Add the server block to the Codex configuration used by your installation.
3. Reload or restart Codex so it reconnects to MCP servers.
4. Run `codex mcp list` or use `/mcp`, then confirm the tool list includes `bootstrap_agent_context`, `find_memories`, `get_memory`, `get_memory_links` and, under `agent`, `save_memory`.
5. Begin each project workflow with `bootstrap_agent_context` and an explicit `project_key`.
6. Search with `find_memories` before saving a new memory; inspect full records and links before relying on them.

The format and client flow were checked against the current official OpenAI Codex MCP
manual on 2026-08-06; the repository separately validates the MAPI endpoint and protocol
surface. Codex releases can change configuration locations or UI, so check the
[current Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp) if the server
does not appear.

## ChatGPT desktop

MAPI exposes Streamable HTTP MCP on localhost. The current official Codex MCP manual
documents shared MCP configuration for ChatGPT desktop, Codex CLI and the IDE extension.
Availability can still depend on the distributed application version and workspace
controls. In a build that exposes MCP server settings:

1. keep MAPI bound to `127.0.0.1`;
2. open **Settings -> MCP servers**, select **Add server**, choose **Streamable HTTP** and add `http://127.0.0.1:8015/mcp/`;
3. reload the application;
4. inspect the available tools and perform bootstrap/search before any write.

This developer preview does not claim compatibility with every ChatGPT desktop build or plan.

## ChatGPT web

The web application cannot connect directly to `127.0.0.1` on your computer. A web integration requires a remotely reachable HTTPS MCP endpoint, authentication and a secure network boundary. Keep the admin workshop disabled and unreachable remotely. Preserve MCP HTTP streaming at the reverse proxy and use one controlled SQLite writer.

The local quickstart is not a public-hosting or authentication tutorial. Do not expose the local port directly to the internet.

## Generic MCP client

```json
{
  "mcpServers": {
    "mapi": {
      "url": "http://127.0.0.1:8015/mcp/",
      "transport": "http"
    }
  }
}
```

Some clients use different field names. The URL and HTTP protocol are the interoperable parts verified by the smoke.

## Read and write contract

- bootstrap: `bootstrap_agent_context`;
- read: `find_memories` -> `get_memory` -> `get_memory_links`;
- explicit durable write: `save_memory` after user/client authorization;
- uncertain agent material: `propose_memory`;
- lifecycle: preview, retain the returned hash, then guarded apply with an authorized profile;
- audit: inspect current state, links, timeline and run records.

## Profiles and errors

`reader` is read-only, `agent` enables ordinary explicit memory/proposal workflows, `maintainer` enables controlled maintenance, and `admin` exposes dangerous local operations only when separately enabled. A payload cannot grant a higher profile. Unknown tools can be hidden; workshop calls can return `denied`, validation failures, stale-preview failures or missing-capability status.

Clients should use stable source/operation references where schemas expose them, retain preview hashes and never infer successful mutation from a timeout.
