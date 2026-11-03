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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

import httpx
import mcp_types as types
from mcp.server.session import ServerSession

from app_store_connect import __version__
from app_store_connect.config import Config
from app_store_connect.confirmflow import (
    ConfirmationBook,
    ConfirmationRefusedError,
    InputRequiredSignal,
    ask_human,
)
from app_store_connect.identity import compare_with_review, current_identity
from app_store_connect.report import (
    ReportFacts,
    ReportInputs,
    ReportResult,
    build_report_with_facts,
    direct_packages,
    installed_packages,
)
from app_store_connect.review import (
    Review,
    check_review,
    load_review,
    now_local,
    stamp_review,
)
from app_store_connect.spec import APPLE_API_SPEC_VERSION
from app_store_connect.textsafe import escape_display

logger = logging.getLogger(__name__)

STATUS: Final = "maintenance_status"
REPORT: Final = "maintenance_report"
CONFIRM: Final = "maintenance_confirm_review"
MAX_SUMMARY_CHARS: Final = 500
TERMINAL_COMMAND: Final = (
    "uvx --from git+https://github.com/RayanBayat/app-store-connect@main app-store-connect review"
)
# No input fields: the human's own Accept button confirms they read the report (see
# tools/confirm.py for why there is no checkbox).
CONFIRM_SCHEMA: Final[dict[str, Any]] = {"type": "object", "properties": {}}
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

ResultFn = Callable[[list[str]], ReportResult]
# A review may only be confirmed on a report that this server built a short while ago.
REPORT_MAX_AGE: Final = timedelta(minutes=30)


def text_result(text: str, *, is_error: bool = False) -> types.CallToolResult:
    """Wrap plain text as a tool result."""
    return types.CallToolResult(content=[types.TextContent(text=text)], is_error=is_error)


def new_report_client() -> httpx.Client:
    """The HTTP client for the report. It never follows redirects by itself."""
    return httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=False)


def make_report_result(current_tools: list[str]) -> ReportResult:
    """Build the real report with its figures (network access). Run it in a worker thread."""
    with new_report_client() as client:
        inputs = ReportInputs(
            client=client,
            now=now_local(),
            review=load_review(),
            current_tools=current_tools,
            packages=installed_packages(),
            direct_packages=direct_packages(),
        )
        return build_report_with_facts(inputs)


def make_report(current_tools: list[str]) -> str:
    """Build the real report text only (used by the `review` command)."""
    return make_report_result(current_tools).text


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
    lines += compare_with_review(
        review.code if review else None, current_identity(), has_review=review is not None
    )
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
    report_fn: ResultFn = field(default=make_report_result)
    _last_report: tuple[datetime, ReportFacts] | None = field(default=None, init=False)
    _book: ConfirmationBook = field(default_factory=ConfirmationBook, init=False)

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        session: ServerSession,
        tool_names: list[str],
        *,
        input_responses: Mapping[str, Any] | None = None,
        request_state: str | None = None,
    ) -> types.CallToolResult | types.InputRequiredResult:
        """Run one maintenance tool by name."""
        if name == STATUS:
            return text_result(self.status())
        if name == REPORT:
            result = await asyncio.to_thread(self.report_fn, tool_names)
            self._last_report = (self.clock(), result.facts)  # this session built a report
            return text_result(result.text)
        try:
            return await self.confirm(
                str(arguments.get("summary", "")),
                session,
                tool_names,
                arguments=arguments,
                input_responses=input_responses,
                request_state=request_state,
            )
        except InputRequiredSignal as signal:  # the human is asked; the client calls again
            return signal.result

    def status(self) -> str:
        """Text for maintenance_status."""
        return status_text(
            max_days=self.config.review_max_days,
            now=self.clock(),
            review=load_review(),
            writes=self.config.allow_writes,
        )

    async def confirm(
        self,
        summary: str,
        session: ServerSession,
        tool_names: list[str],
        *,
        arguments: Mapping[str, Any] | None = None,
        input_responses: Mapping[str, Any] | None = None,
        request_state: str | None = None,
    ) -> types.CallToolResult:
        """Ask the human to confirm; stamp the review only on an explicit accept of `read`."""
        summary = summary.strip()
        if not summary or len(summary) > MAX_SUMMARY_CHARS:
            return text_result(
                f"summary must be 1 to {MAX_SUMMARY_CHARS} characters describing the outcome.",
                is_error=True,
            )
        recent = self._recent_report()
        if recent is None:
            return text_result(
                "No maintenance report was built in this session in the last 30 minutes. "
                "Run maintenance_report, show it to the user, then call this tool again.",
                is_error=True,
            )
        try:
            await self._ask(
                summary,
                session,
                *recent,
                arguments=arguments or {"summary": summary},
                input_responses=input_responses,
                request_state=request_state,
            )
        except ConfirmationRefusedError as exc:
            return text_result(
                f"The review was NOT recorded: {exc}. Ask the user to run this in a terminal "
                f"instead: {TERMINAL_COMMAND}",
                is_error=True,
            )
        stamp_review(self.clock(), APPLE_API_SPEC_VERSION, summary, tool_names)
        return text_result("Review recorded. App Store Connect tools are available again.")

    def _recent_report(self) -> tuple[datetime, ReportFacts] | None:
        """The report built in this server session, if it is at most 30 minutes old."""
        if self._last_report is None:
            return None
        built_at, facts = self._last_report
        age = self.clock() - built_at
        return (built_at, facts) if timedelta(0) <= age <= REPORT_MAX_AGE else None

    async def _ask(
        self,
        summary: str,
        session: ServerSession,
        built_at: datetime,
        facts: ReportFacts,
        *,
        arguments: Mapping[str, Any],
        input_responses: Mapping[str, Any] | None,
        request_state: str | None,
    ) -> None:
        """Show the form (either protocol era); return only after an explicit read=true accept."""
        await ask_human(
            session,
            self._book,
            tool_name=CONFIRM,
            arguments=arguments,
            message=review_form(summary, built_at, facts, load_review()),
            schema=CONFIRM_SCHEMA,
            approve_field="read",
            input_responses=input_responses,
            request_state=request_state,
        )


def _count(value: int | None) -> str:
    return "NOT CHECKED" if value is None else str(value)


def review_form(summary: str, built_at: datetime, facts: ReportFacts, review: Review | None) -> str:
    """The text of the review form: the server's own figures first, Claude's summary last.

    The figures come from the report this server built, never from the model, so a rosy
    summary cannot hide a bad report. The summary is escaped and labelled as the model's.
    """
    spec = (
        f"missing endpoints {facts.missing_endpoints}, deprecated {facts.deprecated_endpoints}"
        if facts.spec_checked
        else "NOT CHECKED"
    )
    lines = [
        "Confirm the monthly App Store Connect maintenance review.",
        "",
        "Figures from this server's own report (not from Claude):",
        f"- Report built: {built_at.isoformat(timespec='seconds')}",
        f"- Apple API spec (checked against {APPLE_API_SPEC_VERSION}): {spec}; "
        f"parts not checked: {facts.not_checked}",
        f"- Known vulnerabilities in dependencies: {_count(facts.vulnerabilities)}",
        f"- Outdated dependencies: {_count(facts.outdated)}",
        *compare_with_review(
            review.code if review else None, current_identity(), has_review=review is not None
        ),
        "",
        f"Claude's summary (written by the model, not checked by the server): "
        f"{escape_display(summary)}",
        "",
        "Only confirm if you read the maintenance report yourself.",
    ]
    return "\n".join(lines)
