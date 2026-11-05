"""Tests for the command line."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app_store_connect import cli
from app_store_connect.auth import TokenProvider
from app_store_connect.client import AscClient
from app_store_connect.maintenance_record import load_record, stamp_record
from app_store_connect.spec import APPLE_API_SPEC_VERSION

from .conftest import NOW
from .helpers import write_test_key


def good_env(tmp_path: Path) -> dict[str, str]:
    key, _ = write_test_key(tmp_path / "keys")
    return {
        "ASC_KEY_ID": "ABCDE12345",
        "ASC_ISSUER_ID": "issuer-uuid",
        "ASC_PRIVATE_KEY_PATH": str(key),
    }


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.startswith("app-store-connect ")


def test_status_without_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Last review: never" in out
    assert f"Apple API spec checked against: {APPLE_API_SPEC_VERSION}" in out


def test_review_refuses_without_a_terminal(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.cmd_review(interactive=False, ask=lambda _p: "y", report_fn=lambda _t: "REPORT")
    assert code == 2
    assert "interactive terminal" in capsys.readouterr().err
    assert load_record() is None


def test_review_stamps_on_y(capsys: pytest.CaptureFixture[str]) -> None:
    answers = iter(["y", "looked fine"])
    code = cli.cmd_review(
        interactive=True, ask=lambda _p: next(answers), report_fn=lambda _t: "REPORT TEXT"
    )
    assert code == 0
    assert "REPORT TEXT" in capsys.readouterr().out
    review = load_record()
    assert review is not None
    assert review.outcome == "looked fine"
    assert review.spec_version == APPLE_API_SPEC_VERSION
    assert "upload_screenshot" in review.tools


@pytest.mark.parametrize("reply", ["", "n", "no", "maybe"])
def test_review_does_not_stamp_unless_yes(reply: str) -> None:
    code = cli.cmd_review(interactive=True, ask=lambda _p: reply, report_fn=lambda _t: "R")
    assert code == 1
    assert load_record() is None


def test_review_keeps_old_review_when_declined() -> None:
    stamp_record(NOW, "4.5.0", "earlier", ["a"])
    cli.cmd_review(interactive=True, ask=lambda _p: "n", report_fn=lambda _t: "R")
    review = load_record()
    assert review is not None
    assert review.outcome == "earlier"


def test_doctor_ok_masks_the_key_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.cmd_doctor(good_env(tmp_path), online=False) == 0
    out = capsys.readouterr().out
    assert "******2345" in out
    assert "ABCDE12345" not in out
    assert "issuer-uuid" not in out
    assert "an API token can be signed" in out


def test_doctor_reports_open_key_permissions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = good_env(tmp_path)
    Path(env["ASC_PRIVATE_KEY_PATH"]).chmod(0o644)
    assert cli.cmd_doctor(env, online=False) == 1
    assert "chmod 600" in capsys.readouterr().out


def test_doctor_reports_missing_settings(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.cmd_doctor({}, online=False) == 1
    assert "ASC_KEY_ID is not set" in capsys.readouterr().out


async def test_online_check_uses_only_a_list_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"data": []})

    real = AscClient

    def fake_client(tokens: TokenProvider) -> AscClient:
        return real(tokens, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(cli, "AscClient", fake_client)
    assert (await cli.online_check(good_env(tmp_path))).ok
    assert seen == [("GET", "/v1/apps")]


def test_doctor_masks_key_id_in_key_file_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = good_env(tmp_path)
    original = Path(env["ASC_PRIVATE_KEY_PATH"])
    named = original.with_name(f"AuthKey_{env['ASC_KEY_ID']}.p8")
    original.rename(named)
    env["ASC_PRIVATE_KEY_PATH"] = str(named)
    assert cli.cmd_doctor(env, online=False) == 0
    assert env["ASC_KEY_ID"] not in capsys.readouterr().out


def test_doctor_with_an_encrypted_key_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An encrypted key must give a readable doctor message, not a TypeError."""
    env = good_env(tmp_path)
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"pw"),
    )
    Path(env["ASC_PRIVATE_KEY_PATH"]).write_bytes(pem)
    assert cli.cmd_doctor(env, online=False) == 1
    assert "unsupported or encrypted" in capsys.readouterr().out
