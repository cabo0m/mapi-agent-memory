from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import memory_config  # noqa: E402

print(
    json.dumps(
        {
            "root": str(memory_config.ROOT),
            "data_dir": str(memory_config.DATA_DIR),
            "db_path": str(memory_config.DB_PATH),
            "allowed_roots": [str(path) for path in memory_config.ALLOWED_ROOTS],
        },
        sort_keys=True,
    )
)
