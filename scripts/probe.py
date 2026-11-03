#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=2.3.0"]
# ///
"""List (and optionally call) tools of the App Store Connect MCP server over stdio.

Run with: uv run scripts/probe.py [options]

The server is started with its working directory set to the maintenance state
directory, so a stray `.app-store-connect-mcp.toml` in the current project cannot
change which profile or settings the probe sees.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MAX_OUTPUT = 4000


def state_dir() -> Path:
    """Same state-dir rule as asc_maint.py (kept separate: this file is standalone)."""
    override = os.environ.get("ASC_MCP_MAINT_STATE_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "asc-mcp-maintenance"


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binary", default="~/.local/bin/app-store-connect-mcp")
    parser.add_argument("--profile", default="main")
    parser.add_argument("--allow-writes", action="store_true", help="start the server writable")
    parser.add_argument("--root", help="directory passed to the server as --root")
    parser.add_argument("--names-only", action="store_true", help="print tool names, one per line")
    parser.add_argument(
        "--call", metavar="TOOL", help="call one tool; meant for READ-ONLY tools only"
    )
    parser.add_argument("--args", default="{}", help="JSON arguments for --call (default {})")
    return parser.parse_args()


def server_params(args: argparse.Namespace, cwd: Path) -> StdioServerParameters:
    """Build the command used to launch the server over stdio."""
    command = ["serve", "--profile", args.profile]
    if args.allow_writes:
        command.append("--allow-writes")
    if args.root:
        command += ["--root", args.root]
    binary = str(Path(args.binary).expanduser())
    return StdioServerParameters(command=binary, args=command, cwd=cwd)


async def list_tools(session: ClientSession, names_only: bool) -> None:
    """Print the server's tools, either as bare names or as a readable summary."""
    tools = (await session.list_tools()).tools
    if names_only:
        for tool in sorted(tools, key=lambda t: t.name):
            print(tool.name)
        return
    print(f"tools: {len(tools)}")
    writes = 0
    for tool in sorted(tools, key=lambda t: t.name):
        is_write = tool.annotations is not None and tool.annotations.read_only_hint is False
        writes += is_write
        print(f"  {tool.name}{' [write]' if is_write else ''}")
    print(f"write tools: {writes}, read-only/unannotated: {len(tools) - writes}")


async def call_tool(session: ClientSession, name: str, raw_args: str) -> None:
    """Call one tool and print its text content, truncated."""
    result = await session.call_tool(name, json.loads(raw_args))
    text = "\n".join(c.text for c in result.content if getattr(c, "text", None) is not None)
    if result.is_error:
        print("TOOL ERROR:")
    print(text[:MAX_OUTPUT])
    if len(text) > MAX_OUTPUT:
        print(f"... truncated ({len(text)} chars total)")


async def run(args: argparse.Namespace) -> None:
    """Start the server, initialize, then list or call."""
    cwd = state_dir()
    cwd.mkdir(parents=True, exist_ok=True)
    async with (
        stdio_client(server_params(args, cwd)) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        if not args.names_only:
            info = init.server_info
            print(f"server: {info.name} {info.version}")
        if args.call:
            await call_tool(session, args.call, args.args)
        else:
            await list_tools(session, args.names_only)


def main() -> None:
    """Entry point."""
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
