"""Every write tool: exact request on accept, nothing sent otherwise, not registered when off.

Each write tool has a small fake Apple (`<tool>_fake`) and one test that shows the exact requests
it sends. A few generic tests then run every tool through the cases where nothing may be sent.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app_store_connect.tools import read_tools, write_tools

from .apple_fake import FakeApple, FormAction, FormAnswerer, connected, one, resource, run, text_of

Request = tuple[str, str, Any]  # (method, path, JSON body)


def sent_requests(fake: FakeApple) -> list[Request]:
    """What the tool sent to Apple, apart from reads."""
    return [(write.method, write.path, write.body) for write in fake.writes()]


# ------------------------------------------------------------------ one small fake per tool

UPDATE_VERSION_TEXT = {"localization_id": "l1", "description": "new text"}


def update_version_localization_fake() -> FakeApple:
    fake = FakeApple()
    path = "/v1/appStoreVersionLocalizations/l1"
    kind = "appStoreVersionLocalizations"
    fake.on("GET", path, one(resource(kind, "l1", locale="en-US", description="old text")))
    fake.on("PATCH", path, one(resource(kind, "l1", description="new text")))
    return fake


UPDATE_APP_INFO = {"localization_id": "i1", "name": "New Name"}


def update_app_info_localization_fake() -> FakeApple:
    fake = FakeApple()
    kind = "appInfoLocalizations"
    fake.on("GET", "/v1/appInfoLocalizations/i1", one(resource(kind, "i1", name="Old Name")))
    fake.on("PATCH", "/v1/appInfoLocalizations/i1", one(resource(kind, "i1", name="New Name")))
    return fake


CREATE_VERSION = {"app_id": "1", "version_string": "2.0", "release_type": "MANUAL"}


def create_app_store_version_fake() -> FakeApple:
    fake = FakeApple()
    fake.on("GET", "/v1/apps/1", one(resource("apps", "1", name="Fake App")))
    created = one(resource("appStoreVersions", "v2", versionString="2.0"))
    fake.on("POST", "/v1/appStoreVersions", created, 201)
    return fake


SELECT_BUILD = {"version_id": "v1", "build_id": "b1"}


def select_build_for_version_fake() -> FakeApple:
    fake = FakeApple()
    fake.on("GET", "/v1/appStoreVersions/v1/relationships/build", {"data": None})
    fake.on("GET", "/v1/builds/b1", one(resource("builds", "b1", version="7")))
    fake.on("PATCH", "/v1/appStoreVersions/v1/relationships/build", {}, 204)
    return fake


ADD_TESTER = {"beta_group_id": "g1", "email": "a@b.co", "first_name": "Ann"}


def add_beta_tester_fake() -> FakeApple:
    fake = FakeApple()
    fake.on("GET", "/v1/betaGroups/g1", one(resource("betaGroups", "g1", name="Friends")))
    fake.on("POST", "/v1/betaTesters", one(resource("betaTesters", "t1", email="a@b.co")), 201)
    return fake


REMOVE_TESTER = {"beta_group_id": "g1", "beta_tester_id": "t1"}


def remove_beta_tester_fake() -> FakeApple:
    fake = FakeApple()
    fake.on("GET", "/v1/betaGroups/g1", one(resource("betaGroups", "g1", name="Friends")))
    fake.on("GET", "/v1/betaTesters/t1", one(resource("betaTesters", "t1", email="a@b.co")))
    fake.on("DELETE", "/v1/betaGroups/g1/relationships/betaTesters", {}, 204)
    return fake


ADD_BUILD_TO_GROUP = {"beta_group_id": "g1", "build_id": "b1"}


def add_build_to_beta_group_fake() -> FakeApple:
    fake = FakeApple()
    fake.on("GET", "/v1/betaGroups/g1", one(resource("betaGroups", "g1", name="Friends")))
    fake.on("GET", "/v1/builds/b1", one(resource("builds", "b1", version="7")))
    fake.on("POST", "/v1/betaGroups/g1/relationships/builds", {}, 204)
    return fake


BETA_REVIEW = {"build_id": "b1"}


def submit_build_for_beta_review_fake() -> FakeApple:
    fake = FakeApple()
    fake.on("GET", "/v1/builds/b1", one(resource("builds", "b1", version="7")))
    submission = one({"type": "betaAppReviewSubmissions", "id": "bs1"})
    fake.on("POST", "/v1/betaAppReviewSubmissions", submission, 201)
    return fake


APP_REVIEW = {"app_id": "1", "version_id": "v1"}


def submit_for_app_review_fake() -> FakeApple:
    fake = FakeApple()
    fake.on("GET", "/v1/apps/1", one(resource("apps", "1", name="Fake App")))
    versions = {"data": [resource("appStoreVersions", "v1", versionString="1.0")]}
    fake.on("GET", "/v1/apps/1/appStoreVersions", versions)
    version = resource(
        "appStoreVersions", "v1", versionString="1.0", appVersionState="PREPARE_FOR_SUBMISSION"
    )
    fake.on("GET", "/v1/appStoreVersions/v1", one(version))
    fake.on("GET", "/v1/apps/1/reviewSubmissions", {"data": []})  # no open draft
    fake.on("POST", "/v1/reviewSubmissions", one({"type": "reviewSubmissions", "id": "rs1"}), 201)
    item = one({"type": "reviewSubmissionItems", "id": "ri1"})
    fake.on("POST", "/v1/reviewSubmissionItems", item, 201)
    waiting = one(resource("reviewSubmissions", "rs1", state="WAITING_FOR_REVIEW"))
    fake.on("PATCH", "/v1/reviewSubmissions/rs1", waiting)
    return fake


REPLY = {"review_id": "r1", "response_body": "Thanks!"}


def reply_to_customer_review_fake() -> FakeApple:
    fake = FakeApple()
    review = resource("customerReviews", "r1", rating=2, title="Meh", reviewerNickname="bob")
    fake.on("GET", "/v1/customerReviews/r1", one(review))
    response = one({"type": "customerReviewResponses", "id": "cr1"})
    fake.on("POST", "/v1/customerReviewResponses", response, 201)
    return fake


# ------------------------------------------------------------------ the exact requests, per tool


async def confirm_and_run(
    tmp_path: Path, fake: FakeApple, tool: str, args: dict[str, Any]
) -> FormAnswerer:
    """Accept the form, check the call worked, and return the answerer (one form expected)."""
    answerer = FormAnswerer()
    result = await run(tmp_path, fake, tool, args, answerer)
    assert not result.is_error, text_of(result)
    assert len(answerer.forms) == 1  # exactly one confirmation per tool call
    return answerer


async def test_update_version_localization_patches_only_the_changed_text(tmp_path: Path) -> None:
    fake = update_version_localization_fake()
    await confirm_and_run(tmp_path, fake, "update_version_localization", UPDATE_VERSION_TEXT)
    assert sent_requests(fake) == [
        (
            "PATCH",
            "/v1/appStoreVersionLocalizations/l1",
            {
                "data": {
                    "type": "appStoreVersionLocalizations",
                    "id": "l1",
                    "attributes": {"description": "new text"},
                }
            },
        )
    ]


async def test_update_app_info_localization_patches_only_the_changed_name(tmp_path: Path) -> None:
    fake = update_app_info_localization_fake()
    await confirm_and_run(tmp_path, fake, "update_app_info_localization", UPDATE_APP_INFO)
    expected_body = {
        "data": {"type": "appInfoLocalizations", "id": "i1", "attributes": {"name": "New Name"}}
    }
    assert sent_requests(fake) == [("PATCH", "/v1/appInfoLocalizations/i1", expected_body)]


async def test_create_app_store_version_posts_a_draft_for_the_app(tmp_path: Path) -> None:
    fake = create_app_store_version_fake()
    await confirm_and_run(tmp_path, fake, "create_app_store_version", CREATE_VERSION)
    expected_body = {
        "data": {
            "type": "appStoreVersions",
            "attributes": {"versionString": "2.0", "platform": "IOS", "releaseType": "MANUAL"},
            "relationships": {"app": {"data": {"type": "apps", "id": "1"}}},
        }
    }
    assert sent_requests(fake) == [("POST", "/v1/appStoreVersions", expected_body)]


async def test_select_build_for_version_patches_the_build_relationship(tmp_path: Path) -> None:
    fake = select_build_for_version_fake()
    await confirm_and_run(tmp_path, fake, "select_build_for_version", SELECT_BUILD)
    expected_body = {"data": {"type": "builds", "id": "b1"}}
    assert sent_requests(fake) == [
        ("PATCH", "/v1/appStoreVersions/v1/relationships/build", expected_body)
    ]


async def test_add_beta_tester_posts_the_tester_into_the_group(tmp_path: Path) -> None:
    fake = add_beta_tester_fake()
    await confirm_and_run(tmp_path, fake, "add_beta_tester", ADD_TESTER)
    expected_body = {
        "data": {
            "type": "betaTesters",
            "attributes": {"email": "a@b.co", "firstName": "Ann"},
            "relationships": {"betaGroups": {"data": [{"type": "betaGroups", "id": "g1"}]}},
        }
    }
    assert sent_requests(fake) == [("POST", "/v1/betaTesters", expected_body)]


async def test_remove_beta_tester_deletes_the_membership_only(tmp_path: Path) -> None:
    fake = remove_beta_tester_fake()
    await confirm_and_run(tmp_path, fake, "remove_beta_tester", REMOVE_TESTER)
    expected_body = {"data": [{"type": "betaTesters", "id": "t1"}]}
    assert sent_requests(fake) == [
        ("DELETE", "/v1/betaGroups/g1/relationships/betaTesters", expected_body)
    ]


async def test_add_build_to_beta_group_posts_the_build_relationship(tmp_path: Path) -> None:
    fake = add_build_to_beta_group_fake()
    await confirm_and_run(tmp_path, fake, "add_build_to_beta_group", ADD_BUILD_TO_GROUP)
    expected_body = {"data": [{"type": "builds", "id": "b1"}]}
    assert sent_requests(fake) == [
        ("POST", "/v1/betaGroups/g1/relationships/builds", expected_body)
    ]


async def test_submit_build_for_beta_review_posts_one_submission(tmp_path: Path) -> None:
    fake = submit_build_for_beta_review_fake()
    await confirm_and_run(tmp_path, fake, "submit_build_for_beta_review", BETA_REVIEW)
    expected_body = {
        "data": {
            "type": "betaAppReviewSubmissions",
            "relationships": {"build": {"data": {"type": "builds", "id": "b1"}}},
        }
    }
    assert sent_requests(fake) == [("POST", "/v1/betaAppReviewSubmissions", expected_body)]


async def test_submit_for_app_review_creates_adds_and_submits(tmp_path: Path) -> None:
    fake = submit_for_app_review_fake()
    await confirm_and_run(tmp_path, fake, "submit_for_app_review", APP_REVIEW)
    create_body = {
        "data": {
            "type": "reviewSubmissions",
            "attributes": {"platform": "IOS"},
            "relationships": {"app": {"data": {"type": "apps", "id": "1"}}},
        }
    }
    add_body = {
        "data": {
            "type": "reviewSubmissionItems",
            "relationships": {
                "reviewSubmission": {"data": {"type": "reviewSubmissions", "id": "rs1"}},
                "appStoreVersion": {"data": {"type": "appStoreVersions", "id": "v1"}},
            },
        }
    }
    submit_body = {
        "data": {"type": "reviewSubmissions", "id": "rs1", "attributes": {"submitted": True}}
    }
    assert sent_requests(fake) == [
        ("POST", "/v1/reviewSubmissions", create_body),
        ("POST", "/v1/reviewSubmissionItems", add_body),
        ("PATCH", "/v1/reviewSubmissions/rs1", submit_body),
    ]


async def test_reply_to_customer_review_posts_the_public_reply(tmp_path: Path) -> None:
    fake = reply_to_customer_review_fake()
    await confirm_and_run(tmp_path, fake, "reply_to_customer_review", REPLY)
    expected_body = {
        "data": {
            "type": "customerReviewResponses",
            "attributes": {"responseBody": "Thanks!"},
            "relationships": {"review": {"data": {"type": "customerReviews", "id": "r1"}}},
        }
    }
    assert sent_requests(fake) == [("POST", "/v1/customerReviewResponses", expected_body)]


# ------------------------------------------------------------------ when nothing may be sent

# Every write tool: its name, the arguments to call it with, and how to build its fake Apple.
EVERY_WRITE_TOOL: list[tuple[str, dict[str, Any], Callable[[], FakeApple]]] = [
    ("update_version_localization", UPDATE_VERSION_TEXT, update_version_localization_fake),
    ("update_app_info_localization", UPDATE_APP_INFO, update_app_info_localization_fake),
    ("create_app_store_version", CREATE_VERSION, create_app_store_version_fake),
    ("select_build_for_version", SELECT_BUILD, select_build_for_version_fake),
    ("add_beta_tester", ADD_TESTER, add_beta_tester_fake),
    ("remove_beta_tester", REMOVE_TESTER, remove_beta_tester_fake),
    ("add_build_to_beta_group", ADD_BUILD_TO_GROUP, add_build_to_beta_group_fake),
    ("submit_build_for_beta_review", BETA_REVIEW, submit_build_for_beta_review_fake),
    ("submit_for_app_review", APP_REVIEW, submit_for_app_review_fake),
    ("reply_to_customer_review", REPLY, reply_to_customer_review_fake),
]
TOOL_IDS = [case[0] for case in EVERY_WRITE_TOOL]


@pytest.mark.parametrize("action", ["decline", "cancel"])
@pytest.mark.parametrize(("tool", "args", "make_fake"), EVERY_WRITE_TOOL, ids=TOOL_IDS)
async def test_anything_but_explicit_accept_sends_nothing(
    tmp_path: Path,
    tool: str,
    args: dict[str, Any],
    make_fake: Callable[[], FakeApple],
    action: FormAction,
) -> None:
    fake = make_fake()
    result = await run(tmp_path, fake, tool, args, FormAnswerer(action))
    assert result.is_error
    assert "Nothing was changed" in text_of(result)
    assert fake.writes() == []


@pytest.mark.parametrize(("tool", "args", "make_fake"), EVERY_WRITE_TOOL, ids=TOOL_IDS)
async def test_client_without_elicitation_sends_nothing(
    tmp_path: Path, tool: str, args: dict[str, Any], make_fake: Callable[[], FakeApple]
) -> None:
    fake = make_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=None) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error
    assert "cannot show confirmation forms" in text_of(result)
    assert fake.writes() == []


def test_every_write_tool_is_covered_by_this_file() -> None:
    covered = set(TOOL_IDS)
    # These are tested in their own files (localizations, availability, pricing, age rating,
    # screenshots), which have their own fakes.
    tested_elsewhere = {
        "create_version_localization",
        "create_app_info_localization",
        "update_app_primary_locale",
        "set_territory_availability",
        "set_free_price",
        "update_age_rating",
        "upload_screenshot",
    }
    assert {tool.name for tool in write_tools()} == covered | tested_elsewhere


# ------------------------------------------------------------------ the forms


async def test_form_shows_old_and_new_value(tmp_path: Path) -> None:
    answerer = await confirm_and_run(
        tmp_path,
        update_version_localization_fake(),
        "update_version_localization",
        UPDATE_VERSION_TEXT,
    )
    assert "description: old text  ->  new text" in answerer.forms[0]
    assert "en-US" in answerer.forms[0]


async def test_unchanged_value_needs_no_confirmation_and_sends_nothing(tmp_path: Path) -> None:
    fake, answerer = update_version_localization_fake(), FormAnswerer()
    args = {"localization_id": "l1", "description": "old text"}
    result = await run(tmp_path, fake, "update_version_localization", args, answerer)
    assert "Nothing to change" in text_of(result)
    assert answerer.forms == []
    assert fake.writes() == []


async def test_submitting_for_app_review_is_labelled_public_and_irreversible(
    tmp_path: Path,
) -> None:
    answerer = await confirm_and_run(
        tmp_path, submit_for_app_review_fake(), "submit_for_app_review", APP_REVIEW
    )
    assert "PUBLIC AND IRREVERSIBLE" in answerer.forms[0]


async def test_a_public_reply_is_labelled_public_and_irreversible(tmp_path: Path) -> None:
    answerer = await confirm_and_run(
        tmp_path, reply_to_customer_review_fake(), "reply_to_customer_review", REPLY
    )
    assert "PUBLIC AND IRREVERSIBLE" in answerer.forms[0]


# ------------------------------------------------------------------ App Review submission details


async def test_partial_submission_failure_is_explained(tmp_path: Path) -> None:
    fake = submit_for_app_review_fake()
    fake.routes.pop(("POST", "/v1/reviewSubmissionItems"))  # 404 on the second step
    result = await run(tmp_path, fake, "submit_for_app_review", APP_REVIEW, FormAnswerer())
    assert result.is_error
    assert "rs1 was created but NOT submitted" in text_of(result)


async def test_submit_refuses_a_version_that_belongs_to_another_app(tmp_path: Path) -> None:
    """The form would name app 2 while submitting a version of app 1."""
    fake, answerer = submit_for_app_review_fake(), FormAnswerer()
    fake.on("GET", "/v1/apps/2", one(resource("apps", "2", name="Other App")))
    fake.on("GET", "/v1/apps/2/appStoreVersions", {"data": []})
    args = {"app_id": "2", "version_id": "v1"}
    result = await run(tmp_path, fake, "submit_for_app_review", args, answerer)
    assert result.is_error
    assert "does not belong" in text_of(result)
    assert answerer.forms == []
    assert fake.writes() == []


async def test_submit_reuses_an_open_draft_submission(tmp_path: Path) -> None:
    """A failed earlier attempt leaves an empty draft; Apple allows only one, so reuse it."""
    fake = submit_for_app_review_fake()
    old_draft = resource("reviewSubmissions", "rs-old", state="READY_FOR_REVIEW", platform="IOS")
    fake.on("GET", "/v1/apps/1/reviewSubmissions", {"data": [old_draft]})
    waiting = one(resource("reviewSubmissions", "rs-old", state="WAITING_FOR_REVIEW"))
    fake.on("PATCH", "/v1/reviewSubmissions/rs-old", waiting)
    await confirm_and_run(tmp_path, fake, "submit_for_app_review", APP_REVIEW)
    writes = [(w.method, w.path) for w in fake.writes()]
    assert ("POST", "/v1/reviewSubmissions") not in writes
    assert writes == [
        ("POST", "/v1/reviewSubmissionItems"),
        ("PATCH", "/v1/reviewSubmissions/rs-old"),
    ]


async def test_bad_email_is_rejected_before_any_request(tmp_path: Path) -> None:
    fake = add_beta_tester_fake()
    args = {"beta_group_id": "g1", "email": "not-an-email"}
    result = await run(tmp_path, fake, "add_beta_tester", args, FormAnswerer())
    assert result.is_error
    assert fake.seen == []


# ------------------------------------------------------------------ registration


async def test_writes_are_not_registered_when_off(tmp_path: Path) -> None:
    fake = add_beta_tester_fake()
    async with connected(tmp_path, fake, allow_writes=False, elicit=FormAnswerer()) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        result = await client.call_tool("add_beta_tester", ADD_TESTER)
    assert names == {t.name for t in read_tools()} | {
        "maintenance_status",
        "maintenance_report",
        "maintenance_confirm_review",
    }
    assert result.is_error
    assert "ASC_ALLOW_WRITES=1" in text_of(result)
    assert fake.seen == []


async def test_writes_are_registered_when_on(tmp_path: Path) -> None:
    async with connected(tmp_path, FakeApple(), allow_writes=True) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert {t.name for t in write_tools()} <= names


def test_no_tool_has_a_confirm_argument_and_annotations_are_honest() -> None:
    for tool in [*read_tools(), *write_tools()]:
        mcp_tool = tool.to_tool()
        assert mcp_tool.annotations is not None
        assert not {"confirm", "approve", "yes", "force"} & set(tool.properties)
        assert mcp_tool.annotations.read_only_hint is (not tool.write)
    assert all(not t.write for t in read_tools())
    assert all(t.write for t in write_tools())


def test_destructive_hint_set_for_public_or_removing_tools() -> None:
    destructive = {t.name for t in write_tools() if t.destructive}
    assert {
        "submit_for_app_review",
        "reply_to_customer_review",
        "remove_beta_tester",
    } <= destructive
