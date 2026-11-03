"""Shared fixtures: every test gets an isolated state dir and a clean environment."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

NOW = datetime.fromisoformat("2026-11-03T12:00:00+01:00")


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the state dir at tmp_path and drop env knobs that change behaviour."""
    state = tmp_path / "state"
    monkeypatch.setenv("ASC_MCP_MAINT_STATE_DIR", str(state))
    monkeypatch.delenv("ASC_MCP_REVIEW_MAX_DAYS", raising=False)
    monkeypatch.delenv("ASC_MCP_BIN", raising=False)
    return state
