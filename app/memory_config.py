from __future__ import annotations

import os
from pathlib import Path


def _env_path(*names: str) -> Path | None:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return Path(value).expanduser().resolve()
    return None


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ROOT = _env_path("MAPI_ROOT", "ASSISTANT_ROOT") or _REPOSITORY_ROOT
DATA_DIR = _env_path("MAPI_DATA_DIR") or (ROOT / "data").resolve()
DB_PATH = _env_path("MAPI_DB_PATH", "DB_PATH") or (DATA_DIR / "mapi.db").resolve()

# Administrative file helpers, when explicitly enabled, remain confined to the
# repository root. Public defaults never grant arbitrary host filesystem roots.
ALLOWED_ROOTS = [ROOT.resolve()]
