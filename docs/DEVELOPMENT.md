# Development

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Focused workflow:

```bash
pytest tests/test_public_surface.py tests/test_public_packaging.py
ruff check .
mapi-capabilities
python scripts/audit_public_repository.py
git diff --check
```

Before a release candidate, run the full target test suite from a clean environment and repeat installation/migration/seed/doctor/server/MCP smoke outside the checkout. Do not use an editable install of another MAPI repository or an inherited `PYTHONPATH`.

Code changes require tests. Registry changes require regenerated `docs/CAPABILITIES.md`. Migration changes require fresh-chain and upgrade tests.

The canonical Ruff gate currently covers syntax-invalid constructs and undefined
names (`E9`, `F63`, `F7`, `F82`). Import ordering and full style normalization
remain tracked cleanup work; the initial public export intentionally avoids a
mass formatting refactor.
