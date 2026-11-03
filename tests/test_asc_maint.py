"""Tests for scripts/asc_maint.py."""

from __future__ import annotations

import io
import json
import stat
from datetime import timedelta
from pathlib import Path

import asc_maint
import pytest
from conftest import NOW


def run_gate(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "mcp__asc__apps"})))
    assert asc_maint.main(["gate"], now=NOW) == 0
    return capsys.readouterr().out


def write_state(state_dir: Path, days_ago: int, tools: list[str] | None = None) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    when = (NOW - timedelta(days=days_ago)).isoformat()
    data = {"schema": 1, "reviewed_at": when, "server_version": "0.22.0", "outcome": "ok",
            "tools": tools or [], "history": []}  # fmt: skip
    (state_dir / "state.json").write_text(json.dumps(data), encoding="utf-8")


def stamp(argv: list[str], now=NOW) -> int:
    return asc_maint.main(["stamp", *argv], now=now)


def test_gate_never_reviewed_denies(monkeypatch, capsys) -> None:
    out = json.loads(run_gate(monkeypatch, capsys))
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "has never been done" in reason
    assert reason.endswith("then stamp the review.")


def test_gate_output_shape_is_exact(monkeypatch, capsys) -> None:
    out = json.loads(run_gate(monkeypatch, capsys))
    assert list(out) == ["hookSpecificOutput"]
    inner = out["hookSpecificOutput"]
    assert set(inner) == {"hookEventName", "permissionDecision", "permissionDecisionReason"}
    assert inner["hookEventName"] == "PreToolUse"
    assert inner["permissionDecision"] == "deny"


def test_gate_fresh_review_prints_nothing(isolated_state, monkeypatch, capsys) -> None:
    write_state(isolated_state, 29)
    assert run_gate(monkeypatch, capsys) == ""


def test_gate_overdue_mentions_date_age_and_limit(isolated_state, monkeypatch, capsys) -> None:
    write_state(isolated_state, 30)
    reason = json.loads(run_gate(monkeypatch, capsys))["hookSpecificOutput"][
        "permissionDecisionReason"
    ]
    assert "overdue (last review 2026-10-04, 30 days ago; limit 30 days)" in reason


def test_gate_env_override_changes_limit(isolated_state, monkeypatch, capsys) -> None:
    write_state(isolated_state, 10)
    monkeypatch.setenv("ASC_MCP_REVIEW_MAX_DAYS", "7")
    assert "limit 7 days" in run_gate(monkeypatch, capsys)


@pytest.mark.parametrize("value", ["0", "366", "abc", "", "-5", "2.5"])
def test_max_days_invalid_env_falls_back_to_30(monkeypatch, value) -> None:
    monkeypatch.setenv("ASC_MCP_REVIEW_MAX_DAYS", value)
    assert asc_maint.max_days() == 30


def test_gate_corrupt_state_denies(isolated_state, monkeypatch, capsys) -> None:
    isolated_state.mkdir(parents=True)
    (isolated_state / "state.json").write_text("{not json", encoding="utf-8")
    assert "never been done" in run_gate(monkeypatch, capsys)


def test_gate_naive_timestamp_denies(isolated_state, monkeypatch, capsys) -> None:
    isolated_state.mkdir(parents=True)
    (isolated_state / "state.json").write_text('{"reviewed_at": "2026-11-01T00:00:00"}')
    assert run_gate(monkeypatch, capsys) != ""


def test_gate_future_review_date_denies(isolated_state, monkeypatch, capsys) -> None:
    write_state(isolated_state, -2)  # two days in the future: wrong clock or edited file
    assert "lies in the future" in run_gate(monkeypatch, capsys)


def test_gate_tolerates_small_clock_difference(isolated_state, monkeypatch, capsys) -> None:
    isolated_state.mkdir(parents=True)
    when = (NOW + timedelta(minutes=30)).isoformat()
    (isolated_state / "state.json").write_text(json.dumps({"reviewed_at": when}))
    assert run_gate(monkeypatch, capsys) == ""


def test_gate_fails_closed_on_unexpected_exception(monkeypatch, capsys) -> None:
    def boom(_now):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(asc_maint, "deny_reason", boom)
    out = json.loads(run_gate(monkeypatch, capsys))
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason == "asc-mcp-maintenance gate error: kaboom; fix the plugin or run the review"


def test_stamp_writes_state_with_strict_permissions(isolated_state, capsys) -> None:
    assert stamp(["--server-version", "0.22.0", "--outcome", "all good"]) == 0
    path = isolated_state / "state.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(isolated_state.stat().st_mode) == 0o700
    data = json.loads(path.read_text())
    assert data["schema"] == 1
    assert data["reviewed_at"] == "2026-11-03T12:00:00+01:00"
    assert data["server_version"] == "0.22.0"
    assert data["history"] == []
    assert [p.name for p in isolated_state.iterdir()] == ["state.json"]


def test_stamp_is_atomic_via_rename(isolated_state, monkeypatch) -> None:
    calls = []
    real = Path.replace

    def spy(self, target):
        calls.append((self, Path(target)))
        return real(self, target)

    monkeypatch.setattr(Path, "replace", spy)
    stamp(["--server-version", "1", "--outcome", "x"])
    assert len(calls) == 1
    assert calls[0][1] == isolated_state / "state.json"


def test_stamp_tools_file_sorted_unique_blank_ignored(isolated_state, tmp_path) -> None:
    tools = tmp_path / "tools.txt"
    tools.write_text("b\n\na\nb\n  \n", encoding="utf-8")
    stamp(["--server-version", "1", "--outcome", "x", "--tools-file", str(tools)])
    assert json.loads((isolated_state / "state.json").read_text())["tools"] == ["a", "b"]


def test_stamp_without_tools_file_keeps_previous_tools(isolated_state) -> None:
    write_state(isolated_state, 5, tools=["a", "b"])
    stamp(["--server-version", "2", "--outcome", "y"])
    data = json.loads((isolated_state / "state.json").read_text())
    assert data["tools"] == ["a", "b"]
    assert "tools" not in data["history"][0]
    assert data["history"][0]["server_version"] == "0.22.0"


def test_stamp_history_capped_at_12_newest_first(isolated_state) -> None:
    for i in range(15):
        stamp(["--server-version", f"v{i}", "--outcome", "x"])
    history = json.loads((isolated_state / "state.json").read_text())["history"]
    assert len(history) == 12
    assert history[0]["server_version"] == "v13"


def test_diff_tools_added_and_removed(isolated_state, tmp_path, capsys) -> None:
    write_state(isolated_state, 1, tools=["a", "b"])
    tools = tmp_path / "t.txt"
    tools.write_text("b\nc\n", encoding="utf-8")
    asc_maint.main(["diff-tools", "--tools-file", str(tools)], now=NOW)
    assert capsys.readouterr().out == "added:\n  c\nremoved:\n  a\n"


def test_diff_tools_no_changes(isolated_state, tmp_path, capsys) -> None:
    write_state(isolated_state, 1, tools=["a"])
    tools = tmp_path / "t.txt"
    tools.write_text("a\n", encoding="utf-8")
    asc_maint.main(["diff-tools", "--tools-file", str(tools)], now=NOW)
    assert capsys.readouterr().out == "no changes\n"


def test_status_without_gh_or_binary(isolated_state, monkeypatch, capsys) -> None:
    write_state(isolated_state, 10)
    monkeypatch.setenv("ASC_MCP_BIN", "definitely-not-a-real-binary")
    monkeypatch.setattr(asc_maint.shutil, "which", lambda _name: None)
    assert asc_maint.main(["status", "--json"], now=NOW) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["installed_version"] == "not installed"
    assert info["latest_upstream"] == "unknown"
    assert info["days_ago"] == 10
    assert info["days_left"] == 20
    assert info["overdue"] is False


def test_status_text_when_never_reviewed(monkeypatch, capsys) -> None:
    monkeypatch.setattr(asc_maint.shutil, "which", lambda _name: None)
    assert asc_maint.main(["status"], now=NOW) == 0
    out = capsys.readouterr().out
    assert "last review:       never" in out
    assert "overdue:           yes" in out


def test_installed_version_parses_last_token(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "fakebin"
    fake.write_text("#!/bin/sh\necho 'app-store-connect-mcp 0.22.0'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("ASC_MCP_BIN", str(fake))
    assert asc_maint.installed_version() == "0.22.0"
