"""Screenshots: list sets (read) and upload a file (write, confined to ASC_ROOT)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, BinaryIO, Final

from app_store_connect.client import AscError
from app_store_connect.tools.base import (
    ToolContext,
    ToolRefusedError,
    ToolSpec,
    relationship,
    require_str,
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
        item_id=require_str(args, "localization_id"),
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
                "screenshots": [summarize(s, ("fileName", "assetDeliveryState")) for s in shots],
            }
        )
    return to_text(result)


def confine_to_root(raw_path: str, root: Path | None) -> Path:
    """Return the real path of `raw_path` if, after following symlinks, it is inside `root`."""
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
    except (OSError, RuntimeError) as exc:
        raise ToolRefusedError(f"The file {raw_path!r} could not be found.") from exc
    if not resolved.is_relative_to(real_root):
        raise ToolRefusedError(f"Refused: {raw_path!r} is outside the allowed folder (ASC_ROOT).")
    if not resolved.is_file():
        raise ToolRefusedError(f"{raw_path!r} is not a regular file.")
    if resolved.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ToolRefusedError("Only .png, .jpg and .jpeg screenshots can be uploaded.")
    if resolved.stat().st_size > MAX_FILE_BYTES:
        raise ToolRefusedError("The file is larger than 50 MB.")
    return resolved


def md5_hex(handle: BinaryIO) -> str:
    """MD5 of the whole file (Apple's required checksum; not used for security)."""
    digest = hashlib.md5(usedforsecurity=False)
    handle.seek(0)
    while chunk := handle.read(HASH_CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


def check_operations(operations: Any, size: int) -> list[dict[str, Any]]:
    """Validate the upload instructions Apple returned before using them."""
    if not isinstance(operations, list) or not 0 < len(operations) <= MAX_PARTS:
        raise AscError("Apple returned unusable upload instructions.")
    for op in operations:
        offset, length = op.get("offset"), op.get("length")
        if not (isinstance(offset, int) and isinstance(length, int) and offset >= 0 and length > 0):
            raise AscError("Apple returned an invalid upload part.")
        if offset + length > size or not op.get("url") or not op.get("method"):
            raise AscError("Apple returned an upload part outside the file.")
    return operations


async def send_parts(ctx: ToolContext, handle: BinaryIO, operations: list[dict[str, Any]]) -> None:
    """Upload each part to its presigned URL (no Authorization header is ever added)."""
    for op in operations:
        handle.seek(op["offset"])
        data = handle.read(op["length"])
        headers = [(h["name"], h["value"]) for h in op.get("requestHeaders") or []]
        await ctx.client.upload_part(str(op["method"]), str(op["url"]), headers, data)


async def upload_screenshot(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Upload one screenshot into a screenshot set: reserve, upload parts, commit with MD5."""
    set_id = require_str(args, "screenshot_set_id")
    path = confine_to_root(require_str(args, "file_path"), ctx.config.root)
    size = path.stat().st_size
    shot_set = (await ctx.client.request("GET", "/v1/appScreenshotSets/{id}", item_id=set_id))[
        "data"
    ]
    display = (shot_set.get("attributes") or {}).get("screenshotDisplayType")
    lines = [f"File: {path.name} ({size} bytes)", f"Added to screenshot set: {display} ({set_id})"]
    await confirm_change(ctx, "Upload a screenshot", lines)
    reserve = {
        "data": {
            "type": "appScreenshots",
            "attributes": {"fileName": path.name, "fileSize": size},
            "relationships": {"appScreenshotSet": relationship("appScreenshotSets", set_id)},
        }
    }
    created = (await ctx.client.request("POST", "/v1/appScreenshots", body=reserve))["data"]
    operations = check_operations((created.get("attributes") or {}).get("uploadOperations"), size)
    with path.open("rb") as handle:
        await send_parts(ctx, handle, operations)
        checksum = md5_hex(handle)
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
    return "Uploaded: " + to_text(summarize(done["data"], ("fileName", "assetDeliveryState")))


READ_TOOLS = [
    ToolSpec(
        "list_screenshot_sets",
        "List the screenshot sets (per device type) of an App Store version localization, "
        "with the number of screenshots in each.",
        {"localization_id": string_prop("The version localization's ID.")},
        ("localization_id",),
        list_screenshot_sets,
    ),
]

WRITE_TOOLS = [
    ToolSpec(
        "upload_screenshot",
        "Upload a .png/.jpg screenshot from the allowed folder (ASC_ROOT) into a screenshot "
        "set. Asks the user to confirm.",
        {
            "screenshot_set_id": string_prop("The screenshot set's ID."),
            "file_path": string_prop("Path of the image, inside the allowed folder."),
        },
        ("screenshot_set_id", "file_path"),
        upload_screenshot,
        write=True,
    ),
]
