"""A fake Apple API for tests: routes by (method, path), records every request."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import mcp_types as types
from mcp import Client
from mcp.client.session import ClientRequestContext

from app_store_connect.auth import TokenProvider
from app_store_connect.client import AscClient
from app_store_connect.maintenance import Maintenance
from app_store_connect.maintenance_record import stamp_record
from app_store_connect.report import ReportFacts, ReportResult
from app_store_connect.server import AppServer
from app_store_connect.spec import APPLE_API_SPEC_VERSION

from .conftest import NOW
from .helpers import make_config

# The two ways an MCP server can ask the human a question. Confirmations must work on both, so
# tests that confirm something run once per protocol (see `PROTOCOLS`).
#
# - Older protocol (up to 2025-11-25): the server asks during the call. It sends the form to the
#   client in the middle of the tool call and waits for the answer.
# - Protocol 2026-07-28: the call returns "input required" with the form and some sealed state.
#   The client shows the form and calls the tool again with the answer and the state.
#
# The values are what `Client(mode=...)` accepts: "legacy" is the older handshake, and a
# version string such as "2026-07-28" selects that protocol.
OLD_PROTOCOL = "legacy"
NEW_PROTOCOL = "2026-07-28"
PROTOCOLS = [OLD_PROTOCOL, NEW_PROTOCOL]

FormAction = Literal["accept", "decline", "cancel"]

FACTS = ReportFacts(
    spec_checked=True, missing_endpoints=0, deprecated_endpoints=0, outdated=0, vulnerabilities=0
)


def fake_report(tools: list[str]) -> ReportResult:
    """A canned report for tests (no network)."""
    return ReportResult(f"REPORT {tools}", FACTS)


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
        """Every request that was not a GET (the ones that would change data on Apple)."""
        return [r for r in self.seen if r.method != "GET"]


class FormAnswerer:
    """Stands in for the human: answers every confirmation form with one fixed action.

    Pass it to `connected(..., elicit=answerer)`. Every form's text is kept in `.forms`, so a test
    can check what the human would have seen. The forms have no fields, so "accept" is just
    `ElicitResult(action="accept")`.

    Two optional extras for the few tests that need them: `.schemas` keeps the schema of each
    form, and `on_form` is a function called with the number of the form that was just shown
    (1 for the first), before the answer is given. Use it to change something "while the human
    reads the form".
    """

    def __init__(
        self, action: FormAction = "accept", *, on_form: Callable[[int], None] | None = None
    ) -> None:
        self.action: FormAction = action
        self.on_form = on_form
        self.forms: list[str] = []
        self.schemas: list[dict[str, Any]] = []

    async def __call__(
        self, context: ClientRequestContext, params: types.ElicitRequestParams
    ) -> types.ElicitResult:
        self.forms.append(params.message)
        schema = getattr(params, "requested_schema", None)
        if schema is not None:
            as_dict = schema if isinstance(schema, dict) else schema.model_dump(exclude_none=True)
            self.schemas.append(as_dict)
        if self.on_form is not None:
            self.on_form(len(self.forms))
        return types.ElicitResult(action=self.action)


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
    elicit: FormAnswerer | None = None,
    fresh_review: bool = True,
    root: Path | None = None,
    protocol: str = OLD_PROTOCOL,
) -> AsyncIterator[Client]:
    """Run the real server in-process against the fake Apple API and connect a test client.

    `elicit` answers the confirmation forms (None: this client cannot show forms).
    `fresh_review` stamps a recent monthly review so the gate is open.
    `protocol` is OLD_PROTOCOL or NEW_PROTOCOL.
    """
    config = make_config(tmp_path / "keys", allow_writes=allow_writes, root=root)
    if fresh_review:
        stamp_record(NOW, APPLE_API_SPEC_VERSION, "test", ["list_apps"])
    client = AscClient(TokenProvider(config), transport=httpx.MockTransport(fake.handler))
    maintenance = Maintenance(config, clock=lambda: NOW, report_fn=fake_report)
    app = AppServer(config, client, maintenance=maintenance, clock=lambda: NOW)
    async with Client(
        app.build_server(), mode=protocol, cache=None, elicitation_callback=elicit
    ) as connection:
        yield connection


async def run(
    tmp_path: Path,
    fake: FakeApple,
    tool: str,
    args: dict[str, Any],
    answerer: FormAnswerer,
    protocol: str = OLD_PROTOCOL,
) -> types.CallToolResult:
    """Call one write tool with writes on, answering its form with `answerer`."""
    async with connected(
        tmp_path, fake, allow_writes=True, elicit=answerer, protocol=protocol
    ) as client:
        return await client.call_tool(tool, args)


def text_of(result: types.CallToolResult) -> str:
    """The text of the first content block of a tool result."""
    first = result.content[0]
    assert isinstance(first, types.TextContent)
    return first.text
