#!/usr/bin/env python3
"""Maintenance gate and review bookkeeping for the App Store Connect MCP server.

Why this exists: the `asc` MCP server is third-party code that talks to Apple with
real credentials. A hook calls `gate` before every `mcp__asc__*` tool call and
blocks it when nobody has reviewed the server recently. The review skill finishes
by calling `stamp`, which records the review and unblocks the gate.

Only the standard library is used, so the hook works with any plain python3.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA = 1
DEFAULT_MAX_DAYS = 30
HISTORY_LIMIT = 12
UPSTREAM_REPO = "idoo/app-store-connect-mcp"
DEFAULT_BIN = "app-store-connect-mcp"
SUBPROCESS_TIMEOUT = 20
FUTURE_TOLERANCE = timedelta(hours=1)  # allows small clock differences, nothing more

REMEDIATION = (
    "Before using any mcp__asc__ tool, run the asc-mcp-maintenance skill: review new "
    "releases, dependencies, vulnerabilities and new/deprecated tools, update the server "
    "if needed, then stamp the review."
)


def now_local() -> datetime:
    """Return the current time as a timezone-aware datetime in the local zone."""
    return datetime.now(UTC).astimezone()


def state_dir() -> Path:
    """Return the directory that holds state.json (env override, XDG, then ~/.local)."""
    override = os.environ.get("ASC_MCP_MAINT_STATE_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "asc-mcp-maintenance"


def state_path() -> Path:
    """Return the full path of the state file."""
    return state_dir() / "state.json"


def max_days() -> int:
    """Return the allowed review age in days (env value if integer 1-365, else 30)."""
    raw = os.environ.get("ASC_MCP_REVIEW_MAX_DAYS", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_DAYS
    return value if 1 <= value <= 365 else DEFAULT_MAX_DAYS


def load_state() -> dict | None:
    """Read the state file; return None when it is missing or unusable.

    A corrupt file is treated like "no review happened", so the gate stays closed.
    """
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        parse_time(data["reviewed_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def parse_time(text: str) -> datetime:
    """Parse an ISO 8601 timestamp and insist it carries a UTC offset."""
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp {text!r} has no UTC offset")
    return parsed


def age_days(state: dict, now: datetime) -> int:
    """Return whole days elapsed since the review recorded in `state`."""
    return (now - parse_time(state["reviewed_at"])).days


def deny_reason(now: datetime) -> str | None:
    """Return the text explaining why the gate must deny, or None if the review is fresh."""
    limit = max_days()
    state = load_state()
    if state is None:
        return f"App Store Connect MCP maintenance review has never been done. {REMEDIATION}"
    reviewed_at = parse_time(state["reviewed_at"])
    if reviewed_at > now + FUTURE_TOLERANCE:
        # A future date (wrong clock, hand-edited file) would otherwise keep the gate open forever.
        return (
            f"App Store Connect MCP review date {reviewed_at.isoformat()} lies in the future, "
            f"so it cannot be trusted. {REMEDIATION}"
        )
    days = age_days(state, now)
    if days < limit:
        return None
    date = parse_time(state["reviewed_at"]).date().isoformat()
    return (
        f"App Store Connect MCP maintenance review is overdue (last review {date}, "
        f"{days} days ago; limit {limit} days). {REMEDIATION}"
    )


def deny_json(reason: str) -> str:
    """Build the exact PreToolUse deny payload Claude Code expects."""
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    return json.dumps(payload)


def cmd_gate(now: datetime) -> int:
    """Hook mode: print a deny decision when overdue. Fails closed on any error."""
    try:
        sys.stdin.read()  # The hook JSON is irrelevant: every asc tool is gated alike.
        reason = deny_reason(now)
    except Exception as exc:
        reason = f"asc-mcp-maintenance gate error: {exc}; fix the plugin or run the review"
    if reason is not None:
        print(deny_json(reason))
    return 0


def read_tools_file(path: Path) -> list[str]:
    """Return sorted unique tool names from a file with one name per line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return sorted({line.strip() for line in lines if line.strip()})


def write_state_atomically(state: dict) -> None:
    """Write state.json via temp file + Path.replace so readers never see half a file."""
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix="state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
        Path(tmp_name).chmod(0o600)
        Path(tmp_name).replace(state_path())  # atomic rename on the same filesystem
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def build_state(
    previous: dict | None, now: datetime, server_version: str, outcome: str, tools: list[str] | None
) -> dict:
    """Assemble the new state, pushing the previous review into the history."""
    history: list[dict] = []
    old_tools: list[str] = []
    if previous is not None:
        old_tools = list(previous.get("tools", []))
        entry = {k: v for k, v in previous.items() if k not in ("tools", "history")}
        history = [entry, *previous.get("history", [])]
    return {
        "schema": SCHEMA,
        "reviewed_at": now.isoformat(timespec="seconds"),
        "server_version": server_version,
        "outcome": outcome,
        "tools": tools if tools is not None else old_tools,
        "history": history[:HISTORY_LIMIT],
    }


def cmd_stamp(args: argparse.Namespace, now: datetime) -> int:
    """Record a completed review."""
    tools = read_tools_file(Path(args.tools_file)) if args.tools_file else None
    state = build_state(load_state(), now, args.server_version, args.outcome, tools)
    write_state_atomically(state)
    print(f"Stamped review at {state['reviewed_at']} (server {args.server_version}).")
    return 0


def cmd_diff_tools(args: argparse.Namespace) -> int:
    """Print tools added/removed compared with the last stamped snapshot."""
    state = load_state()
    old = set(state.get("tools", [])) if state else set()
    new = set(read_tools_file(Path(args.tools_file)))
    added, removed = sorted(new - old), sorted(old - new)
    if not added and not removed:
        print("no changes")
        return 0
    print("added:")
    for name in added:
        print(f"  {name}")
    print("removed:")
    for name in removed:
        print(f"  {name}")
    return 0


def installed_version() -> str:
    """Return the installed server version, or "not installed"."""
    binary = os.environ.get("ASC_MCP_BIN", DEFAULT_BIN)
    if shutil.which(binary) is None:
        return "not installed"
    try:
        result = subprocess.run(
            [binary, "version"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            check=True,
        )
        return result.stdout.split()[-1]
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


def latest_upstream() -> str:
    """Return the latest upstream version via `gh` (tag without "v", like the installed one)."""
    if shutil.which("gh") is None:
        return "unknown"
    try:
        result = subprocess.run(
            ["gh", "release", "view", "-R", UPSTREAM_REPO, "--json", "tagName", "-q", ".tagName"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip().removeprefix("v") or "unknown"


def collect_status(now: datetime) -> dict:
    """Gather everything `status` reports into one dict."""
    limit = max_days()
    state = load_state()
    info: dict = {
        "last_review": None,
        "days_ago": None,
        "days_left": None,
        "overdue": True,
        "max_days": limit,
        "reviewed_server_version": None,
        "outcome": None,
    }
    if state is not None:
        days = age_days(state, now)
        info.update(
            last_review=state["reviewed_at"],
            days_ago=days,
            days_left=limit - days,
            overdue=days >= limit,
            reviewed_server_version=state.get("server_version"),
            outcome=state.get("outcome"),
        )
    info["installed_version"] = installed_version()
    info["latest_upstream"] = latest_upstream()
    return info


def cmd_status(args: argparse.Namespace, now: datetime) -> int:
    """Print the review status, as text or JSON."""
    info = collect_status(now)
    if args.json:
        print(json.dumps(info, indent=2))
        return 0
    print(f"last review:       {info['last_review'] or 'never'}")
    print(f"days ago:          {info['days_ago'] if info['days_ago'] is not None else 'n/a'}")
    print(f"days left:         {info['days_left'] if info['days_left'] is not None else 'n/a'}")
    print(
        f"overdue:           {'yes' if info['overdue'] else 'no'} (limit {info['max_days']} days)"
    )
    print(f"reviewed version:  {info['reviewed_server_version'] or 'n/a'}")
    print(f"outcome:           {info['outcome'] or 'n/a'}")
    print(f"installed version: {info['installed_version']}")
    print(f"latest upstream:   {info['latest_upstream']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("gate", help="PreToolUse hook mode: reads hook JSON on stdin")
    status = sub.add_parser("status", help="show review status")
    status.add_argument("--json", action="store_true", help="machine-readable output")
    stamp = sub.add_parser("stamp", help="record a completed review")
    stamp.add_argument("--server-version", required=True)
    stamp.add_argument("--outcome", required=True)
    stamp.add_argument("--tools-file", help="file with one tool name per line")
    diff = sub.add_parser("diff-tools", help="compare tool names with the last snapshot")
    diff.add_argument("--tools-file", required=True)
    return parser


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    """Run the CLI. `now` is injectable so tests control the clock."""
    now = now or now_local()
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["gate"]:
        return cmd_gate(now)  # Skip argparse: gate must never fail on arguments.
    args = build_parser().parse_args(argv)
    if args.command == "stamp":
        return cmd_stamp(args, now)
    if args.command == "diff-tools":
        return cmd_diff_tools(args)
    return cmd_status(args, now)


if __name__ == "__main__":
    sys.exit(main())
