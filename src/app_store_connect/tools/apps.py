"""Read tools for apps."""

from __future__ import annotations

from typing import Any

from app_store_connect.tools.base import (
    LIMIT_PROP,
    ToolContext,
    ToolSpec,
    limit_arg,
    optional_str,
    require_str,
    string_prop,
    summarize,
    to_text,
)

WRAPPED = ("name",)  # an app name is typed by a person
APP_FIELDS = ("name", "bundleId", "sku", "primaryLocale")


async def list_apps(ctx: ToolContext, args: dict[str, Any]) -> str:
    """List apps, optionally filtered by name or bundle ID."""
    params: dict[str, str] = {}
    if name := optional_str(args, "name"):
        params["filter[name]"] = name
    if bundle_id := optional_str(args, "bundle_id"):
        params["filter[bundleId]"] = bundle_id
    items = await ctx.client.paginate("/v1/apps", params=params, max_items=limit_arg(args))
    return to_text([summarize(item, APP_FIELDS, wrap=WRAPPED) for item in items])


async def get_app(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Return one app's details."""
    data = await ctx.client.request("GET", "/v1/apps/{id}", item_id=require_str(args, "app_id"))
    return to_text(summarize(data["data"], APP_FIELDS, wrap=WRAPPED))


READ_TOOLS = [
    ToolSpec(
        "list_apps",
        "List the apps in the App Store Connect account (id, name, bundle ID).",
        {
            "name": string_prop("Only apps with exactly this name."),
            "bundle_id": string_prop("Only the app with this bundle ID."),
            "limit": LIMIT_PROP,
        },
        (),
        list_apps,
    ),
    ToolSpec(
        "get_app",
        "Get one app's details by its App Store Connect app ID.",
        {"app_id": string_prop("The app's numeric App Store Connect ID.")},
        ("app_id",),
        get_app,
    ),
]
