"""Command-line entry point: `app-store-connect [serve|review|status|doctor|--version]`.

With no command it runs the MCP server over stdio (this is what the MCP client starts).
The other commands are for a person at a terminal.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app_store_connect import __version__
from app_store_connect.auth import AuthError, TokenProvider
from app_store_connect.client import AscClient, AscError
from app_store_connect.config import (
    ConfigError,
    is_truthy,
    load_config,
    masked_path,
    parse_review_days,
)
from app_store_connect.maintenance import make_report, status_text
from app_store_connect.maintenance_record import load_record, now_local, stamp_record
from app_store_connect.server import create_app, serve_stdio, snapshot_tool_names
from app_store_connect.spec import APPLE_API_SPEC_VERSION

ReportFn = Callable[[list[str]], str]
Ask = Callable[[str], str]
DEFAULT_OUTCOME = "Report read in a terminal."


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="app-store-connect",
        description="App Store Connect for AI assistants (MCP server) with a monthly review.",
    )
    parser.add_argument("--version", action="version", version=f"app-store-connect {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server over stdio (the default)")
    sub.add_parser("review", help="print the maintenance report and record that you read it")
    sub.add_parser("status", help="show when the last review was and when the next is due")
    doctor = sub.add_parser("doctor", help="check your settings and key file")
    doctor.add_argument(
        "--online", action="store_true", help="also make one read-only call to Apple"
    )
    return parser


def cmd_status(env: Mapping[str, str]) -> int:
    """Print the review status (works without credentials)."""
    text = status_text(
        max_days=parse_review_days(env.get("ASC_REVIEW_MAX_DAYS")),
        now=now_local(),
        review=load_record(),
        writes=is_truthy(env.get("ASC_ALLOW_WRITES")),
    )
    print(text)
    return 0


def cmd_review(*, interactive: bool, ask: Ask, report_fn: ReportFn) -> int:
    """Print the report, ask the person if they read it, and record the review on 'y'.

    Refuses when stdin is not a terminal, so a script (or an AI) cannot confirm for you.
    """
    if not interactive:
        print(
            "review needs an interactive terminal: run it yourself, not from a script.",
            file=sys.stderr,
        )
        return 2
    print(report_fn(snapshot_tool_names()))
    if ask("Have you read it? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Not recorded.")
        return 1
    note = ask("One line about what you decided (Enter to skip): ").strip()
    stamp_record(
        now_local(), APPLE_API_SPEC_VERSION, note or DEFAULT_OUTCOME, snapshot_tool_names()
    )
    print("Review recorded. App Store Connect tools are available again.")
    return 0


@dataclass(frozen=True, slots=True)
class Check:
    """One line of the `doctor` report: whether it passed, and the text to print."""

    ok: bool
    text: str


def check_credentials(env: Mapping[str, str]) -> list[Check]:
    """Local checks: settings, key file permissions, and that a token can be signed."""
    try:
        config = load_config(env)
    except ConfigError as exc:
        return [Check(ok=False, text=str(exc))]
    checks = [
        Check(
            ok=True,
            text=f"OK  settings are valid (key ID {config.masked_key_id}, {config.key_kind} key)",
        ),
        Check(
            ok=True,
            text=f"OK  key file {masked_path(config.private_key_path)} is private "
            "(mode 600 in a private folder)",
        ),
    ]
    try:
        TokenProvider(config).token()
    except AuthError as exc:
        return [*checks, Check(ok=False, text=f"FAIL {exc}")]
    writes = "ENABLED" if config.allow_writes else "off (read-only)"
    root = config.root or "not set (file tools off)"
    return [
        *checks,
        Check(ok=True, text="OK  an API token can be signed with this key"),
        Check(ok=True, text=f"    writes: {writes}"),  # information, not a check
        Check(ok=True, text=f"    file access folder (ASC_ROOT): {root}"),
    ]


async def online_check(env: Mapping[str, str]) -> Check:
    """One read-only call (list one app) to prove Apple accepts the credentials."""
    config = load_config(env)
    client = AscClient(TokenProvider(config))
    try:
        await client.request("GET", "/v1/apps", params={"limit": "1"})
    except AscError as exc:
        return Check(ok=False, text=f"FAIL Apple said: {exc}")
    finally:
        await client.aclose()
    return Check(ok=True, text="OK  Apple accepted the credentials (listed apps)")


def cmd_doctor(env: Mapping[str, str], *, online: bool) -> int:
    """Print the doctor results; exit 1 if anything failed."""
    checks = check_credentials(env)
    ok = all(check.ok for check in checks)
    if online and ok:
        checks.append(asyncio.run(online_check(env)))
        ok = checks[-1].ok
    review_lines = status_text(
        max_days=parse_review_days(env.get("ASC_REVIEW_MAX_DAYS")),
        now=now_local(),
        review=load_record(),
        writes=None,
    )
    print("\n".join(check.text for check in checks))
    print()
    print(review_lines)
    return 0 if ok else 1


def report_text(tool_names: list[str]) -> str:
    """The real report as text, for a person reading it in a terminal."""
    return make_report(tool_names).text


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line."""
    args = build_parser().parse_args(argv)
    env = os.environ
    if args.command == "status":
        return cmd_status(env)
    if args.command == "review":
        return cmd_review(interactive=sys.stdin.isatty(), ask=input, report_fn=report_text)
    if args.command == "doctor":
        return cmd_doctor(env, online=args.online)
    asyncio.run(serve_stdio(create_app(env)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
