# Troubleshooting

## `mapi-doctor` reports `attention`

Run `mapi-migrate`, verify `MAPI_DB_PATH`, filesystem permissions and `PRAGMA quick_check`. Registry failures usually mean a manifest action lacks a bound handler.

## Port 8015 is unavailable

Stop the conflicting process or choose `MAPI_RUNTIME_PORT`. Keep the bind on loopback unless a secure deployment boundary exists.

## Semantic search is unavailable

Install `.[semantic]`. The first model use may require network access and disk space. Lexical retrieval remains available without it.

## Gemini is unavailable

Install `.[gemini]`, set a key outside Git and explicitly enable the provider. The model-free core does not need Gemini.

## Admin profile becomes maintainer

This is intentional. Set both `MCP_SURFACE_PROFILE=admin` and `MAPI_ADMIN_TOOLS_ENABLED=true` in a trusted local process.

## SQLite is locked

Ensure only one writer is active, stop abandoned processes and avoid placing SQLite on a network filesystem. Do not delete lock-related files blindly.

## MCP plain HTTP request returns a protocol error

Use an MCP-aware client. A plain browser or `curl` request does not include the required MCP protocol envelope.
