# Known limitations

- Public Release Candidate / Developer Preview, licensed under Apache License 2.0.
- SQLite single-writer behavior limits concurrent mutation workloads.
- No built-in public multi-tenant onboarding or hosted SaaS control plane.
- Remote authentication currently supports one owner only. That single OAuth-authenticated owner receives `admin`; multi-user roles and delegated remote access are intentionally deferred.
- Compatibility-only schema/code from earlier development remains isolated and inactive.
- Semantic search may download a model and is not part of model-free startup.
- Model providers are optional, external and proposal-only.
- Docker and macOS are unverified.
- Documentation is English, while some inherited internal exceptions/comments remain in Polish.
- The canonical Ruff gate is limited to critical syntax/undefined-name classes; full import/style normalization remains pending.
- The exact MCP compatibility matrix is limited to the generic HTTP transport smoke gate.
