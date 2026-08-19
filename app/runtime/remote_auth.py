from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastmcp.server.auth import AccessToken, MultiAuth, OAuthProvider, TokenVerifier
from fastmcp.server.dependencies import get_http_headers, get_http_request
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.runtime.remote_auth_config import RemoteAuthConfig
from app.runtime.remote_auth_contract import (
    PKCE_CHALLENGE_PATTERN,
    PKCE_METHOD,
    REMOTE_AUTH_OWNER_KEY,
    REMOTE_AUTH_POLICY_VERSION,
    REMOTE_AUTH_SCHEMA_VERSION,
    REMOTE_CODEX_PROFILE,
    REMOTE_CODEX_SCOPES,
    REMOTE_OAUTH_PROFILE,
    REMOTE_OAUTH_SCOPES,
    REMOTE_REQUIRED_SCOPE,
    TOKEN_KINDS,
)
from app.runtime.remote_auth_store import ensure_remote_auth_schema

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _now_epoch() -> int:
    return int(time.time())


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fingerprint_from_hash(value: str) -> str:
    return value[:16]


def _json_list(values: Iterable[str]) -> str:
    return json.dumps(sorted({str(value).strip() for value in values if str(value).strip()}), separators=(",", ":"))


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded if str(item).strip()]


def _append_query(url: str, **params: str | None) -> str:
    split = urlsplit(url)
    query = list(parse_qsl(split.query, keep_blank_values=True))
    query.extend((key, value) for key, value in params.items() if value is not None)
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _request_pkce_method() -> str | None:
    try:
        request = get_http_request()
    except RuntimeError:
        return None
    value = request.query_params.get("code_challenge_method")
    return None if value is None else str(value).strip()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class RemoteAuthStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).resolve()
        with _connect(self.db_path) as conn:
            ensure_remote_auth_schema(conn)
            conn.commit()

    def audit(
        self,
        *,
        event_type: str,
        channel: str,
        outcome: str,
        reason_code: str,
        token_hash: str | None = None,
        client_id: str | None = None,
        owner_key: str | None = None,
        profile: str | None = None,
    ) -> None:
        with _connect(self.db_path) as conn:
            ensure_remote_auth_schema(conn)
            conn.execute(
                """
                INSERT INTO remote_auth_audit_events (
                    event_type, channel, client_id, owner_key, profile,
                    outcome, reason_code, token_fingerprint, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event_type),
                    str(channel),
                    client_id,
                    owner_key,
                    profile,
                    str(outcome),
                    str(reason_code),
                    None if token_hash is None else _fingerprint_from_hash(token_hash),
                    _utc_now_iso(),
                ),
            )
            conn.commit()

    def rate_allowed(
        self,
        *,
        bucket: str,
        action: str,
        window_seconds: int,
        max_attempts: int,
    ) -> bool:
        now = _now_epoch()
        threshold = now - int(window_seconds)
        bucket_hash = _secret_hash(bucket)
        with _connect(self.db_path) as conn:
            ensure_remote_auth_schema(conn)
            conn.execute("DELETE FROM remote_auth_rate_events WHERE occurred_at < ?", (threshold,))
            count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM remote_auth_rate_events WHERE bucket_hash=? AND action=? AND occurred_at>=?",
                    (bucket_hash, action, threshold),
                ).fetchone()[0]
            )
            if count >= int(max_attempts):
                conn.commit()
                return False
            conn.execute(
                "INSERT INTO remote_auth_rate_events(bucket_hash, action, occurred_at) VALUES (?, ?, ?)",
                (bucket_hash, action, now),
            )
            conn.commit()
            return True

    def insert_authorization_code(
        self,
        *,
        raw_code: str,
        client_id: str,
        redirect_uri: str,
        scopes: Iterable[str],
        code_challenge: str,
        owner_key: str,
        profile: str,
        expires_at: int,
    ) -> None:
        with _connect(self.db_path) as conn:
            ensure_remote_auth_schema(conn)
            conn.execute(
                """
                INSERT INTO remote_auth_authorization_codes (
                    code_hash, client_id, redirect_uri, scopes_json, code_challenge,
                    owner_key, profile, expires_at, created_at, consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    _secret_hash(raw_code),
                    client_id,
                    redirect_uri,
                    _json_list(scopes),
                    code_challenge,
                    owner_key,
                    profile,
                    int(expires_at),
                    _utc_now_iso(),
                ),
            )
            conn.commit()

    def load_authorization_code(self, raw_code: str) -> sqlite3.Row | None:
        code_hash = _secret_hash(raw_code)
        with _connect(self.db_path) as conn:
            ensure_remote_auth_schema(conn)
            row = conn.execute(
                "SELECT * FROM remote_auth_authorization_codes WHERE code_hash=?",
                (code_hash,),
            ).fetchone()
            return row

    def consume_authorization_code(self, raw_code: str) -> bool:
        code_hash = _secret_hash(raw_code)
        now = _now_epoch()
        with _connect(self.db_path) as conn:
            ensure_remote_auth_schema(conn)
            cursor = conn.execute(
                """
                UPDATE remote_auth_authorization_codes
                SET consumed_at=?
                WHERE code_hash=? AND consumed_at IS NULL AND expires_at>?
                """,
                (_utc_now_iso(), code_hash, now),
            )
            conn.commit()
            return int(cursor.rowcount or 0) == 1

    def insert_token(
        self,
        *,
        raw_token: str,
        token_kind: str,
        client_id: str,
        owner_key: str,
        profile: str,
        scopes: Iterable[str],
        expires_at: int | None,
        pair_hash: str | None = None,
        label: str | None = None,
    ) -> str:
        if token_kind not in TOKEN_KINDS:
            raise ValueError("invalid_remote_token_kind")
        token_hash = _secret_hash(raw_token)
        with _connect(self.db_path) as conn:
            ensure_remote_auth_schema(conn)
            conn.execute(
                """
                INSERT INTO remote_auth_tokens (
                    token_hash, token_kind, client_id, owner_key, profile,
                    scopes_json, expires_at, pair_hash, rotated_to_hash, label,
                    created_at, last_seen_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL)
                """,
                (
                    token_hash,
                    token_kind,
                    client_id,
                    owner_key,
                    profile,
                    _json_list(scopes),
                    expires_at,
                    pair_hash,
                    label,
                    _utc_now_iso(),
                ),
            )
            conn.commit()
        return token_hash

    def load_token(self, raw_token: str, *, token_kind: str | None = None) -> sqlite3.Row | None:
        token_hash = _secret_hash(raw_token)
        with _connect(self.db_path) as conn:
            ensure_remote_auth_schema(conn)
            if token_kind is None:
                return conn.execute(
                    "SELECT * FROM remote_auth_tokens WHERE token_hash=?",
                    (token_hash,),
                ).fetchone()
            return conn.execute(
                "SELECT * FROM remote_auth_tokens WHERE token_hash=? AND token_kind=?",
                (token_hash, token_kind),
            ).fetchone()

    def touch_token(self, raw_token: str) -> None:
        with _connect(self.db_path) as conn:
            ensure_remote_auth_schema(conn)
            conn.execute(
                "UPDATE remote_auth_tokens SET last_seen_at=? WHERE token_hash=?",
                (_utc_now_iso(), _secret_hash(raw_token)),
            )
            conn.commit()

    def revoke_token_pair(self, raw_token: str, *, rotated_to_hash: str | None = None) -> None:
        token_hash = _secret_hash(raw_token)
        with _connect(self.db_path) as conn:
            ensure_remote_auth_schema(conn)
            row = conn.execute(
                "SELECT pair_hash FROM remote_auth_tokens WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
            hashes = [token_hash]
            if row is not None and row["pair_hash"]:
                hashes.append(str(row["pair_hash"]))
            now = _utc_now_iso()
            for item_hash in hashes:
                conn.execute(
                    """
                    UPDATE remote_auth_tokens
                    SET revoked_at=COALESCE(revoked_at, ?),
                        rotated_to_hash=COALESCE(?, rotated_to_hash)
                    WHERE token_hash=?
                    """,
                    (now, rotated_to_hash, item_hash),
                )
            conn.commit()

    def token_status(self, row: sqlite3.Row | None) -> str:
        if row is None:
            return "missing"
        if row["revoked_at"]:
            return "revoked"
        expires_at = row["expires_at"]
        if expires_at is not None and int(expires_at) <= _now_epoch():
            return "expired"
        return "ok"

    def list_redacted_tokens(self, *, token_kind: str | None = None) -> list[dict[str, Any]]:
        with _connect(self.db_path) as conn:
            ensure_remote_auth_schema(conn)
            if token_kind is None:
                rows = conn.execute(
                    "SELECT * FROM remote_auth_tokens ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM remote_auth_tokens WHERE token_kind=? ORDER BY created_at DESC",
                    (token_kind,),
                ).fetchall()
            return [
                {
                    "token_fingerprint": _fingerprint_from_hash(str(row["token_hash"])),
                    "token_kind": row["token_kind"],
                    "client_id": row["client_id"],
                    "owner_key": row["owner_key"],
                    "profile": row["profile"],
                    "scopes": _parse_json_list(row["scopes_json"]),
                    "expires_at": row["expires_at"],
                    "label": row["label"],
                    "created_at": row["created_at"],
                    "last_seen_at": row["last_seen_at"],
                    "revoked_at": row["revoked_at"],
                }
                for row in rows
            ]

    def revoke_by_fingerprint(self, fingerprint: str) -> int:
        normalized = str(fingerprint or "").strip().lower()
        if len(normalized) < 8 or not re.fullmatch(r"[0-9a-f]+", normalized):
            raise ValueError("invalid_token_fingerprint")
        with _connect(self.db_path) as conn:
            ensure_remote_auth_schema(conn)
            rows = conn.execute(
                "SELECT token_hash, pair_hash FROM remote_auth_tokens WHERE token_hash LIKE ?",
                (normalized + "%",),
            ).fetchall()
            hashes = {str(row["token_hash"]) for row in rows}
            hashes.update(str(row["pair_hash"]) for row in rows if row["pair_hash"])
            if len(rows) != 1:
                return 0
            now = _utc_now_iso()
            for token_hash in hashes:
                conn.execute(
                    "UPDATE remote_auth_tokens SET revoked_at=COALESCE(revoked_at, ?) WHERE token_hash=?",
                    (now, token_hash),
                )
            conn.commit()
            return len(hashes)


IdentityResolver = Callable[[], str | None]


def _default_identity_resolver(config: RemoteAuthConfig) -> str | None:
    try:
        headers = get_http_headers(include_all=True)
    except RuntimeError:
        return None
    actual = ""
    for key, value in headers.items():
        if str(key).strip().lower() == config.identity_header:
            actual = str(value).strip()
            break
    if not actual or not config.identity_value:
        return None
    if hmac.compare_digest(actual.casefold(), config.identity_value.casefold()):
        return config.owner_key
    return None


class PrivateSQLiteOAuthProvider(OAuthProvider):
    def __init__(
        self,
        *,
        config: RemoteAuthConfig,
        db_path: str | Path,
        identity_resolver: IdentityResolver | None = None,
    ) -> None:
        errors = config.validate()
        if errors:
            raise ValueError("invalid_remote_auth_config:" + ",".join(errors))
        super().__init__(
            base_url=config.base_url,
            resource_base_url=config.base_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=False,
                valid_scopes=list(REMOTE_OAUTH_SCOPES),
                default_scopes=list(REMOTE_OAUTH_SCOPES),
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[REMOTE_REQUIRED_SCOPE],
        )
        self.config = config
        self.store = RemoteAuthStore(db_path)
        self.identity_resolver = identity_resolver or (lambda: _default_identity_resolver(config))
        self.client = OAuthClientInformationFull(
            client_id=config.oauth_client_id,
            client_name="Private ChatGPT MCP client",
            redirect_uris=list(config.oauth_redirect_uris),
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=" ".join(REMOTE_OAUTH_SCOPES),
        )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if hmac.compare_digest(str(client_id), self.config.oauth_client_id):
            return self.client
        return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError("dynamic_client_registration_disabled")

    def _rate_or_raise(self, *, bucket: str, action: str, channel: str) -> None:
        if self.store.rate_allowed(
            bucket=bucket,
            action=action,
            window_seconds=self.config.rate_limit_window_seconds,
            max_attempts=self.config.rate_limit_max_attempts,
        ):
            return
        self.store.audit(
            event_type=action,
            channel=channel,
            outcome="denied",
            reason_code="rate_limited",
            client_id=self.config.oauth_client_id,
            owner_key=self.config.owner_key,
            profile=REMOTE_OAUTH_PROFILE,
        )
        raise AuthorizeError(error="temporarily_unavailable", error_description="rate_limited")

    def _token_rate_or_raise(self, *, bucket: str, action: str) -> None:
        if self.store.rate_allowed(
            bucket=bucket,
            action=action,
            window_seconds=self.config.rate_limit_window_seconds,
            max_attempts=self.config.rate_limit_max_attempts,
        ):
            return
        self.store.audit(
            event_type=action,
            channel="oauth",
            outcome="denied",
            reason_code="rate_limited",
            client_id=self.config.oauth_client_id,
            owner_key=self.config.owner_key,
            profile=REMOTE_OAUTH_PROFILE,
        )
        raise TokenError("temporarily_unavailable", "rate_limited")

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        client_id = str(client.client_id or "")
        self._rate_or_raise(bucket=client_id, action="oauth_authorize", channel="oauth")
        if not hmac.compare_digest(client_id, self.config.oauth_client_id):
            raise AuthorizeError(error="unauthorized_client", error_description="unknown_client")
        identity = self.identity_resolver()
        if identity != self.config.owner_key:
            self.store.audit(
                event_type="oauth_authorize",
                channel="oauth",
                outcome="denied",
                reason_code="identity_denied",
                client_id=client_id,
                owner_key=None,
                profile=None,
            )
            raise AuthorizeError(error="access_denied", error_description="private_identity_denied")
        redirect_uri = str(params.redirect_uri)
        if redirect_uri not in self.config.oauth_redirect_uris:
            raise AuthorizeError(error="invalid_request", error_description="redirect_uri_not_allowed")
        request_pkce_method = _request_pkce_method()
        if request_pkce_method is not None and request_pkce_method != PKCE_METHOD:
            self.store.audit(
                event_type="oauth_authorize",
                channel="oauth",
                outcome="denied",
                reason_code="pkce_method_not_s256",
                client_id=client_id,
                owner_key=self.config.owner_key,
                profile=REMOTE_OAUTH_PROFILE,
            )
            raise AuthorizeError(error="invalid_request", error_description="pkce_s256_required")
        if not PKCE_CHALLENGE_PATTERN.fullmatch(str(params.code_challenge or "")):
            raise AuthorizeError(error="invalid_request", error_description="pkce_s256_required")
        requested_scopes = tuple(params.scopes or REMOTE_OAUTH_SCOPES)
        if not requested_scopes or not set(requested_scopes).issubset(set(REMOTE_OAUTH_SCOPES)):
            raise AuthorizeError(error="invalid_scope", error_description="scope_not_allowed")
        if REMOTE_REQUIRED_SCOPE not in requested_scopes:
            raise AuthorizeError(error="invalid_scope", error_description="required_scope_missing")
        raw_code = "mapi_ac_" + secrets.token_urlsafe(32)
        expires_at = _now_epoch() + self.config.authorization_code_ttl_seconds
        self.store.insert_authorization_code(
            raw_code=raw_code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scopes=requested_scopes,
            code_challenge=str(params.code_challenge),
            owner_key=self.config.owner_key,
            profile=REMOTE_OAUTH_PROFILE,
            expires_at=expires_at,
        )
        self.store.audit(
            event_type="oauth_authorize",
            channel="oauth",
            outcome="allowed",
            reason_code="authorization_code_issued",
            token_hash=_secret_hash(raw_code),
            client_id=client_id,
            owner_key=self.config.owner_key,
            profile=REMOTE_OAUTH_PROFILE,
        )
        return _append_query(redirect_uri, code=raw_code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        row = self.store.load_authorization_code(authorization_code)
        if row is None:
            return None
        if row["consumed_at"] or int(row["expires_at"]) <= _now_epoch():
            return None
        if str(row["client_id"]) != str(client.client_id or ""):
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=_parse_json_list(row["scopes_json"]),
            expires_at=float(row["expires_at"]),
            client_id=str(row["client_id"]),
            code_challenge=str(row["code_challenge"]),
            redirect_uri=str(row["redirect_uri"]),
            redirect_uri_provided_explicitly=True,
        )

    def _insert_pair(
        self,
        *,
        client_id: str,
        scopes: Iterable[str],
        rotated_from_raw: str | None = None,
    ) -> OAuthToken:
        now = _now_epoch()
        access_raw = "mapi_at_" + secrets.token_urlsafe(48)
        refresh_raw = "mapi_rt_" + secrets.token_urlsafe(48)
        access_hash = _secret_hash(access_raw)
        refresh_hash = _secret_hash(refresh_raw)
        self.store.insert_token(
            raw_token=access_raw,
            token_kind="access",
            client_id=client_id,
            owner_key=self.config.owner_key,
            profile=REMOTE_OAUTH_PROFILE,
            scopes=scopes,
            expires_at=now + self.config.access_ttl_seconds,
            pair_hash=refresh_hash,
        )
        self.store.insert_token(
            raw_token=refresh_raw,
            token_kind="refresh",
            client_id=client_id,
            owner_key=self.config.owner_key,
            profile=REMOTE_OAUTH_PROFILE,
            scopes=scopes,
            expires_at=now + self.config.refresh_ttl_seconds,
            pair_hash=access_hash,
        )
        if rotated_from_raw is not None:
            self.store.revoke_token_pair(rotated_from_raw, rotated_to_hash=refresh_hash)
        return OAuthToken(
            access_token=access_raw,
            token_type="Bearer",
            expires_in=self.config.access_ttl_seconds,
            refresh_token=refresh_raw,
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._token_rate_or_raise(
            bucket=f"{client.client_id}:authorization_code",
            action="oauth_token_exchange",
        )
        if not self.store.consume_authorization_code(authorization_code.code):
            raise TokenError("invalid_grant", "authorization_code_invalid_or_consumed")
        token = self._insert_pair(client_id=str(client.client_id or ""), scopes=authorization_code.scopes)
        self.store.audit(
            event_type="oauth_token_exchange",
            channel="oauth",
            outcome="allowed",
            reason_code="access_and_refresh_issued",
            token_hash=_secret_hash(token.access_token),
            client_id=str(client.client_id or ""),
            owner_key=self.config.owner_key,
            profile=REMOTE_OAUTH_PROFILE,
        )
        return token

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        self._token_rate_or_raise(
            bucket=f"{client.client_id}:refresh_load",
            action="oauth_refresh_load",
        )
        row = self.store.load_token(refresh_token, token_kind="refresh")
        status = self.store.token_status(row)
        if status != "ok" or row is None:
            self.store.audit(
                event_type="oauth_refresh_load",
                channel="oauth",
                outcome="denied",
                reason_code=f"refresh_{status}",
                token_hash=_secret_hash(refresh_token),
                client_id=str(client.client_id or ""),
            )
            return None
        if str(row["client_id"]) != str(client.client_id or ""):
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=str(row["client_id"]),
            scopes=_parse_json_list(row["scopes_json"]),
            expires_at=None if row["expires_at"] is None else int(row["expires_at"]),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self._token_rate_or_raise(
            bucket=f"{client.client_id}:refresh_exchange",
            action="oauth_refresh_exchange",
        )
        original_scopes = set(refresh_token.scopes)
        requested_scopes = list(scopes or refresh_token.scopes)
        if not set(requested_scopes).issubset(original_scopes):
            raise TokenError("invalid_scope", "refresh_scope_escalation_denied")
        token = self._insert_pair(
            client_id=str(client.client_id or ""),
            scopes=requested_scopes,
            rotated_from_raw=refresh_token.token,
        )
        self.store.audit(
            event_type="oauth_refresh_exchange",
            channel="oauth",
            outcome="allowed",
            reason_code="refresh_rotated",
            token_hash=_secret_hash(token.access_token),
            client_id=str(client.client_id or ""),
            owner_key=self.config.owner_key,
            profile=REMOTE_OAUTH_PROFILE,
        )
        return token

    async def load_access_token(self, token: str) -> AccessToken | None:
        token_hash = _secret_hash(token)
        bucket = "oauth-token:" + _fingerprint_from_hash(token_hash)
        if not self.store.rate_allowed(
            bucket=bucket,
            action="oauth_access_verify",
            window_seconds=self.config.rate_limit_window_seconds,
            max_attempts=self.config.rate_limit_max_attempts,
        ):
            self.store.audit(
                event_type="oauth_access_verify",
                channel="oauth",
                outcome="denied",
                reason_code="rate_limited",
                token_hash=token_hash,
            )
            return None
        row = self.store.load_token(token, token_kind="access")
        status = self.store.token_status(row)
        if status != "ok" or row is None:
            self.store.audit(
                event_type="oauth_access_verify",
                channel="oauth",
                outcome="denied",
                reason_code=f"access_{status}",
                token_hash=token_hash,
            )
            return None
        if str(row["owner_key"]) != self.config.owner_key or str(row["profile"]) != REMOTE_OAUTH_PROFILE:
            return None
        self.store.touch_token(token)
        return AccessToken(
            token=token,
            client_id=str(row["client_id"]),
            scopes=_parse_json_list(row["scopes_json"]),
            expires_at=None if row["expires_at"] is None else int(row["expires_at"]),
            claims={
                "owner_key": str(row["owner_key"]),
                "profile": str(row["profile"]),
                "auth_channel": "oauth",
                "token_kind": "access",
                "subject": str(row["owner_key"]),
            },
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.store.revoke_token_pair(token.token)
        self.store.audit(
            event_type="oauth_revoke",
            channel="oauth",
            outcome="allowed",
            reason_code="token_pair_revoked",
            token_hash=_secret_hash(token.token),
            client_id=token.client_id,
            owner_key=self.config.owner_key,
            profile=REMOTE_OAUTH_PROFILE,
        )


class CodexBearerVerifier(TokenVerifier):
    def __init__(self, *, config: RemoteAuthConfig, db_path: str | Path) -> None:
        errors = config.validate()
        if errors:
            raise ValueError("invalid_remote_auth_config:" + ",".join(errors))
        super().__init__(base_url=config.base_url, required_scopes=[REMOTE_REQUIRED_SCOPE])
        self.config = config
        self.store = RemoteAuthStore(db_path)

    async def verify_token(self, token: str) -> AccessToken | None:
        token_hash = _secret_hash(token)
        bucket = "codex-token:" + _fingerprint_from_hash(token_hash)
        if not self.store.rate_allowed(
            bucket=bucket,
            action="codex_bearer_verify",
            window_seconds=self.config.rate_limit_window_seconds,
            max_attempts=self.config.rate_limit_max_attempts,
        ):
            self.store.audit(
                event_type="codex_bearer_verify",
                channel="codex",
                outcome="denied",
                reason_code="rate_limited",
                token_hash=token_hash,
            )
            return None
        row = self.store.load_token(token, token_kind="codex")
        status = self.store.token_status(row)
        if status != "ok" or row is None:
            self.store.audit(
                event_type="codex_bearer_verify",
                channel="codex",
                outcome="denied",
                reason_code=f"codex_{status}",
                token_hash=token_hash,
            )
            return None
        if str(row["owner_key"]) != self.config.owner_key or str(row["profile"]) != REMOTE_CODEX_PROFILE:
            return None
        self.store.touch_token(token)
        return AccessToken(
            token=token,
            client_id=str(row["client_id"]),
            scopes=_parse_json_list(row["scopes_json"]),
            expires_at=None if row["expires_at"] is None else int(row["expires_at"]),
            claims={
                "owner_key": str(row["owner_key"]),
                "profile": str(row["profile"]),
                "auth_channel": "codex",
                "token_kind": "codex",
                "subject": str(row["owner_key"]),
                "label": row["label"],
            },
        )


def issue_codex_bearer_token(**_: Any) -> dict[str, Any]:
    raise RuntimeError("codex_bearer_retired_single_owner_admin_oauth")


def build_remote_auth_provider(
    *,
    config: RemoteAuthConfig,
    db_path: str | Path,
    identity_resolver: IdentityResolver | None = None,
) -> MultiAuth:
    oauth = PrivateSQLiteOAuthProvider(
        config=config,
        db_path=db_path,
        identity_resolver=identity_resolver,
    )
    return MultiAuth(
        server=oauth,
        verifiers=[],
        base_url=config.base_url,
        resource_base_url=config.base_url,
        required_scopes=[REMOTE_REQUIRED_SCOPE],
    )


def configure_remote_auth(mcp: Any, *, db_path: str | Path, config: RemoteAuthConfig | None = None) -> dict[str, Any]:
    resolved = config or RemoteAuthConfig.from_env()
    if not resolved.enabled:
        mcp.auth = None
        return {
            "status": "disabled",
            "schema_version": REMOTE_AUTH_SCHEMA_VERSION,
            "policy_version": REMOTE_AUTH_POLICY_VERSION,
            "enabled": False,
        }
    errors = resolved.validate()
    if errors:
        raise RuntimeError("remote_auth_config_invalid:" + ",".join(errors))
    mcp.auth = build_remote_auth_provider(config=resolved, db_path=db_path)
    return remote_auth_status(db_path=db_path, config=resolved)


def remote_auth_status(
    *,
    db_path: str | Path,
    config: RemoteAuthConfig | None = None,
) -> dict[str, Any]:
    resolved = config or RemoteAuthConfig.from_env()
    path = Path(db_path).resolve()
    store = RemoteAuthStore(path)
    with _connect(path) as conn:
        ensure_remote_auth_schema(conn)
        counts = {
            "authorization_codes": int(conn.execute("SELECT COUNT(*) FROM remote_auth_authorization_codes").fetchone()[0]),
            "active_access_tokens": int(
                conn.execute(
                    "SELECT COUNT(*) FROM remote_auth_tokens WHERE token_kind='access' AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)",
                    (_now_epoch(),),
                ).fetchone()[0]
            ),
            "active_refresh_tokens": int(
                conn.execute(
                    "SELECT COUNT(*) FROM remote_auth_tokens WHERE token_kind='refresh' AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)",
                    (_now_epoch(),),
                ).fetchone()[0]
            ),
            "active_codex_tokens": int(
                conn.execute(
                    "SELECT COUNT(*) FROM remote_auth_tokens WHERE token_kind='codex' AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)",
                    (_now_epoch(),),
                ).fetchone()[0]
            ),
            "revoked_tokens": int(
                conn.execute("SELECT COUNT(*) FROM remote_auth_tokens WHERE revoked_at IS NOT NULL").fetchone()[0]
            ),
            "audit_events": int(conn.execute("SELECT COUNT(*) FROM remote_auth_audit_events").fetchone()[0]),
        }
    return {
        "status": "ready" if resolved.enabled and not resolved.validate() else ("disabled" if not resolved.enabled else "blocked"),
        "schema_version": REMOTE_AUTH_SCHEMA_VERSION,
        "policy_version": REMOTE_AUTH_POLICY_VERSION,
        "enabled": resolved.enabled,
        "owner_key": resolved.owner_key,
        "oauth": {
            "client_id": resolved.oauth_client_id,
            "redirect_uri_count": len(resolved.oauth_redirect_uris),
            "pkce_method": PKCE_METHOD,
            "dynamic_registration": False,
            "refresh_rotation": True,
            "access_ttl_seconds": resolved.access_ttl_seconds,
            "refresh_ttl_seconds": resolved.refresh_ttl_seconds,
            "profile": REMOTE_OAUTH_PROFILE,
        },
        "legacy_codex": {
            "status": "retired_not_accepted",
            "stored_token_rows_ignored": True,
        },
        "remote_admin_exposed": True,
        "remote_admin_auth_channel": "owner_oauth_only",
        "single_remote_user": True,
        "profiles_derive_from_auth": True,
        "raw_tokens_stored": False,
        "rate_limit": {
            "window_seconds": resolved.rate_limit_window_seconds,
            "max_attempts": resolved.rate_limit_max_attempts,
        },
        "counts": counts,
        "config_errors": resolved.validate(),
        "tokens": store.list_redacted_tokens(),
    }
