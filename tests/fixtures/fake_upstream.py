"""A FAKE upstream MCP server for tests. It never talks to Apple or reads any credentials.

Usage: python fake_upstream.py serve   (stdio MCP server)
       python fake_upstream.py doctor  (prints a line and exits 0)

Tools mimic the real upstream names so the proxy can be tested end to end:
- apps_list: a harmless read tool
- toolsets, web_login, asc_api_request: the three tools the proxy must block
- needs_approval: asks the human for approval via MCP elicitation
- show_roots: asks the client for its roots
- crash_once: kills the process the first time it is called (tests restart)
On start-up it writes its argv and environment to env.json in its working directory so
tests can check exactly what the proxy let through.
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations
from pydantic import BaseModel

server = MCPServer("fake-upstream")
READ_ONLY = ToolAnnotations(read_only_hint=True)


class Approval(BaseModel):
    approve: bool


@server.tool(annotations=READ_ONLY)
def apps_list() -> str:
    """List apps (fake)."""
    return "apps: Fake App"


@server.tool()
def toolsets() -> str:
    """Would enable more toolsets (must be blocked)."""
    return "toolsets: SHOULD NEVER RUN"


@server.tool()
def web_login() -> str:
    """Would open an Apple ID sign-in (must be blocked)."""
    return "web_login: SHOULD NEVER RUN"


@server.tool()
def asc_api_request() -> str:
    """Would send a raw request (must be blocked)."""
    return "asc_api_request: SHOULD NEVER RUN"


@server.tool()
async def needs_approval(ctx: Context) -> str:
    """Ask the human to approve something."""
    result = await ctx.elicit("Approve the fake change?", Approval)
    if result.action == "accept" and result.data.approve:
        return "approved"
    return f"not approved ({result.action})"


@server.tool()
async def show_roots(ctx: Context) -> str:
    """Return the client's roots."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        roots = await ctx.session.list_roots()
    return ",".join(str(root.uri) for root in roots.roots)


@server.tool()
def crash_once() -> str:
    """Exit the process the first time (marker file), succeed afterwards."""
    marker = Path("crashed.marker")
    if not marker.exists():
        marker.write_text("x", encoding="utf-8")
        os._exit(1)
    return "recovered"


def main(argv: list[str]) -> int:
    """Entry point mirroring the upstream CLI shape."""
    if argv[:1] == ["doctor"]:
        print("fake doctor: ok")
        return 0
    if argv[:1] != ["serve"]:
        print("usage: fake_upstream.py serve|doctor", file=sys.stderr)
        return 2
    dump = {"argv": argv, "env": dict(os.environ), "cwd": str(Path.cwd())}
    Path("env.json").write_text(json.dumps(dump), encoding="utf-8")
    server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
