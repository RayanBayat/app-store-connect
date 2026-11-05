"""create_version_localization, create_app_info_localization, update_app_primary_locale."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mcp_types as types
import pytest
from mcp.client.session import ClientRequestContext

from app_store_connect.spec import ENDPOINTS_USED
from app_store_connect.tools.locales import APP_STORE_LOCALES

from .apple_fake import FakeApple, connected, one, resource, text_of

ERAS = ["legacy", "2026-07-28"]


class Answers:
    def __init__(self, action: str = "accept") -> None:
        self.action = action
        self.forms: list[str] = []

    async def __call__(
        self, context: ClientRequestContext, params: types.ElicitRequestParams
    ) -> types.ElicitResult:
        self.forms.append(params.message)
        if self.action != "accept":
            return types.ElicitResult(action=self.action)  # type: ignore[arg-type]
        return types.ElicitResult(action="accept", content={"approve": True})


def apple() -> FakeApple:
    fake = FakeApple()
    created = one(resource("appStoreVersionLocalizations", "new1", locale="sv"))
    fake.on("POST", "/v1/appStoreVersionLocalizations", created, 201)
    info = one(resource("appInfoLocalizations", "new2", locale="sv", name="Namn"))
    fake.on("POST", "/v1/appInfoLocalizations", info, 201)
    fake.on("GET", "/v1/apps/7", one(resource("apps", "7", name="My App", primaryLocale="en-US")))
    fake.on("PATCH", "/v1/apps/7", one(resource("apps", "7", primaryLocale="sv")))
    fake.on("GET", "/v1/apps/7/appInfos", {"data": [resource("appInfos", "i1")]})
    loc = resource("appInfoLocalizations", "x", locale="sv", name="Namn")
    fake.on("GET", "/v1/appInfos/i1/appInfoLocalizations", {"data": [loc]})
    return fake


VERSION_ARGS: dict[str, Any] = {
    "version_id": "v9",
    "locale": "sv",
    "description": "Beskrivning åäö",
    "keywords": "a,b",
    "whats_new": "Nytt",
    "promotional_text": "Kampanj",
    "marketing_url": "https://example.com/m",
    "support_url": "https://example.com/s",
}
VERSION_BODY = {
    "data": {
        "type": "appStoreVersionLocalizations",
        "attributes": {
            "locale": "sv",
            "description": "Beskrivning åäö",
            "keywords": "a,b",
            "whatsNew": "Nytt",
            "promotionalText": "Kampanj",
            "marketingUrl": "https://example.com/m",
            "supportUrl": "https://example.com/s",
        },
        "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": "v9"}}},
    }
}
INFO_ARGS: dict[str, Any] = {
    "app_info_id": "i1",
    "locale": "sv",
    "name": "Namn",
    "subtitle": "Under",
    "privacy_policy_url": "https://example.com/p",
}
INFO_BODY = {
    "data": {
        "type": "appInfoLocalizations",
        "attributes": {
            "locale": "sv",
            "name": "Namn",
            "subtitle": "Under",
            "privacyPolicyUrl": "https://example.com/p",
        },
        "relationships": {"appInfo": {"data": {"type": "appInfos", "id": "i1"}}},
    }
}
PRIMARY_BODY = {"data": {"type": "apps", "id": "7", "attributes": {"primaryLocale": "sv"}}}

CASES = [
    ("create_version_localization", VERSION_ARGS, "POST", "/v1/appStoreVersionLocalizations", VERSION_BODY),
    ("create_app_info_localization", INFO_ARGS, "POST", "/v1/appInfoLocalizations", INFO_BODY),
    ("update_app_primary_locale", {"app_id": "7", "locale": "sv"}, "PATCH", "/v1/apps/7", PRIMARY_BODY),
]  # fmt: skip
IDS = [c[0] for c in CASES]


def test_new_endpoints_are_listed() -> None:
    assert {
        ("POST", "/v1/appStoreVersionLocalizations"),
        ("POST", "/v1/appInfoLocalizations"),
        ("PATCH", "/v1/apps/{id}"),
    } <= ENDPOINTS_USED


@pytest.mark.parametrize("mode", ERAS)
@pytest.mark.parametrize(("tool", "args", "method", "path", "body"), CASES, ids=IDS)
async def test_accept_sends_exactly_one_correct_request(
    tmp_path: Path, mode: str, tool: str, args: dict[str, Any], method: str, path: str, body: Any
) -> None:
    fake, answers = apple(), Answers()
    async with connected(tmp_path, fake, allow_writes=True, elicit=answers, mode=mode) as c:
        result = await c.call_tool(tool, args)
    assert not result.is_error, text_of(result)
    assert [(w.method, w.path, w.body) for w in fake.writes()] == [(method, path, body)]
    assert len(answers.forms) == 1


@pytest.mark.parametrize("mode", ERAS)
@pytest.mark.parametrize("action", ["decline", "cancel"])
@pytest.mark.parametrize(("tool", "args", "method", "path", "body"), CASES, ids=IDS)
async def test_decline_and_cancel_send_nothing(
    tmp_path: Path, mode: str, action: str, tool: str, args: dict[str, Any],
    method: str, path: str, body: Any,
) -> None:  # fmt: skip
    fake = apple()
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answers(action), mode=mode) as c:
        result = await c.call_tool(tool, args)
    assert result.is_error and fake.writes() == []


async def test_tools_are_not_registered_when_writes_are_off(tmp_path: Path) -> None:
    async with connected(tmp_path, apple(), allow_writes=False) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        result = await c.call_tool("update_app_primary_locale", {"app_id": "7", "locale": "sv"})
    assert not set(IDS) & names
    assert result.is_error and "ASC_ALLOW_WRITES" in text_of(result)


async def test_version_form_shows_every_field_in_full_escaped_with_counts(tmp_path: Path) -> None:
    answers, fake = Answers(), apple()
    args = {**VERSION_ARGS, "description": "Line one\nLine two‮" + "x" * 300}
    async with connected(tmp_path, fake, allow_writes=True, elicit=answers) as c:
        await c.call_tool("create_version_localization", args)
    form = answers.forms[0]
    assert "‮" not in form and "\\u202e" in form and "⏎" in form
    assert "x" * 300 in form and f"({len(args['description'])} characters)" in form
    for text in ("a,b", "Nytt", "Kampanj", "https://example.com/m", "https://example.com/s", "sv"):
        assert text in form


# ------------------------------------------------------------------ invalid input: no form, no request


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("create_version_localization", {**VERSION_ARGS, "locale": "xx-YY"}),
        ("create_version_localization", {**VERSION_ARGS, "locale": "../x"}),
        ("create_version_localization", {**VERSION_ARGS, "locale": "SV"}),
        ("create_version_localization", {**VERSION_ARGS, "description": "x" * 4001}),
        ("create_version_localization", {**VERSION_ARGS, "keywords": "x" * 101}),
        ("create_version_localization", {**VERSION_ARGS, "promotional_text": "x" * 171}),
        ("create_version_localization", {**VERSION_ARGS, "version_id": "../v"}),
        ("create_version_localization", {"version_id": "v9"}),
        ("create_app_info_localization", {**INFO_ARGS, "locale": "english"}),
        ("create_app_info_localization", {**INFO_ARGS, "name": "x" * 31}),
        ("create_app_info_localization", {**INFO_ARGS, "subtitle": "x" * 31}),
        ("create_app_info_localization", {k: v for k, v in INFO_ARGS.items() if k != "name"}),
        ("create_app_info_localization", {**INFO_ARGS, "app_info_id": "a/b"}),
        ("update_app_primary_locale", {"app_id": "7", "locale": "xx"}),
        ("update_app_primary_locale", {"app_id": "7"}),
    ],
)
async def test_invalid_input_is_refused_before_any_form_or_request(
    tmp_path: Path, tool: str, args: dict[str, Any]
) -> None:
    fake, answers = apple(), Answers()
    async with connected(tmp_path, fake, allow_writes=True, elicit=answers) as c:
        result = await c.call_tool(tool, args)
    assert result.is_error
    assert answers.forms == [] and fake.seen == []


# ------------------------------------------------------------------ primary locale specifics


async def test_primary_locale_form_explains_and_shows_old_and_new(tmp_path: Path) -> None:
    answers = Answers()
    async with connected(tmp_path, apple(), allow_writes=True, elicit=answers) as c:
        await c.call_tool("update_app_primary_locale", {"app_id": "7", "locale": "sv"})
    form = answers.forms[0]
    assert "en-US  ->  sv" in form
    assert "when their own language isn't available" in form
    assert "My App" in form


async def test_primary_locale_refused_without_app_info_localization_in_that_locale(
    tmp_path: Path,
) -> None:
    fake, answers = apple(), Answers()
    fake.on("GET", "/v1/appInfos/i1/appInfoLocalizations", {"data": [
        resource("appInfoLocalizations", "x", locale="en-US", name="N")]})  # fmt: skip
    async with connected(tmp_path, fake, allow_writes=True, elicit=answers) as c:
        result = await c.call_tool("update_app_primary_locale", {"app_id": "7", "locale": "sv"})
    assert result.is_error and "app info localization" in text_of(result)
    assert answers.forms == [] and fake.writes() == []


async def test_primary_locale_unchanged_needs_no_request(tmp_path: Path) -> None:
    fake, answers = apple(), Answers()
    async with connected(tmp_path, fake, allow_writes=True, elicit=answers) as c:
        result = await c.call_tool("update_app_primary_locale", {"app_id": "7", "locale": "en-US"})
    assert "already" in text_of(result)
    assert answers.forms == [] and fake.writes() == []


def test_locale_list_has_documented_examples_and_only_plain_codes() -> None:
    assert {"sv", "en-US", "zh-Hans", "pt-BR", "de-DE"} <= APP_STORE_LOCALES
    assert all(code.replace("-", "").isalnum() for code in APP_STORE_LOCALES)


async def test_update_tools_take_snake_case_arguments_and_send_apple_attribute_names(
    tmp_path: Path,
) -> None:
    fake = FakeApple()
    old_texts = resource(
        "appStoreVersionLocalizations", "l1", locale="sv", whatsNew="Old", supportUrl="https://old"
    )
    fake.on("GET", "/v1/appStoreVersionLocalizations/l1", one(old_texts))
    fake.on("PATCH", "/v1/appStoreVersionLocalizations/l1", one(old_texts))
    old_info = resource("appInfoLocalizations", "i1", locale="sv", privacyPolicyUrl="https://old")
    fake.on("GET", "/v1/appInfoLocalizations/i1", one(old_info))
    fake.on("PATCH", "/v1/appInfoLocalizations/i1", one(old_info))
    async with connected(tmp_path, fake, allow_writes=True, elicit=Answers()) as c:
        await c.call_tool(
            "update_version_localization",
            {"localization_id": "l1", "whats_new": "New", "support_url": "https://new"},
        )
        await c.call_tool(
            "update_app_info_localization",
            {"localization_id": "i1", "privacy_policy_url": "https://new"},
        )
    sent = [write.body["data"]["attributes"] for write in fake.writes()]
    assert sent == [
        {"whatsNew": "New", "supportUrl": "https://new"},
        {"privacyPolicyUrl": "https://new"},
    ]
