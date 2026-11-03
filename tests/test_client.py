"""Tests for client.py against a mocked Apple API (httpx MockTransport)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app_store_connect import client as client_module
from app_store_connect.auth import TokenProvider
from app_store_connect.client import AscApiError, AscClient, AscError

from .helpers import make_config

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(tmp_path: Path, handler: Handler, sleeps: list[float] | None = None) -> AscClient:
    async def fake_sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    tokens = TokenProvider(make_config(tmp_path / "k"))
    return AscClient(tokens, transport=httpx.MockTransport(handler), sleep=fake_sleep)


async def test_auth_header_sent_to_apple_host_only(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    client = make_client(tmp_path, handler)
    await client.request("GET", "/v1/apps")
    assert seen[0].url.host == "api.appstoreconnect.apple.com"
    assert seen[0].headers["authorization"].startswith("Bearer ey")


async def test_unlisted_endpoint_is_refused_without_a_request(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(_r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={})

    client = make_client(tmp_path, handler)
    with pytest.raises(AscError, match="not an allowed endpoint"):
        await client.request("GET", "/v1/users")
    with pytest.raises(AscError, match="not an allowed endpoint"):
        await client.request("DELETE", "/v1/apps/{id}", item_id="1")
    assert calls == []


@pytest.mark.parametrize("bad", ["../x", "1/2", "a b", "", "1?x=y", "%2e%2e"])
async def test_ids_that_could_change_the_path_are_refused(tmp_path: Path, bad: str) -> None:
    client = make_client(tmp_path, lambda _r: httpx.Response(200, json={}))
    with pytest.raises(AscError, match="not a valid"):
        await client.request("GET", "/v1/apps/{id}", item_id=bad)


async def test_pagination_follows_next_on_same_host(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "2":
            return httpx.Response(200, json={"data": [{"id": "2"}]})
        nxt = "https://api.appstoreconnect.apple.com/v1/apps?cursor=2"
        return httpx.Response(200, json={"data": [{"id": "1"}], "links": {"next": nxt}})

    items = await make_client(tmp_path, handler).paginate("/v1/apps")
    assert [i["id"] for i in items] == ["1", "2"]


@pytest.mark.parametrize(
    "evil",
    ["https://evil.example/v1/apps?cursor=2", "http://api.appstoreconnect.apple.com/v1/apps",
     "https://api.appstoreconnect.apple.com.evil.example/x"],
)  # fmt: skip
async def test_pagination_to_another_host_is_refused_and_never_requested(
    tmp_path: Path, evil: str
) -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(str(request.url.host))
        return httpx.Response(200, json={"data": [{"id": "1"}], "links": {"next": evil}})

    with pytest.raises(AscError, match="another host"):
        await make_client(tmp_path, handler).paginate("/v1/apps")
    assert hosts == ["api.appstoreconnect.apple.com"]


async def test_pagination_stops_at_max_items(tmp_path: Path) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        links = {"next": "https://api.appstoreconnect.apple.com/v1/apps?c=2"}
        return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}], "links": links})

    assert len(await make_client(tmp_path, handler).paginate("/v1/apps", max_items=3)) == 3


async def test_retries_429_honouring_retry_after_then_succeeds(tmp_path: Path) -> None:
    replies = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json={"ok": 1}),
    ]
    sleeps: list[float] = []
    client = make_client(tmp_path, lambda _r: replies.pop(0), sleeps)
    assert await client.request("GET", "/v1/apps") == {"ok": 1}
    assert sleeps == [7.0]


async def test_retries_5xx_with_backoff_at_most_three_times(tmp_path: Path) -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def handler(_r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    client = make_client(tmp_path, handler, sleeps)
    with pytest.raises(AscApiError) as info:
        await client.request("GET", "/v1/apps")
    assert info.value.status == 503
    assert len(calls) == 4  # first try + 3 retries
    assert sleeps == [1.0, 2.0, 4.0]


async def test_post_is_not_repeated_after_a_5xx(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(_r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500)

    with pytest.raises(AscApiError):
        await make_client(tmp_path, handler).request("POST", "/v1/appStoreVersions", body={})
    assert len(calls) == 1


async def test_post_is_retried_after_429(tmp_path: Path) -> None:
    replies = [httpx.Response(429), httpx.Response(201, json={"data": {}})]
    client = make_client(tmp_path, lambda _r: replies.pop(0))
    assert await client.request("POST", "/v1/appStoreVersions", body={}) == {"data": {}}


async def test_error_message_built_from_errors_array(tmp_path: Path) -> None:
    body = {"errors": [{"title": "Entity error", "detail": "keywords too long", "code": "X"}]}
    client = make_client(tmp_path, lambda _r: httpx.Response(409, json=body))
    with pytest.raises(AscApiError) as info:
        await client.request("PATCH", "/v1/appInfoLocalizations/{id}", item_id="9", body={})
    message = str(info.value)
    assert "409" in message
    assert "Entity error: keywords too long" in message
    assert "/v1/appInfoLocalizations/9" in message


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_helpful_hint_for_common_statuses(tmp_path: Path, status: int) -> None:
    client = make_client(tmp_path, lambda _r: httpx.Response(status, json={}))
    with pytest.raises(AscApiError) as info:
        await client.request("GET", "/v1/apps")
    assert client_module._HINTS[status] in str(info.value)


async def test_error_message_never_contains_the_token(tmp_path: Path) -> None:
    client = make_client(tmp_path, lambda _r: httpx.Response(401, json={"errors": []}))
    with pytest.raises(AscApiError) as info:
        await client.request("GET", "/v1/apps")
    assert "Bearer" not in str(info.value)
    assert "eyJ" not in str(info.value)


async def test_204_returns_empty_dict(tmp_path: Path) -> None:
    client = make_client(tmp_path, lambda _r: httpx.Response(204))
    result = await client.request(
        "DELETE", "/v1/betaGroups/{id}/relationships/betaTesters", item_id="g", body={"data": []}
    )
    assert result == {}


async def test_oversized_response_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 10)
    client = make_client(tmp_path, lambda _r: httpx.Response(200, content=b"x" * 100))
    with pytest.raises(AscError, match="larger than"):
        await client.request("GET", "/v1/apps")


async def test_network_failure_is_a_readable_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(AscError, match="Could not reach Apple"):
        await make_client(tmp_path, handler).request("GET", "/v1/apps")


async def test_redirects_are_not_followed(tmp_path: Path) -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(str(request.url.host))
        return httpx.Response(302, headers={"location": "https://evil.example/"})

    with pytest.raises(AscApiError):
        await make_client(tmp_path, handler).request("GET", "/v1/apps")
    assert hosts == ["api.appstoreconnect.apple.com"]


async def test_presigned_upload_has_no_authorization_header(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    client = make_client(tmp_path, handler)
    headers = [("Content-Type", "image/png"), ("Authorization", "Bearer sneaky")]
    await client.upload_part(
        "PUT", "https://store-001.blobstore.apple.com/part1?sig=abc", headers, b"data"
    )
    assert "authorization" not in seen[0].headers
    assert seen[0].headers["content-type"] == "image/png"
    assert seen[0].content == b"data"


async def test_presigned_upload_must_be_https(tmp_path: Path) -> None:
    client = make_client(tmp_path, lambda _r: httpx.Response(200))
    with pytest.raises(AscError, match="not https"):
        await client.upload_part("PUT", "http://store-001.blobstore.apple.com/p", [], b"x")


async def test_presigned_upload_failure_status_reported(tmp_path: Path) -> None:
    client = make_client(tmp_path, lambda _r: httpx.Response(403))
    with pytest.raises(AscError, match="status 403"):
        await client.upload_part("PUT", "https://store-001.blobstore.apple.com/p", [], b"x")


@pytest.mark.parametrize("bad", ["..", ".", "a.b", "a_b", "-x"])
async def test_dot_and_underscore_ids_are_refused_before_any_request(
    tmp_path: Path, bad: str
) -> None:
    """Regression (L1): app_id='..' used to climb out of /v1/apps/{id}/betaGroups."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"data": []})

    with pytest.raises(AscError, match="not a valid"):
        await make_client(tmp_path, handler).paginate("/v1/apps/{id}/betaGroups", item_id=bad)
    assert seen == []


async def test_uuid_and_numeric_ids_are_accepted(tmp_path: Path) -> None:
    client = make_client(tmp_path, lambda _r: httpx.Response(200, json={"data": []}))
    await client.paginate(
        "/v1/apps/{id}/betaGroups", item_id="6f9619ff-8b86-d011-b42d-00c04fc964ff"
    )
    await client.paginate("/v1/apps/{id}/betaGroups", item_id="1234567890")


def test_filled_template_path_must_survive_url_parsing_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defence in depth: even if an ID check were bypassed, the final path is compared."""
    monkeypatch.setattr(client_module, "fill_template", lambda _t, _i: "/v1/apps/../users")
    with pytest.raises(AscError, match="path"):
        client_module.build_url("/v1/apps/{id}/betaGroups", "x")


async def test_next_link_to_another_endpoint_on_apple_host_is_refused(tmp_path: Path) -> None:
    """Regression (INFO): links.next pointing at /v1/users must not be followed."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        nxt = "https://api.appstoreconnect.apple.com/v1/users?x=1"
        return httpx.Response(200, json={"data": [{"id": "1"}], "links": {"next": nxt}})

    with pytest.raises(AscError, match="not an allowed endpoint"):
        await make_client(tmp_path, handler).paginate("/v1/apps")
    assert paths == ["/v1/apps"]


async def test_next_link_matching_an_allowed_get_template_is_followed(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor"):
            return httpx.Response(200, json={"data": [{"id": "2"}]})
        nxt = "https://api.appstoreconnect.apple.com/v1/apps/12/betaGroups?cursor=x"
        return httpx.Response(200, json={"data": [{"id": "1"}], "links": {"next": nxt}})

    items = await make_client(tmp_path, handler).paginate("/v1/apps/{id}/betaGroups", item_id="12")
    assert [i["id"] for i in items] == ["1", "2"]
