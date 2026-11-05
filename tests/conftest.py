"""Shared fixtures: every test gets isolated XDG folders and a clean environment."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

NOW = datetime.fromisoformat("2026-11-03T12:00:00+01:00")


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep saved files and the home folder inside tmp_path, and drop ASC_* env vars."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in list(os.environ):
        if name.startswith("ASC_"):
            monkeypatch.delenv(name)
    return tmp_path
