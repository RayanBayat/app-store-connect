"""Download helpers for the maintenance report: size-capped and careful with redirects.

The report talks to PyPI, OSV and Apple's developer site. None of them should send us huge
bodies or bounce us to another host, so every download goes through `fetch_limited`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final
from urllib.parse import urljoin, urlsplit

import httpx

MAX_REDIRECTS: Final = 3
REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})


class DownloadError(Exception):
    """A download failed or broke a rule. The text is meant for the report."""


def _redirect_allowed(url: str, host_suffixes: Sequence[str]) -> bool:
    """True for https URLs whose host is one of, or a subdomain of, `host_suffixes`."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(
        host == suffix or host.endswith("." + suffix) for suffix in host_suffixes
    )


def fetch_limited(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    limit: int,
    payload: dict[str, Any] | None = None,
    redirect_hosts: Sequence[str] = (),
) -> bytes:
    """Send one request and return the body, refusing bodies over `limit` bytes.

    Redirects are not followed unless the target is https on one of `redirect_hosts`
    (default: none). Non-2xx answers are errors.
    """
    for _ in range(MAX_REDIRECTS + 1):
        try:
            with client.stream(method, url, json=payload, follow_redirects=False) as response:
                if response.status_code in REDIRECT_STATUSES:
                    target = urljoin(url, response.headers.get("location", ""))
                    if not _redirect_allowed(target, redirect_hosts):
                        raise DownloadError(f"{url} redirected somewhere not allowed; refusing it.")
                    url = target
                    continue
                response.raise_for_status()
                return _read(response, url, limit)
        except httpx.HTTPError as exc:
            raise DownloadError(f"Could not download {url}: {exc}") from exc
    raise DownloadError(f"{url} redirected too many times.")


def _read(response: httpx.Response, url: str, limit: int) -> bytes:
    """Read the body in pieces and stop as soon as it is bigger than `limit`."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > limit:
            raise DownloadError(f"{url} is larger than {limit} bytes; refusing it.")
        chunks.append(chunk)
    return b"".join(chunks)
