"""Tests for github.py with the HTTP layer mocked."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app_store_connect import github
from app_store_connect.github import GitHubClient, GitHubError

Handler = Callable[[httpx.Request], httpx.Response]


def client_for(handler: Handler) -> GitHubClient:
    return GitHubClient(httpx.Client(transport=httpx.MockTransport(handler)))


def test_releases_skips_drafts_and_parses_fields() -> None:
    payload = [
        {"tag_name": "v0.23.0", "name": "0.23.0", "body": "notes", "draft": False,
         "published_at": "2026-10-01T10:00:00Z", "prerelease": False},
        {"tag_name": "v0.24.0", "draft": True},
    ]  # fmt: skip
    releases = client_for(lambda _r: httpx.Response(200, json=payload)).releases()
    assert [r.version for r in releases] == ["0.23.0"]
    assert releases[0].published_at is not None
    assert releases[0].published_at.year == 2026


def test_repo_info_parses_licence_and_archived() -> None:
    payload = {"archived": True, "pushed_at": "2026-01-01T00:00:00Z", "license": {"spdx_id": "MIT"}}
    info = client_for(lambda _r: httpx.Response(200, json=payload)).repo_info()
    assert info.archived
    assert info.license_id == "MIT"


def test_repo_info_without_licence() -> None:
    info = client_for(lambda _r: httpx.Response(200, json={"license": None})).repo_info()
    assert info.license_id is None
    assert not info.archived


def test_advisories_parse_ranges() -> None:
    vulnerable = [{"vulnerable_version_range": "< 0.5"}]
    payload = [
        {
            "ghsa_id": "GHSA-1",
            "severity": "high",
            "summary": "Bad",
            "published_at": "2026-02-02",
            "vulnerabilities": vulnerable,
        }
    ]
    advisory = client_for(lambda _r: httpx.Response(200, json=payload)).advisories()[0]
    assert advisory.vulnerable_ranges == ("< 0.5",)


def test_file_at_tag_requests_raw_and_ref() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="module m\n")

    assert client_for(handler).file_at_tag("go.mod", "v0.22.0") == "module m\n"
    assert seen[0].url.params["ref"] == "v0.22.0"
    assert "raw" in seen[0].headers["accept"]


def test_rate_limit_has_clear_message() -> None:
    reply = httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
        client_for(lambda _r: reply).repo_info()


def test_other_http_errors_raise() -> None:
    with pytest.raises(GitHubError, match="404"):
        client_for(lambda _r: httpx.Response(404)).repo_info()


def test_network_failure_raises() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(GitHubError, match="offline"):
        client_for(broken).repo_info()


def test_token_is_sent_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok123")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    client_for(handler).repo_info()
    assert seen[0].headers["authorization"] == "Bearer tok123"


def test_no_authorization_header_without_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    client_for(handler).repo_info()
    assert "authorization" not in seen[0].headers


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("0.23.0", "0.22.0", True),
        ("v0.22.0", "0.22.0", False),
        ("0.10.0", "0.9.9", True),
        ("x", "1", False),
    ],
)
def test_is_newer(candidate: str, current: str, expected: bool) -> None:
    assert github.is_newer(candidate, current) is expected


@pytest.mark.parametrize(
    ("version", "rng", "expected"),
    [
        ("0.22.0", "< 0.23.0", True),
        ("0.22.0", "< 0.22.0", False),
        ("0.22.0", ">= 0.1, < 0.5", False),
        ("0.22.0", ">= 0.1, <= 0.22.0", True),
        ("0.22.0", "weird range", True),
    ],
)
def test_range_affects(version: str, rng: str, expected: bool) -> None:
    assert github.range_affects(version, rng) is expected
