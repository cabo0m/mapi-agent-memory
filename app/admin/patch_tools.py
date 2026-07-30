from __future__ import annotations

"""Safe marker-based text patch helpers for MAPI admin tools.

These helpers are intentionally narrower than arbitrary shell or full-file
writes. They only modify text files inside the server's existing safe_path
boundary and require an exact marker/find string.
"""

from pathlib import Path
from typing import Any, Callable


def _read_text(path: Path, *, encoding: str) -> str:
    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Path is not a file: {path}")
    return path.read_text(encoding=encoding)


def _backup_file(path: Path, *, suffix: str) -> Path:
    backup = path.with_name(path.name + suffix)
    backup.write_bytes(path.read_bytes())
    return backup


def _write_if_changed(path: Path, content: str, *, encoding: str, dry_run: bool) -> bool:
    if dry_run:
        return False
    path.write_text(content, encoding=encoding)
    return True


def _preview_window(text: str, index: int, inserted_len: int = 0, *, radius: int = 240) -> str:
    start = max(0, index - radius)
    end = min(len(text), index + inserted_len + radius)
    return text[start:end]


def insert_before_marker_payload(
    *,
    safe_path: Callable[[str | None], Path],
    rel_path: Callable[[Path], str],
    path: str,
    marker: str,
    content: str,
    encoding: str = "utf-8",
    dry_run: bool = False,
    backup: bool = True,
    require_marker_once: bool = True,
) -> dict[str, Any]:
    target = safe_path(path)
    if not marker:
        return {"status": "error", "error": "marker cannot be empty"}
    if content is None:
        return {"status": "error", "error": "content cannot be null"}
    try:
        original = _read_text(target, encoding=encoding)
    except Exception as exc:
        return {"status": "error", "error": str(exc), "error_type": type(exc).__name__}
    count = original.count(marker)
    if count == 0:
        return {"status": "error", "error": "marker_not_found", "marker": marker}
    if require_marker_once and count != 1:
        return {"status": "error", "error": "marker_not_unique", "marker": marker, "count": count}
    index = original.index(marker)
    updated = original[:index] + content + original[index:]
    backup_path = None
    if backup and not dry_run and updated != original:
        backup_path = _backup_file(target, suffix=".bak-before-marker")
    written = _write_if_changed(target, updated, encoding=encoding, dry_run=dry_run)
    return {
        "status": "ok",
        "operation": "insert_before_marker",
        "path": rel_path(target),
        "absolute_path": str(target),
        "marker_count": count,
        "inserted_bytes": len(content.encode(encoding)),
        "dry_run": dry_run,
        "written": written,
        "backup_path": None if backup_path is None else rel_path(backup_path),
        "preview": _preview_window(updated, index, len(content)),
    }


def insert_after_marker_payload(
    *,
    safe_path: Callable[[str | None], Path],
    rel_path: Callable[[Path], str],
    path: str,
    marker: str,
    content: str,
    encoding: str = "utf-8",
    dry_run: bool = False,
    backup: bool = True,
    require_marker_once: bool = True,
) -> dict[str, Any]:
    target = safe_path(path)
    if not marker:
        return {"status": "error", "error": "marker cannot be empty"}
    if content is None:
        return {"status": "error", "error": "content cannot be null"}
    try:
        original = _read_text(target, encoding=encoding)
    except Exception as exc:
        return {"status": "error", "error": str(exc), "error_type": type(exc).__name__}
    count = original.count(marker)
    if count == 0:
        return {"status": "error", "error": "marker_not_found", "marker": marker}
    if require_marker_once and count != 1:
        return {"status": "error", "error": "marker_not_unique", "marker": marker, "count": count}
    index = original.index(marker) + len(marker)
    updated = original[:index] + content + original[index:]
    backup_path = None
    if backup and not dry_run and updated != original:
        backup_path = _backup_file(target, suffix=".bak-after-marker")
    written = _write_if_changed(target, updated, encoding=encoding, dry_run=dry_run)
    return {
        "status": "ok",
        "operation": "insert_after_marker",
        "path": rel_path(target),
        "absolute_path": str(target),
        "marker_count": count,
        "inserted_bytes": len(content.encode(encoding)),
        "dry_run": dry_run,
        "written": written,
        "backup_path": None if backup_path is None else rel_path(backup_path),
        "preview": _preview_window(updated, index, len(content)),
    }


def replace_once_payload(
    *,
    safe_path: Callable[[str | None], Path],
    rel_path: Callable[[Path], str],
    path: str,
    find: str,
    replace: str,
    encoding: str = "utf-8",
    dry_run: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    target = safe_path(path)
    if not find:
        return {"status": "error", "error": "find cannot be empty"}
    if replace is None:
        return {"status": "error", "error": "replace cannot be null"}
    try:
        original = _read_text(target, encoding=encoding)
    except Exception as exc:
        return {"status": "error", "error": str(exc), "error_type": type(exc).__name__}
    count = original.count(find)
    if count == 0:
        return {"status": "error", "error": "find_not_found"}
    if count != 1:
        return {"status": "error", "error": "find_not_unique", "count": count}
    index = original.index(find)
    updated = original.replace(find, replace, 1)
    backup_path = None
    if backup and not dry_run and updated != original:
        backup_path = _backup_file(target, suffix=".bak-replace-once")
    written = _write_if_changed(target, updated, encoding=encoding, dry_run=dry_run)
    return {
        "status": "ok",
        "operation": "replace_once",
        "path": rel_path(target),
        "absolute_path": str(target),
        "replaced_count": count,
        "dry_run": dry_run,
        "written": written,
        "backup_path": None if backup_path is None else rel_path(backup_path),
        "preview": _preview_window(updated, index, len(replace)),
    }
