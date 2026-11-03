"""Tests for osv.py (HTTP mocked, no real network)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app_store_connect import osv

Handler = Callable[[httpx.Request], httpx.Response]


def client_for(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def vuln_record() -> dict[str, object]:
    events = [{"introduced": "0"}, {"fixed": "1.2.4"}]
    return {"id": "PYSEC-1", "summary": "Bad thing", "affected": [{"ranges": [{"events": events}]}]}


def test_check_packages_reports_vulnerability_and_uses_pypi_ecosystem() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"results": [{"vulns": [{"id": "PYSEC-1"}]}]})
        return httpx.Response(200, json=vuln_record())

    vulns = osv.check_packages(client_for(handler), [("httpx", "0.1.0")])
    assert vulns == [osv.Vulnerability("httpx", "0.1.0", "PYSEC-1", "Bad thing", ("1.2.4",))]
    assert seen[0]["queries"] == [
        {"package": {"name": "httpx", "ecosystem": "PyPI"}, "version": "0.1.0"}
    ]


def test_check_packages_clean_returns_empty() -> None:
    client = client_for(lambda _r: httpx.Response(200, json={"results": [{}]}))
    assert osv.check_packages(client, [("httpx", "0.28.1")]) == []


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
        osv.check_packages(client_for(broken), [("a", "1")])


def test_http_error_status_raises_osv_error() -> None:
    with pytest.raises(osv.OsvError, match="500"):
        osv.check_packages(client_for(lambda _r: httpx.Response(500)), [("a", "1")])


def test_format_vulnerabilities() -> None:
    assert osv.format_vulnerabilities([]) == "No known vulnerabilities found."
    text = osv.format_vulnerabilities([osv.Vulnerability("p", "1", "GO-1", "Bad", ("2",))])
    assert "GO-1 - Bad (fixed in: 2)" in text
