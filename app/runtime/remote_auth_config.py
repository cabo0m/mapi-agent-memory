from __future__ import annotations

import os
from dataclasses import dataclass

from app.runtime.owner_credentials import valid_owner_password_hash
from app.runtime.remote_auth_contract import REMOTE_AUTH_OWNER_KEY


def _normalize_csv(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


@dataclass(frozen=True)
class RemoteAuthConfig:
    enabled: bool
    base_url: str
    owner_key: str
    oauth_client_id: str
    oauth_redirect_uris: tuple[str, ...]
    owner_login: str = "owner"
    owner_password_hash: str = ""
    # Legacy proxy-identity settings are kept for config compatibility only.
    identity_header: str = ""
    identity_value: str = ""
    access_ttl_seconds: int = 900
    refresh_ttl_seconds: int = 30 * 24 * 3600
    authorization_code_ttl_seconds: int = 300
    login_challenge_ttl_seconds: int = 900
    rate_limit_window_seconds: int = 60
    rate_limit_max_attempts: int = 120

    @classmethod
    def from_env(cls) -> "RemoteAuthConfig":
        enabled = str(os.environ.get("MAPI_REMOTE_AUTH_ENABLED", "0")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return cls(
            enabled=enabled,
            base_url=str(os.environ.get("MAPI_REMOTE_BASE_URL", "https://mapi.invalid")).strip().rstrip("/"),
            owner_key=str(os.environ.get("MAPI_REMOTE_OWNER_KEY", REMOTE_AUTH_OWNER_KEY)).strip().lower(),
            oauth_client_id=str(os.environ.get("MAPI_REMOTE_OAUTH_CLIENT_ID", "chatgpt-private")).strip(),
            oauth_redirect_uris=_normalize_csv(os.environ.get("MAPI_REMOTE_OAUTH_REDIRECT_URIS")),
            owner_login=str(os.environ.get("MAPI_REMOTE_OWNER_LOGIN", "owner")).strip(),
            owner_password_hash=str(os.environ.get("MAPI_REMOTE_OWNER_PASSWORD_HASH", "")).strip(),
            identity_header=str(os.environ.get("MAPI_REMOTE_IDENTITY_HEADER", "")).strip().lower(),
            identity_value=str(os.environ.get("MAPI_REMOTE_IDENTITY_VALUE", "")).strip(),
            access_ttl_seconds=max(60, int(os.environ.get("MAPI_REMOTE_ACCESS_TTL_SECONDS", "900"))),
            refresh_ttl_seconds=max(
                3600,
                int(os.environ.get("MAPI_REMOTE_REFRESH_TTL_SECONDS", str(30 * 24 * 3600))),
            ),
            authorization_code_ttl_seconds=max(
                60, int(os.environ.get("MAPI_REMOTE_AUTH_CODE_TTL_SECONDS", "300"))
            ),
            login_challenge_ttl_seconds=max(
                300, int(os.environ.get("MAPI_REMOTE_LOGIN_CHALLENGE_TTL_SECONDS", "900"))
            ),
            rate_limit_window_seconds=max(
                1, int(os.environ.get("MAPI_REMOTE_RATE_WINDOW_SECONDS", "60"))
            ),
            rate_limit_max_attempts=max(
                1, int(os.environ.get("MAPI_REMOTE_RATE_MAX_ATTEMPTS", "120"))
            ),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.owner_key != REMOTE_AUTH_OWNER_KEY:
            errors.append("remote_owner_must_be_owner")
        if not self.base_url.startswith("https://"):
            errors.append("remote_base_url_must_use_https")
        if not self.oauth_client_id:
            errors.append("oauth_client_id_required")
        if not self.oauth_redirect_uris:
            errors.append("oauth_redirect_allowlist_required")
        if any(not uri.startswith("https://") for uri in self.oauth_redirect_uris):
            errors.append("oauth_redirect_uris_must_use_https")
        if not self.owner_login:
            errors.append("owner_login_required")
        if not valid_owner_password_hash(self.owner_password_hash):
            errors.append("owner_password_hash_required")
        return errors

    def validate_runtime_host(self, host: str) -> list[str]:
        normalized = str(host or "").strip().lower()
        if self.enabled and normalized not in {"127.0.0.1", "::1", "localhost"}:
            return ["remote_auth_requires_loopback_bind"]
        return []
