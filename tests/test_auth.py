"""Tests for auth.py: header, claims, caching, and that errors do not leak."""

from __future__ import annotations

from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app_store_connect.auth import AuthError, TokenProvider

from .helpers import ISSUER_ID, KEY_ID, make_config


def test_team_token_header_and_claims(tmp_path: Path) -> None:
    config = make_config(tmp_path / "k")
    token = TokenProvider(config, clock=lambda: 1_000_000.0).token()
    header = jwt.get_unverified_header(token)
    assert header == {"alg": "ES256", "kid": KEY_ID, "typ": "JWT"}
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims == {
        "iss": ISSUER_ID,
        "iat": 1_000_000,
        "exp": 1_000_900,
        "aud": "appstoreconnect-v1",
    }


def test_signature_verifies_with_the_public_key(tmp_path: Path) -> None:
    config = make_config(tmp_path / "k")
    private = serialization.load_pem_private_key(
        config.private_key_path.read_bytes(), password=None
    )
    assert isinstance(private, ec.EllipticCurvePrivateKey)
    token = TokenProvider(config).token()
    claims = jwt.decode(
        token, private.public_key(), algorithms=["ES256"], audience="appstoreconnect-v1"
    )
    assert claims["iss"] == ISSUER_ID


def test_lifetime_is_under_apples_20_minute_limit(tmp_path: Path) -> None:
    token = TokenProvider(make_config(tmp_path / "k")).token()
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] <= 20 * 60


def test_individual_key_uses_sub_user_and_no_iss(tmp_path: Path) -> None:
    config = make_config(tmp_path / "k", key_kind="individual")
    claims = jwt.decode(TokenProvider(config).token(), options={"verify_signature": False})
    assert claims["sub"] == "user"
    assert "iss" not in claims


def test_token_is_cached_then_renewed_before_expiry(tmp_path: Path) -> None:
    now = [1_000_000.0]
    provider = TokenProvider(make_config(tmp_path / "k"), clock=lambda: now[0])
    first = provider.token()
    now[0] += 600
    assert provider.token() == first
    now[0] += 250  # 850s: less than a minute left of 900s
    assert provider.token() != first


def test_key_is_read_only_when_signing(tmp_path: Path) -> None:
    config = make_config(tmp_path / "k")
    provider = TokenProvider(config)
    config.private_key_path.unlink()  # not needed until the first token is created
    with pytest.raises(AuthError, match="Could not sign"):
        provider.token()


def test_bad_key_error_does_not_leak_key_contents(tmp_path: Path) -> None:
    config = make_config(tmp_path / "k")
    config.private_key_path.write_text("-----BEGIN PRIVATE KEY-----\nLEAKME\n", encoding="utf-8")
    with pytest.raises(AuthError) as info:
        TokenProvider(config).token()
    assert "LEAKME" not in str(info.value)
    assert info.value.__cause__ is None
