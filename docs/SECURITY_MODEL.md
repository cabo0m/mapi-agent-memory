# Security model

## Trust boundaries

The MCP client is trusted only for the profile granted by process configuration or an external authentication layer. Tool payloads are untrusted. Model-provider output is untrusted. SQLite, backups and logs contain sensitive user data.

## Safe defaults

- bind to `127.0.0.1`;
- profile `agent`;
- admin tools disabled unless both the profile and explicit gate are enabled;
- external providers and semantic model downloads disabled;
- no public onboarding or self-registration;
- no shell, arbitrary SQL, file mutation, Git or migration action for ordinary clients.

Unknown profiles and unknown action requirements fail closed.

## Risk classes

- `R0`: read or deterministic preview;
- `R1`: limited explicit write;
- `R2`: maintenance mutation requiring a maintainer;
- `R3`: dangerous local administration, often requiring backup evidence.

## Dangerous tools

The `admin` workshop includes filesystem, SQL, process and migration functions. It is available locally only with the explicit admin gate, and remotely only to the single OAuth-authenticated owner in `vps-remote-auth` mode. Do not expose it through an unauthenticated reverse proxy.

## Provider trust

Provider requests are minimized and redacted. Provider responses are proposals, schema-validated and never treated as external facts. Basic startup and lexical memory operations make no provider calls.

## Data protection

Protect the SQLite file and backups with operating-system permissions and encrypted storage where appropriate. Do not place them inside the repository. Audit logs must not contain credentials or authorization headers.

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| Profile spoofing in payload | Profiles come from runtime/auth context, never payload |
| Remote admin exposure | Loopback origin, built-in OAuth, external owner identity boundary, explicit admin gate, single-owner mapping |
| Destructive lifecycle action | Preview hashes, profile checks, audit and rollback records |
| Provider hallucination | Proposal-only contract and evidence allowlists |
| Secret committed to Git | Public audit, `.gitignore`, CI scan |
| Database copied into release | Exact file manifest and forbidden binary/database rules |
| Concurrent writers | Writer guard and SQLite single-writer guidance |

Remote authentication is part of the application contract in `vps-remote-auth` mode: one owner OAuth identity maps to `admin`, while the origin remains loopback-only behind HTTPS and the trusted identity boundary.
