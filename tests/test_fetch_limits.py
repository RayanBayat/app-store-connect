"""INFO: report fetches have size caps, a page limit and never follow redirects blindly."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app_store_connect import osv, report
from app_store_connect.maintenance import new_report_client

Handler = Callable[[httpx.Request], httpx.Response]


def client_for(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_oversized_pypi_answer_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report, "MAX_PYPI_BYTES", 1000)
    big = b"<rss>" + b"x" * 5000 + b"</rss>"
    with pytest.raises(report.ReportError, match="larger than"):
        report.latest_on_pypi(client_for(lambda _r: httpx.Response(200, content=big)), "httpx")


def test_oversized_osv_answer_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(osv, "MAX_OSV_BYTES", 1000)
    big = json.dumps({"results": [{}], "pad": "x" * 5000}).encode()
    with pytest.raises(osv.OsvError, match="larger than"):
        osv.check_packages(client_for(lambda _r: httpx.Response(200, content=big)), [("a", "1")])


def test_endless_osv_next_page_token_stops_at_the_limit() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"results": [{"vulns": [], "next_page_token": "again"}]})

    with pytest.raises(osv.OsvError, match="too many pages"):
        osv.query_vuln_ids(client_for(handler), [("a", "1")])
    assert len(calls) <= osv.MAX_PAGES_PER_QUERY + 1


def test_redirects_are_not_followed_for_pypi_or_osv() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(str(request.url.host))
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    with pytest.raises(report.ReportError):
        report.latest_on_pypi(client_for(handler), "httpx")
    with pytest.raises(osv.OsvError):
        osv.check_packages(client_for(handler), [("a", "1")])
    assert "evil.example" not in hosts


def test_report_client_does_not_follow_redirects() -> None:
    with new_report_client() as client:
        assert client.follow_redirects is False
    assert osv.new_client().follow_redirects is False


def spec_handler(location: str, hits: list[str]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url.host))
        if request.url.host == "developer.apple.com":
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200, content=b"nope")

    return handler


def test_spec_may_redirect_within_developer_apple_com_only() -> None:
    hits: list[str] = []
    handler = spec_handler("https://docs-assets.developer.apple.com/spec.zip", hits)
    with pytest.raises(report.ReportError, match="could not be read"):  # got bytes, not a zip
        report.load_spec(client_for(handler))
    assert hits == ["developer.apple.com", "docs-assets.developer.apple.com"]


@pytest.mark.parametrize(
    "location",
    ["https://evil.example/spec.zip", "https://developer.apple.com.evil.example/x",
     "http://developer.apple.com/spec.zip"],
)  # fmt: skip
def test_spec_redirect_elsewhere_is_refused(location: str) -> None:
    hits: list[str] = []
    with pytest.raises(report.ReportError, match="redirect"):
        report.load_spec(client_for(spec_handler(location, hits)))
    assert hits == ["developer.apple.com"]
