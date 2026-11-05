"""Human confirmation for every change, through MCP elicitation.

The model cannot confirm for itself: there is no "confirm" argument anywhere. We show the
human's own client a form that states exactly what will change, and only an explicit
"accept" from the human lets the write proceed. Anything else (decline, cancel, a client
without form support) means nothing is sent to Apple.
"""

from __future__ import annotations

from typing import Any, Final

from app_store_connect.confirmflow import ConfirmationRefusedError, ask_human
from app_store_connect.textsafe import escape_display
from app_store_connect.tools.base import ToolContext, ToolRefusedError

# No input fields: the form shows the change, and the human's own Accept button is the
# approval. (A required checkbox could not be ticked in Claude Code's form, which blocked
# every confirmation.) The model cannot press Accept: only the human's client can.
APPROVE_SCHEMA: Final[dict[str, Any]] = {"type": "object", "properties": {}}


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
    """Ask the human to approve a change; raise `ToolRefusedError` unless they explicitly accept.

    On protocol 2026-07-28 the first call raises `InputRequiredSignal` (the server returns the
    form to the client) and the retried call, which rebuilds this same form from current data,
    comes back here with the answer. See `confirmflow` for how that is made safe.
    """
    if ctx.session is None:
        raise ToolRefusedError(
            "No client session is available to ask for confirmation. Nothing was changed."
        )
    try:
        await ask_human(
            ctx.session,
            ctx.confirmations,
            tool_name=ctx.tool_name,
            arguments=ctx.arguments,
            message=build_message(title, lines, public=public),
            schema=APPROVE_SCHEMA,
            input_responses=ctx.input_responses,
            request_state=ctx.request_state,
        )
    except ConfirmationRefusedError as exc:
        raise ToolRefusedError(
            f"The change was not confirmed: {exc}. Nothing was changed."
        ) from exc
