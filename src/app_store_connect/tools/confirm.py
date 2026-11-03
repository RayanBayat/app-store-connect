"""Human confirmation for every change, through MCP elicitation.

The model cannot confirm for itself: there is no "confirm" argument anywhere. We show the
human's own client a form that states exactly what will change, and only an explicit
"accept" with the approval box ticked lets the write proceed. Anything else (decline,
cancel, a client without form support) means nothing is sent to Apple.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from mcp.shared.exceptions import MCPError

from app_store_connect.tools.base import ToolContext, ToolRefusedError

logger = logging.getLogger(__name__)

MAX_VALUE_CHARS: Final = 300
APPROVE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {"approve": {"type": "boolean", "title": "Yes, make this change"}},
    "required": ["approve"],
}


def shorten(value: object) -> str:
    """Show a value in a form: None as '(empty)', long text cut."""
    text = "(empty)" if value in (None, "") else str(value)
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "..."


def change_line(label: str, old: object, new: object) -> str:
    """One 'label: old -> new' line."""
    return f"{label}: {shorten(old)}  ->  {shorten(new)}"


def build_message(title: str, lines: list[str], *, public: bool) -> str:
    """Compose the text of the confirmation form."""
    head = ["*** PUBLIC AND IRREVERSIBLE ***"] if public else []
    return "\n".join([*head, title, "", *lines])


async def confirm_change(
    ctx: ToolContext, title: str, lines: list[str], *, public: bool = False
) -> None:
    """Ask the human to approve a change; raise `ToolRefusedError` unless they explicitly accept."""
    if ctx.session is None:
        raise ToolRefusedError(
            "No client session is available to ask for confirmation. Nothing was changed."
        )
    try:
        result = await ctx.session.elicit_form(
            build_message(title, lines, public=public), APPROVE_SCHEMA
        )
    except MCPError as exc:
        logger.info("confirmation form could not be shown: %s", exc)
        raise ToolRefusedError(
            "This client cannot show confirmation forms (MCP elicitation), and every change "
            "needs your explicit confirmation. Nothing was changed."
        ) from exc
    approved = result.action == "accept" and (result.content or {}).get("approve") is True
    if not approved:
        raise ToolRefusedError(
            f"The change was not confirmed ({result.action}). Nothing was changed."
        )
