"""Tests for auth.py: header, claims, caching, and that errors do not leak."""

from __future__ import annotations

import os
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from app_store_connect.auth import AuthError, TokenProvider

from .helpers import ISSUER_ID, KEY_ID, FakeClock, make_config


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
    clock = FakeClock(1_000_000.0)
    provider = TokenProvider(make_config(tmp_path / "k"), clock=clock)
    first = provider.token()
    clock.now += 600
    assert provider.token() == first
    clock.now += 250  # 850s: less than a minute left of 900s
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


def sign_error(tmp_path: Path, mutate: object = None) -> str:
    config = make_config(tmp_path / "k")
    if callable(mutate):
        mutate(config.private_key_path)
    with pytest.raises(AuthError) as info:
        TokenProvider(config).token()
    return str(info.value)


def test_key_that_became_group_or_world_readable_is_refused_at_signing(tmp_path: Path) -> None:
    """Regression (L5): permissions were only checked at start-up, not when signing."""
    message = sign_error(tmp_path, lambda p: p.chmod(0o640))
    assert "chmod 600" in message


def test_key_path_swapped_for_a_fifo_is_refused_and_does_not_hang(tmp_path: Path) -> None:
    def make_fifo(path: Path) -> None:
        path.unlink()
        os.mkfifo(path, 0o600)

    assert "regular file" in sign_error(tmp_path, make_fifo)


def test_key_owned_by_someone_else_is_refused_at_signing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path / "k")
    monkeypatch.setattr(os, "getuid", lambda: config.private_key_path.stat().st_uid + 1)
    with pytest.raises(AuthError, match="owned by you"):
        TokenProvider(config).token()


def test_oversized_key_file_is_refused_at_signing(tmp_path: Path) -> None:
    def grow(path: Path) -> None:
        path.write_bytes(b"A" * 9000)

    assert "8 KiB" in sign_error(tmp_path, grow)


def encrypted_key_pem() -> bytes:
    private = ec.generate_private_key(ec.SECP256R1())
    return private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"hunter2"),
    )


def test_encrypted_key_gives_a_clear_message_not_a_crash(tmp_path: Path) -> None:
    """Regression (L6): PyJWT raises TypeError for an encrypted key; it used to escape."""
    message = sign_error(tmp_path, lambda p: p.write_bytes(encrypted_key_pem()))
    assert "unsupported or encrypted" in message
    assert "hunter2" not in message


def test_unsupported_key_type_gives_a_clear_message(tmp_path: Path) -> None:
    pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    assert "unsupported or encrypted" in sign_error(tmp_path, lambda p: p.write_bytes(pem))
