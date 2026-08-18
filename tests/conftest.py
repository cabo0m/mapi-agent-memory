from __future__ import annotations

import importlib.util
import os
os.environ["MAPI_RUNTIME_MODE"] = "legacy_public_mpbm"
os.environ["MAPI_WRITER_GUARD_ENABLED"] = "0"
os.environ["MAPI_RUNTIME_ENFORCE_FRESHNESS"] = "0"
os.environ["MAPI_REMOTE_AUTH_ENABLED"] = "0"
import shutil
import sys
import tempfile
import types
import uuid
from pathlib import Path
from typing import Any, Callable

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DB_PATH = (REPO_ROOT / "data" / "agent_memory.db").resolve()
PYTEST_SESSION_ROOT = Path(
    tempfile.mkdtemp(prefix="agent_pytest_session_")
).resolve()
PYTEST_SESSION_DATA_DIR = (PYTEST_SESSION_ROOT / "data").resolve()
PYTEST_SESSION_DB_PATH = (
    PYTEST_SESSION_DATA_DIR / "agent_memory.db"
).resolve()
PYTEST_SESSION_DATA_DIR.mkdir(parents=True, exist_ok=True)

os.environ["MAPI_PYTEST_SESSION_ROOT"] = str(PYTEST_SESSION_ROOT)
os.environ["MAPI_PYTEST_SESSION_DB_PATH"] = str(PYTEST_SESSION_DB_PATH)
# Workshop tests run with an explicit non-admin operator surface. Tests of the
# missing-profile contract remove this variable locally and assert public.
os.environ["MCP_SURFACE_PROFILE"] = "clean_operator"

_sitecustomize_path = PYTEST_SESSION_ROOT / "sitecustomize.py"
_sitecustomize_path.write_text(
    "\n".join(
        [
            "from pathlib import Path",
            "import os",
            "",
            "root = Path(os.environ['MAPI_PYTEST_SESSION_ROOT']).resolve()",
            "data_dir = (root / 'data').resolve()",
            "db_path = Path(os.environ['MAPI_PYTEST_SESSION_DB_PATH']).resolve()",
            "data_dir.mkdir(parents=True, exist_ok=True)",
            "",
            "from app import db, memory_config",
            "db.DATA_DIR = data_dir",
            "db.DB_PATH = db_path",
            "memory_config.ROOT = root",
            "memory_config.DATA_DIR = data_dir",
            "memory_config.DB_PATH = db_path",
            "",
        ]
    ),
    encoding="utf-8",
)
_existing_pythonpath = os.environ.get("PYTHONPATH", "")
_pythonpath_parts = [str(PYTEST_SESSION_ROOT), str(REPO_ROOT)]
if _existing_pythonpath:
    _pythonpath_parts.append(_existing_pythonpath)
os.environ["PYTHONPATH"] = os.pathsep.join(_pythonpath_parts)

from app import db as app_db
from app import memory_config

app_db.DATA_DIR = PYTEST_SESSION_DATA_DIR
app_db.DB_PATH = PYTEST_SESSION_DB_PATH
memory_config.ROOT = PYTEST_SESSION_ROOT
memory_config.DATA_DIR = PYTEST_SESSION_DATA_DIR
memory_config.DB_PATH = PYTEST_SESSION_DB_PATH


def _restore_workshop_registry() -> None:
    from app.runtime import admin_tools, freshness, private_mode, server_runtime, timeline_tools
    from app.workshops.runtime_registry import bind_workshop_handlers

    bind_workshop_handlers(_authoritative_server_core, replace=True, strict=False)
    bind_workshop_handlers(server_runtime, replace=True, strict=False, local_only=True)
    bind_workshop_handlers(freshness, replace=True, strict=False, local_only=True)
    bind_workshop_handlers(private_mode, replace=True, strict=False, local_only=True)
    bind_workshop_handlers(admin_tools, replace=True, strict=False, local_only=True)
    bind_workshop_handlers(timeline_tools, replace=True, strict=False, local_only=True)


def _restore_session_database_paths() -> None:
    app_db.DATA_DIR = PYTEST_SESSION_DATA_DIR
    app_db.DB_PATH = PYTEST_SESSION_DB_PATH
    memory_config.ROOT = PYTEST_SESSION_ROOT
    memory_config.DATA_DIR = PYTEST_SESSION_DATA_DIR
    memory_config.DB_PATH = PYTEST_SESSION_DB_PATH

    for module_name in ("server_core", "server"):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        module.ROOT = PYTEST_SESSION_ROOT
        module.DATA_DIR = PYTEST_SESSION_DATA_DIR
        module.DB_PATH = PYTEST_SESSION_DB_PATH
        module_config = getattr(module, "config", None)
        if module_config is not None:
            module_config.ROOT = PYTEST_SESSION_ROOT
            module_config.DATA_DIR = PYTEST_SESSION_DATA_DIR
            module_config.DB_PATH = PYTEST_SESSION_DB_PATH

    if "_SESSION_SERVER_CORE_BASELINE" in globals():
        for name, value in _SESSION_SERVER_CORE_BASELINE.items():
            if value is _MISSING_SERVER_CORE_ATTR:
                if hasattr(_authoritative_server_core, name):
                    delattr(_authoritative_server_core, name)
            else:
                setattr(_authoritative_server_core, name, value)
    _restore_workshop_registry()


ServerModule = Any
MemoryFactory = Callable[..., int]
PairChecker = Callable[[list[dict[str, Any]], int, int], bool]
ItemIdCollector = Callable[[list[dict[str, Any]]], set[int]]


def install_fastmcp_stub() -> None:
    if "fastmcp" in sys.modules:
        return

    module = types.ModuleType("fastmcp")

    class FastMCP:
        def __init__(self, name: str) -> None:
            self.name = name

        def tool(self, fn):
            return fn

        def run(self, *args, **kwargs) -> None:
            return None

    module.FastMCP = FastMCP
    sys.modules["fastmcp"] = module


install_fastmcp_stub()


SERVER_CORE_RUNTIME_ATTRS = (
    "_sync_config",
    "get_db_connection",
    "_insert_memory",
    "_create_link",
    "create_sleep_run",
    "add_sleep_action",
    "finalize_sleep_run",
    "recall_memory",
    "_timeline_original_insert_memory",
    "_timeline_original_create_link",
)
_MISSING_SERVER_CORE_ATTR = object()
import server_core as _authoritative_server_core

_SESSION_SERVER_CORE_BASELINE = {
    name: getattr(_authoritative_server_core, name, _MISSING_SERVER_CORE_ATTR)
    for name in SERVER_CORE_RUNTIME_ATTRS
}


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del session, exitstatus
    shutil.rmtree(PYTEST_SESSION_ROOT, ignore_errors=True)


def load_server_module(server_path: Path, module_name: str) -> ServerModule:
    spec = importlib.util.spec_from_file_location(module_name, server_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolated_tmp_root() -> Path:
    tmp_root = Path(tempfile.mkdtemp(prefix="agent_pytest_"))
    try:
        yield tmp_root.resolve()
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


@pytest.fixture(scope="session")
def session_test_root() -> Path:
    return PYTEST_SESSION_ROOT


@pytest.fixture(autouse=True)
def restore_session_database_paths():
    _restore_session_database_paths()
    try:
        yield
    finally:
        _restore_session_database_paths()


@pytest.fixture
def server(isolated_tmp_root: Path) -> ServerModule:
    import server_core

    server_path = Path(__file__).resolve().parents[1] / "server.py"
    module_name = f"server_under_test_{uuid.uuid4().hex}"
    missing = object()
    original_attrs = {
        name: getattr(server_core, name, missing)
        for name in SERVER_CORE_RUNTIME_ATTRS
    }
    module = load_server_module(server_path, module_name)

    from app import memory_config as config

    config.ROOT = isolated_tmp_root
    config.DATA_DIR = config.ROOT / "data"
    config.DB_PATH = config.DATA_DIR / "agent_memory.db"
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    module.ROOT = config.ROOT
    module.DATA_DIR = config.DATA_DIR
    module.DB_PATH = config.DB_PATH

    try:
        yield module
    finally:
        for name, value in original_attrs.items():
            if value is missing:
                if hasattr(server_core, name):
                    delattr(server_core, name)
            else:
                setattr(server_core, name, value)
        if sys.modules.get(module_name) is module:
            del sys.modules[module_name]


@pytest.fixture
def memory_factory(server: ServerModule) -> MemoryFactory:
    def _create_memory(**kwargs: Any) -> int:
        result = server.create_memory(**kwargs)
        return int(result["memory"]["id"])

    return _create_memory


@pytest.fixture
def pair_present() -> PairChecker:
    def _pair_present(pairs: list[dict[str, Any]], left_id: int, right_id: int) -> bool:
        expected = {int(left_id), int(right_id)}
        for pair in pairs:
            ids = {
                int(pair.get("memory_a_id", pair.get("from_memory_id", -1))),
                int(pair.get("memory_b_id", pair.get("to_memory_id", -2))),
            }
            if ids == expected:
                return True
        return False

    return _pair_present


@pytest.fixture
def ids_from_items() -> ItemIdCollector:
    def _ids_from_items(items: list[dict[str, Any]]) -> set[int]:
        return {int(item["id"]) for item in items}

    return _ids_from_items
