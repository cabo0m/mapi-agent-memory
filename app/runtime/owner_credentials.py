from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def hash_owner_password(password: str, *, iterations: int = PASSWORD_ITERATIONS) -> str:
    raw = str(password)
    if len(raw) < 12:
        raise ValueError("owner_password_too_short")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), salt, int(iterations), dklen=32)
    return f"{PASSWORD_SCHEME}${int(iterations)}${_b64encode(salt)}${_b64encode(digest)}"


def valid_owner_password_hash(value: str | None) -> bool:
    try:
        scheme, iterations, salt, digest = str(value or "").split("$", 3)
        if scheme != PASSWORD_SCHEME or int(iterations) < 100_000:
            return False
        return len(_b64decode(salt)) >= 16 and len(_b64decode(digest)) == 32
    except (TypeError, ValueError, UnicodeError):
        return False


def verify_owner_password(password: str, encoded: str | None) -> bool:
    if not valid_owner_password_hash(encoded):
        return False
    scheme, iterations, salt_text, digest_text = str(encoded).split("$", 3)
    del scheme
    salt = _b64decode(salt_text)
    expected = _b64decode(digest_text)
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        salt,
        int(iterations),
        dklen=len(expected),
    )
    return hmac.compare_digest(actual, expected)
