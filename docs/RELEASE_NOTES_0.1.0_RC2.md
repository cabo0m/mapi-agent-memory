# MAPI v0.1.0-rc2

This is a **Public Release Candidate / Developer Preview**, not a production release.

## Highlights

- Repositioned MAPI as persistent, auditable project memory for Codex, ChatGPT and other MCP clients.
- Rebuilt the README around the user problem, honest product status and a complete local quickstart.
- Added named Codex, ChatGPT desktop, ChatGPT web and generic MCP integration guidance.
- Added a model-free, isolated decision-change demo using guarded preview, apply and current-state resolution.
- Expanded protocol verification and added a controlled lifecycle smoke path.
- Corrected licensing and clarified remote authentication, deployment and SQLite boundaries.
- Added neutral public-directory copy, comparison guidance and privacy-conscious issue templates.

## Compatibility

- Python 3.11 and 3.12 remain the supported interpreter range.
- Default transport remains HTTP MCP at `http://127.0.0.1:8015/mcp/`.
- Default surface remains `agent`; admin remains separately gated.
- Existing databases continue through the ordered migration chain.
- Core operation remains model-free; semantic and provider extras remain optional.

## Upgrade from rc1

1. Stop MAPI and create a verified SQLite backup.
2. Update the source checkout and activate the existing environment.
3. Run `pip install -e .`, `mapi-migrate` and `mapi-doctor`.
4. Start `mapi-server`, then run `python scripts/smoke_mcp.py`.
5. Run `mapi-demo` to verify current-state and preserved-history behavior independently of existing data.

## Fresh installation from the release tag

```bash
git clone --branch v0.1.0-rc2 --depth 1 https://github.com/cabo0m/mapi-agent-memory.git
cd mapi-agent-memory
python -m venv .venv
```

Continue with the platform-specific activation and quickstart in the
[README](https://github.com/cabo0m/mapi-agent-memory#install-and-run).

Independent installation feedback is collected in
[External installation test: MAPI v0.1.0-rc2](https://github.com/cabo0m/mapi-agent-memory/issues/1).

## Known limitations

No hosted SaaS or public multi-user onboarding is included. ChatGPT web requires a separately secured remote deployment. Remote authentication remains outside the quickstart. SQLite has single-writer characteristics. Docker and macOS remain unverified.
