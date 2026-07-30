# Security policy

## Supported versions

Only the latest public release candidate is evaluated. No stable security-support window is promised before the first licensed release.

## Reporting a vulnerability

Do not place credentials, private memories, database contents or exploit details
in a public issue. Please report security vulnerabilities privately through
GitHub Security Advisories. If that channel is unavailable, contact
info@morenatech.work.

Include affected version/commit, impact, minimal reproduction and mitigation ideas without real user data.

## Safe deployment defaults

Use loopback binding, the `agent` profile, disabled admin tools and disabled providers. Remote exposure requires TLS, authentication, profile mapping, filesystem protection, backups and logging review.

## Scope

Security support covers code in this repository and documented default configuration. It does not cover unreviewed reverse proxies, third-party clients, optional model providers or deployments that expose admin tools remotely.
