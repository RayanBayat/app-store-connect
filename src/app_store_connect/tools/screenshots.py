"""Screenshots: list sets (read) and upload a file (write, confined to ASC_ROOT)."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any, Final

from app_store_connect.client import AscError, check_upload_target
from app_store_connect.tools.base import (
    ToolContext,
    ToolRefusedError,
    ToolSpec,
    relationship,
    require_id,
    require_str,
    resource_of,
    string_prop,
    summarize,
    to_text,
)
from app_store_connect.tools.confirm import confirm_change

ALLOWED_SUFFIXES: Final = (".png", ".jpg", ".jpeg")
MAX_FILE_BYTES: Final = 50 * 1024 * 1024
MAX_PARTS: Final = 100
HASH_CHUNK: Final = 1024 * 1024
MAX_SETS: Final = 50


async def list_screenshot_sets(ctx: ToolContext, args: dict[str, Any]) -> str:
    """List screenshot sets of a version localization, with how many screenshots each has."""
    sets = await ctx.client.paginate(
        "/v1/appStoreVersionLocalizations/{id}/appScreenshotSets",
        item_id=require_id(args, "localization_id"),
        max_items=MAX_SETS,
    )
    result: list[dict[str, Any]] = []
    for item in sets:
        shots = await ctx.client.paginate(
            "/v1/appScreenshotSets/{id}/appScreenshots", item_id=str(item["id"])
        )
        result.append(
            {
                **summarize(item, ("screenshotDisplayType",)),
                "screenshot_count": len(shots),
                "screenshots": [
                    summarize(s, ("fileName", "assetDeliveryState"), wrap=("fileName",))
                    for s in shots
                ],
            }
        )
    return to_text(result)


def confine_to_root(raw_path: str, root: Path | None) -> tuple[Path, os.stat_result]:
    """Return the real path of `raw_path` and its stat, if it is inside `root` after symlinks."""
    if root is None:
        raise ToolRefusedError(
            "File access is off. Set ASC_ROOT to the folder that holds your screenshots."
        )
    try:
        real_root = root.expanduser().resolve(strict=True)
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = real_root / candidate
        resolved = candidate.resolve(strict=True)  # follows every symlink
        info = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise ToolRefusedError(f"The file {raw_path!r} could not be found.") from exc
    if not resolved.is_relative_to(real_root):
        raise ToolRefusedError(f"Refused: {raw_path!r} is outside the allowed folder (ASC_ROOT).")
    if not stat.S_ISREG(info.st_mode):
        raise ToolRefusedError(f"{raw_path!r} is not a regular file.")
    if resolved.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ToolRefusedError("Only .png, .jpg and .jpeg screenshots can be uploaded.")
    if info.st_size > MAX_FILE_BYTES:
        raise ToolRefusedError("The file is larger than 50 MB.")
    return resolved, info


def read_checked_file(path: Path, checked: os.stat_result) -> bytes:
    """Open `path` exactly once, verify the open file, and read all its bytes from it.

    Why: checking a path and opening it later lets the file be swapped in between (for a
    symlink to a secret). So we open with O_NOFOLLOW (a symlink is refused) and O_NONBLOCK
    (a FIFO cannot hang the server), then check the *descriptor*: a regular file, within the
    size limit, and the very same file (device and inode) that the path check saw. Everything
    we later hash and upload is these bytes, read once, so what the human approves is
    exactly what is sent.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ToolRefusedError(f"The file {path.name!r} could not be opened safely.") from exc
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ToolRefusedError(f"{path.name!r} is not a regular file.")
        if (info.st_dev, info.st_ino) != (checked.st_dev, checked.st_ino):
            raise ToolRefusedError(f"{path.name!r} changed while it was being checked; refused.")
        if info.st_size > MAX_FILE_BYTES:
            raise ToolRefusedError("The file is larger than 50 MB.")
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) != info.st_size:
        raise ToolRefusedError(f"{path.name!r} changed while it was being read; refused.")
    return data


def md5_hex(data: bytes) -> str:
    """MD5 of the whole file (Apple's required checksum; not used for security)."""
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def check_operations(operations: Any, size: int) -> list[dict[str, Any]]:
    """Validate the upload instructions Apple returned before anything is sent.

    Every part must be a PUT to an Apple upload host, lie inside the file, and not overlap
    another part; together the parts must add up to exactly the file size.
    """
    if not isinstance(operations, list) or not 0 < len(operations) <= MAX_PARTS:
        raise AscError("Apple returned unusable upload instructions.")
    spans: list[tuple[int, int]] = []
    for op in operations:
        if not isinstance(op, dict):
            raise AscError("Apple returned an invalid upload part.")
        offset, length = op.get("offset"), op.get("length")
        if (
            isinstance(offset, bool)
            or isinstance(length, bool)
            or not isinstance(offset, int)
            or not isinstance(length, int)
        ):
            raise AscError("Apple returned an invalid upload part.")
        if offset < 0 or length <= 0 or offset + length > size:
            raise AscError("Apple returned an upload part outside the file.")
        if not isinstance(op.get("url"), str) or not isinstance(op.get("method"), str):
            raise AscError("Apple returned an invalid upload part.")
        check_upload_target(op["method"], op["url"])
        _check_headers(op.get("requestHeaders"))
        spans.append((offset, offset + length))
    spans.sort()
    if any(spans[i][1] > spans[i + 1][0] for i in range(len(spans) - 1)):
        raise AscError("Apple returned overlapping upload parts.")
    if sum(end - start for start, end in spans) != size:
        raise AscError("Apple's upload parts do not add up to the file size.")
    return operations


def _check_headers(headers: Any) -> None:
    """Request headers must be a list of {name, value} strings (or absent)."""
    if headers is None:
        return
    ok = isinstance(headers, list) and all(
        isinstance(h, dict) and isinstance(h.get("name"), str) and isinstance(h.get("value"), str)
        for h in headers
    )
    if not ok:
        raise AscError("Apple returned invalid upload headers.")


async def send_parts(ctx: ToolContext, data: bytes, operations: list[dict[str, Any]]) -> None:
    """Upload each part to its presigned URL (no Authorization header is ever added)."""
    for op in operations:
        chunk = data[op["offset"] : op["offset"] + op["length"]]
        headers = [(h["name"], h["value"]) for h in op.get("requestHeaders") or []]
        await ctx.client.upload_part(str(op["method"]), str(op["url"]), headers, chunk)


async def upload_screenshot(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Upload one screenshot into a screenshot set: reserve, upload parts, commit with MD5."""
    set_id = require_id(args, "screenshot_set_id")
    path, checked = confine_to_root(require_str(args, "file_path"), ctx.config.root)
    data = read_checked_file(path, checked)
    size, checksum = len(data), md5_hex(data)
    shot_set = (await ctx.client.request("GET", "/v1/appScreenshotSets/{id}", item_id=set_id))[
        "data"
    ]
    display = (shot_set.get("attributes") or {}).get("screenshotDisplayType")
    lines = [
        f"File: {path.name} ({size} bytes, MD5 {checksum})",
        f"Added to screenshot set: {display} ({set_id})",
    ]
    await confirm_change(ctx, "Upload a screenshot", lines)
    reserve = {
        "data": {
            "type": "appScreenshots",
            "attributes": {"fileName": path.name, "fileSize": size},
            "relationships": {"appScreenshotSet": relationship("appScreenshotSets", set_id)},
        }
    }
    created = resource_of(await ctx.client.request("POST", "/v1/appScreenshots", body=reserve))
    operations = check_operations((created.get("attributes") or {}).get("uploadOperations"), size)
    await send_parts(ctx, data, operations)
    commit = {
        "data": {
            "type": "appScreenshots",
            "id": created["id"],
            "attributes": {"uploaded": True, "sourceFileChecksum": checksum},
        }
    }
    done = await ctx.client.request(
        "PATCH", "/v1/appScreenshots/{id}", item_id=str(created["id"]), body=commit
    )
    return "Uploaded: " + to_text(
        summarize(resource_of(done), ("fileName", "assetDeliveryState"), wrap=("fileName",))
    )


READ_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="list_screenshot_sets",
        description=(
            "List the screenshot sets (per device type) of an App Store version localization, "
            "with the number of screenshots in each."
        ),
        properties={"localization_id": string_prop("The version localization's ID.")},
        required=("localization_id",),
        handler=list_screenshot_sets,
    ),
]


WRITE_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="upload_screenshot",
        description=(
            "Upload a .png/.jpg screenshot from the allowed folder (ASC_ROOT) into a screenshot "
            "set. Asks the user to confirm."
        ),
        properties={
            "screenshot_set_id": string_prop("The screenshot set's ID."),
            "file_path": string_prop("Path of the image, inside the allowed folder."),
        },
        required=("screenshot_set_id", "file_path"),
        handler=upload_screenshot,
        write=True,
    ),
]
