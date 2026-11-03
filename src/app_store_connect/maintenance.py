"""The three maintenance tools this server adds next to the App Store Connect tools.

- maintenance_status: read-only, when the last review was and which versions are in play.
- maintenance_report: read-only, the monthly report to read.
- maintenance_confirm_review: records that a HUMAN read the report. The model can never do
  this alone, because the stamp is only written after the human accepts a form shown by
  their MCP client (elicitation). If that is not possible, we tell the user to run the
  `review` command in a terminal instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import httpx
import mcp_types as types
from mcp.server.session import ServerSession
from mcp.shared.exceptions import MCPError

from app_store_connect import __version__
from app_store_connect.config import Config
from app_store_connect.report import ReportInputs, build_report, installed_packages
from app_store_connect.review import (
    Review,
    check_review,
    load_review,
    now_local,
    stamp_review,
)
from app_store_connect.spec import APPLE_API_SPEC_VERSION

logger = logging.getLogger(__name__)

STATUS: Final = "maintenance_status"
REPORT: Final = "maintenance_report"
CONFIRM: Final = "maintenance_confirm_review"
MAX_SUMMARY_CHARS: Final = 500
TERMINAL_COMMAND: Final = (
    "uvx --from git+https://github.com/RayanBayat/app-store-connect@main app-store-connect review"
)
CONFIRM_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "read": {"type": "boolean", "title": "I have read the maintenance report"},
    },
    "required": ["read"],
}
HTTP_TIMEOUT: Final = httpx.Timeout(30.0, connect=10.0)

_EMPTY_INPUT: Final[dict[str, Any]] = {"type": "object", "properties": {}}

TOOLS: Final[list[types.Tool]] = [
    types.Tool(
        name=STATUS,
        description=(
            "Show when the monthly maintenance review was last done, how many days are left, "
            "and which Apple API spec version this server was checked against."
        ),
        input_schema=_EMPTY_INPUT,
        annotations=types.ToolAnnotations(read_only_hint=True, open_world_hint=False),
    ),
    types.Tool(
        name=REPORT,
        description=(
            "Build the monthly maintenance report (Apple API changes, deprecated endpoints, "
            "outdated or vulnerable dependencies, tool changes). Show the whole report to the user."
        ),
        input_schema=_EMPTY_INPUT,
        annotations=types.ToolAnnotations(read_only_hint=True, open_world_hint=True),
    ),
    types.Tool(
        name=CONFIRM,
        description=(
            "Record that the user read the maintenance report. The user is asked to confirm "
            "in a form; nothing is recorded without their explicit yes. Call this only after "
            "showing the user the report from maintenance_report."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One or two sentences: what changed and what was decided.",
                    "maxLength": MAX_SUMMARY_CHARS,
                }
            },
            "required": ["summary"],
        },
        annotations=types.ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    ),
]
NAMES: Final = frozenset(tool.name for tool in TOOLS)

ReportFn = Callable[[list[str]], str]


def text_result(text: str, *, is_error: bool = False) -> types.CallToolResult:
    """Wrap plain text as a tool result."""
    return types.CallToolResult(content=[types.TextContent(text=text)], is_error=is_error)


def new_report_client() -> httpx.Client:
    """The HTTP client for the report. It never follows redirects by itself."""
    return httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=False)


def make_report(current_tools: list[str]) -> str:
    """Build the real report (network access). Run it in a worker thread."""
    with new_report_client() as client:
        inputs = ReportInputs(
            client=client,
            now=now_local(),
            review=load_review(),
            current_tools=current_tools,
            packages=installed_packages(),
        )
        return build_report(inputs)


def describe_review(review: Review | None, now: datetime, max_days: int) -> list[str]:
    """Return status lines about the last review."""
    check = check_review(review, now, max_days)
    if review is None:
        return [f"Last review: never. {check.message}"]
    lines = [
        f"Last review: {review.reviewed_at.isoformat(timespec='minutes')} "
        f"(Apple spec {review.spec_version})",
        f"Outcome: {review.outcome or 'n/a'}",
    ]
    if check.overdue:
        lines.append(f"Status: OVERDUE. {check.message}")
    else:
        lines.append(f"Status: fresh, {check.days_left} days left (limit {max_days} days).")
    return lines


def status_text(*, max_days: int, now: datetime, review: Review | None, writes: bool | None) -> str:
    """Build the text of maintenance_status (shared with the `status` command)."""
    lines = describe_review(review, now, max_days)
    lines.append(f"Apple API spec checked against: {APPLE_API_SPEC_VERSION}")
    lines.append(f"app-store-connect version: {__version__}")
    if writes is not None:
        lines.append(f"Writes: {'ENABLED (every change asks you first)' if writes else 'off'}")
    return "\n".join(lines)


@dataclass(slots=True)
class Maintenance:
    """Handles the maintenance tools. Everything that touches the network is injectable."""

    config: Config
    clock: Callable[[], datetime] = now_local
    report_fn: ReportFn = field(default=make_report)

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        session: ServerSession,
        tool_names: list[str],
    ) -> types.CallToolResult:
        """Run one maintenance tool by name."""
        if name == STATUS:
            return text_result(self.status())
        if name == REPORT:
            return text_result(await asyncio.to_thread(self.report_fn, tool_names))
        return await self.confirm(str(arguments.get("summary", "")), session, tool_names)

    def status(self) -> str:
        """Text for maintenance_status."""
        return status_text(
            max_days=self.config.review_max_days,
            now=self.clock(),
            review=load_review(),
            writes=self.config.allow_writes,
        )

    async def confirm(
        self, summary: str, session: ServerSession, tool_names: list[str]
    ) -> types.CallToolResult:
        """Ask the human to confirm; stamp the review only on an explicit accept of `read`."""
        summary = summary.strip()
        if not summary or len(summary) > MAX_SUMMARY_CHARS:
            return text_result(
                f"summary must be 1 to {MAX_SUMMARY_CHARS} characters describing the outcome.",
                is_error=True,
            )
        if not await self._human_accepts(summary, session):
            return text_result(
                "The review was NOT recorded: the confirmation was declined or this client "
                "cannot show confirmation forms. Ask the user to run this in a terminal "
                f"instead: {TERMINAL_COMMAND}",
                is_error=True,
            )
        stamp_review(self.clock(), APPLE_API_SPEC_VERSION, summary, tool_names)
        return text_result("Review recorded. App Store Connect tools are available again.")

    async def _human_accepts(self, summary: str, session: ServerSession) -> bool:
        """Show the form and return True only for an explicit accept with read=true."""
        message = (
            "Confirm the monthly App Store Connect maintenance review.\n\n"
            f"Summary from Claude: {summary}\n\n"
            "Only confirm if you read the maintenance report yourself."
        )
        try:
            result = await session.elicit_form(message, CONFIRM_SCHEMA)
        except MCPError as exc:
            logger.info("could not ask for confirmation: %s", exc)
            return False
        return result.action == "accept" and (result.content or {}).get("read") is True
