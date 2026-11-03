"""Real download + Sigstore verification of the pinned upstream release.

Run with: uv run pytest -m network. The binary is only hashed, never executed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app_store_connect import upstream


@pytest.mark.network
def test_pinned_release_downloads_and_verifies(isolated_xdg: Path) -> None:
    binary = upstream.ensure_upstream(upstream.PINNED_VERSION)
    record_file = binary.parent / upstream.RECORD_NAME
    recorded = json.loads(record_file.read_text(encoding="utf-8"))["sha256"]
    assert binary.parent.is_relative_to(isolated_xdg / "data")
    assert recorded == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert upstream.installed_binary(upstream.PINNED_VERSION) == binary
