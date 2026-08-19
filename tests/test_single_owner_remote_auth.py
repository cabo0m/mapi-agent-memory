from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

from app.runtime.remote_actor import access_token_actor
from app.runtime.remote_auth_contract import REMOTE_OAUTH_PROFILE, REMOTE_OAUTH_SCOPES


def test_single_remote_oauth_identity_is_admin() -> None:
    assert REMOTE_OAUTH_PROFILE == "admin"
    assert "mapi:admin" in REMOTE_OAUTH_SCOPES


def test_remote_actor_accepts_only_owner_oauth_admin() -> None:
    admin = SimpleNamespace(
        claims={"owner_key": "owner", "profile": "admin", "auth_channel": "oauth"},
        client_id="owner-client",
        scopes=["mapi:read", "mapi:write", "mapi:admin"],
    )
    actor = access_token_actor(admin)
    assert actor is not None
    assert actor["valid"] is True
    assert actor["profile"] == "admin"

    for claims in (
        {"owner_key": "owner", "profile": "agent", "auth_channel": "oauth"},
        {"owner_key": "owner", "profile": "admin", "auth_channel": "codex"},
        {"owner_key": "someone-else", "profile": "admin", "auth_channel": "oauth"},
    ):
        denied = access_token_actor(SimpleNamespace(claims=claims, client_id="x", scopes=[]))
        assert denied is not None
        assert denied["valid"] is False
        assert denied["profile"] == "reader"


def test_runtime_auth_has_only_owner_oauth_path(tmp_path) -> None:
    code = """
from pathlib import Path
from app.runtime.remote_auth import build_remote_auth_provider, issue_codex_bearer_token
from app.runtime.remote_auth_config import RemoteAuthConfig
config = RemoteAuthConfig(
    enabled=True,
    base_url='https://mapi.example.test',
    owner_key='owner',
    oauth_client_id='owner-client',
    oauth_redirect_uris=('https://client.example.test/callback',),
    identity_header='cf-access-authenticated-user-email',
    identity_value='owner@example.test',
)
provider = build_remote_auth_provider(config=config, db_path=Path('auth-test.db'))
assert provider.server is not None
assert provider.verifiers == []
try:
    issue_codex_bearer_token(db_path='ignored.db')
except RuntimeError as exc:
    assert str(exc) == 'codex_bearer_retired_single_owner_admin_oauth'
else:
    raise AssertionError('legacy bearer issuance unexpectedly enabled')
"""
    completed = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
