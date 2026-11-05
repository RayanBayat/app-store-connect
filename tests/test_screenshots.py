"""upload_screenshot: path confinement and Apple's reserve -> upload -> commit flow."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import httpx
import mcp_types as types
import pytest

from app_store_connect.tools import screenshots
from app_store_connect.tools.base import ToolRefusedError

from .apple_fake import FakeApple, FormAnswerer, connected, one, resource, text_of

PNG = b"\x89PNG\r\n\x1a\n" + b"0123456789" * 10  # 108 bytes
PRESIGNED = "https://store-001.blobstore.apple.com/part"


def make_fake(size: int) -> FakeApple:
    fake = FakeApple()
    fake.on(
        "GET",
        "/v1/appScreenshotSets/s1",
        one(resource("appScreenshotSets", "s1", screenshotDisplayType="APP_IPHONE_67")),
    )
    ops = [
        {
            "method": "PUT",
            "url": f"{PRESIGNED}1",
            "offset": 0,
            "length": 60,
            "requestHeaders": [{"name": "Content-Type", "value": "image/png"}],
        },
        {
            "method": "PUT",
            "url": f"{PRESIGNED}2",
            "offset": 60,
            "length": size - 60,
            "requestHeaders": [],
        },
    ]
    fake.on(
        "POST",
        "/v1/appScreenshots",
        one(resource("appScreenshots", "p1", fileName="shot.png", uploadOperations=ops)),
        201,
    )
    fake.on(
        "PATCH",
        "/v1/appScreenshots/p1",
        one(
            resource(
                "appScreenshots",
                "p1",
                fileName="shot.png",
                assetDeliveryState={"state": "UPLOAD_COMPLETE"},
            )
        ),
    )
    fake.on("PUT", "/part1", lambda _r: httpx.Response(200))
    fake.on("PUT", "/part2", lambda _r: httpx.Response(200))
    return fake


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "shots"
    folder.mkdir()
    (folder / "shot.png").write_bytes(PNG)
    return folder


async def upload(
    tmp_path: Path, fake: FakeApple, root: Path | None, path: str
) -> types.CallToolResult:
    async with connected(
        tmp_path, fake, allow_writes=True, elicit=FormAnswerer(), root=root
    ) as client:
        return await client.call_tool(
            "upload_screenshot", {"screenshot_set_id": "s1", "file_path": path}
        )


async def test_full_flow_reserve_upload_parts_commit_with_md5(tmp_path: Path, root: Path) -> None:
    fake = make_fake(len(PNG))
    result = await upload(tmp_path, fake, root, "shot.png")
    assert not result.is_error, text_of(result)
    reserve, part1, part2, commit = (r for r in fake.seen if r.method != "GET")
    assert reserve.path == "/v1/appScreenshots"
    assert reserve.body["data"]["attributes"] == {"fileName": "shot.png", "fileSize": len(PNG)}
    assert reserve.body["data"]["relationships"]["appScreenshotSet"]["data"] == {
        "type": "appScreenshotSets",
        "id": "s1",
    }
    assert (part1.host, part1.path, part2.path) == (
        "store-001.blobstore.apple.com",
        "/part1",
        "/part2",
    )
    assert commit.body["data"]["attributes"] == {
        "uploaded": True,
        "sourceFileChecksum": hashlib.md5(PNG, usedforsecurity=False).hexdigest(),
    }


async def test_presigned_uploads_carry_no_authorization_header(tmp_path: Path, root: Path) -> None:
    fake = make_fake(len(PNG))
    await upload(tmp_path, fake, root, "shot.png")
    uploads = [r for r in fake.seen if r.host == "store-001.blobstore.apple.com"]
    assert len(uploads) == 2
    assert all(r.authorization is None for r in uploads)
    assert all(r.authorization for r in fake.seen if r.host == "api.appstoreconnect.apple.com")


async def test_parts_contain_the_right_byte_ranges(tmp_path: Path, root: Path) -> None:
    sent: list[bytes] = []
    fake = make_fake(len(PNG))

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200)

    fake.on("PUT", "/part1", record)
    fake.on("PUT", "/part2", record)
    await upload(tmp_path, fake, root, "shot.png")
    assert sent == [PNG[:60], PNG[60:]]


async def test_symlink_escaping_root_is_refused_and_nothing_is_sent(
    tmp_path: Path, root: Path
) -> None:
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG)
    (root / "link.png").symlink_to(secret)
    fake = make_fake(len(PNG))
    result = await upload(tmp_path, fake, root, "link.png")
    assert result.is_error
    assert "outside the allowed folder" in text_of(result)
    assert fake.seen == []


async def test_symlinked_directory_escape_is_refused(tmp_path: Path, root: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.png").write_bytes(PNG)
    (root / "dir").symlink_to(outside, target_is_directory=True)
    fake = make_fake(len(PNG))
    assert (await upload(tmp_path, fake, root, "dir/x.png")).is_error
    assert fake.seen == []


@pytest.mark.parametrize("path", ["../secret.png", "/etc/passwd", "shots/../../secret.png"])
async def test_dotdot_and_absolute_paths_outside_root_refused(
    tmp_path: Path, root: Path, path: str
) -> None:
    (tmp_path / "secret.png").write_bytes(PNG)
    fake = make_fake(len(PNG))
    result = await upload(tmp_path, fake, root, path)
    assert result.is_error
    assert fake.seen == []


async def test_symlink_inside_root_to_file_inside_root_is_allowed(
    tmp_path: Path, root: Path
) -> None:
    (root / "alias.png").symlink_to(root / "shot.png")
    assert not (await upload(tmp_path, make_fake(len(PNG)), root, "alias.png")).is_error


async def test_without_asc_root_file_tools_are_off(tmp_path: Path) -> None:
    fake = make_fake(len(PNG))
    result = await upload(tmp_path, fake, None, "/tmp/anything.png")
    assert result.is_error
    assert "ASC_ROOT" in text_of(result)
    assert fake.seen == []


async def test_only_images_are_accepted(tmp_path: Path, root: Path) -> None:
    (root / "notes.txt").write_text("hi", encoding="utf-8")
    fake = make_fake(len(PNG))
    result = await upload(tmp_path, fake, root, "notes.txt")
    assert result.is_error
    assert ".png" in text_of(result)


async def test_apple_upload_instructions_outside_the_file_are_refused(
    tmp_path: Path, root: Path
) -> None:
    fake = make_fake(len(PNG))
    bad: list[dict[str, Any]] = [
        {"method": "PUT", "url": f"{PRESIGNED}1", "offset": 0, "length": 10_000}
    ]
    fake.on(
        "POST",
        "/v1/appScreenshots",
        one(resource("appScreenshots", "p1", uploadOperations=bad)),
        201,
    )
    result = await upload(tmp_path, fake, root, "shot.png")
    assert result.is_error
    assert not [r for r in fake.seen if r.host == "store-001.blobstore.apple.com"]


async def test_non_https_upload_url_is_refused(tmp_path: Path, root: Path) -> None:
    fake = make_fake(len(PNG))
    ops = [
        {
            "method": "PUT",
            "url": "http://store-001.blobstore.apple.com/p",
            "offset": 0,
            "length": len(PNG),
        }
    ]
    fake.on(
        "POST",
        "/v1/appScreenshots",
        one(resource("appScreenshots", "p1", uploadOperations=ops)),
        201,
    )
    result = await upload(tmp_path, fake, root, "shot.png")
    assert result.is_error
    assert "not https" in text_of(result)


# ---------------------------------------------------------------- the file is opened only once


async def test_file_swapped_for_a_symlink_while_the_form_is_open_is_not_followed(
    tmp_path: Path, root: Path
) -> None:
    """Attack prevented: swapping the file after the human said yes, so other bytes were uploaded."""
    secret = tmp_path / "secret.p8"
    secret.write_bytes(b"-----BEGIN PRIVATE KEY-----SECRETSECRET" + b"x" * 69)
    sent: list[bytes] = []
    fake = make_fake(len(PNG))

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200)

    fake.on("PUT", "/part1", record)
    fake.on("PUT", "/part2", record)
    shot = root / "shot.png"

    def swap_file_for_symlink(form_number: int) -> None:
        shot.unlink()
        shot.symlink_to(secret)  # swapped while the human reads the form

    answerer = FormAnswerer(on_form=swap_file_for_symlink)
    async with connected(tmp_path, fake, allow_writes=True, elicit=answerer, root=root) as client:
        await client.call_tool(
            "upload_screenshot", {"screenshot_set_id": "s1", "file_path": "shot.png"}
        )
    assert b"".join(sent) in (b"", PNG)
    assert b"SECRET" not in b"".join(sent)
    commit = [r for r in fake.seen if r.method == "PATCH"]
    if commit:
        assert (
            commit[0].body["data"]["attributes"]["sourceFileChecksum"]
            == hashlib.md5(PNG, usedforsecurity=False).hexdigest()
        )


def test_open_refuses_fifo_without_hanging(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.png"
    os.mkfifo(fifo)
    with pytest.raises(ToolRefusedError, match="regular file"):
        screenshots.read_checked_file(fifo, fifo.stat())


def test_open_refuses_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "t.png"
    target.write_bytes(PNG)
    link = tmp_path / "l.png"
    link.symlink_to(target)
    with pytest.raises(ToolRefusedError):
        screenshots.read_checked_file(link, target.stat())


def test_open_refuses_a_different_file_than_the_one_checked(tmp_path: Path) -> None:
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(PNG)
    b.write_bytes(PNG)
    with pytest.raises(ToolRefusedError, match="changed"):
        screenshots.read_checked_file(a, b.stat())


def test_open_refuses_oversized_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = tmp_path / "a.png"
    a.write_bytes(PNG)
    monkeypatch.setattr(screenshots, "MAX_FILE_BYTES", 10)
    with pytest.raises(ToolRefusedError, match="larger"):
        screenshots.read_checked_file(a, a.stat())


# ---------------------------------------------------------------- upload instructions from Apple


async def run_with_ops(
    tmp_path: Path, root: Path, ops: list[dict[str, Any]]
) -> tuple[Any, FakeApple]:
    fake = make_fake(len(PNG))
    fake.on(
        "POST",
        "/v1/appScreenshots",
        one(resource("appScreenshots", "p1", uploadOperations=ops)),
        201,
    )
    fake.on("PUT", "/internal", lambda _r: httpx.Response(200))
    fake.on("POST", "/internal", lambda _r: httpx.Response(200))
    return await upload(tmp_path, fake, root, "shot.png"), fake


def part(url: str, offset: int = 0, length: int = len(PNG), method: str = "PUT") -> dict[str, Any]:
    return {"method": method, "url": url, "offset": offset, "length": length, "requestHeaders": []}


GOOD = "https://store-001.blobstore.apple.com/part"


@pytest.mark.parametrize(
    "ops",
    [
        [part("https://127.0.0.1:8443/internal", method="POST")],  # an internal address
        [part("https://127.0.0.1/internal")],
        [part("https://[::1]/internal")],
        [part("https://store.apple.com.evil.example/internal")],
        [part("https://evilapple.com/internal")],
        [part("https://store-001.blobstore.apple.com:8443/internal")],
        [part("http://store-001.blobstore.apple.com/internal")],
        [part(GOOD, method="POST")],
        [part(GOOD, method="GET")],
        [part(GOOD + "1", 0, 60), part(GOOD + "2", 50, len(PNG) - 50)],  # overlap
        [part(GOOD + "1", 0, 60), part(GOOD + "2", 60, 1000)],  # out of range
        [part(GOOD + "1", 0, 60)],  # total is shorter than the file
        [part(GOOD + "1", 0, 60), part(GOOD + "2", 0, 60)],  # same range twice
        [part(GOOD + "1", -1, len(PNG))],
        [part(GOOD + "1", 0, 0)],
    ],
    ids=[
        "poc-127-post",
        "ip-v4",
        "ip-v6",
        "suffix-trick",
        "no-dot-boundary",
        "port",
        "http",
        "post",
        "get",
        "overlap",
        "out-of-range",
        "short-total",
        "duplicate",
        "negative",
        "empty",
    ],
)
async def test_bad_upload_instructions_send_no_part(
    tmp_path: Path, root: Path, ops: list[dict[str, Any]]
) -> None:
    result, fake = await run_with_ops(tmp_path, root, ops)
    assert result.is_error
    assert [r for r in fake.seen if r.host != "api.appstoreconnect.apple.com"] == []


async def test_good_instructions_with_exact_cover_are_sent(tmp_path: Path, root: Path) -> None:
    ops = [part(GOOD + "1", 0, 60), part(GOOD + "2", 60, len(PNG) - 60)]
    result, _ = await run_with_ops(tmp_path, root, ops)
    assert not result.is_error, text_of(result)


async def test_redirect_from_the_upload_host_is_a_failure(tmp_path: Path, root: Path) -> None:
    fake = make_fake(len(PNG))
    fake.on("PUT", "/part1", lambda _r: httpx.Response(302, headers={"location": "https://x/"}))
    result = await upload(tmp_path, fake, root, "shot.png")
    assert result.is_error
    assert "302" in text_of(result)
