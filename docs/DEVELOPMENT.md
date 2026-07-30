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

CI keeps the default installation model-free. The four Windows/Linux and
Python 3.11/3.12 core jobs install `.[dev]` and run:

```bash
pytest -m "not semantic"
```

The optional semantic contract is tested separately on Windows and Linux with
Python 3.12:

```bash
pip install -e ".[dev,semantic]"
pytest -m semantic
```

Semantic tests use deterministic local embeddings and offline guards. They
exercise the declared `sqlite-vec` extension without downloading an embedding
model or calling an external provider. Together the core and semantic jobs
cover the complete suite.

Before a release candidate, run the full target test suite from a clean environment and repeat installation/migration/seed/doctor/server/MCP smoke outside the checkout. Do not use an editable install of another MAPI repository or an inherited `PYTHONPATH`.

Code changes require tests. Registry changes require regenerated `docs/CAPABILITIES.md`. Migration changes require fresh-chain and upgrade tests.

The canonical Ruff gate currently covers syntax-invalid constructs and undefined
names (`E9`, `F63`, `F7`, `F82`). Import ordering and full style normalization
remain tracked cleanup work; the initial public export intentionally avoids a
mass formatting refactor.
