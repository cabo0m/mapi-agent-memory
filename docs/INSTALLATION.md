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

## Initialize a fresh instance

```bash
mapi-init
mapi-server
```

`mapi-init` is the supported day-zero path. The interactive wizard defaults to a local instance. It stores persistent runtime state outside the source checkout at `~/.mapi-agent-memory`, generates the runtime `.env`, creates data/backup/log directories, applies migrations, records only explicit neutral self-model bootstrap evidence and runs doctor checks. It never seeds demo data.

For automation, use `--non-interactive` plus explicit flags. For example:

```bash
mapi-init --non-interactive --mode local --agent-name MyAgent
```

For a server behind an authenticated TLS proxy:

```bash
mapi-init --mode vps-proxy --public-url https://mapi.example.com
```

The VPS bootstrap generates `generated/mapi.service` and `generated/reverse-proxy-security-template.txt` inside the instance root. On interactive Linux it offers to install and start the systemd service immediately using `sudo` when required. For non-interactive provisioning add `--install-service`; without that flag no privileged change is attempted. Firewall, DNS and the authenticated TLS proxy remain separate infrastructure boundaries.

After service installation MAPI waits for `127.0.0.1:<port>` and probes the MCP URL. The final JSON contains `connection.recommended_mcp_url`, and the terminal prints `MAPI MCP address: ...`. Public reachability is reported separately, so a missing proxy cannot be mistaken for a working public endpoint.

Use `mapi-init --resume` only to finish the same initialization. Resume is idempotent and fails if identity, project namespace, profile, port or remote-auth configuration differs from the existing `.env`. Configuration changes are deliberately not an init side effect.

After the runtime starts, run `python scripts/smoke_mcp.py`. Run `mapi-demo` separately when you want fictional product-demo data. If you choose a non-default instance root, pass `--root <path>` to runtime CLI commands such as `mapi-server`, `mapi-doctor`, `mapi-migrate`, `mapi-recover` and `mapi-seed-demo`.

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
