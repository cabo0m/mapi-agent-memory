# Configuration

The authoritative neutral template is [`.env.example`](../.env.example). The core quickstart does not require an environment file.

| Variable | Default | Required | Security impact |
|---|---|---:|---|
| `MAPI_DATA_DIR` | `./data` | no | Contains private agent data |
| `MAPI_DB_PATH` | `./data/mapi.db` | no | Direct read access exposes memories |
| `MAPI_RUNTIME_HOST` | `127.0.0.1` | no | Non-loopback binds require auth and TLS |
| `MAPI_RUNTIME_PORT` | `8015` | no | Ensure the port is not publicly exposed |
| `MCP_SURFACE_PROFILE` | `agent` | no | Higher profiles expose more mutations |
| `MAPI_ADMIN_TOOLS_ENABLED` | `false` | no | Must be true before `admin` is effective |
| `MAPI_OWNER_KEY` | `owner` | no | Single-instance identity namespace |
| `MAPI_SEMANTIC_ENABLED` | `false` | no | May trigger optional model use |
| `MAPI_EMBEDDING_MODEL` | MiniLM example | no | First use may download model files |
| `GEMINI_API_KEY` | empty | no | Secret; never commit |
| `MAPI_GEMINI_ENABLED` | `false` | no | Enables external provider eligibility |
| `MAPI_LOCAL_MODEL_ENABLED` | `false` | no | Enables local provider eligibility |
| `MAPI_LOCAL_MODEL_URL` | loopback | no | Do not point at untrusted endpoints |
| `MAPI_LOG_LEVEL` | `INFO` | no | Debug logs may contain operational context |
| `MAPI_BACKUP_DIR` | `./backups` | no | Protect like the primary database |
| `MAPI_REQUEST_TIMEOUT_SECONDS` | `30` | no | Limits optional outbound provider waits |

Environment variables are process configuration, not authorization. A payload field can never grant a higher profile.

## Remote authentication status

The runtime contains remote-auth integration points, but remote authentication is not part of the local quickstart and is classified as experimental in this public candidate. A supported remote deployment still requires a complete external identity and TLS boundary, tested token handling, restricted profile mapping and an unreachable admin surface. No private owner values, redirect URIs, endpoints or provider configuration are required or included.
