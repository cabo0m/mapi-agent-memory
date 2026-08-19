# Configuration

The authoritative neutral template is [`.env.example`](../.env.example). `mapi-init` generates the effective private `.env` for a new instance, normally under `~/.mapi-agent-memory`. Runtime commands load that file automatically; explicit process environment variables take precedence.

| Variable | Default | Required | Security impact |
|---|---|---:|---|
| `MAPI_INSTANCE_ROOT` | `~/.mapi-agent-memory` | no | Discovery root for the generated instance `.env` |
| `MAPI_ROOT` | instance root | no | Runtime root used for relative paths and generated state |
| `MAPI_DATA_DIR` | `<root>/data` | no | Contains private agent data |
| `MAPI_DB_PATH` | `./data/mapi.db` | no | Direct read access exposes memories |
| `MAPI_RUNTIME_HOST` | `127.0.0.1` | no | Non-loopback binds require auth and TLS |
| `MAPI_RUNTIME_PORT` | `8015` | no | Ensure the port is not publicly exposed |
| `MCP_SURFACE_PROFILE` | `agent` | no | Higher profiles expose more mutations |
| `MAPI_ADMIN_TOOLS_ENABLED` | `false` | no | Must be true before `admin` is effective |
| `MAPI_OWNER_KEY` | `owner` | no | Single-instance identity namespace |
| `MAPI_AGENT_SUBJECT_KEY` | `agent` | no | Stable subject key for Agent Self Model |
| `MAPI_AGENT_DISPLAY_NAME` | `Agent` | no | Display label only; not authorization |
| `MAPI_AGENT_PROJECT_KEY` | `agent-self` | no | Dedicated self-evidence namespace; keep separate from customer projects |
| `MAPI_SEMANTIC_ENABLED` | `false` | no | May trigger optional model use |
| `MAPI_EMBEDDING_MODEL` | MiniLM example | no | First use may download model files |
| `GEMINI_API_KEY` | empty | no | Secret; never commit |
| `MAPI_GEMINI_ENABLED` | `false` | no | Enables external provider eligibility |
| `MAPI_LOCAL_MODEL_ENABLED` | `false` | no | Enables local provider eligibility |
| `MAPI_LOCAL_MODEL_URL` | loopback | no | Do not point at untrusted endpoints |
| `MAPI_LOG_DIR` | `<root>/logs` | no | Runtime log destination; protect as operational data |
| `MAPI_LOG_LEVEL` | `INFO` | no | Debug logs may contain operational context |
| `MAPI_BACKUP_DIR` | `./backups` | no | Protect like the primary database |
| `MAPI_REQUEST_TIMEOUT_SECONDS` | `30` | no | Limits optional outbound provider waits |
| `MAPI_RECOVERY_COMMAND_JSON` | empty | no | Explicit local JSON argv for `mapi-recover --execute`; never shell-evaluated |

Environment variables are process configuration, not authorization. A payload field can never grant a higher profile.

## First-run configuration

Every initialized instance has a deterministic MCP URL: `http://127.0.0.1:<port>/mcp/` locally, or `<public HTTPS origin>/mcp/` for VPS modes. `mapi-init` and `mapi-server` print the recommended URL. A reported public URL is considered verified only when the endpoint probe succeeds.

`mapi-init` owns instance creation. Its generated file is private state and must not be committed. The default instance root is outside the source checkout. Local bootstrap may explicitly enable the admin surface only when `--profile admin` is chosen; VPS bootstrap rejects an admin profile. Resume never acts as an implicit configuration editor.

The generated self-model records are operational evidence from the operator's explicit configuration: one identity record and one namespace-separation guardrail. They are not demo memories or inferred personality traits.

## Remote authentication status

The runtime contains remote-auth integration points, but remote authentication is not part of the local quickstart and is classified as experimental in this public candidate. A supported remote deployment still requires a complete external identity and TLS boundary, tested token handling, restricted profile mapping and an unreachable admin surface. No private owner values, redirect URIs, endpoints or provider configuration are required or included.

## Agent Self Model

`MAPI_AGENT_SUBJECT_KEY`, `MAPI_AGENT_DISPLAY_NAME` and `MAPI_AGENT_PROJECT_KEY` configure the neutral, read-only self-model surfaces. The self-model is evidence-first: ordinary project notes are not promoted into identity merely because their text sounds personal. Explicit self evidence should live in the dedicated self project and use identity/autobiographical layers or explicit subject/self-model tags.

The self-model can feed Context Engine and source-bound Gravity. Those readers never create durable truth, relations, importance changes or recall changes on their own.

## Controlled Self Narrative

The deterministic self narrative uses only allowlisted claims derived from Agent Self Model evidence. With `provider=gemini`, the external model may only select known claim IDs through a strict JSON schema. It cannot return source memory IDs or prose; final text is rendered locally from approved claims. Provider calls are stateless (`store=false`) and use no tools or background execution.
