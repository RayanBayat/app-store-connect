"""Age rating: get_age_rating, update_age_rating (with the all_others convenience)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app_store_connect.tools import read_tools, write_tools
from app_store_connect.tools.agerating import BOOLEAN_QUESTIONS, FREQUENCY_QUESTIONS

from .apple_fake import (
    PROTOCOLS,
    FakeApple,
    FormAction,
    FormAnswerer,
    connected,
    listing,
    one,
    resource,
    run,
    text_of,
)

DECLARATION_PATH = "/v1/ageRatingDeclarations/d1"


def age_fake(state: str = "PREPARE_FOR_SUBMISSION", **answers: Any) -> FakeApple:
    fake = FakeApple()
    fake.on(
        "GET",
        "/v1/apps/1/appInfos",
        listing(
            resource("appInfos", "i1", state=state),
            resource("appInfos", "i0", state="READY_FOR_DISTRIBUTION"),
        ),
    )
    fake.on("GET", "/v1/appInfos/i1", one(resource("appInfos", "i1", state=state)))
    fake.on(
        "GET", "/v1/appInfos/i0", one(resource("appInfos", "i0", state="READY_FOR_DISTRIBUTION"))
    )
    fake.on(
        "GET",
        "/v1/appInfos/i1/ageRatingDeclaration",
        one(resource("ageRatingDeclarations", "d1", **answers)),
    )
    fake.on(
        "GET",
        "/v1/appInfos/i0/ageRatingDeclaration",
        one(resource("ageRatingDeclarations", "d0", advertising=False, gambling=False)),
    )
    fake.on("PATCH", DECLARATION_PATH, one(resource("ageRatingDeclarations", "d1")))
    return fake


# ---------------------------------------------------------------- get_age_rating


async def test_read_shape_lists_unanswered_questions(tmp_path: Path) -> None:
    fake = age_fake(advertising=True, violenceRealistic="NONE", ageRatingOverrideV2="NONE")
    async with connected(tmp_path, fake) as client:
        result = await client.call_tool("get_age_rating", {"app_id": "1"})
    assert not result.is_error, text_of(result)
    infos = json.loads(text_of(result))
    assert [i["app_info_id"] for i in infos] == ["i1", "i0"]
    editable, live = infos
    assert editable["state"] == "PREPARE_FOR_SUBMISSION" and editable["editable"] is True
    assert live["state"] == "READY_FOR_DISTRIBUTION" and live["editable"] is False
    assert editable["declaration_id"] == "d1"
    answers = editable["answers"]
    assert answers["advertising"] == "Yes"
    assert answers["violenceRealistic"] == "None"
    assert answers["socialMedia"] == "not answered"
    assert answers["ageRatingOverrideV2"] == "NONE"
    assert "Social media" in editable["not_answered"]
    assert "Advertising" not in editable["not_answered"]
    assert "Gambling" not in live["not_answered"]
    assert len(editable["not_answered"]) == len(BOOLEAN_QUESTIONS) + len(FREQUENCY_QUESTIONS) - 2


async def test_read_makes_only_gets(tmp_path: Path) -> None:
    fake = age_fake()
    async with connected(tmp_path, fake) as client:
        await client.call_tool("get_age_rating", {"app_id": "1"})
    assert fake.writes() == []


# ---------------------------------------------------------------- registration


def test_tools_registered_in_the_right_lists() -> None:
    assert "get_age_rating" in {t.name for t in read_tools()}
    assert "update_age_rating" in {t.name for t in write_tools()}
    assert "update_age_rating" not in {t.name for t in read_tools()}


async def test_update_not_registered_when_writes_are_off(tmp_path: Path) -> None:
    async with connected(tmp_path, age_fake()) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        result = await client.call_tool("update_age_rating", {"app_info_id": "i1", "answers": {}})
    assert "get_age_rating" in names and "update_age_rating" not in names
    assert result.is_error


async def test_all_others_is_documented_in_the_schema() -> None:
    tool = next(t for t in write_tools() if t.name == "update_age_rating")
    assert "all_others" in tool.properties["answers"]["description"]


# ---------------------------------------------------------------- update: happy path


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_accept_sends_exactly_one_patch(tmp_path: Path, protocol: str) -> None:
    fake, answer = age_fake(gambling=False), FormAnswerer()
    args = {
        "app_info_id": "i1",
        "answers": {"advertising": True, "gambling": False, "violenceRealistic": "NONE"},
    }
    result = await run(tmp_path, fake, "update_age_rating", args, answer, protocol)
    assert not result.is_error, text_of(result)
    assert [(w.method, w.path, w.body) for w in fake.writes()] == [
        (
            "PATCH",
            DECLARATION_PATH,
            {
                "data": {
                    "type": "ageRatingDeclarations",
                    "id": "d1",
                    "attributes": {"advertising": True, "violenceRealistic": "NONE"},
                }
            },
        )
    ]
    assert len(answer.forms) == 1
    form = answer.forms[0]
    assert "Advertising: not answered  ->  Yes" in form
    assert "Realistic violence: not answered  ->  None" in form
    assert "Gambling" not in form  # unchanged
    assert "legal declaration" in form and "age rating" in form


@pytest.mark.parametrize("action", ["decline", "cancel"])
@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_decline_or_cancel_sends_nothing(
    tmp_path: Path, action: FormAction, protocol: str
) -> None:
    fake = age_fake()
    args = {"app_info_id": "i1", "answers": {"socialMedia": False}}
    result = await run(tmp_path, fake, "update_age_rating", args, FormAnswerer(action), protocol)
    assert result.is_error and "Nothing was changed" in text_of(result)
    assert fake.writes() == []


async def test_nothing_to_change_asks_nothing(tmp_path: Path) -> None:
    fake, answer = age_fake(socialMedia=False), FormAnswerer()
    args = {"app_info_id": "i1", "answers": {"socialMedia": False}}
    result = await run(tmp_path, fake, "update_age_rating", args, answer)
    assert not result.is_error and "Nothing to change" in text_of(result)
    assert answer.forms == [] and fake.writes() == []


# ---------------------------------------------------------------- update: refusals


@pytest.mark.parametrize(
    "answers",
    [
        {"nonsense": True},
        {"advertising": "yes"},
        {"advertising": 1},
        {"advertising": None},
        {"violenceRealistic": "SOME"},
        {"violenceRealistic": True},
        {"ageRatingOverrideV2": "TWELVE_PLUS"},
        {"koreaAgeRatingOverride": "NINE_PLUS"},
        {"kidsAgeBand": "TEN"},
        {"ageRatingOverride": "NONE"},  # deprecated by Apple
        {"gracRatingClassificationNumber": "x"},  # not a question
        {"all_others": "yes"},
        {"all_others": True},
        {},
        [],
        "advertising",
    ],
    ids=str,
)
async def test_invalid_names_or_values_are_refused_before_any_form(
    tmp_path: Path, answers: Any
) -> None:
    fake, answer = age_fake(), FormAnswerer()
    result = await run(
        tmp_path, fake, "update_age_rating", {"app_info_id": "i1", "answers": answers}, answer
    )
    assert result.is_error
    assert answer.forms == [] and fake.writes() == []
    assert fake.seen == []  # nothing was even read


async def test_unknown_name_is_named_in_the_error(tmp_path: Path) -> None:
    args = {"app_info_id": "i1", "answers": {"nonsense": True}}
    result = await run(tmp_path, age_fake(), "update_age_rating", args, FormAnswerer())
    assert "nonsense" in text_of(result)


async def test_bad_app_info_id_is_refused(tmp_path: Path) -> None:
    args = {"app_info_id": "../x", "answers": {"advertising": True}}
    result = await run(tmp_path, age_fake(), "update_age_rating", args, FormAnswerer())
    assert result.is_error


async def test_non_editable_app_info_is_refused(tmp_path: Path) -> None:
    fake, answer = age_fake(), FormAnswerer()
    args = {"app_info_id": "i0", "answers": {"advertising": True}}
    result = await run(tmp_path, fake, "update_age_rating", args, answer)
    assert result.is_error and "READY_FOR_DISTRIBUTION" in text_of(result)
    assert answer.forms == [] and fake.writes() == []


@pytest.mark.parametrize("state", ["PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED"])
async def test_editable_states_are_accepted(tmp_path: Path, state: str) -> None:
    fake = age_fake(state=state)
    args = {"app_info_id": "i1", "answers": {"advertising": True}}
    result = await run(tmp_path, fake, "update_age_rating", args, FormAnswerer())
    assert not result.is_error, text_of(result)
    assert len(fake.writes()) == 1


@pytest.mark.parametrize(
    "state", ["IN_REVIEW", "WAITING_FOR_REVIEW", "PENDING_RELEASE", "ACCEPTED"]
)
async def test_other_states_are_refused(tmp_path: Path, state: str) -> None:
    fake = age_fake(state=state)
    args = {"app_info_id": "i1", "answers": {"advertising": True}}
    result = await run(tmp_path, fake, "update_age_rating", args, FormAnswerer())
    assert result.is_error and fake.writes() == []


# ---------------------------------------------------------------- all_others


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_all_others_none_fills_the_rest_and_lists_every_change(
    tmp_path: Path, protocol: str
) -> None:
    fake = age_fake(
        gambling=True, violenceRealistic="FREQUENT", ageRatingOverrideV2="NINE_PLUS", lootBox=False
    )
    answer = FormAnswerer()
    args = {
        "app_info_id": "i1",
        "answers": {"all_others": "none", "userGeneratedContent": True},
    }
    result = await run(tmp_path, fake, "update_age_rating", args, answer, protocol)
    assert not result.is_error, text_of(result)
    (patch,) = fake.writes()
    sent = patch.body["data"]["attributes"]
    expected: dict[str, Any] = dict.fromkeys(BOOLEAN_QUESTIONS, False)
    expected.update(dict.fromkeys(FREQUENCY_QUESTIONS, "NONE"))
    expected["userGeneratedContent"] = True
    del expected["lootBox"]  # already false: unchanged
    assert sent == expected
    assert "ageRatingOverrideV2" not in sent and "kidsAgeBand" not in sent
    (form,) = answer.forms
    assert form.count("  ->  ") == len(expected)
    assert "Gambling: Yes  ->  No" in form
    assert "Realistic violence: Frequent  ->  None" in form
    assert "User-generated content: not answered  ->  Yes" in form
    assert "legal declaration" in form
