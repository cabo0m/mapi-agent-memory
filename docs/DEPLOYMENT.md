# Deployment

## First server bootstrap

Use `mapi-init` rather than composing first-run state by hand. `mapi-init --mode vps-proxy --public-url https://mapi.example.com` creates the database, migrations, instance configuration and operator artifacts while keeping the MCP runtime on loopback. `vps-remote-auth` additionally validates the built-in OAuth/PKCE remote-auth configuration and requires an HTTPS redirect allowlist plus a trusted proxy-injected identity value.

Neither VPS mode performs root actions. Review `generated/mapi.service` and the reverse-proxy security template, install them through your normal privileged deployment process, then run `mapi-doctor` and the MCP smoke. The proxy must authenticate requests and terminate TLS. A generated template is intentionally incomplete until that authentication boundary is configured.

## Localhost development

The supported default is `127.0.0.1:8015`, one MAPI process and one SQLite writer.

## Private LAN or single server

Bind beyond loopback only behind an authenticated reverse proxy with TLS. Keep `admin` disabled remotely. Restrict filesystem permissions for the data, backup and log directories. Use a persistent local volume and avoid network filesystems for SQLite.

Remote authentication integration remains experimental in this release and is outside the quickstart. Treat the reverse proxy and identity provider as a separate security boundary; do not accept a requested profile from an untrusted MCP payload.

## Reverse proxy

Preserve HTTP streaming required by MCP, enforce request limits/timeouts and terminate TLS. Authentication must map to an allowed profile outside untrusted payloads.

## Persistence and backups

Mount a persistent directory for `MAPI_DATA_DIR`. Use SQLite-consistent backups, checksums and restore drills. Expect one writer; scale readers or services only after designing database coordination.

## Health and shutdown

Run `mapi-doctor` and a protocol-aware MCP smoke test. Configure graceful process termination and wait for SQLite transactions to close.

## Upgrade and rollback

Back up, stop, upgrade, migrate, doctor and smoke test. Restore both prior code and the matching verified database backup for rollback.

## Docker

Docker packaging is intentionally omitted because this candidate has not completed a tested non-root image and volume/health-check gate. Decorative container files would create a false support claim.
