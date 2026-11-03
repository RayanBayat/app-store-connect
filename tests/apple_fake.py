"""A fake Apple API for tests: routes by (method, path), records every request."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import mcp_types as types
from mcp import Client
from mcp.client.session import ElicitationFnT

from app_store_connect.auth import TokenProvider
from app_store_connect.client import AscClient
from app_store_connect.maintenance import Maintenance
from app_store_connect.review import stamp_review
from app_store_connect.server import AppServer
from app_store_connect.spec import APPLE_API_SPEC_VERSION

from .conftest import NOW
from .helpers import make_config

Reply = dict[str, Any] | Callable[[httpx.Request], httpx.Response]


@dataclass
class Recorded:
    method: str
    host: str
    path: str
    params: dict[str, str]
    body: Any
    authorization: str | None


@dataclass
class FakeApple:
    """Register replies with `on(method, path, json_or_callable, status=200)`."""

    routes: dict[tuple[str, str], tuple[int, Reply]] = field(default_factory=dict)
    seen: list[Recorded] = field(default_factory=list)

    def on(self, method: str, path: str, reply: Reply, status: int = 200) -> None:
        self.routes[(method, path)] = (status, reply)

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content[:1] in (b"{", b"[") else None
        self.seen.append(
            Recorded(
                request.method,
                str(request.url.host),
                request.url.path,
                dict(request.url.params),
                body,
                request.headers.get("authorization"),
            )
        )
        route = self.routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, json={"errors": [{"title": "Not found", "detail": "fake"}]})
        status, reply = route
        if callable(reply):
            return reply(request)
        return (
            httpx.Response(status, json=reply)
            if reply != {} or status != 204
            else httpx.Response(204)
        )

    def writes(self) -> list[Recorded]:
        """Requests that would change data (anything but GET), excluding presigned uploads."""
        return [r for r in self.seen if r.method != "GET"]


def resource(kind: str, item_id: str, **attributes: Any) -> dict[str, Any]:
    """A JSON:API resource object."""
    return {"type": kind, "id": item_id, "attributes": attributes}


def listing(*items: dict[str, Any]) -> dict[str, Any]:
    """A JSON:API list response."""
    return {"data": list(items)}


def one(item: dict[str, Any]) -> dict[str, Any]:
    """A JSON:API single-resource response."""
    return {"data": item}


@asynccontextmanager
async def connected(
    tmp_path: Path,
    fake: FakeApple,
    *,
    allow_writes: bool = False,
    elicit: ElicitationFnT | None = None,
    fresh_review: bool = True,
    root: Path | None = None,
) -> AsyncIterator[Client]:
    """Run the real server in-process against the fake Apple API and connect a test client."""
    config = make_config(tmp_path / "keys", allow_writes=allow_writes, root=root)
    if fresh_review:
        stamp_review(NOW, APPLE_API_SPEC_VERSION, "test", ["list_apps"])
    client = AscClient(TokenProvider(config), transport=httpx.MockTransport(fake.handler))
    maintenance = Maintenance(config, clock=lambda: NOW, report_fn=lambda tools: f"REPORT {tools}")
    app = AppServer(config, client, maintenance=maintenance, clock=lambda: NOW)
    async with Client(
        app.build_server(), mode="legacy", cache=None, elicitation_callback=elicit
    ) as connection:
        yield connection


def text_of(result: types.CallToolResult) -> str:
    """The text of the first content block of a tool result."""
    first = result.content[0]
    assert isinstance(first, types.TextContent)
    return first.text
