"""Store text: version localizations (description, keywords, ...) and app info localizations."""

from __future__ import annotations

from typing import Any, Final

from app_store_connect.tools.base import (
    ToolContext,
    ToolInputError,
    ToolSpec,
    optional_str,
    require_str,
    string_prop,
    summarize,
    to_text,
)
from app_store_connect.tools.confirm import change_line, confirm_change

VERSION_TEXT_FIELDS: Final = (
    "description", "keywords", "whatsNew", "promotionalText", "marketingUrl", "supportUrl",
)  # fmt: skip
VERSION_FIELDS: Final = ("locale", *VERSION_TEXT_FIELDS)
INFO_TEXT_FIELDS: Final = ("name", "subtitle", "privacyPolicyUrl")
INFO_FIELDS: Final = ("locale", *INFO_TEXT_FIELDS)


async def get_version_localizations(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Return the store text of every language of an App Store version."""
    items = await ctx.client.paginate(
        "/v1/appStoreVersions/{id}/appStoreVersionLocalizations",
        item_id=require_str(args, "version_id"),
    )
    return to_text([summarize(item, VERSION_FIELDS) for item in items])


async def get_app_info_localizations(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Return name, subtitle and privacy URL per language for each app info of an app."""
    infos = await ctx.client.paginate("/v1/apps/{id}/appInfos", item_id=require_str(args, "app_id"))
    result: list[dict[str, Any]] = []
    for info in infos:
        items = await ctx.client.paginate(
            "/v1/appInfos/{id}/appInfoLocalizations", item_id=str(info["id"])
        )
        result.append(
            {
                "app_info_id": info["id"],
                "attributes": info.get("attributes") or {},
                "localizations": [summarize(item, INFO_FIELDS) for item in items],
            }
        )
    return to_text(result)


def requested_changes(args: dict[str, Any], fields: tuple[str, ...]) -> dict[str, str]:
    """Collect the text fields the model wants to change; at least one is required."""
    changes = {name: value for name in fields if (value := optional_str(args, name)) is not None}
    if not changes:
        raise ToolInputError(f"Give at least one of: {', '.join(fields)}.")
    return changes


async def update_localization(
    ctx: ToolContext,
    args: dict[str, Any],
    *,
    template: str,
    kind: str,
    fields: tuple[str, ...],
    title: str,
) -> str:
    """Show old -> new for each changed field, ask the human, then PATCH only those fields."""
    loc_id = require_str(args, "localization_id")
    changes = requested_changes(args, fields)
    current = (await ctx.client.request("GET", template, item_id=loc_id))["data"]
    old = current.get("attributes") or {}
    changed = {k: v for k, v in changes.items() if old.get(k) != v}
    if not changed:
        return "Nothing to change: the new values are the same as the current ones."
    lines = [f"Language: {old.get('locale')}  (localization {loc_id})"]
    lines += [change_line(name, old.get(name), value) for name, value in changed.items()]
    await confirm_change(ctx, title, lines)
    body = {"data": {"type": kind, "id": loc_id, "attributes": changed}}
    data = await ctx.client.request("PATCH", template, item_id=loc_id, body=body)
    return "Updated: " + to_text(summarize(data["data"], tuple(changed)))


async def update_version_localization(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Change the store text of one language of an App Store version."""
    return await update_localization(
        ctx,
        args,
        template="/v1/appStoreVersionLocalizations/{id}",
        kind="appStoreVersionLocalizations",
        fields=VERSION_TEXT_FIELDS,
        title="Change the App Store text of a version",
    )


async def update_app_info_localization(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Change an app's name, subtitle or privacy policy URL for one language."""
    return await update_localization(
        ctx,
        args,
        template="/v1/appInfoLocalizations/{id}",
        kind="appInfoLocalizations",
        fields=INFO_TEXT_FIELDS,
        title="Change the app name / subtitle / privacy URL",
    )


READ_TOOLS = [
    ToolSpec(
        "get_version_localizations",
        "Get the App Store text (description, keywords, what's new, promotional text, URLs) "
        "for every language of an App Store version.",
        {"version_id": string_prop("The App Store version's ID.")},
        ("version_id",),
        get_version_localizations,
    ),
    ToolSpec(
        "get_app_info_localizations",
        "Get the app name, subtitle and privacy policy URL for every language of an app.",
        {"app_id": string_prop("The app's ID.")},
        ("app_id",),
        get_app_info_localizations,
    ),
]

WRITE_TOOLS = [
    ToolSpec(
        "update_version_localization",
        "Change store text of one language of an App Store version (use "
        "get_version_localizations to find the localization ID). Asks the user to confirm.",
        {
            "localization_id": string_prop("The version localization's ID."),
            **{name: string_prop(f"New {name}.") for name in VERSION_TEXT_FIELDS},
        },
        ("localization_id",),
        update_version_localization,
        write=True,
    ),
    ToolSpec(
        "update_app_info_localization",
        "Change the app name, subtitle or privacy policy URL of one language (use "
        "get_app_info_localizations to find the ID). Asks the user to confirm.",
        {
            "localization_id": string_prop("The app info localization's ID."),
            **{name: string_prop(f"New {name}.") for name in INFO_TEXT_FIELDS},
        },
        ("localization_id",),
        update_app_info_localization,
        write=True,
    ),
]
