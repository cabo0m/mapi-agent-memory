from __future__ import annotations

"""File-system admin helper functions for MAPI tools."""

import base64
import shutil
from pathlib import Path
from typing import Any, Callable


def get_root_payload(*, root: Path, sync_config: Callable[[], None]) -> dict[str, Any]:
    sync_config()
    return {"root": str(root), "exists": root.exists(), "is_dir": root.is_dir()}


def list_dir_payload(*, root: Path, safe_path: Callable[[str | None], Path], rel_path: Callable[[Path], str], guess_mime: Callable[[Path], str | None], path: str = ".") -> dict[str, Any]:
    target = safe_path(path)
    if not target.exists():
        return {"status": "error", "error": f"Nie istnieje: {path}"}
    if not target.is_dir():
        return {"status": "error", "error": f"To nie jest katalog: {path}"}
    items: list[dict[str, Any]] = []
    for entry in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        stat = entry.stat()
        items.append({
            "name": entry.name,
            "path": rel_path(entry),
            "type": "directory" if entry.is_dir() else "file",
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "mime": None if entry.is_dir() else guess_mime(entry),
        })
    return {"root": str(root), "path": rel_path(target), "items": items}


def read_file_text_payload(*, safe_path: Callable[[str | None], Path], rel_path: Callable[[Path], str], guess_mime: Callable[[Path], str | None], path: str, encoding: str = "utf-8", errors: str = "strict") -> dict[str, Any]:
    target = safe_path(path)
    if not target.exists():
        return {"status": "error", "error": f"Nie istnieje: {path}"}
    if not target.is_file():
        return {"status": "error", "error": f"To nie jest plik: {path}"}
    return {"path": rel_path(target), "absolute_path": str(target), "encoding": encoding, "mime": guess_mime(target), "content": target.read_text(encoding=encoding, errors=errors)}


def read_file_base64_payload(*, safe_path: Callable[[str | None], Path], rel_path: Callable[[Path], str], guess_mime: Callable[[Path], str | None], path: str) -> dict[str, Any]:
    target = safe_path(path)
    if not target.exists():
        return {"status": "error", "error": f"Nie istnieje: {path}"}
    if not target.is_file():
        return {"status": "error", "error": f"To nie jest plik: {path}"}
    data = target.read_bytes()
    return {"path": rel_path(target), "absolute_path": str(target), "mime": guess_mime(target), "base64": base64.b64encode(data).decode("ascii"), "size": len(data)}


def write_file_text_payload(*, safe_path: Callable[[str | None], Path], rel_path: Callable[[Path], str], path: str, content: str, encoding: str = "utf-8", create_parents: bool = True) -> dict[str, Any]:
    target = safe_path(path)
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding=encoding)
    return {"path": rel_path(target), "absolute_path": str(target), "written": True, "bytes": len(content.encode(encoding))}


def write_file_base64_payload(*, safe_path: Callable[[str | None], Path], rel_path: Callable[[Path], str], path: str, base64_content: str, create_parents: bool = True) -> dict[str, Any]:
    target = safe_path(path)
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(base64_content)
    target.write_bytes(data)
    return {"path": rel_path(target), "absolute_path": str(target), "written": True, "bytes": len(data)}


def append_file_text_payload(*, safe_path: Callable[[str | None], Path], rel_path: Callable[[Path], str], path: str, content: str, encoding: str = "utf-8", create_parents: bool = True) -> dict[str, Any]:
    target = safe_path(path)
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding=encoding, newline="") as handle:
        handle.write(content)
    return {"path": rel_path(target), "absolute_path": str(target), "appended": True, "bytes": len(content.encode(encoding))}


def make_dir_payload(*, safe_path: Callable[[str | None], Path], rel_path: Callable[[Path], str], path: str, parents: bool = True, exist_ok: bool = True) -> dict[str, Any]:
    target = safe_path(path)
    target.mkdir(parents=parents, exist_ok=exist_ok)
    return {"path": rel_path(target), "absolute_path": str(target), "created": True}


def move_path_payload(*, safe_path: Callable[[str | None], Path], rel_path: Callable[[Path], str], src: str, dst: str, create_parents: bool = True) -> dict[str, Any]:
    source = safe_path(src)
    target = safe_path(dst)
    if not source.exists():
        return {"status": "error", "error": f"Nie istnieje: {src}"}
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return {"source": rel_path(source), "target": rel_path(target), "moved": True}


def delete_path_payload(*, root: Path, safe_path: Callable[[str | None], Path], rel_path: Callable[[Path], str], path: str, recursive: bool = True) -> dict[str, Any]:
    target = safe_path(path)
    if target == root:
        return {"status": "error", "error": f"Nie usuwam katalogu głównego {root}"}
    if not target.exists():
        return {"status": "error", "error": f"Nie istnieje: {path}"}
    if target.is_dir():
        if recursive:
            shutil.rmtree(target)
        else:
            target.rmdir()
        kind = "directory"
    else:
        target.unlink()
        kind = "file"
    return {"path": rel_path(target), "deleted": True, "type": kind}


def stat_path_payload(*, safe_path: Callable[[str | None], Path], rel_path: Callable[[Path], str], guess_mime: Callable[[Path], str | None], path: str = ".") -> dict[str, Any]:
    target = safe_path(path)
    if not target.exists():
        return {"status": "error", "error": f"Nie istnieje: {path}"}
    stat = target.stat()
    return {"path": rel_path(target), "absolute_path": str(target), "exists": True, "is_file": target.is_file(), "is_dir": target.is_dir(), "size": stat.st_size, "created": stat.st_ctime, "modified": stat.st_mtime, "mime": None if target.is_dir() else guess_mime(target)}


def search_text_payload(*, safe_path: Callable[[str | None], Path], rel_path: Callable[[Path], str], query: str, path: str = ".", case_sensitive: bool = False, max_results: int = 100) -> dict[str, Any]:
    if not query:
        return {"status": "error", "error": "query nie może być puste"}
    start = safe_path(path)
    if not start.exists():
        return {"status": "error", "error": f"Nie istnieje: {path}"}
    needle = query if case_sensitive else query.lower()
    results: list[dict[str, Any]] = []
    candidates = [start] if start.is_file() else list(start.rglob("*"))
    for file_path in candidates:
        if len(results) >= max_results:
            break
        if not file_path.is_file():
            continue
        try:
            with file_path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line_no, line in enumerate(handle, start=1):
                    haystack = line if case_sensitive else line.lower()
                    if needle in haystack:
                        results.append({"path": rel_path(file_path), "line": line_no, "text": line.rstrip("\n")})
                        if len(results) >= max_results:
                            break
        except OSError:
            continue
    return {"query": query, "path": rel_path(start), "count": len(results), "results": results}
