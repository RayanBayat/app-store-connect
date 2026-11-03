"""A small async client for Apple's App Store Connect API.

Safety rules enforced here, in one place:
- Only requests listed in `ENDPOINTS_USED` can be sent (no arbitrary endpoints).
- Only https://api.appstoreconnect.apple.com ever receives the Authorization header.
  Pagination links from a response are followed only when they stay on that host.
- Presigned upload URLs (screenshots) are on other hosts, must be https, and get NO
  Authorization header, because they carry their own permission in the URL.
- Timeouts, a response size limit, and at most 3 retries with backoff on 429/5xx.
- Errors are turned into readable messages from Apple's `errors[]`; tokens never appear.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from app_store_connect.auth import TokenProvider
from app_store_connect.spec import API_BASE_URL, API_HOST, ENDPOINTS_USED

MAX_RETRIES: Final = 3
MAX_RESPONSE_BYTES: Final = 10 * 1024 * 1024
MAX_PAGES: Final = 20
MAX_BACKOFF_SECONDS: Final = 30.0
TIMEOUT: Final = httpx.Timeout(30.0, connect=10.0)
UPLOAD_TIMEOUT: Final = httpx.Timeout(120.0, connect=10.0)
RETRY_STATUSES: Final = frozenset({429, 500, 502, 503, 504})
# A POST may have been processed before a 5xx came back, so we never repeat it after one.
SAFE_TO_REPEAT: Final = frozenset({"GET", "PATCH", "DELETE", "PUT"})
ID_PATTERN: Final = re.compile(r"[A-Za-z0-9._-]{1,128}")

Sleep = Callable[[float], Awaitable[None]]

_HINTS: Final = {
    401: "Authentication failed. Check ASC_KEY_ID, ASC_ISSUER_ID and the .p8 key file.",
    403: "Apple refused this: the API key's role does not allow it (try the App Manager role).",
    404: "Apple could not find that item. Check the ID.",
    429: "Apple's rate limit was hit and retries did not help. Wait a little and try again.",
}


class AscError(Exception):
    """Anything that goes wrong talking to Apple. The text is meant for the user."""


class AscApiError(AscError):
    """Apple answered with an error status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def valid_id(value: str) -> str:
    """Return `value` if it is a plain Apple ID (safe to put into a URL path), else raise."""
    if not ID_PATTERN.fullmatch(value):
        raise AscError(f"{value!r} is not a valid App Store Connect ID.")
    return value


def fill_template(template: str, item_id: str | None) -> str:
    """Put `item_id` into the `{id}` slot of a path template."""
    if "{id}" not in template:
        return template
    if item_id is None:
        raise AscError(f"internal error: {template} needs an id")
    return template.replace("{id}", valid_id(item_id))


def is_api_url(url: str) -> bool:
    """True only for https URLs on Apple's API host."""
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == API_HOST and parts.port in (None, 443)


def format_api_error(status: int, method: str, url: str, body: dict[str, Any] | None) -> str:
    """Build a readable message from Apple's `errors[]` array (or a generic one)."""
    path = urlsplit(url).path
    details: list[str] = []
    for item in (body or {}).get("errors", []):
        if isinstance(item, dict):
            title, detail = item.get("title", ""), item.get("detail", "")
            details.append(f"{title}: {detail}".strip(": "))
    parts = [f"Apple API error {status} on {method} {path}."]
    if details:
        parts.append(" ".join(details))
    if status in _HINTS:
        parts.append(_HINTS[status])
    return " ".join(parts)


def retry_delay(attempt: int, retry_after: str | None) -> float:
    """Seconds to wait before retry number `attempt` (0-based); honours Retry-After."""
    if retry_after is not None:
        try:
            return min(max(float(retry_after), 0.0), MAX_BACKOFF_SECONDS)
        except ValueError:
            pass  # an HTTP-date Retry-After: fall back to plain backoff
    return min(float(2**attempt), MAX_BACKOFF_SECONDS)


class AscClient:
    """Sends the allowed requests to Apple. Create one per server and reuse it."""

    def __init__(
        self,
        tokens: TokenProvider,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._tokens = tokens
        self._sleep = sleep
        # redirects are never followed: a redirect could carry us (and the token) elsewhere
        self._api = httpx.AsyncClient(transport=transport, timeout=TIMEOUT, follow_redirects=False)
        self._uploads = httpx.AsyncClient(
            transport=transport, timeout=UPLOAD_TIMEOUT, follow_redirects=False
        )

    async def aclose(self) -> None:
        """Close the underlying connections."""
        await self._api.aclose()
        await self._uploads.aclose()

    async def request(
        self,
        method: str,
        template: str,
        *,
        item_id: str | None = None,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send one allowed request. `template` must be listed in ENDPOINTS_USED."""
        if (method, template) not in ENDPOINTS_USED:
            raise AscError(f"internal error: {method} {template} is not an allowed endpoint.")
        url = API_BASE_URL + fill_template(template, item_id)
        return await self._send(method, url, params, body)

    async def paginate(
        self,
        template: str,
        *,
        item_id: str | None = None,
        params: dict[str, str] | None = None,
        max_items: int = 200,
    ) -> list[dict[str, Any]]:
        """GET a list endpoint and follow `links.next` (same host only) up to `max_items`."""
        if ("GET", template) not in ENDPOINTS_USED:
            raise AscError(f"internal error: GET {template} is not an allowed endpoint.")
        page_params: dict[str, str] | None = {"limit": str(min(max_items, 200)), **(params or {})}
        url = API_BASE_URL + fill_template(template, item_id)
        items: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            page = await self._send("GET", url, page_params, None)
            items.extend(page.get("data", []))
            next_url = (page.get("links") or {}).get("next")
            if len(items) >= max_items or not next_url:
                break
            if not isinstance(next_url, str) or not is_api_url(next_url):
                raise AscError(
                    "Apple returned a pagination link to another host; not following it."
                )
            url, page_params = next_url, None  # the next link already contains the query
        return items[:max_items]

    async def upload_part(
        self, method: str, url: str, headers: list[tuple[str, str]], data: bytes
    ) -> None:
        """Send one chunk of a file to a presigned upload URL, with NO Authorization header."""
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise AscError("Apple gave an upload address that is not https; refusing to use it.")
        safe = {k: v for k, v in headers if k.lower() not in {"authorization", "cookie"}}
        try:
            response = await self._uploads.request(method, url, headers=safe, content=data)
        except httpx.HTTPError as exc:
            raise AscError(f"Uploading a file part failed: {type(exc).__name__}.") from exc
        if response.status_code >= 400:
            raise AscError(f"Uploading a file part failed with status {response.status_code}.")

    async def _send(
        self, method: str, url: str, params: dict[str, str] | None, body: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Send to Apple's API with retries; the host is checked on every call."""
        if not is_api_url(url):
            raise AscError("internal error: refusing to send credentials to a non-Apple host.")
        for attempt in range(MAX_RETRIES + 1):
            status, headers, data = await self._once(method, url, params, body)
            can_retry = status == 429 or (status in RETRY_STATUSES and method in SAFE_TO_REPEAT)
            if can_retry and attempt < MAX_RETRIES:
                await self._sleep(retry_delay(attempt, headers.get("retry-after")))
                continue
            if status >= 300:  # redirects are errors too: we never follow them
                raise AscApiError(status, format_api_error(status, method, url, data))
            return data or {}
        raise AscError("internal error: retry loop ended without a result.")

    async def _once(
        self, method: str, url: str, params: dict[str, str] | None, body: dict[str, Any] | None
    ) -> tuple[int, httpx.Headers, dict[str, Any] | None]:
        """One HTTP exchange: returns (status, headers, parsed JSON or None)."""
        headers = {"Authorization": f"Bearer {self._tokens.token()}", "Accept": "application/json"}
        try:
            async with self._api.stream(
                method, url, params=params, json=body, headers=headers
            ) as response:
                raw = await self._read_limited(response)
                return response.status_code, response.headers, self._parse(raw)
        except httpx.HTTPError as exc:
            raise AscError(f"Could not reach Apple ({type(exc).__name__}).") from exc

    @staticmethod
    async def _read_limited(response: httpx.Response) -> bytes:
        """Read the body, refusing anything bigger than MAX_RESPONSE_BYTES."""
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise AscError("Apple's response is larger than the 10 MB limit; refusing it.")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _parse(raw: bytes) -> dict[str, Any] | None:
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
