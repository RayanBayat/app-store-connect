#!/usr/bin/env python3
"""Check the dependencies in a go.mod file against the OSV vulnerability database.

Usage: python3 osv_check.py path/to/go.mod

Exit codes: 0 = no known vulnerabilities, 1 = vulnerabilities found, 2 = error.

Version format: OSV's Go ecosystem stores versions without the leading "v"
(e.g. "1.2.3"), so we strip it before querying. The Go standard library is
queried as the package "stdlib" using the version from the `toolchain` or `go` line.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL = "https://api.osv.dev/v1/vulns/"
TIMEOUT = 20
# OSV's own examples cap a batch well below this; chunking keeps requests small.
BATCH_SIZE = 500

Query = tuple[str, str]  # (package name, version without "v")


class OsvError(Exception):
    """Raised for network or parse problems (maps to exit code 2)."""


def strip_comment(line: str) -> str:
    """Remove a trailing `// ...` comment (including `// indirect`)."""
    return line.split("//", 1)[0].strip()


def parse_requirement(text: str) -> Query | None:
    """Turn "module/path v1.2.3" into (module, "1.2.3"); None if not that shape."""
    parts = text.split()
    if len(parts) != 2:
        return None
    return parts[0], parts[1].removeprefix("v")


def parse_go_mod(text: str) -> list[Query]:
    """Extract (package, version) pairs from go.mod text.

    Only `require` entries and the Go toolchain are read; replace, exclude and
    retract directives are skipped on purpose (they do not add dependencies).
    """
    queries: list[Query] = []
    block: str | None = None  # name of the directive whose "(...)" block we are in
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


def http_json(url: str, payload: dict | None = None) -> dict:
    """GET (or POST when payload is given) JSON with a timeout; wrap failures."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise OsvError(f"OSV request to {url} failed: {exc}") from exc


def make_query(name: str, version: str, page_token: str | None = None) -> dict:
    """Build one OSV query object for a Go package."""
    query: dict = {"package": {"name": name, "ecosystem": "Go"}, "version": version}
    if page_token:
        query["page_token"] = page_token
    return query


def query_vuln_ids(queries: list[Query]) -> dict[Query, set[str]]:
    """Return vulnerability ids per query, following `next_page_token` pagination."""
    found: dict[Query, set[str]] = {q: set() for q in queries}
    pending: list[tuple[Query, str | None]] = [(q, None) for q in queries]
    while pending:
        chunk, pending = pending[:BATCH_SIZE], pending[BATCH_SIZE:]
        body = {"queries": [make_query(n, v, tok) for (n, v), tok in chunk]}
        results = http_json(OSV_BATCH_URL, body).get("results", [])
        if len(results) != len(chunk):
            raise OsvError("OSV returned a different number of results than queries")
        for (query, _), result in zip(chunk, results, strict=True):
            found[query].update(v["id"] for v in result.get("vulns", []))
            if token := result.get("next_page_token"):
                pending.append((query, token))
    return found


def fixed_versions(vuln: dict) -> list[str]:
    """Collect every `fixed` version mentioned in an OSV record."""
    fixed: set[str] = set()
    for affected in vuln.get("affected", []):
        for rng in affected.get("ranges", []):
            fixed.update(e["fixed"] for e in rng.get("events", []) if "fixed" in e)
    return sorted(fixed)


def describe(vuln_id: str) -> str:
    """Fetch one vulnerability record and format it for humans."""
    vuln = http_json(OSV_VULN_URL + vuln_id)
    summary = vuln.get("summary") or "(no summary)"
    fixes = ", ".join(fixed_versions(vuln)) or "none listed"
    return f"  {vuln_id}: {summary}\n    fixed in: {fixes}"


def run(go_mod: Path) -> int:
    """Check one go.mod file and print a report. Returns the exit code."""
    queries = parse_go_mod(go_mod.read_text(encoding="utf-8"))
    if not queries:
        print("No requirements found in go.mod.")
        return 0
    hits = {q: ids for q, ids in query_vuln_ids(queries).items() if ids}
    if not hits:
        print(f"No known vulnerabilities in {len(queries)} checked modules.")
        return 0
    for (name, version), ids in sorted(hits.items()):
        print(f"{name} {version}")
        for vuln_id in sorted(ids):
            print(describe(vuln_id))
    return 1


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: osv_check.py path/to/go.mod", file=sys.stderr)
        return 2
    try:
        return run(Path(args[0]))
    except (OsvError, OSError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
