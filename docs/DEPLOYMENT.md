# Deployment

## First server bootstrap

Use `mapi-init` rather than composing first-run state by hand. `mapi-init --mode vps-proxy --public-url https://mapi.example.com` creates the database, migrations, instance configuration and operator artifacts while keeping the MCP runtime on loopback. `vps-remote-auth` is the single-owner deployment mode: it validates OAuth/PKCE, requires an HTTPS redirect allowlist plus a trusted proxy-injected owner identity, and maps that one authenticated owner directly to the `admin` profile.

VPS mode can install and start the generated systemd service as part of `mapi-init`. Use `--service-name <name>` to avoid collisions on shared hosts; the normalized unit name is persisted in the instance configuration and reused by `--resume`. Interactive installation offers service installation automatically; provisioning scripts use `--install-service`. First-run also creates and verifies a SQLite-consistent initial backup. The installer never modifies firewall or DNS and does not silently publish an unauthenticated proxy. The proxy must authenticate requests and terminate TLS. A generated proxy template is intentionally incomplete until that authentication boundary is configured.

The installer reports both the loopback MCP URL and the final public MCP URL. It marks the public endpoint reachable only after an HTTP probe succeeds; otherwise the returned address is the configured target that still needs the external proxy boundary.

## Localhost development

The supported default is `127.0.0.1:8015`, one MAPI process and one SQLite writer.

## Private LAN or single server

Keep the MAPI origin on loopback behind an authenticated reverse proxy with TLS. In `vps-remote-auth` the single authenticated owner maps to `admin`; do not expose that origin without the authentication boundary. Restrict filesystem permissions for the data, backup and log directories. Use a persistent local volume and avoid network filesystems for SQLite.

Treat the reverse proxy and identity provider as a separate security boundary; do not accept a requested profile from an untrusted MCP payload.

## Reverse proxy

Preserve HTTP streaming required by MCP, enforce request limits/timeouts and terminate TLS. Authentication must map to an allowed profile outside untrusted payloads.

## Persistence and backups

Mount a persistent directory for `MAPI_DATA_DIR`. `mapi-init` creates and verifies the first SQLite-consistent backup automatically; ongoing backup scheduling, retention and restore drills remain deployment operations. Expect one writer; scale readers or services only after designing database coordination.

## Health and shutdown

Run `mapi-doctor` and a protocol-aware MCP smoke test. Configure graceful process termination and wait for SQLite transactions to close.

## Upgrade and rollback

Back up, stop, upgrade, migrate, doctor and smoke test. Restore both prior code and the matching verified database backup for rollback.

## Docker

Docker packaging is intentionally omitted because this candidate has not completed a tested non-root image and volume/health-check gate. Decorative container files would create a false support claim.
