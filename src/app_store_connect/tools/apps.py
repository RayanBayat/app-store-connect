"""Apps: list and read apps (read), and change the primary language (write)."""

from __future__ import annotations

from typing import Any, Final

from app_store_connect.tools.base import (
    LIMIT_PROP,
    ToolContext,
    ToolRefusedError,
    ToolSpec,
    limit_arg,
    optional_str,
    require_id,
    require_str,
    resource_of,
    string_prop,
    summarize,
    to_text,
)
from app_store_connect.tools.confirm import change_line, confirm_change
from app_store_connect.tools.locales import valid_locale

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
    data = await ctx.client.request("GET", "/v1/apps/{id}", item_id=require_id(args, "app_id"))
    return to_text(summarize(resource_of(data), APP_FIELDS, wrap=WRAPPED))


async def has_info_localization(ctx: ToolContext, app_id: str, locale: str) -> bool:
    """True when any app info of the app already has a localization in `locale`."""
    infos = await ctx.client.paginate("/v1/apps/{id}/appInfos", item_id=app_id)
    for info in infos:
        items = await ctx.client.paginate(
            "/v1/appInfos/{id}/appInfoLocalizations", item_id=str(info["id"])
        )
        if any((item.get("attributes") or {}).get("locale") == locale for item in items):
            return True
    return False


async def update_app_primary_locale(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Change the app's primary language (PATCH /v1/apps/{id}, only `primaryLocale`)."""
    app_id = require_id(args, "app_id")
    locale = valid_locale(require_str(args, "locale"))
    app = resource_of(await ctx.client.request("GET", "/v1/apps/{id}", item_id=app_id))
    attributes = app.get("attributes") or {}
    old = attributes.get("primaryLocale")
    if old == locale:
        return f"Nothing to change: the primary language is already {locale}."
    if not await has_info_localization(ctx, app_id, locale):
        raise ToolRefusedError(
            f"The app has no app info localization in {locale} yet. Create one first with "
            "create_app_info_localization, then change the primary language."
        )
    lines = [
        f"App: {attributes.get('name')} ({app_id})",
        change_line("Primary language", old, locale),
        "The primary language is what customers see when their own language isn't available.",
    ]
    await confirm_change(ctx, "Change the app's primary language", lines)
    body = {"data": {"type": "apps", "id": app_id, "attributes": {"primaryLocale": locale}}}
    await ctx.client.request("PATCH", "/v1/apps/{id}", item_id=app_id, body=body)
    return f"The primary language is now {locale} (it was {old})."


READ_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="list_apps",
        description="List the apps in the App Store Connect account (id, name, bundle ID).",
        properties={
            "name": string_prop("Only apps with exactly this name."),
            "bundle_id": string_prop("Only the app with this bundle ID."),
            "limit": LIMIT_PROP,
        },
        required=(),
        handler=list_apps,
    ),
    ToolSpec(
        name="get_app",
        description="Get one app's details by its App Store Connect app ID.",
        properties={"app_id": string_prop("The app's numeric App Store Connect ID.")},
        required=("app_id",),
        handler=get_app,
    ),
]


WRITE_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="update_app_primary_locale",
        description="Change the app's primary language. The app must already have an app info "
        "localization in that language. Asks the user to confirm.",
        properties={
            "app_id": string_prop("The app's ID."),
            "locale": string_prop("App Store locale code, e.g. sv, en-US."),
        },
        required=("app_id", "locale"),
        handler=update_app_primary_locale,
        write=True,
    ),
]
