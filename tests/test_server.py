"""The review gate, maintenance tools, confirmation flow and degraded start-up mode."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import mcp_types as types
import pytest
from mcp import Client
from mcp.client.session import ClientRequestContext, ElicitationFnT

from app_store_connect.review import load_review, stamp_review
from app_store_connect.server import AppServer, create_app, snapshot_tool_names
from app_store_connect.spec import APPLE_API_SPEC_VERSION

from .apple_fake import FakeApple, connected, listing, resource, text_of
from .conftest import NOW
from .helpers import write_test_key


def apps_fake() -> FakeApple:
    fake = FakeApple()
    fake.on("GET", "/v1/apps", listing(resource("apps", "1", name="Fake")))
    return fake


async def test_overdue_review_refuses_tools_and_never_calls_apple(tmp_path: Path) -> None:
    fake = apps_fake()
    async with connected(tmp_path, fake, fresh_review=False) as client:
        result = await client.call_tool("list_apps", {})
    assert result.is_error
    assert "review" in text_of(result)
    assert "maintenance_report" in text_of(result)
    assert "maintenance_confirm_review" in text_of(result)
    assert fake.seen == []


async def test_old_review_is_overdue_and_fresh_one_is_not(tmp_path: Path) -> None:
    fake = apps_fake()
    stamp_review(NOW - timedelta(days=40), "4.5.1", "old", [])
    async with connected(tmp_path, fake, fresh_review=False) as client:
        assert (await client.call_tool("list_apps", {})).is_error
    stamp_review(NOW - timedelta(days=5), "4.5.1", "recent", [])
    async with connected(tmp_path, fake, fresh_review=False) as client:
        assert not (await client.call_tool("list_apps", {})).is_error


async def test_maintenance_tools_work_while_overdue(tmp_path: Path) -> None:
    async with connected(tmp_path, apps_fake(), fresh_review=False) as client:
        status = await client.call_tool("maintenance_status", {})
        report = await client.call_tool("maintenance_report", {})
    assert not status.is_error
    assert "never" in text_of(status)
    assert f"spec checked against: {APPLE_API_SPEC_VERSION}" in text_of(status)
    assert "Writes: off" in text_of(status)
    assert text_of(report).startswith("REPORT [")
    assert "list_apps" in text_of(report)


async def test_unknown_tool_is_refused(tmp_path: Path) -> None:
    async with connected(tmp_path, apps_fake()) as client:
        result = await client.call_tool("asc_api_request", {"path": "/v1/users"})
    assert result.is_error
    assert "Unknown tool" in text_of(result)


def test_no_raw_endpoint_or_login_tool_exists() -> None:
    names = set(snapshot_tool_names())
    assert not {n for n in names if "raw" in n or "login" in n or "request" in n}
    assert not {"toolsets", "web_login", "asc_api_request"} & names


def answer(result: types.ElicitResult) -> ElicitationFnT:
    async def callback(
        context: ClientRequestContext, params: types.ElicitRequestParams
    ) -> types.ElicitResult:
        assert "Only confirm if you read" in params.message
        return result

    return callback


async def test_confirm_review_stamps_only_on_accept_with_read_true(tmp_path: Path) -> None:
    accept = answer(types.ElicitResult(action="accept", content={"read": True}))
    fake = apps_fake()
    async with connected(tmp_path, fake, fresh_review=False, elicit=accept) as client:
        await client.call_tool("maintenance_report", {})
        result = await client.call_tool("maintenance_confirm_review", {"summary": "all fine"})
        assert not result.is_error
        assert not (await client.call_tool("list_apps", {})).is_error  # the gate reopened
    review = load_review()
    assert review is not None
    assert (review.outcome, review.spec_version) == ("all fine", APPLE_API_SPEC_VERSION)
    assert "list_apps" in review.tools
    assert "upload_screenshot" in review.tools  # the snapshot covers write tools too


@pytest.mark.parametrize(
    "reply",
    [
        types.ElicitResult(action="decline"),
        types.ElicitResult(action="cancel"),
    ],
)
async def test_confirm_review_does_not_stamp_otherwise(
    tmp_path: Path, reply: types.ElicitResult
) -> None:
    async with connected(tmp_path, apps_fake(), fresh_review=False, elicit=answer(reply)) as client:
        await client.call_tool("maintenance_report", {})
        result = await client.call_tool("maintenance_confirm_review", {"summary": "ok"})
    assert result.is_error
    assert "app-store-connect@main app-store-connect review" in text_of(result)
    assert load_review() is None


async def test_confirm_review_without_elicitation_support_does_not_stamp(tmp_path: Path) -> None:
    async with connected(tmp_path, apps_fake(), fresh_review=False, elicit=None) as client:
        await client.call_tool("maintenance_report", {})
        result = await client.call_tool("maintenance_confirm_review", {"summary": "ok"})
    assert result.is_error
    assert "terminal" in text_of(result)
    assert load_review() is None


async def test_confirm_review_rejects_empty_summary(tmp_path: Path) -> None:
    async with connected(tmp_path, apps_fake(), fresh_review=False) as client:
        result = await client.call_tool("maintenance_confirm_review", {"summary": "  "})
    assert result.is_error
    assert load_review() is None


async def test_degraded_mode_returns_startup_error_for_every_call() -> None:
    app = AppServer(None, startup_error="app-store-connect cannot start: ASC_KEY_ID is not set")
    async with Client(app.build_server(), mode="legacy", cache=None) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == {"maintenance_status", "maintenance_report", "maintenance_confirm_review"}
        for tool in ("list_apps", "maintenance_status", "submit_for_app_review"):
            result = await client.call_tool(tool, {})
            assert result.is_error
            assert "ASC_KEY_ID is not set" in text_of(result)


def test_create_app_with_bad_env_is_degraded_and_prints_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = create_app({})
    assert app.startup_error is not None
    assert "ASC_KEY_ID" in capsys.readouterr().err


def test_create_app_with_good_env(tmp_path: Path) -> None:
    key, _ = write_test_key(tmp_path / "k")
    env = {
        "ASC_KEY_ID": "K",
        "ASC_ISSUER_ID": "I",
        "ASC_PRIVATE_KEY_PATH": str(key),
        "ASC_ALLOW_WRITES": "1",
    }
    app = create_app(env)
    assert app.startup_error is None
    assert app.config is not None
    assert app.config.allow_writes


def test_degraded_mode_error_shows_the_home_folder_as_tilde(
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = Path.home()
    key = home / "secret-project" / "AuthKey_ABCDEFGHIJ.p8"
    app = create_app({"ASC_KEY_ID": "K", "ASC_ISSUER_ID": "I", "ASC_PRIVATE_KEY_PATH": str(key)})
    assert app.startup_error is not None
    assert str(home) not in app.startup_error
    assert "~/secret-project" in app.startup_error
