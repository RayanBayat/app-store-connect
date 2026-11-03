"""Tests for osv.py (HTTP mocked, no real network)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app_store_connect import osv

GO_MOD = """\
module example.com/app

go 1.24.0

toolchain go1.24.3

require github.com/a/direct v1.2.3

require (
\tgithub.com/b/block v0.4.0
\tgithub.com/c/indirect v2.0.0+incompatible // indirect
)

replace github.com/a/direct => ../direct

replace (
\tgithub.com/x/y v1.0.0 => github.com/z/y v1.0.1
)

exclude github.com/bad/mod v9.9.9
retract v1.0.0
"""

Handler = Callable[[httpx.Request], httpx.Response]


def client_for(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def vuln_record() -> dict[str, object]:
    events = [{"introduced": "0"}, {"fixed": "1.2.4"}]
    return {"id": "GO-1", "summary": "Bad thing", "affected": [{"ranges": [{"events": events}]}]}


def test_parse_go_mod_collects_requires_and_toolchain() -> None:
    assert osv.parse_go_mod(GO_MOD) == [
        ("github.com/a/direct", "1.2.3"),
        ("github.com/b/block", "0.4.0"),
        ("github.com/c/indirect", "2.0.0+incompatible"),
        ("stdlib", "1.24.3"),
    ]


def test_parse_go_mod_falls_back_to_go_line() -> None:
    assert osv.parse_go_mod("module m\ngo 1.23.1\n") == [("stdlib", "1.23.1")]


def test_parse_go_mod_skips_replace_exclude_retract_blocks() -> None:
    text = "exclude (\n\tgithub.com/p/q v1.0.0\n)\nretract (\n\tv1.0.0\n)\n"
    assert osv.parse_go_mod(text) == []


def test_check_go_mod_reports_vulnerability_and_strips_v() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"results": [{"vulns": [{"id": "GO-1"}]}]})
        return httpx.Response(200, json=vuln_record())

    vulns = osv.check_go_mod(client_for(handler), "module m\nrequire github.com/a/direct v1.2.3\n")
    assert vulns == [
        osv.Vulnerability("github.com/a/direct", "1.2.3", "GO-1", "Bad thing", ("1.2.4",))
    ]
    assert seen[0]["queries"] == [
        {"package": {"name": "github.com/a/direct", "ecosystem": "Go"}, "version": "1.2.3"}
    ]


def test_check_go_mod_clean_returns_empty() -> None:
    client = client_for(lambda _r: httpx.Response(200, json={"results": [{}]}))
    assert osv.check_go_mod(client, "module m\nrequire github.com/a/direct v1.2.3\n") == []


def test_pagination_follows_next_page_token() -> None:
    replies = [
        {"results": [{"vulns": [{"id": "A"}], "next_page_token": "tok"}]},
        {"results": [{"vulns": [{"id": "B"}]}]},
    ]
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=replies.pop(0))

    found = osv.query_vuln_ids(client_for(handler), [("m", "1.0.0")])
    assert found[("m", "1.0.0")] == {"A", "B"}
    assert bodies[1]["queries"][0]["page_token"] == "tok"  # type: ignore[index]


def test_result_count_mismatch_raises() -> None:
    client = client_for(lambda _r: httpx.Response(200, json={"results": []}))
    with pytest.raises(osv.OsvError, match="different number"):
        osv.query_vuln_ids(client, [("m", "1.0.0")])


def test_network_error_raises_osv_error() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(osv.OsvError, match="offline"):
        osv.check_go_mod(client_for(broken), "module m\nrequire a/b v1.0.0\n")


def test_http_error_status_raises_osv_error() -> None:
    client = client_for(lambda _r: httpx.Response(500))
    with pytest.raises(osv.OsvError, match="500"):
        osv.check_go_mod(client, "module m\nrequire a/b v1.0.0\n")


def test_format_vulnerabilities() -> None:
    assert osv.format_vulnerabilities([]) == "No known vulnerabilities found."
    text = osv.format_vulnerabilities([osv.Vulnerability("p", "1", "GO-1", "Bad", ("2",))])
    assert "GO-1 - Bad (fixed in: 2)" in text
