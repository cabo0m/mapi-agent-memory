# Installation

## Tested systems

- Windows 11 with Python 3.12.10;
- Ubuntu 24.04 under WSL2 with the Python version recorded in `PUBLIC_RELEASE_AUDIT.md`.

macOS is expected to work for the model-free core but is not verified.

## Prerequisites

- Git for source checkout;
- Python 3.11 or 3.12;
- a writable local data directory.

No model provider, API key, GPU or model download is required.
The core package includes lightweight `tzdata` so named IANA timezones remain
available when the operating system does not provide a timezone database.

## Source and development installation

```bash
git clone https://github.com/cabo0m/mapi-agent-memory.git
cd mapi-agent-memory
python -m venv .venv
```

Activate the environment, then:

```bash
python -m pip install --upgrade pip
pip install -e .
```

For tests and lint:

```bash
pip install -e ".[dev]"
```

## Initialize

```bash
mapi-migrate
mapi-seed-demo
mapi-doctor
mapi-server
```

In another activated shell, run `python scripts/smoke_mcp.py` and `mapi-demo`.

## Optional extras

```bash
pip install -e ".[semantic]"
pip install -e ".[gemini]"
```

Install extras only when the feature will be configured and tested.
The semantic extra may install model libraries, but core installation and core
CI do not install or import them.

## Upgrade

1. Stop the runtime.
2. Back up the SQLite database and verify the backup.
3. Update source and environment.
4. Run `mapi-migrate`.
5. Run `mapi-doctor`.
6. Start the runtime and perform an MCP smoke test.

Migrations are forward-only. Roll back code and restore the verified pre-upgrade database if an upgrade fails.

## Uninstall and local data deletion

Stop MAPI, uninstall the package with `pip uninstall mapi-agent-memory`, then remove the virtual environment. Data is retained separately; delete the configured data directory only after confirming backups and the exact path.
