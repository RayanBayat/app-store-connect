"""App Store versions (the releases shown on the store): read, create a draft, pick a build."""

from __future__ import annotations

from typing import Any, Final

from app_store_connect.tools.base import (
    LIMIT_PROP,
    PLATFORMS,
    ToolContext,
    ToolSpec,
    attributes_of,
    choice,
    limit_arg,
    optional_choice,
    optional_str,
    relationship,
    require_id,
    require_str,
    resource_of,
    string_prop,
    summarize,
    to_text,
)
from app_store_connect.tools.confirm import change_line, confirm_change

RELEASE_TYPES: Final = ("MANUAL", "AFTER_APPROVAL", "SCHEDULED")
# appVersionState replaces the deprecated appStoreState in Apple's current spec.
VERSION_FIELDS: Final = (
    "versionString",
    "platform",
    "appVersionState",
    "releaseType",
    "createdDate",
)


async def list_app_store_versions(ctx: ToolContext, args: dict[str, Any]) -> str:
    """List an app's store versions, optionally only those in the given state(s)."""
    params: dict[str, str] = {}
    if state := optional_str(args, "state"):
        params["filter[appVersionState]"] = state
    if platform := optional_str(args, "platform"):
        params["filter[platform]"] = platform
    items = await ctx.client.paginate(
        "/v1/apps/{id}/appStoreVersions",
        item_id=require_id(args, "app_id"),
        params=params,
        max_items=limit_arg(args),
    )
    return to_text([summarize(item, VERSION_FIELDS) for item in items])


async def get_version_status(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Return the state of one store version (for example READY_FOR_REVIEW or IN_REVIEW)."""
    data = await ctx.client.request(
        "GET", "/v1/appStoreVersions/{id}", item_id=require_id(args, "version_id")
    )
    return to_text(summarize(resource_of(data), VERSION_FIELDS))


async def create_app_store_version(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Create a new App Store version (a draft; nothing is submitted)."""
    app_id = require_id(args, "app_id")
    version_string = require_str(args, "version_string")
    platform = choice(args, "platform", PLATFORMS, "IOS")
    release_type = optional_choice(args, "release_type", RELEASE_TYPES)
    app = await ctx.client.request("GET", "/v1/apps/{id}", item_id=app_id)
    lines = [
        f"App: {attributes_of(resource_of(app)).get('name')} ({app_id})",
        change_line("Version", None, version_string),
        f"Platform: {platform}",
        f"Release type: {release_type or '(Apple default)'}",
    ]
    await confirm_change(ctx, "Create a new App Store version (draft)", lines)
    attributes: dict[str, Any] = {"versionString": version_string, "platform": platform}
    if release_type:
        attributes["releaseType"] = release_type
    body = {
        "data": {
            "type": "appStoreVersions",
            "attributes": attributes,
            "relationships": {"app": relationship("apps", app_id)},
        }
    }
    data = await ctx.client.request("POST", "/v1/appStoreVersions", body=body)
    return to_text(summarize(resource_of(data), VERSION_FIELDS))


async def select_build_for_version(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Attach a processed build to an App Store version."""
    version_id, build_id = require_id(args, "version_id"), require_id(args, "build_id")
    current = await ctx.client.request(
        "GET", "/v1/appStoreVersions/{id}/relationships/build", item_id=version_id
    )
    build = await ctx.client.request("GET", "/v1/builds/{id}", item_id=build_id)
    old_id = (current.get("data") or {}).get("id")
    number = attributes_of(resource_of(build)).get("version")
    lines = [
        f"Version ID: {version_id}",
        change_line("Build", old_id, f"{build_id} (build number {number})"),
    ]
    await confirm_change(ctx, "Select the build for an App Store version", lines)
    body = relationship("builds", build_id)
    await ctx.client.request(
        "PATCH", "/v1/appStoreVersions/{id}/relationships/build", item_id=version_id, body=body
    )
    return f"Build {build_id} (build number {number}) is now selected for version {version_id}."


READ_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="list_app_store_versions",
        description=(
            "List an app's App Store versions with their state (e.g. PREPARE_FOR_SUBMISSION, "
            "WAITING_FOR_REVIEW, IN_REVIEW, REJECTED, READY_FOR_DISTRIBUTION)."
        ),
        properties={
            "app_id": string_prop("The app's ID."),
            "state": string_prop(
                "Optional appVersionState filter; several values separated by commas."
            ),
            "platform": string_prop("Optional: IOS, MAC_OS, TV_OS or VISION_OS."),
            "limit": LIMIT_PROP,
        },
        required=("app_id",),
        handler=list_app_store_versions,
    ),
    ToolSpec(
        name="get_version_status",
        description="Get the current state of one App Store version by its version ID.",
        properties={"version_id": string_prop("The App Store version's ID.")},
        required=("version_id",),
        handler=get_version_status,
    ),
]


WRITE_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="create_app_store_version",
        description="Create a new draft App Store version for an app. Asks the user to confirm.",
        properties={
            "app_id": string_prop("The app's ID."),
            "version_string": string_prop("The new version, e.g. 1.3.0."),
            "platform": string_prop("IOS (default), MAC_OS, TV_OS or VISION_OS."),
            "release_type": string_prop("Optional: MANUAL, AFTER_APPROVAL or SCHEDULED."),
        },
        required=("app_id", "version_string"),
        handler=create_app_store_version,
        write=True,
    ),
    ToolSpec(
        name="select_build_for_version",
        description=(
            "Choose which processed build an App Store version uses. Asks the user to confirm."
        ),
        properties={
            "version_id": string_prop("The App Store version's ID."),
            "build_id": string_prop("The build's ID (processing state VALID)."),
        },
        required=("version_id", "build_id"),
        handler=select_build_for_version,
        write=True,
    ),
]
