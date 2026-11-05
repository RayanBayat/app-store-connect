"""Tests for maintenance_record.py: fail-closed rules, history cap, atomic 0600 writes."""

from __future__ import annotations

import json
import stat
from datetime import timedelta
from pathlib import Path

import pytest

from app_store_connect import maintenance_record
from app_store_connect.maintenance_record import (
    MaintenanceRecord,
    check_record,
    load_record,
    stamp_record,
)

from .conftest import NOW


@pytest.fixture
def state(isolated_xdg: Path) -> Path:
    return isolated_xdg / "state" / "app-store-connect"


def write_raw(state: Path, days_ago: int, tools: list[str] | None = None) -> None:
    state.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": 1,
        "reviewed_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "spec_version": "4.5.1",
        "outcome": "ok",
        "tools": tools or [],
        "history": [],
    }
    (state / "review.json").write_text(json.dumps(data), encoding="utf-8")


def test_missing_file_is_overdue(state: Path) -> None:
    result = check_record(load_record(), NOW, 30)
    assert result.overdue
    assert "never been done" in result.message


def test_fresh_review_is_not_overdue(state: Path) -> None:
    write_raw(state, 29)
    result = check_record(load_record(), NOW, 30)
    assert not result.overdue
    assert result.days_left == 1


def test_review_at_limit_is_overdue_and_message_has_details(state: Path) -> None:
    write_raw(state, 30)
    result = check_record(load_record(), NOW, 30)
    assert result.overdue
    assert "overdue (last review 2026-10-04, 30 days ago; limit 30 days)" in result.message
    assert "maintenance_report" in result.message


def test_custom_limit_applies(state: Path) -> None:
    write_raw(state, 10)
    assert check_record(load_record(), NOW, 7).overdue


def test_corrupt_file_is_overdue(state: Path) -> None:
    state.mkdir(parents=True)
    (state / "review.json").write_text("{not json", encoding="utf-8")
    assert load_record() is None


def test_naive_timestamp_is_overdue(state: Path) -> None:
    state.mkdir(parents=True)
    (state / "review.json").write_text('{"reviewed_at": "2026-11-01T00:00:00"}', encoding="utf-8")
    assert load_record() is None


def test_wrong_shape_is_overdue(state: Path) -> None:
    state.mkdir(parents=True)
    (state / "review.json").write_text('["a"]', encoding="utf-8")
    assert load_record() is None


def test_future_date_is_overdue(state: Path) -> None:
    write_raw(state, -2)
    result = check_record(load_record(), NOW, 30)
    assert result.overdue
    assert "in the future" in result.message


def test_small_clock_difference_is_tolerated(state: Path) -> None:
    state.mkdir(parents=True)
    when = (NOW + timedelta(minutes=30)).isoformat()
    (state / "review.json").write_text(json.dumps({"reviewed_at": when}), encoding="utf-8")
    assert not check_record(load_record(), NOW, 30).overdue


def test_stamp_writes_strict_permissions_and_expected_fields(state: Path) -> None:
    stamp_record(NOW, "4.5.1", "all good", ["b", "a"])
    path = state / "review.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == 1
    assert data["reviewed_at"] == "2026-11-03T12:00:00+01:00"
    assert data["spec_version"] == "4.5.1"
    assert data["tools"] == ["a", "b"]
    assert data["history"] == []
    assert [p.name for p in state.iterdir()] == ["review.json"]


def test_stamp_is_atomic_via_rename(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, Path]] = []
    real = Path.replace

    def spy(self: Path, target: str | Path) -> Path:
        calls.append((self, Path(target)))
        return real(self, target)

    monkeypatch.setattr(Path, "replace", spy)
    stamp_record(NOW, "4.5.1", "x", None)
    assert len(calls) == 1
    assert calls[0][1] == state / "review.json"


def test_failed_write_leaves_no_temp_file(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: Path, target: str | Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        stamp_record(NOW, "4.5.1", "x", None)
    assert list(state.iterdir()) == []


def test_stamp_without_tools_keeps_previous_snapshot(state: Path) -> None:
    write_raw(state, 5, tools=["a", "b"])
    stamp_record(NOW, "2", "y", None)
    loaded = load_record()
    assert isinstance(loaded, MaintenanceRecord)
    assert loaded.tools == ("a", "b")
    assert loaded.history[0].spec_version == "4.5.1"


def test_history_is_capped_at_12_newest_first(state: Path) -> None:
    for i in range(15):
        stamp_record(NOW, f"4.5.{i}", "x", None)
    loaded = load_record()
    assert loaded is not None
    assert len(loaded.history) == maintenance_record.HISTORY_LIMIT == 12
    assert loaded.history[0].spec_version == "4.5.13"
