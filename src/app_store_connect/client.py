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
import ipaddress
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
# Apple's presigned screenshot uploads go to hosts under apple.com (they look like
# store-NNN.blobstore.apple.com). This is Apple's upload host pattern as we understand it and
# MUST BE VERIFIED ON THE FIRST REAL UPLOAD; if Apple uses another domain, change it here, in
# this one place. Anything else (other domains, IP addresses) is refused, so a bad answer
# cannot make the server call an internal address.
UPLOAD_HOST_SUFFIX: Final = ".apple.com"
# Apple IDs are numbers or UUIDs. No dots (so ".." cannot climb a path), no underscores, and
# the first character is never a dash.
ID_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}")

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


def build_url(template: str, item_id: str | None) -> str:
    """Return the full API URL for `template`, proving the path is exactly what we filled in.

    Even with a checked ID, we parse the URL the way httpx will and compare the path, so a
    surprise in URL normalisation (dot segments, encoding) can never reach another endpoint.
    """
    path = fill_template(template, item_id)
    url = API_BASE_URL + path
    if httpx.URL(url).path != path:
        raise AscError("internal error: the request path changed while building the URL.")
    return url


def is_allowed_get_path(path: str) -> bool:
    """True when `path` is a concrete path of a GET endpoint in ENDPOINTS_USED."""
    for method, template in ENDPOINTS_USED:
        if method != "GET":
            continue
        pattern = re.escape(template).replace(re.escape("{id}"), ID_PATTERN.pattern)
        if re.fullmatch(pattern, path):
            return True
    return False


def is_api_url(url: str) -> bool:
    """True only for https URLs on Apple's API host."""
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == API_HOST and parts.port in (None, 443)


MAX_REASONS: Final = 20


def error_text(error: object) -> str:
    """One Apple error object as 'title: detail' (empty text when it is not an error object)."""
    if not isinstance(error, dict):
        return ""
    return f"{error.get('title', '')}: {error.get('detail', '')}".strip(": ")


def associated_reasons(item: dict[str, Any]) -> list[str]:
    """Return the problems Apple lists under `meta.associatedErrors`.

    For some refusals (for example "this version is not in a valid state for review") the
    top-level error says nothing useful; the real reasons, such as a missing copyright, are here.
    """
    associated = (item.get("meta") or {}).get("associatedErrors")
    if not isinstance(associated, dict):
        return []
    reasons: list[str] = []
    for errors in associated.values():
        if not isinstance(errors, list):
            continue
        reasons += [text for error in errors if (text := error_text(error))]
    return reasons


def format_api_error(status: int, method: str, url: str, body: dict[str, Any] | None) -> str:
    """Build a readable message from Apple's `errors[]` array (or a generic one)."""
    path = urlsplit(url).path
    details: list[str] = []
    reasons: list[str] = []
    for item in (body or {}).get("errors", []):
        if isinstance(item, dict):
            title, detail = item.get("title", ""), item.get("detail", "")
            details.append(f"{title}: {detail}".strip(": "))
            reasons.extend(associated_reasons(item))
    parts = [f"Apple API error {status} on {method} {path}."]
    if details:
        parts.append(" ".join(details))
    if reasons:
        parts.append("Apple lists these problems: " + "; ".join(reasons[:MAX_REASONS]) + ".")
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


def check_upload_target(method: str, url: str) -> None:
    """Refuse any presigned upload that is not a PUT over https to an Apple upload host."""
    if method != "PUT":
        raise AscError("Apple gave an upload step that is not a PUT; refusing to use it.")
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not host:
        raise AscError("Apple gave an upload address that is not https; refusing to use it.")
    try:
        port = parts.port
    except ValueError:
        port = -1
    if port not in (None, 443):
        raise AscError("Apple gave an upload address with an unusual port; refusing to use it.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # not an IP address: good
    else:
        raise AscError("Apple gave an upload address that is an IP address; refusing to use it.")
    if not host.endswith(UPLOAD_HOST_SUFFIX):
        raise AscError("Apple gave an upload address outside apple.com; refusing to use it.")


def parse_error_body(raw: bytes) -> dict[str, Any] | None:
    """Read an error answer as JSON, or None when it is not JSON (the status is still reported)."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_success_body(raw: bytes, method: str, url: str) -> dict[str, Any]:
    """Read a successful (2xx) answer. An empty body (for example 204) means no data: {}.

    Anything else must be a JSON object. If it is not, we stop with a clear message instead of
    pretending Apple sent nothing: the caller would otherwise fail later with a confusing error.
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise AscError(
            f"Apple's answer to {method} {urlsplit(url).path} was not a JSON object, so it "
            "cannot be used. This can happen when Apple's service has a problem; try again later."
        )
    return data


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
        url = build_url(template, item_id)
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
        url = build_url(template, item_id)
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
            if not is_allowed_get_path(urlsplit(next_url).path):
                raise AscError(
                    "Apple returned a pagination link to an endpoint that is not an allowed "
                    "endpoint; not following it."
                )
            url, page_params = next_url, None  # the next link already contains the query
        return items[:max_items]

    async def upload_part(
        self, method: str, url: str, headers: list[tuple[str, str]], data: bytes
    ) -> None:
        """Send one chunk of a file to a presigned upload URL, with NO Authorization header."""
        check_upload_target(method, url)
        safe = {
            name: value
            for name, value in headers
            if name.lower() not in {"authorization", "cookie"}
        }
        try:
            response = await self._uploads.request(method, url, headers=safe, content=data)
        except httpx.HTTPError as exc:
            raise AscError(f"Uploading a file part failed: {type(exc).__name__}.") from exc
        if response.status_code >= 300:  # redirects are failures: we never follow them
            raise AscError(f"Uploading a file part failed with status {response.status_code}.")

    async def _send(
        self, method: str, url: str, params: dict[str, str] | None, body: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Send to Apple's API with retries; the host is checked on every call."""
        if not is_api_url(url):
            raise AscError("internal error: refusing to send credentials to a non-Apple host.")
        for attempt in range(MAX_RETRIES + 1):
            status, headers, raw = await self._once(method, url, params, body)
            can_retry = status == 429 or (status in RETRY_STATUSES and method in SAFE_TO_REPEAT)
            if can_retry and attempt < MAX_RETRIES:
                await self._sleep(retry_delay(attempt, headers.get("retry-after")))
                continue
            if status >= 300:  # redirects are errors too: we never follow them
                error_body = parse_error_body(raw)
                raise AscApiError(status, format_api_error(status, method, url, error_body))
            return parse_success_body(raw, method, url)
        raise AscError("internal error: retry loop ended without a result.")

    async def _once(
        self, method: str, url: str, params: dict[str, str] | None, body: dict[str, Any] | None
    ) -> tuple[int, httpx.Headers, bytes]:
        """One HTTP exchange: returns (status, headers, the raw body bytes)."""
        headers = {"Authorization": f"Bearer {self._tokens.token()}", "Accept": "application/json"}
        try:
            async with self._api.stream(
                method, url, params=params, json=body, headers=headers
            ) as response:
                raw = await self._read_limited(response)
                return response.status_code, response.headers, raw
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
