"""The MCP server: App Store Connect tools plus the monthly-review gate.

Rules, applied in this order to every tool call:

1. If start-up failed (bad settings), answer with that problem. We still start so the user
   can see the message in their client instead of a silent "failed to connect".
2. Maintenance tools are always allowed.
3. Unknown tools are refused. Write tools are not even registered unless ASC_ALLOW_WRITES is on.
4. If the monthly review is overdue, refuse and say how to fix it.
5. Run the tool. Every write tool asks the human to confirm first (see tools/confirm.py).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import mcp_types as types
from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.server.request_state import RequestStateBoundary, RequestStateSecurity
from mcp.server.stdio import stdio_server

from app_store_connect import APP_NAME, __version__
from app_store_connect.auth import AuthError, TokenProvider
from app_store_connect.client import AscClient, AscError
from app_store_connect.config import Config, ConfigError, load_config
from app_store_connect.confirmflow import STATE_TTL_SECONDS, ConfirmationBook, InputRequiredSignal
from app_store_connect.maintenance import MAINTENANCE_TOOL_NAMES, MAINTENANCE_TOOLS, Maintenance
from app_store_connect.maintenance_record import check_record, load_record, now_local
from app_store_connect.paths import tildify
from app_store_connect.tools import all_tools, write_tools
from app_store_connect.tools.base import (
    ISSUES_URL,
    ToolContext,
    ToolInputError,
    ToolRefusedError,
    ToolSpec,
    text_result,
)

logger = logging.getLogger(__name__)


def error_result(text: str) -> types.CallToolResult:
    """A tool result that tells the model (and user) something went wrong."""
    return text_result(text, is_error=True)


def snapshot_tool_names() -> list[str]:
    """Every tool name this server can ever offer (writes included), for the review snapshot."""
    return sorted({tool.name for tool in all_tools(allow_writes=True)} | MAINTENANCE_TOOL_NAMES)


class AppServer:
    """The handlers behind the MCP server. One instance serves one client connection."""

    def __init__(
        self,
        config: Config | None,
        client: AscClient | None = None,
        *,
        startup_error: str | None = None,
        maintenance: Maintenance | None = None,
        clock: Callable[[], datetime] = now_local,
    ) -> None:
        self.config = config
        self.startup_error = startup_error
        self._client = client
        self._clock = clock
        self._maintenance = maintenance
        self._confirmations = ConfirmationBook()
        self._request_state = RequestStateSecurity.ephemeral(ttl=STATE_TTL_SECONDS)
        allow = bool(config and config.allow_writes)
        self._tools: dict[str, ToolSpec] = {t.name: t for t in all_tools(allow_writes=allow)}
        self._write_names = {t.name for t in write_tools()}

    async def list_tools(
        self, ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        """Read tools (and write tools when enabled) plus the maintenance tools."""
        del ctx, params  # the list does not depend on the request
        if self.startup_error is not None:
            return types.ListToolsResult(tools=list(MAINTENANCE_TOOLS))
        return types.ListToolsResult(
            tools=[*(t.to_tool() for t in self._tools.values()), *MAINTENANCE_TOOLS]
        )

    async def call_tool(
        self, ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult | types.InputRequiredResult:
        """Apply the rules in order; see the module docstring."""
        name, arguments = params.name, params.arguments or {}
        if self.startup_error is not None:
            return error_result(self.startup_error)
        if name in MAINTENANCE_TOOL_NAMES:
            return await self._maintenance_call(name, arguments, ctx, params)
        tool = self._tools.get(name)
        if tool is None:
            return error_result(self._unknown_message(name))
        gate = self._review_gate()
        if gate is not None:
            return error_result(gate)
        return await self._run(tool, arguments, ctx, params)

    def _unknown_message(self, name: str) -> str:
        if name in self._write_names:
            return (
                f"'{name}' changes data and writes are off. The user can turn them on by "
                "setting ASC_ALLOW_WRITES=1 in the server's configuration."
            )
        return f"Unknown tool '{name}'."

    def _review_gate(self) -> str | None:
        """Return the reason to refuse (review overdue), or None to allow."""
        if self.config is None:
            return "The server is not configured."
        check = check_record(load_record(), self._clock(), self.config.review_max_days)
        return check.message if check.overdue else None

    async def _maintenance_call(
        self,
        name: str,
        arguments: dict[str, Any],
        ctx: ServerRequestContext[Any],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult | types.InputRequiredResult:
        if self._maintenance is None:
            return error_result("The server is not configured.")
        return await self._maintenance.call(
            name,
            arguments,
            ctx.session,
            snapshot_tool_names(),
            input_responses=params.input_responses,
            request_state=params.request_state,
        )

    async def _run(
        self,
        tool: ToolSpec,
        arguments: dict[str, Any],
        ctx: ServerRequestContext[Any],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult | types.InputRequiredResult:
        """Run one tool, turning every expected failure into a readable error result."""
        if self._client is None or self.config is None:
            return error_result("The server is not configured.")
        context = ToolContext(
            self._client,
            self.config,
            ctx.session,
            tool_name=tool.name,
            arguments=arguments,
            input_responses=params.input_responses,
            request_state=params.request_state,
            confirmations=self._confirmations,
        )
        try:
            return text_result(await tool.handler(context, arguments))
        except InputRequiredSignal as signal:  # the human is asked; the client calls again
            return signal.result
        except (ToolInputError, ToolRefusedError, AscError, AuthError) as exc:
            return error_result(str(exc))
        except Exception as exc:  # last resort: the type only goes to the model, never raw details
            # The full traceback goes to stderr (Python prints it when nothing else is set up), so
            # the maintainer can debug it. The model and the user only see the short message.
            logger.exception("tool %s crashed", tool.name)
            return error_result(
                f"Unexpected error in {tool.name} ({type(exc).__name__}). This is a bug: the "
                "details are in the server log (stderr). Please report it at "
                f"{ISSUES_URL}."
            )

    @asynccontextmanager
    async def lifespan(self, _server: Server[Any]) -> AsyncIterator[dict[str, Any]]:
        """Close the Apple client when the server stops."""
        try:
            yield {}
        finally:
            if self._client is not None:
                await self._client.aclose()

    def build_server(self) -> Server[Any]:
        """Create the low-level MCP server wired to this object."""
        server: Server[Any] = Server(
            APP_NAME,
            version=__version__,
            lifespan=self.lifespan,
            on_list_tools=self.list_tools,
            on_call_tool=self.call_tool,
        )
        # Seals the request_state of 2026-07-28 confirmations (AES-GCM, random per-process key,
        # bound to the tool name and exact arguments, expires after 10 minutes).
        server.middleware.append(
            RequestStateBoundary(self._request_state, default_audience=APP_NAME)
        )
        return server


def create_app(env: Mapping[str, str]) -> AppServer:
    """Build the server from the environment; on bad settings return a degraded server."""
    try:
        config = load_config(env)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return AppServer(None, startup_error=tildify(str(exc)))
    client = AscClient(TokenProvider(config))
    return AppServer(config, client, maintenance=Maintenance(config))


async def serve_stdio(app: AppServer) -> None:
    """Run the server over this process's stdin/stdout until the client disconnects."""
    server = app.build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
