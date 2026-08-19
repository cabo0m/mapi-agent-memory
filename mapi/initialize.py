from __future__ import annotations

"""Safe first-run bootstrap for a fresh public MAPI instance."""

import getpass
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse

from mapi.env import default_instance_root, parse_environment_file
from mapi.system_install import (
    install_systemd_service,
    mcp_connection_urls,
    probe_http_endpoint,
    wait_for_listener,
)

MAPI_INIT_SCHEMA = "mapi_instance_init.v1"
MAPI_INIT_MANIFEST_SCHEMA = "mapi_instance_manifest.v1"
INIT_MODES = ("local", "vps-proxy", "vps-remote-auth")
SAFE_PROFILES = ("reader", "agent", "maintainer", "admin")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class InitOptions:
    root: Path
    mode: str = "local"
    owner_key: str = "owner"
    agent_subject_key: str = "agent"
    agent_display_name: str = "Agent"
    agent_project_key: str = "agent-self"
    port: int = 8015
    profile: str = "agent"
    public_url: str | None = None
    oauth_client_id: str = "chatgpt-private"
    oauth_redirect_uris: tuple[str, ...] = ()
    identity_header: str = "cf-access-authenticated-user-email"
    identity_value: str | None = None
    service_user: str | None = None
    recovery_command_json: str | None = None
    resume: bool = False
    seed_self: bool = True
    install_service: bool = False
    allow_sudo_prompt: bool = False
    verify_endpoint: bool = True


def _text(value: Any) -> str:
    return str(value or "").strip()


def slugify_identity(value: str, *, default: str = "agent") -> str:
    raw = _text(value).casefold()
    raw = re.sub(r"[^a-z0-9._:-]+", "-", raw).strip("-._:")
    return raw[:128] or default


def _validated_identifier(value: str, field: str) -> str:
    normalized = _text(value)
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"invalid_{field}")
    return normalized


def _validate_public_url(value: str | None, *, required: bool) -> str | None:
    text = _text(value)
    if not text:
        if required:
            raise ValueError("public_url_required_for_vps_mode")
        return None
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("public_url_must_be_https_origin")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("public_url_must_be_origin_only")
    return f"https://{parsed.netloc}"


def validate_init_options(options: InitOptions) -> dict[str, Any]:
    mode = _text(options.mode).casefold()
    if mode not in INIT_MODES:
        raise ValueError("invalid_init_mode")
    profile = _text(options.profile).casefold()
    if profile not in SAFE_PROFILES:
        raise ValueError("invalid_surface_profile")
    if mode == "vps-proxy" and profile == "admin":
        raise ValueError("unauthenticated_vps_admin_profile_not_allowed")
    if mode == "vps-remote-auth" and profile != "admin":
        raise ValueError("single_owner_remote_auth_requires_admin_profile")
    if not 1 <= int(options.port) <= 65535:
        raise ValueError("invalid_runtime_port")
    owner_key = _validated_identifier(options.owner_key, "owner_key")
    subject_key = _validated_identifier(options.agent_subject_key, "agent_subject_key")
    project_key = _validated_identifier(options.agent_project_key, "agent_project_key")
    display_name = _text(options.agent_display_name)
    if not display_name or len(display_name) > 200:
        raise ValueError("invalid_agent_display_name")
    if project_key.casefold() == subject_key.casefold():
        raise ValueError("agent_project_key_must_be_distinct_namespace")

    public_url = _validate_public_url(options.public_url, required=mode != "local")
    redirects = tuple(dict.fromkeys(_text(uri) for uri in options.oauth_redirect_uris if _text(uri)))
    if mode == "vps-remote-auth":
        if not redirects:
            raise ValueError("oauth_redirect_allowlist_required")
        if any(not uri.startswith("https://") for uri in redirects):
            raise ValueError("oauth_redirect_uris_must_use_https")
        if not _text(options.identity_value):
            raise ValueError("remote_identity_value_required")
        if not _text(options.identity_header):
            raise ValueError("remote_identity_header_required")
    return {
        "mode": mode,
        "profile": profile,
        "owner_key": owner_key,
        "agent_subject_key": subject_key,
        "agent_project_key": project_key,
        "agent_display_name": display_name,
        "public_url": public_url,
        "oauth_redirect_uris": redirects,
    }


def _env_encode(value: Any) -> str:
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:@,+\-]*", text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _write_atomic(path: Path, content: str, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    if private and os.name != "nt":
        path.chmod(0o600)


def _database_has_user_state(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(path, timeout=5)
        try:
            table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'").fetchone()
            if table is None:
                return True
            return int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]) > 0
        finally:
            conn.close()
    except sqlite3.Error:
        return True


def _environment_values(options: InitOptions, validated: Mapping[str, Any]) -> dict[str, str]:
    root = options.root.expanduser().resolve()
    data_dir = (root / "data").resolve()
    db_path = (data_dir / "mapi.db").resolve()
    backup_dir = (root / "backups").resolve()
    log_dir = (root / "logs").resolve()
    mode = str(validated["mode"])
    remote_enabled = mode == "vps-remote-auth"
    values = {
        "MAPI_ROOT": str(root),
        "MAPI_DATA_DIR": str(data_dir),
        "MAPI_DB_PATH": str(db_path),
        "MAPI_BACKUP_DIR": str(backup_dir),
        "MAPI_LOG_DIR": str(log_dir),
        "MAPI_RUNTIME_HOST": "127.0.0.1",
        "MAPI_RUNTIME_PORT": str(int(options.port)),
        "MCP_SURFACE_PROFILE": str(validated["profile"]),
        "MAPI_ADMIN_TOOLS_ENABLED": "true" if str(validated["profile"]) == "admin" and mode in {"local", "vps-remote-auth"} else "false",
        "MAPI_OWNER_KEY": str(validated["owner_key"]),
        "MAPI_AGENT_SUBJECT_KEY": str(validated["agent_subject_key"]),
        "MAPI_AGENT_DISPLAY_NAME": str(validated["agent_display_name"]),
        "MAPI_AGENT_PROJECT_KEY": str(validated["agent_project_key"]),
        "MAPI_SEMANTIC_ENABLED": "false",
        "MAPI_GEMINI_ENABLED": "false",
        "MAPI_LOCAL_MODEL_ENABLED": "false",
        "MAPI_LOG_LEVEL": "INFO",
        "MAPI_REQUEST_TIMEOUT_SECONDS": "30",
        "MAPI_REMOTE_AUTH_ENABLED": "true" if remote_enabled else "false",
    }
    if options.recovery_command_json:
        values["MAPI_RECOVERY_COMMAND_JSON"] = options.recovery_command_json
    if validated.get("public_url"):
        values["MAPI_REMOTE_BASE_URL"] = str(validated["public_url"])
    if remote_enabled:
        values.update(
            {
                "MAPI_REMOTE_OWNER_KEY": "owner",
                "MAPI_REMOTE_OAUTH_CLIENT_ID": _text(options.oauth_client_id) or "chatgpt-private",
                "MAPI_REMOTE_OAUTH_REDIRECT_URIS": ",".join(validated["oauth_redirect_uris"]),
                "MAPI_REMOTE_IDENTITY_HEADER": _text(options.identity_header).casefold(),
                "MAPI_REMOTE_IDENTITY_VALUE": _text(options.identity_value),
            }
        )
    return values


def render_env(values: Mapping[str, str]) -> str:
    header = [
        "# Generated by mapi-init. Contains instance configuration; protect this file.",
        "# Explicit process environment variables override these values at runtime.",
        "",
    ]
    body = [f"{key}={_env_encode(values[key])}" for key in sorted(values)]
    return "\n".join([*header, *body, ""])


def _systemd_exec_start() -> str:
    executable = shutil.which("mapi-server")
    if executable:
        return str(Path(executable).resolve())
    python = sys.executable
    return f'{python} -c "from mapi.cli import server; server()"'


def render_systemd_unit(*, root: Path, env_file: Path, service_user: str) -> str:
    return f"""[Unit]\nDescription=MAPI Agent Memory MCP Runtime\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\nUser={service_user}\nWorkingDirectory={root}\nEnvironmentFile={env_file}\nExecStart={_systemd_exec_start()}\nRestart=on-failure\nRestartSec=3\nTimeoutStopSec=30\nUMask=0077\nNoNewPrivileges=true\nPrivateTmp=true\n\n[Install]\nWantedBy=multi-user.target\n"""


def render_proxy_security_template(*, public_url: str, port: int, remote_auth_enabled: bool) -> str:
    host = urlparse(public_url).netloc
    auth_note = (
        "MAPI remote auth is enabled, but the trusted identity header still MUST be injected only by your authenticated proxy/access gateway."
        if remote_auth_enabled
        else "MAPI remote auth is disabled. Your reverse proxy/access gateway MUST authenticate every request before proxying it."
    )
    return f"""# SECURITY TEMPLATE ONLY. Do not deploy this as an unauthenticated public proxy.\n# {auth_note}\n# TLS must terminate at the authenticated proxy. MAPI itself stays on loopback.\n\n# Example origin: {public_url}\n# Example upstream: 127.0.0.1:{port}\n#\n# Caddy skeleton after you add/verify authentication and trusted header handling:\n# {host} {{\n#     reverse_proxy 127.0.0.1:{port}\n# }}\n"""


@contextmanager
def _temporary_environment(values: Mapping[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = str(value)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _seed_self_model(db_path: Path, *, validated: Mapping[str, Any]) -> list[int]:
    import server_core

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        subject = str(validated["agent_subject_key"])
        project = str(validated["agent_project_key"])
        display = str(validated["agent_display_name"])
        definitions = [
            {
                "event": f"mapi-init:{subject}:identity",
                "content": f"{display} is the configured agent identity for this MAPI instance.",
                "summary": f"Agent identity: {display}",
                "memory_type": "identity",
                "entry_type": "user_profile",
                "truth_kind": "fact",
                "tags": f"agent-self,self-model,self-evidence,identity,bootstrap,subject:{subject},agent:{subject}",
                "layer_code": "identity",
                "area_code": "identity",
                "identity_weight": 1.0,
            },
            {
                "event": f"mapi-init:{subject}:namespace-guardrail",
                "content": f"Self evidence for {display} belongs in project namespace {project}; customer/project memories must remain separate.",
                "summary": "Keep agent self evidence separate from customer projects",
                "memory_type": "guardrail",
                "entry_type": "decision",
                "truth_kind": "decision",
                "tags": f"agent-self,self-model,guardrail,invariant,safety,bootstrap,subject:{subject},agent:{subject}",
                "layer_code": "core",
                "area_code": "meta",
                "identity_weight": 0.8,
            },
        ]
        ids: list[int] = []
        for item in definitions:
            row = conn.execute("SELECT id FROM memories WHERE source_event_ref=? LIMIT 1", (item["event"],)).fetchone()
            if row is not None:
                ids.append(int(row["id"]))
                continue
            created = server_core._insert_memory(
                conn,
                content=item["content"],
                summary_short=item["summary"],
                memory_type=item["memory_type"],
                source="mapi-init",
                importance_score=0.9,
                confidence_score=1.0,
                tags=item["tags"],
                layer_code=item["layer_code"],
                area_code=item["area_code"],
                state_code="validated",
                scope_code="project",
                identity_weight=float(item["identity_weight"]),
                project_key=project,
                entry_type=item["entry_type"],
                truth_kind=item["truth_kind"],
                title=item["summary"],
                source_context="Generated from explicit first-run operator configuration.",
                source_event_ref=item["event"],
                importance_level="high",
                owner_role="project_maintainer",
                priority="high",
                ensure_embedding=False,
            )
            ids.append(int(created["id"]))
        conn.commit()
        return ids
    finally:
        conn.close()


def _manifest_fingerprint(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def initialize_instance(options: InitOptions) -> dict[str, Any]:
    validated = validate_init_options(options)
    root = options.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    env_file = root / ".env"
    generated_dir = root / "generated"
    values = _environment_values(options, validated)
    db_path = Path(values["MAPI_DB_PATH"])

    if not options.resume:
        if env_file.exists():
            raise RuntimeError("existing_env_detected_use_resume")
        if _database_has_user_state(db_path):
            raise RuntimeError("existing_instance_detected_use_resume")
    elif env_file.exists():
        existing = parse_environment_file(env_file)
        guarded_keys = (
            "MAPI_ROOT", "MAPI_DATA_DIR", "MAPI_DB_PATH", "MAPI_RUNTIME_HOST", "MAPI_RUNTIME_PORT",
            "MCP_SURFACE_PROFILE", "MAPI_OWNER_KEY", "MAPI_AGENT_SUBJECT_KEY", "MAPI_AGENT_DISPLAY_NAME",
            "MAPI_AGENT_PROJECT_KEY", "MAPI_REMOTE_AUTH_ENABLED", "MAPI_REMOTE_BASE_URL",
            "MAPI_REMOTE_OAUTH_CLIENT_ID", "MAPI_REMOTE_OAUTH_REDIRECT_URIS",
            "MAPI_REMOTE_IDENTITY_HEADER", "MAPI_REMOTE_IDENTITY_VALUE",
        )
        mismatches = [key for key in guarded_keys if key in existing or key in values if existing.get(key) != values.get(key)]
        if mismatches:
            raise RuntimeError("resume_config_mismatch:" + ",".join(sorted(set(mismatches))))

    for directory in (Path(values["MAPI_DATA_DIR"]), Path(values["MAPI_BACKUP_DIR"]), Path(values["MAPI_LOG_DIR"]), generated_dir):
        directory.mkdir(parents=True, exist_ok=True)
    _write_atomic(env_file, render_env(values), private=True)

    from app.runtime.context import get_runtime_context, configure_runtime_context

    previous_context = get_runtime_context()
    migration_versions: list[str] = []
    migration_tail: str | None = None
    self_memory_ids: list[int] = []
    doctor: dict[str, Any]
    try:
        with _temporary_environment(values):
            configure_runtime_context(root=root, data_dir=Path(values["MAPI_DATA_DIR"]), db_path=db_path)
            from app import db_migrations

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                migration_versions = db_migrations.apply_all_migrations(conn)
                conn.commit()
                all_versions = sorted(db_migrations.applied_migration_versions(conn))
                migration_tail = all_versions[-1] if all_versions else None
            finally:
                conn.close()

            if options.seed_self:
                self_memory_ids = _seed_self_model(db_path, validated=validated)

            if validated["mode"] == "vps-remote-auth":
                from app.runtime.remote_auth_config import RemoteAuthConfig

                errors = RemoteAuthConfig.from_env().validate()
                if errors:
                    raise RuntimeError("remote_auth_config_invalid:" + ",".join(errors))

            from app.runtime.doctor import collect_doctor_report

            doctor = collect_doctor_report(root=root, db_path=db_path, deep=False)
    finally:
        configure_runtime_context(
            root=previous_context.root,
            data_dir=previous_context.data_dir,
            db_path=previous_context.db_path,
        )

    artifacts: dict[str, str] = {"env_file": str(env_file)}
    operator_steps: list[str] = []
    connection = mcp_connection_urls(public_origin=validated.get("public_url"), port=int(options.port))
    service_result: dict[str, Any] = {"status": "not_requested", "active": False}
    listener_result: dict[str, Any] = {"status": "not_checked"}
    loopback_probe: dict[str, Any] = {"status": "not_checked", "url": connection["loopback_mcp_url"]}
    public_probe: dict[str, Any] = {
        "status": "not_configured" if not connection.get("public_mcp_url") else "not_checked",
        "url": connection.get("public_mcp_url"),
    }

    if validated["mode"] != "local":
        service_user = _text(options.service_user) or getpass.getuser()
        systemd_path = generated_dir / "mapi.service"
        proxy_path = generated_dir / "reverse-proxy-security-template.txt"
        _write_atomic(systemd_path, render_systemd_unit(root=root, env_file=env_file, service_user=service_user))
        _write_atomic(
            proxy_path,
            render_proxy_security_template(
                public_url=str(validated["public_url"]),
                port=int(options.port),
                remote_auth_enabled=validated["mode"] == "vps-remote-auth",
            ),
        )
        artifacts.update({"systemd_unit": str(systemd_path), "proxy_security_template": str(proxy_path)})
        if options.install_service:
            try:
                service_result = install_systemd_service(
                    systemd_path,
                    allow_sudo_prompt=bool(options.allow_sudo_prompt),
                )
            except RuntimeError as exc:
                service_result = {"status": "failed", "active": False, "error": str(exc)}
            if service_result.get("active"):
                listener_result = wait_for_listener("127.0.0.1", int(options.port))
                if listener_result.get("status") == "ready" and options.verify_endpoint:
                    loopback_probe = probe_http_endpoint(str(connection["loopback_mcp_url"]))
                if connection.get("public_mcp_url") and options.verify_endpoint:
                    public_probe = probe_http_endpoint(str(connection["public_mcp_url"]))
        else:
            operator_steps.extend(
                [
                    f"sudo cp {systemd_path} /etc/systemd/system/mapi.service",
                    "sudo systemctl daemon-reload",
                    "sudo systemctl enable --now mapi.service",
                ]
            )
        operator_steps.extend(
            [
                "Configure authenticated TLS reverse proxy using generated security template.",
                f"Connect the MCP client to {connection['recommended_mcp_url']} after the public endpoint is reachable.",
            ]
        )
    else:
        operator_steps.extend(
            [
                "Run mapi-server.",
                f"Connect the MCP client to {connection['recommended_mcp_url']}.",
            ]
        )

    connection_status = "configured"
    if service_result.get("active") and listener_result.get("status") == "ready":
        connection_status = "local_listener_ready"
    if public_probe.get("status") == "reachable":
        connection_status = "public_endpoint_reachable"
    connection.update(
        {
            "status": connection_status,
            "service": service_result,
            "listener": listener_result,
            "loopback_probe": loopback_probe,
            "public_probe": public_probe,
        }
    )

    manifest_core = {
        "schema": MAPI_INIT_MANIFEST_SCHEMA,
        "mode": validated["mode"],
        "root": str(root),
        "database": str(db_path),
        "agent": {
            "subject_key": validated["agent_subject_key"],
            "display_name": validated["agent_display_name"],
            "project_key": validated["agent_project_key"],
        },
        "runtime": {"host": "127.0.0.1", "port": int(options.port), "profile": validated["profile"]},
        "remote_auth_enabled": validated["mode"] == "vps-remote-auth",
        "public_url": validated.get("public_url"),
        "connection": connection,
        "self_memory_ids": self_memory_ids,
        "artifacts": artifacts,
    }
    manifest = {**manifest_core, "fingerprint": _manifest_fingerprint(manifest_core)}
    manifest_path = generated_dir / "mapi-init-manifest.json"
    _write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    artifacts["manifest"] = str(manifest_path)

    service_failed = options.install_service and not bool(service_result.get("active"))
    listener_failed = bool(service_result.get("active")) and listener_result.get("status") != "ready"
    status = "blocked" if doctor.get("status") == "BLOCKED" or service_failed or listener_failed else ("ready" if connection_status in {"local_listener_ready", "public_endpoint_reachable"} else "ready_to_start")
    return {
        "status": status,
        "schema": MAPI_INIT_SCHEMA,
        "mode": validated["mode"],
        "root": str(root),
        "database": str(db_path),
        "env_file": str(env_file),
        "migrations_applied_now": migration_versions,
        "migration_tail": migration_tail,
        "self_memory_ids": self_memory_ids,
        "doctor_status": doctor.get("status"),
        "doctor_findings": doctor.get("findings") or [],
        "connection": connection,
        "system_service": service_result,
        "artifacts": artifacts,
        "operator_steps": operator_steps,
        "safety": {
            "existing_state_overwritten": False,
            "loopback_runtime": True,
            "admin_tools_enabled": validated["profile"] == "admin" and validated["mode"] in {"local", "vps-remote-auth"},
            "demo_seeded": False,
            "privileged_system_changes_performed": bool(options.install_service and service_result.get("status") != "not_requested"),
            "reverse_proxy_auth_required": validated["mode"] != "local",
        },
    }
