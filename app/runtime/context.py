from __future__ import annotations

"""Single runtime path context shared by the local MAPI composition modules."""

from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from app import memory_config as config


@dataclass(frozen=True)
class RuntimeContext:
    root: Path
    data_dir: Path
    db_path: Path


_LOCK = RLock()
_CONTEXT = RuntimeContext(
    root=Path(config.ROOT).resolve(),
    data_dir=Path(config.DATA_DIR).resolve(),
    db_path=Path(config.DB_PATH).resolve(),
)


def get_runtime_context() -> RuntimeContext:
    with _LOCK:
        return _CONTEXT


def configure_runtime_context(
    *,
    root: str | Path | None = None,
    data_dir: str | Path | None = None,
    db_path: str | Path | None = None,
) -> RuntimeContext:
    """Update the explicit runtime paths and the legacy config compatibility view."""
    global _CONTEXT
    with _LOCK:
        current = _CONTEXT
        next_context = RuntimeContext(
            root=Path(root if root is not None else current.root).resolve(),
            data_dir=Path(data_dir if data_dir is not None else current.data_dir).resolve(),
            db_path=Path(db_path if db_path is not None else current.db_path).resolve(),
        )
        _CONTEXT = next_context
        config.ROOT = next_context.root
        config.DATA_DIR = next_context.data_dir
        config.DB_PATH = next_context.db_path
        return next_context


def runtime_root() -> Path:
    return get_runtime_context().root


def runtime_data_dir() -> Path:
    return get_runtime_context().data_dir


def runtime_db_path() -> Path:
    return get_runtime_context().db_path
