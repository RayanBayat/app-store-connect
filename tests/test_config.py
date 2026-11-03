"""Tests for config.py: every refusal, and that messages never leak secrets."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app_store_connect.config import (
    Config,
    ConfigError,
    chmod_target,
    is_truthy,
    load_config,
    mask_key_id,
    masked_path,
    parse_review_days,
)

KEY_ID = "ABCDE12345"
ISSUER = "69a6de70-aaaa-bbbb-cccc-0123456789ab"
KEY_BODY = "-----BEGIN PRIVATE KEY-----\nSECRETSECRET\n-----END PRIVATE KEY-----\n"


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    folder = tmp_path / "keys"
    folder.mkdir(mode=0o700)
    folder.chmod(0o700)
    key = folder / "my-key.p8"
    key.write_text(KEY_BODY, encoding="utf-8")
    key.chmod(0o600)
    return key


@pytest.fixture
def env(key_file: Path) -> dict[str, str]:
    return {"ASC_KEY_ID": KEY_ID, "ASC_ISSUER_ID": ISSUER, "ASC_PRIVATE_KEY_PATH": str(key_file)}


def test_load_config_minimal_uses_safe_defaults(env: dict[str, str]) -> None:
    config = load_config(env)
    assert config.key_kind == "team"
    assert config.allow_writes is False
    assert config.review_max_days == 30
    assert config.root is None


def test_load_config_reads_optional_values(env: dict[str, str], tmp_path: Path) -> None:
    env.update(
        ASC_KEY_KIND="Individual",
        ASC_VENDOR_NUMBER="12345",
        ASC_ALLOW_WRITES="YES",
        ASC_ROOT=str(tmp_path),
        ASC_REVIEW_MAX_DAYS="7",
    )
    config = load_config(env)
    assert config == Config(
        key_id=KEY_ID,
        issuer_id=ISSUER,
        private_key_path=Path(env["ASC_PRIVATE_KEY_PATH"]),
        key_kind="individual",
        vendor_number="12345",
        allow_writes=True,
        root=tmp_path,
        review_max_days=7,
    )


@pytest.mark.parametrize("name", ["ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_PRIVATE_KEY_PATH"])
def test_missing_required_variable_names_it(env: dict[str, str], name: str) -> None:
    del env[name]
    with pytest.raises(ConfigError, match=name):
        load_config(env)


def test_all_problems_are_reported_together() -> None:
    with pytest.raises(ConfigError) as info:
        load_config({})
    for name in ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_PRIVATE_KEY_PATH"):
        assert name in str(info.value)


def test_missing_key_file_refused(env: dict[str, str], tmp_path: Path) -> None:
    env["ASC_PRIVATE_KEY_PATH"] = str(tmp_path / "nope.p8")
    with pytest.raises(ConfigError, match="does not exist or is not a regular file"):
        load_config(env)


def test_key_path_that_is_a_directory_refused(env: dict[str, str], tmp_path: Path) -> None:
    env["ASC_PRIVATE_KEY_PATH"] = str(tmp_path)
    with pytest.raises(ConfigError, match="regular file"):
        load_config(env)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666])
def test_key_file_open_to_group_or_others_refused_with_chmod_hint(
    env: dict[str, str], key_file: Path, mode: int
) -> None:
    key_file.chmod(mode)
    with pytest.raises(ConfigError, match=f"chmod 600 {key_file}"):
        load_config(env)


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o770])
def test_key_folder_open_to_group_or_others_refused_with_chmod_hint(
    env: dict[str, str], key_file: Path, mode: int
) -> None:
    key_file.parent.chmod(mode)
    with pytest.raises(ConfigError, match=f"chmod 700 {key_file.parent}"):
        load_config(env)


def test_private_key_variable_refused_and_points_to_path(env: dict[str, str]) -> None:
    env["ASC_PRIVATE_KEY"] = KEY_BODY
    with pytest.raises(ConfigError, match="ASC_PRIVATE_KEY_PATH"):
        load_config(env)


def test_invalid_key_kind_refused(env: dict[str, str]) -> None:
    env["ASC_KEY_KIND"] = "admin"
    with pytest.raises(ConfigError, match="ASC_KEY_KIND"):
        load_config(env)


def test_asc_root_must_exist(env: dict[str, str], tmp_path: Path) -> None:
    env["ASC_ROOT"] = str(tmp_path / "missing")
    with pytest.raises(ConfigError, match="ASC_ROOT"):
        load_config(env)


def test_error_messages_never_leak_secrets(env: dict[str, str], key_file: Path) -> None:
    env["ASC_PRIVATE_KEY"] = KEY_BODY
    key_file.chmod(0o644)
    env["ASC_KEY_KIND"] = "bad"
    with pytest.raises(ConfigError) as info:
        load_config(env)
    message = str(info.value)
    assert "SECRETSECRET" not in message
    assert ISSUER not in message
    assert KEY_ID not in message


def test_masked_key_id_shows_only_last_four(env: dict[str, str]) -> None:
    assert load_config(env).masked_key_id == "******2345"
    assert mask_key_id("abc") == "abc"


@pytest.mark.parametrize("value", ["0", "366", "abc", "", "-5", "2.5", None])
def test_review_days_invalid_falls_back_to_30(value: str | None) -> None:
    assert parse_review_days(value) == 30


@pytest.mark.parametrize(("value", "expected"), [("1", 1), ("365", 365), (" 12 ", 12)])
def test_review_days_valid(value: str, expected: int) -> None:
    assert parse_review_days(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("0", False),
        ("no", False),
        ("", False),
        (None, False),
    ],
)
def test_is_truthy(value: str | None, expected: bool) -> None:
    assert is_truthy(value) is expected


def test_masked_path_hides_key_id_in_apple_file_name(tmp_path: Path) -> None:
    shown = masked_path(tmp_path / f"AuthKey_{KEY_ID}.p8")
    assert shown.endswith("AuthKey_******2345.p8")
    assert KEY_ID not in shown


def test_masked_path_leaves_other_file_names_alone(tmp_path: Path) -> None:
    path = tmp_path / "my-key.p8"
    assert masked_path(path) == str(path)


def test_chmod_hint_matches_the_real_file_without_the_key_id(tmp_path: Path) -> None:
    path = tmp_path / f"AuthKey_{KEY_ID}.p8"
    path.write_text("x", encoding="utf-8")
    target = chmod_target(path)
    assert KEY_ID not in target
    folder, pattern = target.rsplit("/", 1)
    assert folder.strip("'") == str(tmp_path)
    assert list(tmp_path.glob(pattern)) == [path]


def test_open_key_file_message_hides_key_id(env: dict[str, str], tmp_path: Path) -> None:
    named = tmp_path / f"AuthKey_{KEY_ID}.p8"
    named.write_text(KEY_BODY, encoding="utf-8")
    named.chmod(0o644)
    env["ASC_PRIVATE_KEY_PATH"] = str(named)
    with pytest.raises(ConfigError) as caught:
        load_config(env)
    assert KEY_ID not in str(caught.value)
    assert "chmod 600" in str(caught.value)


def test_key_file_owned_by_another_user_is_refused_at_startup(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, key_file: Path
) -> None:
    monkeypatch.setattr(os, "getuid", lambda: key_file.stat().st_uid + 1)
    with pytest.raises(ConfigError, match="owned by you"):
        load_config(env)


def test_repr_never_shows_key_id_or_issuer_id(env: dict[str, str]) -> None:
    """Regression (INFO): a ToolContext repr in a log line must not leak these IDs."""
    config = load_config(env)
    assert KEY_ID not in repr(config)
    assert ISSUER not in repr(config)
    assert KEY_ID not in str(config)


def test_asc_root_that_others_can_write_is_refused(env: dict[str, str], tmp_path: Path) -> None:
    folder = tmp_path / "shots"
    folder.mkdir()
    folder.chmod(0o775)
    with pytest.raises(ConfigError, match=r"ASC_ROOT.*writable"):
        load_config({**env, "ASC_ROOT": str(folder)})
    folder.chmod(0o757)
    with pytest.raises(ConfigError, match=r"ASC_ROOT.*writable"):
        load_config({**env, "ASC_ROOT": str(folder)})


def test_asc_root_that_is_slash_or_home_is_refused(env: dict[str, str]) -> None:
    Path.home().mkdir(parents=True, exist_ok=True)
    Path.home().chmod(0o700)
    for bad in ("/", str(Path.home()), "~"):
        with pytest.raises(ConfigError, match=r"ASC_ROOT.*too broad"):
            load_config({**env, "ASC_ROOT": bad})


def test_private_asc_root_is_accepted(env: dict[str, str], tmp_path: Path) -> None:
    folder = tmp_path / "shots"
    folder.mkdir(mode=0o755)
    assert load_config({**env, "ASC_ROOT": str(folder)}).root == folder
