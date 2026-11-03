"""Tests for safety.py: the blocklist and the child environment allowlist."""

from __future__ import annotations

from pathlib import Path

import pytest

from app_store_connect import safety
from app_store_connect.config import Config

PARENT = {
    "PATH": "/usr/bin",
    "HOME": "/home/u",
    "LANG": "en_US.UTF-8",
    "LC_ALL": "C",
    "LC_TIME": "sv_SE.UTF-8",
    "TMPDIR": "/tmp/x",
    "ASC_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----",
    "ASC_MCP_CI": "1",
    "ASC_MCP_ALLOW_NO_ELICIT": "1",
    "ASC_MCP_BASE_URL": "http://evil.example",
    "ASC_MCP_ALLOW_WRITES": "1",
    "ASC_MCP_TOOLSETS": "web,admin,signing",
    "ASC_MCP_PROFILE": "other",
    "ASC_MCP_ROOT": "/etc",
    "XDG_CONFIG_HOME": "/home/u/.config",
    "XDG_STATE_HOME": "/home/u/.local/state",
    "SHELL": "/bin/zsh",
    "TERM": "xterm",
    "USER": "u",
    "AWS_SECRET_ACCESS_KEY": "shh",
    "RANDOM_THING": "x",
}


def make_config(tmp_path: Path, **overrides: object) -> Config:
    base: dict[str, object] = {
        "key_id": "KEY1234567",
        "issuer_id": "issuer-id",
        "private_key_path": tmp_path / "k.p8",
    }
    return Config(**{**base, **overrides})  # type: ignore[arg-type]


def test_only_allowlisted_parent_variables_pass(tmp_path: Path) -> None:
    env = safety.build_child_env(make_config(tmp_path), PARENT)
    assert set(env) == {
        "PATH", "HOME", "LANG", "LC_ALL", "LC_TIME", "TMPDIR",
        "XDG_CONFIG_HOME", "XDG_STATE_HOME",
        "ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_KIND", "ASC_PRIVATE_KEY_PATH",
        "ASC_MCP_TOOLSETS",
    }  # fmt: skip


@pytest.mark.parametrize(
    "name",
    ["ASC_PRIVATE_KEY", "ASC_MCP_CI", "ASC_MCP_ALLOW_NO_ELICIT", "ASC_MCP_BASE_URL",
     "ASC_MCP_PROFILE", "SHELL", "TERM", "USER", "AWS_SECRET_ACCESS_KEY", "RANDOM_THING"],
)  # fmt: skip
def test_dangerous_or_random_variables_never_pass(tmp_path: Path, name: str) -> None:
    assert name not in safety.build_child_env(make_config(tmp_path), PARENT)


def test_parent_asc_values_cannot_override_ours(tmp_path: Path) -> None:
    env = safety.build_child_env(make_config(tmp_path), PARENT)
    assert env["ASC_MCP_TOOLSETS"] == safety.TOOLSETS
    assert "ASC_MCP_ALLOW_WRITES" not in env  # parent said 1, our config says off
    assert "ASC_MCP_ROOT" not in env


def test_xdg_homes_point_at_private_dirs_not_the_users(tmp_path: Path) -> None:
    env = safety.build_child_env(make_config(tmp_path), PARENT)
    state = tmp_path / "state" / "app-store-connect"
    assert Path(env["XDG_CONFIG_HOME"]) == state / "upstream-config"
    assert Path(env["XDG_STATE_HOME"]) == state / "upstream-state"
    assert Path(env["XDG_CONFIG_HOME"]).stat().st_mode & 0o077 == 0


def test_writes_flag_maps_to_upstream_variable(tmp_path: Path) -> None:
    on = safety.build_child_env(make_config(tmp_path, allow_writes=True), {})
    off = safety.build_child_env(make_config(tmp_path, allow_writes=False), {})
    assert on["ASC_MCP_ALLOW_WRITES"] == "1"
    assert "ASC_MCP_ALLOW_WRITES" not in off


def test_optional_settings_map_through(tmp_path: Path) -> None:
    config = make_config(tmp_path, vendor_number="999", root=tmp_path, key_kind="individual")
    env = safety.build_child_env(config, {})
    assert env["ASC_VENDOR_NUMBER"] == "999"
    assert env["ASC_MCP_ROOT"] == str(tmp_path)
    assert env["ASC_KEY_KIND"] == "individual"
    assert env["ASC_PRIVATE_KEY_PATH"] == str(tmp_path / "k.p8")


def test_toolsets_exclude_web_admin_signing() -> None:
    names = set(safety.TOOLSETS.split(","))
    assert names == {
        "core",
        "release",
        "testflight",
        "metadata",
        "reviews",
        "reports",
        "screenshots",
    }
    assert not names & {"web", "admin", "signing"}


def test_blocked_tools_are_the_three_audited_ones() -> None:
    assert set(safety.BLOCKED_TOOLS) == {"toolsets", "web_login", "asc_api_request"}
    assert "toolsets" in safety.blocked_message("toolsets")


def test_run_dir_is_private(tmp_path: Path) -> None:
    folder = safety.run_dir()
    assert folder == tmp_path / "state" / "app-store-connect" / "run"
    assert folder.stat().st_mode & 0o077 == 0
