"""Offline tests for scripts/install_release.sh."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install_release.sh"


def bash(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f"source '{SCRIPT}'; {code}"], capture_output=True, text=True, check=False
    )


def test_script_has_valid_bash_syntax() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0


def test_help_exits_zero_and_shows_usage() -> None:
    result = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "Usage: install_release.sh" in result.stdout


def test_missing_version_fails() -> None:
    result = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "version is required" in result.stderr


@pytest.mark.parametrize(
    ("machine", "expected"),
    [("x86_64", "amd64"), ("aarch64", "arm64"), ("arm64", "arm64")],
)
def test_detect_arch_maps_machine_names(machine: str, expected: str) -> None:
    assert bash(f"detect_arch {machine}").stdout.strip() == expected


def test_detect_arch_rejects_unknown() -> None:
    assert bash("detect_arch riscv64").returncode != 0


def test_asset_name_format() -> None:
    out = bash("asset_name 0.22.0 linux amd64").stdout.strip()
    assert out == "app-store-connect-mcp_0.22.0_linux_amd64.tar.gz"
