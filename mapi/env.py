from __future__ import annotations

"""Minimal environment-file loading for installed and source-checkout MAPI runtimes."""

import json
import os
import re
from pathlib import Path
from typing import Any

_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _decode_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
        return str(decoded)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def default_instance_root() -> Path:
    configured = str(os.environ.get("MAPI_INSTANCE_ROOT") or "").strip()
    return Path(configured).expanduser().resolve() if configured else (Path.home() / ".mapi-agent-memory").resolve()


def _environment_candidate(path: str | Path | None) -> Path:
    if path is not None:
        candidate = Path(path).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (Path.cwd() / candidate).resolve()
    explicit = str(os.environ.get("MAPI_ENV_FILE") or "").strip()
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (Path.cwd() / candidate).resolve()
    configured_root = str(os.environ.get("MAPI_ROOT") or "").strip()
    if configured_root:
        return (Path(configured_root).expanduser().resolve() / ".env").resolve()
    local = (Path.cwd() / ".env").resolve()
    if local.exists():
        return local
    return (default_instance_root() / ".env").resolve()


def parse_environment_file(path: str | Path) -> dict[str, str]:
    candidate = Path(path).expanduser().resolve()
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(candidate.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_KEY.fullmatch(key):
            raise ValueError(f"invalid_env_line:{line_number}")
        values[key] = _decode_value(raw_value)
    return values


def load_environment_file(path: str | Path | None = None, *, override: bool = False) -> dict[str, Any]:
    candidate = _environment_candidate(path)
    if not candidate.exists():
        return {"status": "missing", "path": str(candidate), "loaded_keys": []}

    loaded: list[str] = []
    for key, value in parse_environment_file(candidate).items():
        if override or key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return {"status": "loaded", "path": str(candidate), "loaded_keys": loaded}


def _resolve_from_root(value: str | None, *, root: Path, default: Path) -> Path:
    text = str(value or "").strip()
    if not text:
        return default.resolve()
    candidate = Path(text).expanduser()
    return (candidate if candidate.is_absolute() else root / candidate).resolve()


def apply_runtime_environment(path: str | Path | None = None) -> dict[str, Any]:
    loaded = load_environment_file(path)
    root = Path(os.environ.get("MAPI_ROOT") or (Path.cwd() if loaded["status"] == "missing" else Path(loaded["path"]).parent)).expanduser().resolve()
    data_dir = _resolve_from_root(os.environ.get("MAPI_DATA_DIR"), root=root, default=root / "data")
    db_path = _resolve_from_root(os.environ.get("MAPI_DB_PATH"), root=root, default=data_dir / "mapi.db")

    from app.runtime.context import configure_runtime_context

    context = configure_runtime_context(root=root, data_dir=data_dir, db_path=db_path)
    return {
        "status": "ok",
        "env_file": loaded,
        "root": str(context.root),
        "data_dir": str(context.data_dir),
        "db_path": str(context.db_path),
    }
