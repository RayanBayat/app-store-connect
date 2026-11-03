"""The App Store locale codes Apple accepts for localizations.

Apple's OpenAPI spec (4.5.1) types `locale` as a plain string with no list of values, so the
codes below come from Apple's documentation: App Store Connect Help, "App Store localizations"
(the languages App Store Connect supports for app information and version text). When Apple
adds a language, add its code here. Codes are case-sensitive, exactly as Apple writes them.
"""

from __future__ import annotations

from typing import Final

from app_store_connect.tools.base import ToolInputError

APP_STORE_LOCALES: Final = frozenset(
    {
        "ar-SA", "bn-BD", "ca", "cs", "da", "de-DE", "el", "en-AU", "en-CA", "en-GB", "en-US",
        "es-ES", "es-MX", "fi", "fr-CA", "fr-FR", "gu-IN", "he", "hi", "hr", "hu", "id", "it",
        "ja", "kn-IN", "ko", "ml-IN", "mr-IN", "ms", "nl-NL", "no", "or-IN", "pa-IN", "pl",
        "pt-BR", "pt-PT", "ro", "ru", "sk", "sl-SI", "sv", "ta-IN", "te-IN", "th", "tr", "uk",
        "ur-PK", "vi", "zh-Hans", "zh-Hant",
    }
)  # fmt: skip


def valid_locale(value: str) -> str:
    """Return `value` if Apple accepts it as a locale code, else raise a helpful error."""
    if value not in APP_STORE_LOCALES:
        raise ToolInputError(
            f"{value!r} is not an App Store locale code. Use a code such as 'sv', 'en-US' or "
            "'zh-Hans' (case matters)."
        )
    return value
