"""Sign the short-lived token Apple wants with every API request (a JWT).

Apple's rules (App Store Connect API, "Generating tokens for API requests"):
- algorithm ES256; header has `kid` (your key ID) and `typ` JWT;
- claims: `iss` (issuer ID), `iat`, `exp` (at most 20 minutes after `iat`), `aud`
  "appstoreconnect-v1";
- individual keys have no issuer, so they send `sub: "user"` instead of `iss`.

Safety: the key file is read only at the moment of signing and never kept in memory
afterwards. The token and the key are never logged or put into an error message.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Final

import jwt
from cryptography.exceptions import UnsupportedAlgorithm

from app_store_connect.config import Config, KeyFileError, read_key_file

AUDIENCE: Final = "appstoreconnect-v1"
TOKEN_LIFETIME_SECONDS: Final = 15 * 60  # Apple allows up to 20 minutes; we stay well below
RENEW_MARGIN_SECONDS: Final = 60  # renew a token that has less than this left


class AuthError(Exception):
    """The token could not be created. The message never contains key material."""


class TokenProvider:
    """Hands out a valid token, creating a new one shortly before the old one expires."""

    def __init__(self, config: Config, clock: Callable[[], float] = time.time) -> None:
        self._config = config
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0

    def token(self) -> str:
        """Return a token that is valid for at least another minute."""
        now = self._clock()
        if self._token is None or now >= self._expires_at - RENEW_MARGIN_SECONDS:
            self._token = self._sign(int(now))
            self._expires_at = int(now) + TOKEN_LIFETIME_SECONDS
        return self._token

    def _claims(self, issued_at: int) -> dict[str, Any]:
        claims: dict[str, Any] = {
            "iat": issued_at,
            "exp": issued_at + TOKEN_LIFETIME_SECONDS,
            "aud": AUDIENCE,
        }
        if self._config.key_kind == "individual":
            claims["sub"] = "user"
        else:
            claims["iss"] = self._config.issuer_id
        return claims

    def _sign(self, issued_at: int) -> str:
        try:
            key = read_key_file(self._config.private_key_path)
        except KeyFileError as exc:
            raise AuthError(f"Could not sign the API token. {exc}") from None
        try:
            return jwt.encode(
                self._claims(issued_at),
                key,
                algorithm="ES256",
                headers={"kid": self._config.key_id, "typ": "JWT"},
            )
        except (TypeError, UnsupportedAlgorithm, ValueError, jwt.PyJWTError) as exc:
            # TypeError: PyJWT's answer to an encrypted key. UnsupportedAlgorithm / InvalidKeyError:
            # a key type we cannot use. ValueError: not a PEM key at all.
            # Deliberately not chaining the original text: it could quote key material.
            raise AuthError(
                f"Could not sign the API token ({type(exc).__name__}): unsupported or encrypted "
                "key. ASC_PRIVATE_KEY_PATH must point to the unencrypted ES256 .p8 file "
                "downloaded from App Store Connect."
            ) from None
