# MCP integration

## Endpoint and transport

- transport: HTTP MCP;
- default endpoint: `http://127.0.0.1:8015/mcp/`;
- default profile: `agent`.

Generic configuration:

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

## Bootstrap

Call `bootstrap_agent_context`, then `open_workshop` for the relevant area. Search before writing and inspect full records plus links before relying on them.

## Read/write flow

- read: `find_memories` -> `get_memory` -> `get_memory_links`;
- explicit write: `save_memory` when the user/client has authorized persistence;
- uncertain agent write: `propose_memory`;
- activation: `recall_memory`;
- maintenance: preview first, then use an authorized apply action.

## Profiles

`reader` is read-only, `agent` enables ordinary memory/proposal workflows, `maintainer` enables controlled maintenance and `admin` exposes dangerous local operations only when explicitly enabled.

## Errors

Unknown tools may be hidden rather than returning permission details. Workshop calls can return `denied`, validation errors, stale-preview errors or missing-capability status.

## Idempotency

Clients should supply stable operation/source references when schemas expose them, retain preview hashes and avoid retrying non-idempotent writes without checking audit state.

## Client responsibilities

Protect credentials, choose the correct profile, distinguish explicit writes from proposals, avoid sending unnecessary sensitive text to optional providers and never infer successful mutation from a timeout.
