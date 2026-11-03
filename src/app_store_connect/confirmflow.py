"""Ask the human a yes/no question on both MCP protocol eras, and never act on a guess.

Two ways exist to show a form to the person, depending on the protocol version the client
negotiated:

- Up to 2025-11-25 the server may send a request back to the client in the middle of a tool
  call (the "back-channel"): `session.elicit_form(...)`.
- From 2026-07-28 it may not. Instead the tool returns an `input_required` result that carries
  the form; the client shows it and then calls the same tool again with the answer
  (`input_responses`) and the opaque `request_state` we gave it (multi round-trip requests).

In the second era the retry arrives from outside, so nothing in it is trusted. The SDK's
`RequestStateBoundary` (see `build_server`) seals `request_state` with AES-GCM under a random
per-process key, binds it to the tool name and exact arguments, and expires it after 10
minutes. On top of that this module puts a single-use nonce and a digest of the exact change
the human saw. On the retry the caller rebuilds the change from the current arguments and the
current Apple data; if it differs from what the human was shown, a fresh form is returned
instead of acting. Only the human pressing Accept in their own client lets anything proceed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from typing import Any, Final

import mcp_types as types
from mcp.server.request_state import compact_json
from mcp.server.session import ServerSession
from mcp.shared.exceptions import MCPError, NoBackChannelError
from mcp_types.version import is_version_at_least

logger = logging.getLogger(__name__)

INPUT_REQUIRED_VERSION: Final = "2026-07-28"
INPUT_KEY: Final = "confirm"
STATE_TTL_SECONDS: Final = 600.0  # 10 minutes, the same as the SDK's request-state expiry


class ConfirmationRefusedError(Exception):
    """No confirmation was given. The text says why, without any secret."""


class InputRequiredSignal(Exception):  # noqa: N818 - a control-flow signal, not an error
    """Raised to hand an `input_required` result up to the server, which returns it."""

    def __init__(self, result: types.InputRequiredResult) -> None:
        super().__init__("input required")
        self.result = result


class ConfirmationBook:
    """Remembers the single-use nonces we issued, until they expire.

    A nonce is removed the moment it is used, so a replayed `request_state` finds nothing.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._pending: dict[str, float] = {}

    def issue(self) -> str:
        """Create a nonce that is valid for 10 minutes."""
        self._purge()
        nonce = secrets.token_hex(16)
        self._pending[nonce] = self._clock() + STATE_TTL_SECONDS
        return nonce

    def consume(self, nonce: object) -> bool:
        """True exactly once for each issued, unexpired nonce."""
        self._purge()
        return isinstance(nonce, str) and self._pending.pop(nonce, None) is not None

    def _purge(self) -> None:
        now = self._clock()
        for nonce in [n for n, expires in self._pending.items() if expires <= now]:
            del self._pending[nonce]


def change_digest(
    tool_name: str, arguments: Mapping[str, Any], message: str, approve_field: str
) -> str:
    """A fingerprint of the exact question: the tool, its arguments and the form text."""
    canonical = json.dumps([tool_name, arguments, message, approve_field], sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()


def form_support_problem(session: ServerSession) -> str | None:
    """Why this client cannot show forms, or None when it can (or has not said).

    A client that declares `elicitation` with `form`, or an empty `elicitation` object (the
    older way to say "forms"), is accepted. Only an explicit URL-only declaration, or no
    elicitation entry at all, is refused. If the client declared no capabilities at all we
    cannot tell, so we try: nothing is ever done without an explicit accept anyway.
    """
    caps = session.client_capabilities
    if caps is None:
        return None
    elicitation = caps.elicitation
    if elicitation is None:
        return "this client cannot show confirmation forms (it did not declare elicitation support)"
    if elicitation.form is None and elicitation.url is not None:
        return "this client cannot show confirmation forms (it supports only URL elicitation)"
    return None


def _outcome(result: types.ElicitResult) -> None:
    """Return only when the human explicitly pressed Accept in their own client.

    The forms have no input fields, so Accept itself is the approval.
    """
    if result.action == "decline":
        raise ConfirmationRefusedError("the user declined")
    if result.action == "cancel":
        raise ConfirmationRefusedError("the user cancelled")
    if result.action != "accept":
        raise ConfirmationRefusedError(f"unexpected form answer {result.action!r}")


async def _ask_back_channel(session: ServerSession, message: str, schema: dict[str, Any]) -> None:
    """Older protocol versions: send the form to the client in the middle of the call."""
    try:
        result = await session.elicit_form(message, schema)
    except NoBackChannelError as exc:
        raise ConfirmationRefusedError(
            "this connection has no back-channel for forms and the client does not use "
            f"input_required (protocol {session.protocol_version})"
        ) from exc
    except MCPError as exc:
        logger.info("confirmation form could not be shown: %s", exc)
        raise ConfirmationRefusedError(
            "this client cannot show confirmation forms (MCP elicitation)"
        ) from exc
    _outcome(result)


def _first_round(
    book: ConfirmationBook, message: str, schema: dict[str, Any], digest: str
) -> InputRequiredSignal:
    """Build the `input_required` result: the form plus the state to be echoed back."""
    request = types.ElicitRequest(
        params=types.ElicitRequestFormParams(message=message, requested_schema=schema)
    )
    state = compact_json({"n": book.issue(), "d": digest})
    result = types.InputRequiredResult(input_requests={INPUT_KEY: request}, request_state=state)
    return InputRequiredSignal(result)


def _read_state(request_state: str) -> tuple[object, object]:
    """Parse the (already unsealed) state into (nonce, digest); anything odd is a refusal."""
    try:
        state = json.loads(request_state)
        return state["n"], state["d"]
    except (ValueError, KeyError, TypeError):
        raise ConfirmationRefusedError("the confirmation state is malformed") from None


async def _ask_input_required(
    book: ConfirmationBook,
    message: str,
    schema: dict[str, Any],
    digest: str,
    input_responses: Mapping[str, Any] | None,
    request_state: str | None,
) -> None:
    if request_state is None and not input_responses:
        raise _first_round(book, message, schema, digest)
    if request_state is None:
        raise ConfirmationRefusedError("the answer came without the confirmation state")
    nonce, seen_digest = _read_state(request_state)
    if not book.consume(nonce):  # unknown, expired or already used: single use
        raise ConfirmationRefusedError("the confirmation expired or was already used")
    answer = (input_responses or {}).get(INPUT_KEY)
    if not isinstance(answer, types.ElicitResult):
        raise ConfirmationRefusedError("the answer to the form is missing or malformed")
    _outcome(answer)
    if seen_digest != digest:
        # The human approved something else (for example the old value changed meanwhile).
        logger.warning("confirmation does not match the current change; asking again")
        raise _first_round(book, message, schema, digest)


async def ask_human(
    session: ServerSession,
    book: ConfirmationBook,
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
    message: str,
    schema: dict[str, Any],
    approve_field: str,
    input_responses: Mapping[str, Any] | None = None,
    request_state: str | None = None,
) -> None:
    """Return only when the human explicitly approved exactly this `message`.

    Raises `ConfirmationRefusedError` (nothing may be done) or `InputRequiredSignal` (return that
    result to the client; the call is retried with the answer).
    """
    try:
        problem = form_support_problem(session)
        if problem is not None:
            raise ConfirmationRefusedError(problem)
        if is_version_at_least(session.protocol_version, INPUT_REQUIRED_VERSION):
            digest = change_digest(tool_name, arguments, message, approve_field)
            await _ask_input_required(book, message, schema, digest, input_responses, request_state)
        else:
            await _ask_back_channel(session, message, schema)
    except ConfirmationRefusedError as exc:
        logger.warning("no confirmation for %s: %s", tool_name, exc)
        raise
