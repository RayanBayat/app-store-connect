"""Builds uploaded from Xcode or Transporter: read them (read), send one to TestFlight (write)."""

from __future__ import annotations

from typing import Any, Final

from app_store_connect.textsafe import untrusted
from app_store_connect.tools.base import (
    LIMIT_PROP,
    ToolContext,
    ToolSpec,
    attributes_of,
    limit_arg,
    optional_str,
    relationship,
    require_id,
    resource_of,
    string_prop,
    summarize,
    to_text,
)
from app_store_connect.tools.confirm import confirm_change
from app_store_connect.tools.lookups import group_name

BUILD_FIELDS: Final = (
    "version",
    "uploadedDate",
    "expirationDate",
    "expired",
    "processingState",
    "minOsVersion",
)


async def list_builds(ctx: ToolContext, args: dict[str, Any]) -> str:
    """List builds, filtered by app, version and/or processing state."""
    params = {"sort": "-uploadedDate"}
    if app_id := optional_str(args, "app_id"):
        params["filter[app]"] = app_id
    if version := optional_str(args, "version"):
        params["filter[preReleaseVersion.version]"] = version
    if build_number := optional_str(args, "build_number"):
        params["filter[version]"] = build_number
    if state := optional_str(args, "processing_state"):
        params["filter[processingState]"] = state
    items = await ctx.client.paginate("/v1/builds", params=params, max_items=limit_arg(args))
    return to_text([summarize(item, BUILD_FIELDS) for item in items])


async def get_build(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Return one build's details."""
    data = await ctx.client.request("GET", "/v1/builds/{id}", item_id=require_id(args, "build_id"))
    return to_text(summarize(resource_of(data), BUILD_FIELDS))


async def build_label(ctx: ToolContext, build_id: str) -> str:
    """Describe a build for confirmation forms, e.g. 'build number 42 (ID 123)'."""
    data = await ctx.client.request("GET", "/v1/builds/{id}", item_id=build_id)
    return f"build number {attributes_of(resource_of(data)).get('version')} (ID {build_id})"


async def add_build_to_beta_group(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Make a build available to a TestFlight beta group."""
    group_id, build_id = require_id(args, "beta_group_id"), require_id(args, "build_id")
    name, label = await group_name(ctx, group_id), await build_label(ctx, build_id)
    lines = [f"Build: {label}", f"Added to beta group: {name} ({group_id})"]
    await confirm_change(ctx, "Add a build to a TestFlight beta group", lines)
    body = {"data": [{"type": "builds", "id": build_id}]}
    await ctx.client.request(
        "POST", "/v1/betaGroups/{id}/relationships/builds", item_id=group_id, body=body
    )
    return f"Added {label} to beta group {untrusted(name)}."


async def submit_build_for_beta_review(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Send a build to Apple's TestFlight beta app review (needed for external testers)."""
    build_id = require_id(args, "build_id")
    label = await build_label(ctx, build_id)
    lines = [f"Build: {label}", "Action: submit to Apple for TestFlight beta app review"]
    await confirm_change(ctx, "Submit a build for TestFlight beta review", lines)
    body = {
        "data": {
            "type": "betaAppReviewSubmissions",
            "relationships": {"build": relationship("builds", build_id)},
        }
    }
    data = await ctx.client.request("POST", "/v1/betaAppReviewSubmissions", body=body)
    return f"Submitted {label} for beta review (submission {resource_of(data).get('id')})."


READ_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="list_builds",
        description="List builds (newest first). Filter by app, marketing version, build number or "
        "processing state (PROCESSING, FAILED, INVALID, VALID).",
        properties={
            "app_id": string_prop("Only builds of this app."),
            "version": string_prop("Marketing version, e.g. 1.2.0 (needs app_id)."),
            "build_number": string_prop("Build number, e.g. 42."),
            "processing_state": string_prop("PROCESSING, FAILED, INVALID or VALID."),
            "limit": LIMIT_PROP,
        },
        required=(),
        handler=list_builds,
    ),
    ToolSpec(
        name="get_build",
        description="Get one build's details (processing state, expiry) by its build ID.",
        properties={"build_id": string_prop("The build's ID.")},
        required=("build_id",),
        handler=get_build,
    ),
]


WRITE_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="add_build_to_beta_group",
        description="Make a build available to a TestFlight beta group. Asks the user to confirm.",
        properties={
            "beta_group_id": string_prop("The beta group's ID."),
            "build_id": string_prop("The build's ID."),
        },
        required=("beta_group_id", "build_id"),
        handler=add_build_to_beta_group,
        write=True,
    ),
    ToolSpec(
        name="submit_build_for_beta_review",
        description=(
            "Submit a build to Apple's TestFlight beta app review. Asks the user to confirm."
        ),
        properties={"build_id": string_prop("The build's ID.")},
        required=("build_id",),
        handler=submit_build_for_beta_review,
        write=True,
    ),
]
