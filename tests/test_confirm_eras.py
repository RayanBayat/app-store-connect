"""Confirmations on both protocol eras: back-channel (<= 2025-11-25) and input_required (2026-07-28)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import mcp_types as types
import pytest
from mcp import Client
from mcp.client.session import ClientRequestContext
from mcp.shared.exceptions import MCPError, NoBackChannelError

from app_store_connect import confirmflow
from app_store_connect.confirmflow import ConfirmationBook, form_support_problem
from app_store_connect.maintenance_record import load_record

from .apple_fake import FakeApple, connected, one, resource, text_of

MODERN = "2026-07-28"
ERAS = ["legacy", MODERN]
ARGS = {"localization_id": "L1", "description": "new description"}
TOOL = "update_version_localization"
LOC = "appStoreVersionLocalizations"


def loc_fake(old: str = "old description") -> FakeApple:
    fake = FakeApple()
    fake.on(
        "GET",
        "/v1/appStoreVersionLocalizations/L1",
        one(resource(LOC, "L1", locale="en-US", description=old)),
    )
    fake.on(
        "PATCH",
        "/v1/appStoreVersionLocalizations/L1",
        one(resource(LOC, "L1", description="new description")),
    )
    return fake  # fmt: skip


class Answers:
    """An elicitation callback that records forms and answers with a fixed action."""

    def __init__(
        self, action: str = "accept", *, field: str = "approve", value: Any = True
    ) -> None:
        self.action, self.field, self.value = action, field, value
        self.forms: list[str] = []
        self.schemas: list[dict[str, Any]] = []
        self.on_form: Callable[[int], None] | None = None

    async def __call__(
        self, context: ClientRequestContext, params: types.ElicitRequestParams
    ) -> types.ElicitResult:
        self.forms.append(params.message)
        schema = getattr(params, "requested_schema", None)
        if schema is not None:
            self.schemas.append(
                schema if isinstance(schema, dict) else schema.model_dump(exclude_none=True)
            )
        if self.on_form is not None:
            self.on_form(len(self.forms))
        if self.action != "accept":
            return types.ElicitResult(action=self.action)  # type: ignore[arg-type]
        return types.ElicitResult(action="accept", content={self.field: self.value})


def accepted() -> types.InputResponses:
    return {"confirm": types.ElicitResult(action="accept", content={"approve": True})}


async def first_round(
    client: Client, args: dict[str, Any] | None = None
) -> types.InputRequiredResult:
    result = await client.session.call_tool(TOOL, args or ARGS, allow_input_required=True)
    assert isinstance(result, types.InputRequiredResult)
    assert result.request_state is not None
    return result


# ------------------------------------------------------------------ both eras end to end


@pytest.mark.parametrize("mode", ERAS)
async def test_accept_sends_exactly_the_confirmed_request_once(tmp_path: Path, mode: str) -> None:
    fake, answers = loc_fake(), Answers()
    async with connected(tmp_path, fake, allow_writes=True, elicit=answers, mode=mode) as c:
        result = await c.call_tool(TOOL, ARGS)
    assert not result.is_error, text_of(result)
    assert len(answers.forms) == 1
    assert "old description" in answers.forms[0] and "new description" in answers.forms[0]
    assert [(w.method, w.body["data"]["attributes"]) for w in fake.writes()] == [
        ("PATCH", {"description": "new description"})
    ]


@pytest.mark.parametrize("mode", ERAS)
@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_decline_and_cancel_send_nothing_and_say_why(
    tmp_path: Path, mode: str, action: str
) -> None:
    fake = loc_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answers(action), mode=mode) as c:
        result = await c.call_tool(TOOL, ARGS)
    assert result.is_error
    assert ("declined" if action == "decline" else "cancelled") in text_of(result)
    assert "Nothing was changed" in text_of(result)
    assert fake.writes() == []


@pytest.mark.parametrize("mode", ERAS)
async def test_forms_have_no_input_fields(tmp_path: Path, mode: str) -> None:
    """Claude Code could not tick a required checkbox, so the forms ask nothing: Accept approves."""
    fake = loc_fake()
    answers = Answers()
    async with connected(tmp_path, fake, allow_writes=True, elicit=answers, mode=mode) as c:
        await c.call_tool(TOOL, ARGS)
    assert answers.schemas, "no form was shown"
    assert all(schema.get("properties") == {} for schema in answers.schemas)
    assert all("required" not in schema for schema in answers.schemas)


@pytest.mark.parametrize("mode", ERAS)
async def test_client_without_form_support_sends_nothing_and_says_so(
    tmp_path: Path, mode: str
) -> None:
    fake = loc_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=None, mode=mode) as c:
        result = await c.call_tool(TOOL, ARGS)
    assert result.is_error
    assert "cannot show confirmation forms" in text_of(result)
    assert fake.writes() == []


async def test_reason_is_logged_for_stderr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="app_store_connect.confirmflow")
    async with connected(
        tmp_path, loc_fake(), allow_writes=True, elicit=Answers("cancel"), mode=MODERN
    ) as c:
        await c.call_tool(TOOL, ARGS)
    assert "the user cancelled" in caplog.text


# ------------------------------------------------------------------ the 2026-07-28 round trip


async def test_first_round_returns_the_full_form_and_a_sealed_opaque_state(tmp_path: Path) -> None:
    long_value = "A" * 400 + "TAIL"
    fake = loc_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answers(), mode=MODERN) as c:
        first = await first_round(c, {**ARGS, "description": long_value})
    request = first.input_requests["confirm"]  # type: ignore[index]
    assert isinstance(request, types.ElicitRequest)
    assert long_value in request.params.message and "404 characters" in request.params.message
    assert first.request_state.startswith("v1.")  # type: ignore[union-attr]
    assert "TAIL" not in first.request_state and "description" not in first.request_state  # type: ignore[operator]
    assert fake.writes() == []


async def test_manual_retry_with_accept_writes_once_and_a_replay_is_refused(
    tmp_path: Path,
) -> None:
    fake = loc_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answers(), mode=MODERN) as c:
        first = await first_round(c)
        retry = await c.session.call_tool(
            TOOL, ARGS, input_responses=accepted(), request_state=first.request_state
        )
        replay = await c.session.call_tool(
            TOOL, ARGS, input_responses=accepted(), request_state=first.request_state
        )
    assert not retry.is_error, text_of(retry)
    assert replay.is_error and "already used" in text_of(replay)
    assert len(fake.writes()) == 1


async def test_tampered_state_is_refused(tmp_path: Path) -> None:
    fake = loc_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answers(), mode=MODERN) as c:
        first = await first_round(c)
        state = first.request_state
        assert state is not None
        flipped = state[:-3] + ("AAA" if state[-3:] != "AAA" else "BBB")
        with pytest.raises(MCPError):
            await c.session.call_tool(TOOL, ARGS, input_responses=accepted(), request_state=flipped)
        with pytest.raises(MCPError):
            await c.session.call_tool(
                TOOL, ARGS, input_responses=accepted(), request_state="v1.bogus"
            )
    assert fake.writes() == []


async def test_expired_state_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = loc_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answers(), mode=MODERN) as c:
        first = await first_round(c)
        real = time.time
        monkeypatch.setattr("mcp.server.request_state.time.time", lambda: real() + 601)
        with pytest.raises(MCPError):
            await c.session.call_tool(
                TOOL, ARGS, input_responses=accepted(), request_state=first.request_state
            )
    assert fake.writes() == []


def test_nonces_expire_and_are_single_use() -> None:
    now = [1000.0]
    book = ConfirmationBook(clock=lambda: now[0])
    a, b = book.issue(), book.issue()
    assert book.consume(a) and not book.consume(a)
    now[0] += 601
    assert not book.consume(b)
    assert not book.consume("never-issued") and not book.consume(None)


async def test_changed_arguments_on_retry_are_refused(tmp_path: Path) -> None:
    fake = loc_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answers(), mode=MODERN) as c:
        first = await first_round(c)
        with pytest.raises(MCPError):
            await c.session.call_tool(
                TOOL,
                {**ARGS, "description": "something the human never saw"},
                input_responses=accepted(),
                request_state=first.request_state,
            )
    assert fake.writes() == []


async def test_state_from_another_tool_call_is_refused(tmp_path: Path) -> None:
    fake = loc_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answers(), mode=MODERN) as c:
        first = await first_round(c)
        with pytest.raises(MCPError):
            await c.session.call_tool(
                "update_app_info_localization",
                {"localization_id": "L1", "name": "x"},
                input_responses=accepted(),
                request_state=first.request_state,
            )
    assert fake.writes() == []


@pytest.mark.parametrize(
    "responses",
    [
        {"wrong-key": types.ElicitResult(action="accept", content={"approve": True})},
        {"confirm": types.ElicitResult(action="decline")},
        {"confirm": types.ElicitResult(action="cancel")},
        None,
    ],
    ids=["wrong-key", "decline", "cancel", "missing"],
)
async def test_bad_or_missing_answers_send_nothing(
    tmp_path: Path, responses: types.InputResponses | None
) -> None:
    fake = loc_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answers(), mode=MODERN) as c:
        first = await first_round(c)
        result = await c.session.call_tool(
            TOOL, ARGS, input_responses=responses, request_state=first.request_state
        )
    assert isinstance(result, types.CallToolResult) and result.is_error
    assert fake.writes() == []


async def test_answer_without_state_is_refused(tmp_path: Path) -> None:
    fake = loc_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answers(), mode=MODERN) as c:
        result = await c.session.call_tool(TOOL, ARGS, input_responses=accepted())
    assert result.is_error and fake.writes() == []


async def test_old_value_changed_between_form_and_retry_shows_a_fresh_form(
    tmp_path: Path,
) -> None:
    fake = loc_fake("first old value")
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answers(), mode=MODERN) as c:
        first = await first_round(c)
        fake.on(  # someone edits the text on Apple while the human reads the form
            "GET",
            "/v1/appStoreVersionLocalizations/L1",
            one(resource(LOC, "L1", locale="en-US", description="second old value")),
        )
        again = await c.session.call_tool(
            TOOL,
            ARGS,
            input_responses=accepted(),
            request_state=first.request_state,
            allow_input_required=True,
        )
        assert fake.writes() == []  # the first approval does not cover the new situation
        assert isinstance(again, types.InputRequiredResult)
        request = again.input_requests["confirm"]  # type: ignore[index]
        assert isinstance(request, types.ElicitRequest)
        assert "second old value" in request.params.message
        final = await c.session.call_tool(
            TOOL, ARGS, input_responses=accepted(), request_state=again.request_state
        )
    assert not final.is_error, text_of(final)
    assert len(fake.writes()) == 1


async def test_sdk_client_loop_handles_a_changed_old_value_by_asking_again(
    tmp_path: Path,
) -> None:
    fake, answers = loc_fake("first old value"), Answers()

    def change_on_first_form(count: int) -> None:
        if count == 1:
            fake.on("GET", "/v1/appStoreVersionLocalizations/L1",
                    one(resource(LOC, "L1", locale="en-US", description="second old value")))  # fmt: skip

    answers.on_form = change_on_first_form
    async with connected(tmp_path, fake, allow_writes=True, elicit=answers, mode=MODERN) as c:
        result = await c.call_tool(TOOL, ARGS)
    assert not result.is_error, text_of(result)
    assert len(answers.forms) == 2 and "second old value" in answers.forms[1]
    assert len(fake.writes()) == 1


# ------------------------------------------------------------------ review confirmation


@pytest.mark.parametrize("mode", ERAS)
async def test_review_confirmation_stamps_only_on_accept(tmp_path: Path, mode: str) -> None:
    answers = Answers(field="read")
    async with connected(tmp_path, FakeApple(), fresh_review=False, elicit=answers, mode=mode) as c:
        await c.call_tool("maintenance_report", {})
        result = await c.call_tool("maintenance_confirm_review", {"summary": "fine"})
    assert not result.is_error, text_of(result)
    assert "Claude's summary" in answers.forms[0]
    review = load_record()
    assert review is not None and review.outcome == "fine"


@pytest.mark.parametrize("mode", ERAS)
@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_review_confirmation_declined_does_not_stamp(
    tmp_path: Path, mode: str, action: str
) -> None:
    async with connected(
        tmp_path, FakeApple(), fresh_review=False, elicit=Answers(action, field="read"), mode=mode
    ) as c:
        await c.call_tool("maintenance_report", {})
        result = await c.call_tool("maintenance_confirm_review", {"summary": "fine"})
    assert result.is_error and load_record() is None


async def test_review_confirmation_still_needs_a_recent_report_on_modern(tmp_path: Path) -> None:
    answers = Answers(field="read")
    async with connected(
        tmp_path, FakeApple(), fresh_review=False, elicit=answers, mode=MODERN
    ) as c:
        result = await c.call_tool("maintenance_confirm_review", {"summary": "fine"})
    assert result.is_error and answers.forms == [] and load_record() is None


async def test_review_state_replay_and_changed_summary_are_refused(tmp_path: Path) -> None:
    ok: types.InputResponses = {
        "confirm": types.ElicitResult(action="accept", content={"read": True})
    }
    async with connected(
        tmp_path, FakeApple(), fresh_review=False, elicit=Answers(field="read"), mode=MODERN
    ) as c:
        await c.call_tool("maintenance_report", {})
        first = await c.session.call_tool(
            "maintenance_confirm_review", {"summary": "fine"}, allow_input_required=True
        )
        assert isinstance(first, types.InputRequiredResult)
        with pytest.raises(MCPError):  # a different summary than the human saw
            await c.session.call_tool(
                "maintenance_confirm_review",
                {"summary": "different"},
                input_responses=ok,
                request_state=first.request_state,
            )
        done = await c.session.call_tool(
            "maintenance_confirm_review",
            {"summary": "fine"},
            input_responses=ok,
            request_state=first.request_state,
        )
        replay = await c.session.call_tool(
            "maintenance_confirm_review",
            {"summary": "fine"},
            input_responses=ok,
            request_state=first.request_state,
        )
    assert not done.is_error and replay.is_error


# ------------------------------------------------------------------ capability and diagnostics


class CapSession:
    def __init__(self, elicitation: types.ElicitationCapability | None) -> None:
        self.client_capabilities = types.ClientCapabilities(elicitation=elicitation)


@pytest.mark.parametrize(
    ("elicitation", "refused"),
    [
        (types.ElicitationCapability(form=types.FormElicitationCapability()), False),
        (types.ElicitationCapability(), False),  # the older "elicitation: {}" means forms
        (types.ElicitationCapability(form=types.FormElicitationCapability(),
                                     url=types.UrlElicitationCapability()), False),
        (types.ElicitationCapability(url=types.UrlElicitationCapability()), True),
        (None, True),
    ],
    ids=["form", "empty", "form-and-url", "url-only", "none"],
)  # fmt: skip
def test_capability_check_accepts_every_way_of_saying_forms(
    elicitation: types.ElicitationCapability | None, refused: bool
) -> None:
    problem = form_support_problem(CapSession(elicitation))  # type: ignore[arg-type]
    assert (problem is not None) is refused


async def test_no_back_channel_is_reported_as_such(tmp_path: Path) -> None:
    class Session:
        client_capabilities = None
        protocol_version = "2025-11-25"

        async def elicit_form(self, message: str, schema: object) -> types.ElicitResult:
            raise NoBackChannelError("elicitation/create")

    with pytest.raises(confirmflow.ConfirmationRefusedError, match="no back-channel"):
        await confirmflow.ask_human(
            Session(),  # type: ignore[arg-type]
            ConfirmationBook(),
            tool_name="t",
            arguments={},
            message="m",
            schema={},
        )
