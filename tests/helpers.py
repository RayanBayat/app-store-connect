"""Shared test helpers: a generated test key and config (never a real credential)."""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app_store_connect.config import Config

KEY_ID = "TESTKEY123"
ISSUER_ID = "11111111-2222-3333-4444-555555555555"


def write_test_key(folder: Path) -> tuple[Path, ec.EllipticCurvePublicKey]:
    """Generate a throw-away P-256 key, write it as a .p8 file (0600 in a 0700 folder)."""
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    path = folder / "AuthKey_TEST.p8"
    path.write_bytes(pem)
    path.chmod(0o600)
    return path, private.public_key()


def make_config(folder: Path, **overrides: object) -> Config:
    """A valid Config pointing at a freshly generated test key."""
    path, _ = write_test_key(folder)
    values: dict[str, object] = {
        "key_id": KEY_ID,
        "issuer_id": ISSUER_ID,
        "private_key_path": path,
    }
    values.update(overrides)
    return Config(**values)  # type: ignore[arg-type]
