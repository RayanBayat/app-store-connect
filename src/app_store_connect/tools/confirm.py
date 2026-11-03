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

from app_store_connect.textsafe import escape_display
from app_store_connect.tools.base import ToolContext, ToolRefusedError

logger = logging.getLogger(__name__)

APPROVE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {"approve": {"type": "boolean", "title": "Yes, make this change"}},
    "required": ["approve"],
}


def show_value(value: object) -> str:
    """Show a value in a form: None as '(empty)', otherwise the whole text, escaped.

    Never shortened: the human must see exactly what is stored or would be sent.
    """
    return "(empty)" if value in (None, "") else escape_display(value)


def change_line(label: str, old: object, new: object) -> str:
    """One 'label: old -> new (N characters)' line; the count is for the new value."""
    count = "" if new in (None, "") else f" ({len(str(new))} characters)"
    return f"{label}: {show_value(old)}  ->  {show_value(new)}{count}"


def build_message(title: str, lines: list[str], *, public: bool) -> str:
    """Compose the text of the confirmation form.

    Every line is escaped here as a backstop, so no value that reached a form (from the API
    or from the model) can add lines or hide text, whichever tool built it.
    """
    head = ["*** PUBLIC AND IRREVERSIBLE ***"] if public else []
    safe = [escape_display(line) for line in lines]
    return "\n".join([*head, escape_display(title), "", *safe])


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
