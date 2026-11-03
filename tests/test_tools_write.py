"""Every write tool: exact request on accept, nothing sent otherwise, not registered when off."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mcp_types as types
import pytest
from mcp.client.session import ClientRequestContext

from app_store_connect.tools import read_tools, write_tools

from .apple_fake import FakeApple, connected, one, resource, text_of

Expected = list[tuple[str, str, Any]]


def build_fake() -> FakeApple:
    fake = FakeApple()
    loc = resource("appStoreVersionLocalizations", "l1", locale="en-US", description="old text")
    fake.on("GET", "/v1/appStoreVersionLocalizations/l1", one(loc))
    fake.on(
        "PATCH",
        "/v1/appStoreVersionLocalizations/l1",
        one(resource("appStoreVersionLocalizations", "l1", description="new text")),
    )
    info = resource("appInfoLocalizations", "i1", locale="en-US", name="Old Name", subtitle="s")
    fake.on("GET", "/v1/appInfoLocalizations/i1", one(info))
    fake.on(
        "PATCH",
        "/v1/appInfoLocalizations/i1",
        one(resource("appInfoLocalizations", "i1", name="New Name")),
    )
    fake.on("GET", "/v1/apps/1", one(resource("apps", "1", name="Fake App")))
    fake.on(
        "POST",
        "/v1/appStoreVersions",
        one(resource("appStoreVersions", "v2", versionString="2.0")),
        201,
    )
    fake.on("GET", "/v1/appStoreVersions/v1/relationships/build", {"data": None})
    fake.on("GET", "/v1/builds/b1", one(resource("builds", "b1", version="7")))
    fake.on("PATCH", "/v1/appStoreVersions/v1/relationships/build", {}, 204)
    fake.on("GET", "/v1/betaGroups/g1", one(resource("betaGroups", "g1", name="Friends")))
    fake.on("POST", "/v1/betaTesters", one(resource("betaTesters", "t1", email="a@b.co")), 201)
    fake.on("GET", "/v1/betaTesters/t1", one(resource("betaTesters", "t1", email="a@b.co")))
    fake.on("DELETE", "/v1/betaGroups/g1/relationships/betaTesters", {}, 204)
    fake.on("POST", "/v1/betaGroups/g1/relationships/builds", {}, 204)
    fake.on(
        "POST",
        "/v1/betaAppReviewSubmissions",
        one({"type": "betaAppReviewSubmissions", "id": "bs1"}),
        201,
    )
    fake.on(
        "GET",
        "/v1/apps/1/appStoreVersions",
        {"data": [resource("appStoreVersions", "v1", versionString="1.0")]},
    )
    fake.on(
        "GET",
        "/v1/appStoreVersions/v1",
        one(
            resource(
                "appStoreVersions",
                "v1",
                versionString="1.0",
                appVersionState="PREPARE_FOR_SUBMISSION",
                releaseType="MANUAL",
            )
        ),
    )
    fake.on("POST", "/v1/reviewSubmissions", one({"type": "reviewSubmissions", "id": "rs1"}), 201)
    fake.on(
        "POST",
        "/v1/reviewSubmissionItems",
        one({"type": "reviewSubmissionItems", "id": "ri1"}),
        201,
    )
    fake.on(
        "PATCH",
        "/v1/reviewSubmissions/rs1",
        one(resource("reviewSubmissions", "rs1", state="WAITING_FOR_REVIEW")),
    )
    fake.on(
        "GET",
        "/v1/customerReviews/r1",
        one(resource("customerReviews", "r1", rating=2, title="Meh", reviewerNickname="bob")),
    )
    fake.on(
        "POST",
        "/v1/customerReviewResponses",
        one({"type": "customerReviewResponses", "id": "cr1"}),
        201,
    )
    return fake


CASES: list[tuple[str, dict[str, Any], Expected]] = [
    ("update_version_localization", {"localization_id": "l1", "description": "new text"},
     [("PATCH", "/v1/appStoreVersionLocalizations/l1",
       {"data": {"type": "appStoreVersionLocalizations", "id": "l1", "attributes": {"description": "new text"}}})]),
    ("update_app_info_localization", {"localization_id": "i1", "name": "New Name"},
     [("PATCH", "/v1/appInfoLocalizations/i1",
       {"data": {"type": "appInfoLocalizations", "id": "i1", "attributes": {"name": "New Name"}}})]),
    ("create_app_store_version", {"app_id": "1", "version_string": "2.0", "release_type": "MANUAL"},
     [("POST", "/v1/appStoreVersions",
       {"data": {"type": "appStoreVersions",
                 "attributes": {"versionString": "2.0", "platform": "IOS", "releaseType": "MANUAL"},
                 "relationships": {"app": {"data": {"type": "apps", "id": "1"}}}}})]),
    ("select_build_for_version", {"version_id": "v1", "build_id": "b1"},
     [("PATCH", "/v1/appStoreVersions/v1/relationships/build", {"data": {"type": "builds", "id": "b1"}})]),
    ("add_beta_tester", {"beta_group_id": "g1", "email": "a@b.co", "first_name": "Ann"},
     [("POST", "/v1/betaTesters",
       {"data": {"type": "betaTesters", "attributes": {"email": "a@b.co", "firstName": "Ann"},
                 "relationships": {"betaGroups": {"data": [{"type": "betaGroups", "id": "g1"}]}}}})]),
    ("remove_beta_tester", {"beta_group_id": "g1", "beta_tester_id": "t1"},
     [("DELETE", "/v1/betaGroups/g1/relationships/betaTesters", {"data": [{"type": "betaTesters", "id": "t1"}]})]),
    ("add_build_to_beta_group", {"beta_group_id": "g1", "build_id": "b1"},
     [("POST", "/v1/betaGroups/g1/relationships/builds", {"data": [{"type": "builds", "id": "b1"}]})]),
    ("submit_build_for_beta_review", {"build_id": "b1"},
     [("POST", "/v1/betaAppReviewSubmissions",
       {"data": {"type": "betaAppReviewSubmissions", "relationships": {"build": {"data": {"type": "builds", "id": "b1"}}}}})]),
    ("submit_for_app_review", {"app_id": "1", "version_id": "v1"},
     [("POST", "/v1/reviewSubmissions",
       {"data": {"type": "reviewSubmissions", "attributes": {"platform": "IOS"},
                 "relationships": {"app": {"data": {"type": "apps", "id": "1"}}}}}),
      ("POST", "/v1/reviewSubmissionItems",
       {"data": {"type": "reviewSubmissionItems",
                 "relationships": {"reviewSubmission": {"data": {"type": "reviewSubmissions", "id": "rs1"}},
                                   "appStoreVersion": {"data": {"type": "appStoreVersions", "id": "v1"}}}}}),
      ("PATCH", "/v1/reviewSubmissions/rs1",
       {"data": {"type": "reviewSubmissions", "id": "rs1", "attributes": {"submitted": True}}})]),
    ("reply_to_customer_review", {"review_id": "r1", "response_body": "Thanks!"},
     [("POST", "/v1/customerReviewResponses",
       {"data": {"type": "customerReviewResponses", "attributes": {"responseBody": "Thanks!"},
                 "relationships": {"review": {"data": {"type": "customerReviews", "id": "r1"}}}}})]),
]  # fmt: skip


class Answerer:
    """An elicitation callback that records forms and answers with a fixed result."""

    def __init__(self, result: types.ElicitResult) -> None:
        self.result = result
        self.messages: list[str] = []

    async def __call__(
        self, context: ClientRequestContext, params: types.ElicitRequestParams
    ) -> types.ElicitResult:
        self.messages.append(params.message)
        return self.result


def accept() -> Answerer:
    return Answerer(types.ElicitResult(action="accept", content={"approve": True}))


@pytest.mark.parametrize(("tool", "args", "expected"), CASES, ids=[c[0] for c in CASES])
async def test_accept_sends_exactly_the_expected_requests(
    tmp_path: Path, tool: str, args: dict[str, Any], expected: Expected
) -> None:
    fake, answer = build_fake(), accept()
    async with connected(tmp_path, fake, allow_writes=True, elicit=answer) as client:
        result = await client.call_tool(tool, args)
    assert not result.is_error, text_of(result)
    assert [(r.method, r.path, r.body) for r in fake.writes()] == expected
    assert len(answer.messages) == 1  # exactly one confirmation per tool call


@pytest.mark.parametrize(
    "answer",
    [
        types.ElicitResult(action="decline"),
        types.ElicitResult(action="cancel"),
    ],
    ids=["decline", "cancel"],
)
@pytest.mark.parametrize(("tool", "args", "expected"), CASES, ids=[c[0] for c in CASES])
async def test_anything_but_explicit_accept_sends_nothing(
    tmp_path: Path, tool: str, args: dict[str, Any], expected: Expected, answer: types.ElicitResult
) -> None:
    fake = build_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answerer(answer)) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error
    assert "Nothing was changed" in text_of(result)
    assert fake.writes() == []


@pytest.mark.parametrize(("tool", "args", "expected"), CASES, ids=[c[0] for c in CASES])
async def test_client_without_elicitation_sends_nothing(
    tmp_path: Path, tool: str, args: dict[str, Any], expected: Expected
) -> None:
    fake = build_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=None) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error
    assert "cannot show confirmation forms" in text_of(result)
    assert fake.writes() == []


async def test_form_shows_old_and_new_value(tmp_path: Path) -> None:
    answer = accept()
    async with connected(tmp_path, build_fake(), allow_writes=True, elicit=answer) as client:
        await client.call_tool(
            "update_version_localization", {"localization_id": "l1", "description": "new text"}
        )
    assert "description: old text  ->  new text" in answer.messages[0]
    assert "en-US" in answer.messages[0]


async def test_unchanged_value_needs_no_confirmation_and_sends_nothing(tmp_path: Path) -> None:
    fake, answer = build_fake(), accept()
    async with connected(tmp_path, fake, allow_writes=True, elicit=answer) as client:
        result = await client.call_tool(
            "update_version_localization", {"localization_id": "l1", "description": "old text"}
        )
    assert "Nothing to change" in text_of(result)
    assert answer.messages == []
    assert fake.writes() == []


@pytest.mark.parametrize("tool", ["submit_for_app_review", "reply_to_customer_review"])
async def test_public_actions_are_labelled_public_and_irreversible(
    tmp_path: Path, tool: str
) -> None:
    case = next(c for c in CASES if c[0] == tool)
    answer = accept()
    async with connected(tmp_path, build_fake(), allow_writes=True, elicit=answer) as client:
        await client.call_tool(tool, case[1])
    assert "PUBLIC AND IRREVERSIBLE" in answer.messages[0]


async def test_partial_submission_failure_is_explained(tmp_path: Path) -> None:
    fake = build_fake()
    fake.routes.pop(("POST", "/v1/reviewSubmissionItems"))  # 404 on the second step
    async with connected(tmp_path, fake, allow_writes=True, elicit=accept()) as client:
        result = await client.call_tool(
            "submit_for_app_review", {"app_id": "1", "version_id": "v1"}
        )
    assert result.is_error
    assert "rs1 was created but NOT submitted" in text_of(result)


async def test_submit_refuses_a_version_that_belongs_to_another_app(tmp_path: Path) -> None:
    """The form named app 1 while submitting a version of some other app."""
    fake, answer = build_fake(), accept()
    fake.on("GET", "/v1/apps/2", one(resource("apps", "2", name="Other App")))
    fake.on("GET", "/v1/apps/2/appStoreVersions", {"data": []})
    async with connected(tmp_path, fake, allow_writes=True, elicit=answer) as client:
        result = await client.call_tool(
            "submit_for_app_review", {"app_id": "2", "version_id": "v1"}
        )
    assert result.is_error
    assert "does not belong" in text_of(result)
    assert answer.messages == []
    assert fake.writes() == []


async def test_writes_are_not_registered_when_off(tmp_path: Path) -> None:
    fake = build_fake()
    async with connected(tmp_path, fake, allow_writes=False, elicit=accept()) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        result = await client.call_tool(
            "add_beta_tester", {"beta_group_id": "g1", "email": "a@b.co"}
        )
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


async def test_bad_email_is_rejected_before_any_request(tmp_path: Path) -> None:
    fake = build_fake()
    async with connected(tmp_path, fake, allow_writes=True, elicit=accept()) as client:
        result = await client.call_tool(
            "add_beta_tester", {"beta_group_id": "g1", "email": "not-an-email"}
        )
    assert result.is_error
    assert fake.seen == []
