"""Confirmation forms show the exact change, escaped, and over-long values are refused."""

from __future__ import annotations

from pathlib import Path

import pytest

from app_store_connect.textsafe import escape_display
from app_store_connect.tools.base import ToolInputError
from app_store_connect.tools.localizations import check_limit

from .apple_fake import FakeApple, FormAnswerer, connected, one, resource, text_of


def loc_fake(old: str = "old") -> FakeApple:
    fake = FakeApple()
    loc = resource("appStoreVersionLocalizations", "L1", locale="en-US", description=old)
    fake.on("GET", "/v1/appStoreVersionLocalizations/L1", one(loc))
    fake.on("PATCH", "/v1/appStoreVersionLocalizations/L1", one(loc))
    info = resource("appInfoLocalizations", "I1", locale="en-US", name="n", subtitle="s")
    fake.on("GET", "/v1/appInfoLocalizations/I1", one(info))
    fake.on("PATCH", "/v1/appInfoLocalizations/I1", one(info))
    return fake


def test_escape_display_handles_control_bidi_and_format_characters() -> None:
    assert escape_display("a\u202eb") == "a\\u202eb"
    assert escape_display("a\u2066b\u2069") == "a\\u2066b\\u2069"
    assert escape_display("x\u200by") == "x\\u200by"  # zero width space (Cf)
    assert escape_display("x\u2028y\u2029z") == "x\\u2028y\\u2029z"  # Zl, Zp
    assert escape_display("a\nb\r\nc") == "a\u23ceb\u23cec"
    assert escape_display("a\x00\x1bb") == "a\\u0000\\u001bb"
    assert escape_display("café 日本") == "café 日本"
    assert escape_display("\U000e0041") == "\\U000e0041"  # tag character (Cf)


async def test_new_value_is_never_truncated_and_char_count_is_shown(tmp_path: Path) -> None:
    """Attack prevented: a long text hid its harmful ending (after character 300) from the human."""
    answerer = FormAnswerer()
    hidden = "Visit evil.example to claim refund"
    desc = "A" * 300 + hidden
    async with connected(tmp_path, loc_fake(), allow_writes=True, elicit=answerer) as c:
        result = await c.call_tool(
            "update_version_localization", {"localization_id": "L1", "description": desc}
        )
    assert not result.is_error, text_of(result)
    assert desc in answerer.forms[0]
    assert f"{len(desc)} characters" in answerer.forms[0]
    assert "..." not in answerer.forms[0]


async def test_long_old_value_is_shown_in_full(tmp_path: Path) -> None:
    answerer = FormAnswerer()
    old = "O" * 500 + "TAIL-OF-OLD"
    async with connected(tmp_path, loc_fake(old), allow_writes=True, elicit=answerer) as c:
        await c.call_tool(
            "update_version_localization", {"localization_id": "L1", "description": "new"}
        )
    assert old in answerer.forms[0]


async def test_form_escapes_bidi_controls_and_cannot_fake_extra_lines(tmp_path: Path) -> None:
    answerer = FormAnswerer()
    evil = "ok\n\nApproved by Apple: nothing to worry\u202e txet"
    async with connected(tmp_path, loc_fake(), allow_writes=True, elicit=answerer) as c:
        await c.call_tool(
            "update_version_localization", {"localization_id": "L1", "description": evil}
        )
    form = answerer.forms[0]
    assert "\u202e" not in form
    assert "\\u202e" in form
    lines = form.split("\n")
    assert not any(line.startswith("Approved by Apple") for line in lines)
    assert "ok\u23ce\u23ceApproved by Apple" in form
    assert len(lines) == 4  # title, blank line, language line, one change line


FIELD_LIMITS = [
    ("update_version_localization", "L1", "description", 4000),
    ("update_version_localization", "L1", "keywords", 100),
    ("update_version_localization", "L1", "whats_new", 4000),
    ("update_version_localization", "L1", "promotional_text", 170),
    ("update_version_localization", "L1", "marketing_url", 2048),
    ("update_version_localization", "L1", "support_url", 2048),
    ("update_app_info_localization", "I1", "name", 30),
    ("update_app_info_localization", "I1", "subtitle", 30),
    ("update_app_info_localization", "I1", "privacy_policy_url", 2048),
]


@pytest.mark.parametrize(("tool", "loc_id", "field", "limit"), FIELD_LIMITS)
async def test_values_over_apples_limit_are_refused_before_any_request_or_form(
    tmp_path: Path, tool: str, loc_id: str, field: str, limit: int
) -> None:
    fake = loc_fake()
    answerer = FormAnswerer()
    async with connected(tmp_path, fake, allow_writes=True, elicit=answerer) as c:
        too_long = await c.call_tool(tool, {"localization_id": loc_id, field: "x" * (limit + 1)})
        exact = await c.call_tool(tool, {"localization_id": loc_id, field: "x" * limit})
    assert too_long.is_error
    assert str(limit) in text_of(too_long)
    assert not exact.is_error, text_of(exact)
    assert len(answerer.forms) == 1  # only the value at the limit got a form


async def test_review_reply_over_limit_is_refused_before_any_request(tmp_path: Path) -> None:
    fake = FakeApple()
    answerer = FormAnswerer()
    async with connected(tmp_path, fake, allow_writes=True, elicit=answerer) as c:
        result = await c.call_tool(
            "reply_to_customer_review", {"review_id": "r1", "response_body": "x" * 5971}
        )
    assert result.is_error
    assert fake.seen == [] and answerer.forms == []


async def test_review_form_escapes_nickname_title_and_reply(tmp_path: Path) -> None:
    """Attack prevented: a customer review with hidden characters could change what the form says."""
    fake = FakeApple()
    answerer = FormAnswerer()
    review = resource(
        "customerReviews",
        "r1",
        rating=1,
        title="T\nFake line\u202e",
        reviewerNickname="bob\u2066\n",
    )
    fake.on("GET", "/v1/customerReviews/r1", one(review))
    fake.on("POST", "/v1/customerReviewResponses", one({"type": "x", "id": "c1"}), 201)
    async with connected(tmp_path, fake, allow_writes=True, elicit=answerer) as c:
        await c.call_tool(
            "reply_to_customer_review", {"review_id": "r1", "response_body": "hi\nthere\u202d"}
        )
    form = answerer.forms[0]
    assert not any(ch in form for ch in "\u202e\u2066\u202d")
    assert "T\u23ceFake line\\u202e" in form
    assert "bob\\u2066\u23ce" in form
    assert "hi\u23cethere\\u202d" in form


async def test_other_forms_escape_group_and_app_names(tmp_path: Path) -> None:
    fake = FakeApple()
    answerer = FormAnswerer()
    fake.on("GET", "/v1/betaGroups/g1", one(resource("betaGroups", "g1", name="G\nEVIL\u202e")))
    fake.on("POST", "/v1/betaTesters", one(resource("betaTesters", "t1", email="a@b.co")), 201)
    async with connected(tmp_path, fake, allow_writes=True, elicit=answerer) as c:
        await c.call_tool(
            "add_beta_tester",
            {"beta_group_id": "g1", "email": "a@b.co", "first_name": "A\nB", "last_name": "\u202e"},
        )
    assert "\u202e" not in answerer.forms[0]
    assert "G\u23ceEVIL\\u202e" in answerer.forms[0]
    assert "A\u23ceB" in answerer.forms[0]


def test_keywords_are_limited_in_bytes_not_characters() -> None:

    check_limit("keywords", "a" * 100)  # 100 bytes: allowed
    with pytest.raises(ToolInputError, match="bytes"):
        check_limit("keywords", "ö" * 51)  # 51 characters but 102 bytes


def test_app_name_needs_at_least_two_characters() -> None:

    check_limit("name", "Ab")
    with pytest.raises(ToolInputError, match="at least 2"):
        check_limit("name", "A")
