"""Check the Go dependencies of a go.mod file against the OSV vulnerability database.

Version format: OSV's Go ecosystem stores versions without the leading "v" (for example
"1.2.3"), so we strip it before querying. The Go standard library is queried as the package
"stdlib" using the version from the `toolchain` or `go` line.

Every function that talks to the network takes an `httpx.Client`, so tests can pass a client
with a mocked transport instead of touching the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

import httpx

OSV_BATCH_URL: Final = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL: Final = "https://api.osv.dev/v1/vulns/"
TIMEOUT_SECONDS: Final = 20.0
# OSV documents a cap on batch size; chunking keeps every request small.
BATCH_SIZE: Final = 500

Query = tuple[str, str]  # (package name, version without "v")


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
    return httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True)


def strip_comment(line: str) -> str:
    """Remove a trailing `// ...` comment (including `// indirect`)."""
    return line.split("//", 1)[0].strip()


def parse_requirement(text: str) -> Query | None:
    """Turn "module/path v1.2.3" into (module, "1.2.3"); None if it is not that shape."""
    parts = text.split()
    if len(parts) != 2:
        return None
    return parts[0], parts[1].removeprefix("v")


def parse_go_mod(text: str) -> list[Query]:
    """Extract (package, version) pairs from go.mod text.

    Only `require` entries and the Go toolchain are read; replace, exclude and retract
    directives are skipped on purpose (they do not add dependencies).
    """
    queries: list[Query] = []
    block: str | None = None  # name of the directive whose "(...)" block we are inside
    go_version: str | None = None
    toolchain: str | None = None
    for raw in text.splitlines():
        line = strip_comment(raw)
        if not line:
            continue
        if block is not None:
            if line == ")":
                block = None
            elif block == "require" and (req := parse_requirement(line)):
                queries.append(req)
            continue
        word, _, rest = line.partition(" ")
        rest = rest.strip()
        if rest == "(":
            block = word
        elif word == "require" and (req := parse_requirement(rest)):
            queries.append(req)
        elif word == "toolchain" and rest.startswith("go"):
            toolchain = rest.removeprefix("go")
        elif word == "go" and re.fullmatch(r"\d+\.\d+(\.\d+)?", rest):
            go_version = rest
    stdlib_version = toolchain or go_version
    if stdlib_version:
        queries.append(("stdlib", stdlib_version))
    return queries


def _request_json(
    client: httpx.Client, method: str, url: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Send one request and return the JSON object, wrapping every failure in OsvError."""
    try:
        response = client.request(method, url, json=payload)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OsvError(f"OSV request to {url} failed: {exc}") from exc
    if not isinstance(data, dict):
        raise OsvError(f"OSV answered {url} with something that is not a JSON object")
    return data


def make_query(name: str, version: str, page_token: str | None = None) -> dict[str, Any]:
    """Build one OSV query object for a Go package."""
    query: dict[str, Any] = {"package": {"name": name, "ecosystem": "Go"}, "version": version}
    if page_token:
        query["page_token"] = page_token
    return query


def query_vuln_ids(client: httpx.Client, queries: list[Query]) -> dict[Query, set[str]]:
    """Return vulnerability ids per query, following `next_page_token` pagination."""
    found: dict[Query, set[str]] = {q: set() for q in queries}
    pending: list[tuple[Query, str | None]] = [(q, None) for q in queries]
    while pending:
        chunk, pending = pending[:BATCH_SIZE], pending[BATCH_SIZE:]
        body = {"queries": [make_query(n, v, tok) for (n, v), tok in chunk]}
        results = _request_json(client, "POST", OSV_BATCH_URL, body).get("results", [])
        if len(results) != len(chunk):
            raise OsvError("OSV returned a different number of results than queries")
        for (query, _), result in zip(chunk, results, strict=True):
            found[query].update(v["id"] for v in result.get("vulns", []))
            if token := result.get("next_page_token"):
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


def check_go_mod(client: httpx.Client, go_mod_text: str) -> list[Vulnerability]:
    """Return every known vulnerability affecting the requirements in `go_mod_text`."""
    queries = parse_go_mod(go_mod_text)
    found: list[Vulnerability] = []
    for query, ids in sorted(query_vuln_ids(client, queries).items()):
        found.extend(describe(client, query, vuln_id) for vuln_id in sorted(ids))
    return found


def format_vulnerabilities(vulns: list[Vulnerability]) -> str:
    """Render vulnerabilities as short markdown bullets (or a 'none found' line)."""
    if not vulns:
        return "No known vulnerabilities found."
    lines: list[str] = []
    for v in vulns:
        fixes = ", ".join(v.fixed_in) or "none listed"
        lines.append(f"- `{v.package} {v.version}`: {v.vuln_id} - {v.summary} (fixed in: {fixes})")
    return "\n".join(lines)
