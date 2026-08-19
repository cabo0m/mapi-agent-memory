from __future__ import annotations

import pytest

from app.runtime.owner_credentials import hash_owner_password, valid_owner_password_hash, verify_owner_password


def test_owner_password_hash_is_salted_and_verifiable() -> None:
    first = hash_owner_password("correct horse battery staple")
    second = hash_owner_password("correct horse battery staple")

    assert first != second
    assert "correct horse" not in first
    assert valid_owner_password_hash(first) is True
    assert verify_owner_password("correct horse battery staple", first) is True
    assert verify_owner_password("wrong password", first) is False


def test_owner_password_rejects_short_secret() -> None:
    with pytest.raises(ValueError, match="owner_password_too_short"):
        hash_owner_password("too-short")
