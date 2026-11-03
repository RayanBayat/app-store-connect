"""Check installed Python packages against the OSV vulnerability database (PyPI ecosystem).

Every function that talks to the network takes an `httpx.Client`, so tests pass a client with a
mocked transport and never touch the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

import httpx

from app_store_connect.httpio import DownloadError, fetch_limited
from app_store_connect.textsafe import untrusted

OSV_BATCH_URL: Final = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL: Final = "https://api.osv.dev/v1/vulns/"
ECOSYSTEM: Final = "PyPI"
TIMEOUT_SECONDS: Final = 20.0
# OSV documents a cap on batch size; chunking keeps every request small.
BATCH_SIZE: Final = 500
MAX_OSV_BYTES: Final = 5 * 1024 * 1024
# A package with more result pages than this is not plausible; stop instead of looping forever.
MAX_PAGES_PER_QUERY: Final = 20

Query = tuple[str, str]  # (package name, version)


class OsvError(Exception):
    """The OSV lookup failed. Never report "clean" when this happens."""


@dataclass(frozen=True, slots=True)
class Vulnerability:
    """One known vulnerability affecting one package version."""

    package: str
    version: str
    vuln_id: str
    summary: str
    fixed_in: tuple[str, ...]


def new_client() -> httpx.Client:
    """Return an HTTP client with a sane timeout and redirect handling."""
    return httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)


def _request_json(
    client: httpx.Client, method: str, url: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Send one request and return the JSON object, wrapping every failure in OsvError."""
    try:
        data = json.loads(fetch_limited(client, method, url, limit=MAX_OSV_BYTES, payload=payload))
    except (DownloadError, ValueError) as exc:
        raise OsvError(f"OSV request to {url} failed: {exc}") from exc
    if not isinstance(data, dict):
        raise OsvError(f"OSV answered {url} with something that is not a JSON object")
    return data


def make_query(name: str, version: str, page_token: str | None = None) -> dict[str, Any]:
    """Build one OSV query object for a PyPI package."""
    query: dict[str, Any] = {"package": {"name": name, "ecosystem": ECOSYSTEM}, "version": version}
    if page_token:
        query["page_token"] = page_token
    return query


def query_vuln_ids(client: httpx.Client, queries: list[Query]) -> dict[Query, set[str]]:
    """Return vulnerability ids per query, following `next_page_token` pagination."""
    found: dict[Query, set[str]] = {q: set() for q in queries}
    pending: list[tuple[Query, str | None]] = [(q, None) for q in queries]
    pages: dict[Query, int] = dict.fromkeys(queries, 0)
    while pending:
        chunk, pending = pending[:BATCH_SIZE], pending[BATCH_SIZE:]
        body = {"queries": [make_query(n, v, tok) for (n, v), tok in chunk]}
        results = _request_json(client, "POST", OSV_BATCH_URL, body).get("results", [])
        if len(results) != len(chunk):
            raise OsvError("OSV returned a different number of results than queries")
        for (query, _), result in zip(chunk, results, strict=True):
            found[query].update(v["id"] for v in result.get("vulns", []))
            if token := result.get("next_page_token"):
                pages[query] += 1
                if pages[query] > MAX_PAGES_PER_QUERY:
                    raise OsvError(f"OSV returned too many pages for {query[0]}; giving up.")
                pending.append((query, token))
    return found


def fixed_versions(vuln: dict[str, Any]) -> tuple[str, ...]:
    """Collect every `fixed` version mentioned in an OSV record."""
    fixed: set[str] = set()
    for affected in vuln.get("affected", []):
        for rng in affected.get("ranges", []):
            fixed.update(e["fixed"] for e in rng.get("events", []) if "fixed" in e)
    return tuple(sorted(fixed))


def describe(client: httpx.Client, query: Query, vuln_id: str) -> Vulnerability:
    """Fetch one vulnerability record and turn it into a `Vulnerability`."""
    record = _request_json(client, "GET", OSV_VULN_URL + vuln_id)
    return Vulnerability(
        package=query[0],
        version=query[1],
        vuln_id=vuln_id,
        summary=record.get("summary") or "(no summary)",
        fixed_in=fixed_versions(record),
    )


def check_packages(client: httpx.Client, packages: list[Query]) -> list[Vulnerability]:
    """Return every known vulnerability affecting the given (name, version) packages."""
    found: list[Vulnerability] = []
    for query, ids in sorted(query_vuln_ids(client, packages).items()):
        found.extend(describe(client, query, vuln_id) for vuln_id in sorted(ids))
    return found


def format_vulnerabilities(vulns: list[Vulnerability]) -> str:
    """Render vulnerabilities as short markdown bullets (or a 'none found' line)."""
    if not vulns:
        return "No known vulnerabilities found."
    lines: list[str] = []
    for v in vulns:
        fixes = ", ".join(v.fixed_in) or "none listed"
        lines.append(
            f"- `{v.package} {v.version}`: {v.vuln_id} - {untrusted(v.summary)} (fixed in: {fixes})"
        )
    return "\n".join(lines)
