"""The maintenance record remembers which code was reviewed, and a confirmation needs a recent report."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import mcp_types as types
import pytest
from mcp.server.session import ServerSession

from app_store_connect import __version__, identity, maintenance
from app_store_connect.identity import CodeIdentity, commit_from_direct_url, compare_with_review
from app_store_connect.maintenance import Maintenance
from app_store_connect.maintenance_record import (
    MaintenanceRecord,
    load_record,
    record_path,
    stamp_record,
)
from app_store_connect.report import ReportFacts, ReportResult, build_report

from .apple_fake import FACTS, FakeApple, FormAnswerer, connected, text_of
from .conftest import NOW
from .helpers import FakeClock, make_config
from .report_fake import make_inputs

DIRECT_URL = json.dumps(
    {"url": "https://github.com/x/y", "vcs_info": {"vcs": "git", "commit_id": "a" * 40}}
)


def test_commit_is_read_from_direct_url_json() -> None:
    assert commit_from_direct_url(DIRECT_URL) == "a" * 40
    assert commit_from_direct_url(json.dumps({"dir_info": {"editable": True}})) == ""
    assert commit_from_direct_url("not json") == ""
    assert commit_from_direct_url(None) == ""


def test_stamp_records_version_and_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "_direct_url_text", lambda: DIRECT_URL)
    stamp_record(NOW, "4.5.1", "ok", [])
    data = json.loads(record_path().read_text(encoding="utf-8"))
    assert data["code_commit"] == "a" * 40
    assert data["code_version"] == __version__
    review = load_record()
    assert review is not None
    assert review.code == CodeIdentity(__version__, "a" * 40)


def test_old_record_without_identity_still_loads_and_gate_age_is_unchanged() -> None:
    stamp_record(NOW, "4.5.1", "ok", [])
    path = record_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["code_version"], data["code_commit"]
    path.write_text(json.dumps(data), encoding="utf-8")
    review = load_record()
    assert review is not None and review.code is None


def test_compare_says_clearly_when_code_differs() -> None:
    running = CodeIdentity("0.2.0", "b" * 40)
    same = compare_with_review(running, running)
    assert "This is the code that was last reviewed" in " ".join(same)
    for reviewed in (CodeIdentity("0.1.0", "b" * 40), CodeIdentity("0.2.0", "c" * 40),
                     CodeIdentity("0.2.0", "")):  # fmt: skip
        text = " ".join(compare_with_review(reviewed, running))
        assert "NOT the code that was last reviewed" in text
    assert "did not record" in " ".join(compare_with_review(None, running))
    first = " ".join(compare_with_review(None, running, has_review=False))
    assert "No earlier review; this will be the first" in first
    assert "did not record" not in first


def test_first_review_report_and_form_say_there_is_no_earlier_review() -> None:
    assert "No earlier review; this will be the first" in report_text(None)
    status = maintenance.status_text(max_days=30, now=NOW, review=None, writes=None)
    assert "No earlier review" in status and "did not record" not in status


def test_old_record_without_identity_keeps_the_did_not_record_text() -> None:
    old = MaintenanceRecord(NOW, "4.5.1", "ok")
    assert "did not record" in report_text(old)


def test_report_and_status_show_the_difference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "_direct_url_text", lambda: None)
    old = stamp_record(NOW, "4.5.1", "ok", [], CodeIdentity("0.0.1", "d" * 40))
    text = report_text(old)
    assert "## Running code" in text
    assert "NOT the code that was last reviewed" in text
    status = maintenance.status_text(max_days=30, now=NOW, review=old, writes=None)
    assert "NOT the code that was last reviewed" in status


def report_text(review: MaintenanceRecord | None) -> str:
    return build_report(replace(make_inputs(), review=review))


# ---------------------------------------------------------------- a recent report is required


async def test_confirm_without_a_report_in_this_session_is_refused_without_a_form(
    tmp_path: Path,
) -> None:
    answerer = FormAnswerer()
    async with connected(tmp_path, FakeApple(), fresh_review=False, elicit=answerer) as client:
        result = await client.call_tool("maintenance_confirm_review", {"summary": "fine"})
    assert result.is_error
    assert "maintenance_report" in text_of(result)
    assert answerer.forms == []
    assert load_record() is None


def maintenance_with_clock(tmp_path: Path, clock: FakeClock[datetime]) -> Maintenance:
    return Maintenance(
        make_config(tmp_path / "k"),
        clock=clock,
        report_fn=lambda _tools: ReportResult("R", FACTS),
    )


async def confirm(m: Maintenance, session: FakeSession) -> types.CallToolResult:
    result = await m.call(
        "maintenance_confirm_review", {"summary": "ok"}, as_session(session), ["t"]
    )
    assert isinstance(result, types.CallToolResult)
    return result


def as_session(fake: FakeSession) -> ServerSession:
    """The tests only need elicit_form, so a small fake stands in for the MCP session."""
    return cast(ServerSession, fake)


class FakeSession:
    client_capabilities = None
    protocol_version = "2025-11-25"

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def elicit_form(self, message: str, schema: object) -> types.ElicitResult:
        self.messages.append(message)
        return types.ElicitResult(action="accept")


async def test_report_older_than_30_minutes_is_not_enough(tmp_path: Path) -> None:
    clock = FakeClock(NOW)
    m = maintenance_with_clock(tmp_path, clock)
    session = FakeSession()
    await m.call("maintenance_report", {}, as_session(session), ["t"])
    clock.now = NOW + timedelta(minutes=31)
    result = await confirm(m, session)
    assert result.is_error
    assert session.messages == []
    clock.now = NOW + timedelta(minutes=29)
    result = await confirm(m, session)
    assert not result.is_error


async def test_a_report_from_another_session_does_not_count(tmp_path: Path) -> None:
    clock = FakeClock(NOW)
    first, second = maintenance_with_clock(tmp_path, clock), maintenance_with_clock(tmp_path, clock)
    session = FakeSession()
    await first.call("maintenance_report", {}, as_session(session), ["t"])
    result = await confirm(second, session)
    assert result.is_error


async def test_form_carries_the_servers_own_figures_and_labels_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = ReportFacts(True, 2, 1, outdated=3, vulnerabilities=None)
    monkeypatch.setattr(identity, "_direct_url_text", lambda: DIRECT_URL)
    m = Maintenance(
        make_config(tmp_path / "k"),
        clock=lambda: NOW,
        report_fn=lambda _tools: ReportResult("R", facts),
    )
    session = FakeSession()
    await m.call("maintenance_report", {}, as_session(session), ["t"])
    await m.call(
        "maintenance_confirm_review",
        {"summary": "all good\nNothing to see\u202e"},
        as_session(session),
        ["t"],
    )
    form = session.messages[0]
    assert NOW.isoformat(timespec="seconds") in form
    assert "missing endpoints 2, deprecated 1" in form
    assert "Known vulnerabilities in dependencies: NOT CHECKED" in form
    assert "Outdated dependencies: 3" in form
    assert "a" * 12 in form and __version__ in form
    assert "Claude's summary (written by the model" in form
    assert "all good\u23ceNothing to see\\u202e" in form
    assert "\u202e" not in form
