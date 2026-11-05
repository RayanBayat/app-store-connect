"""Store text: version localizations (description, keywords, ...) and app info localizations."""

from __future__ import annotations

from typing import Any, Final

from app_store_connect.tools.base import (
    ToolContext,
    ToolInputError,
    ToolSpec,
    optional_str,
    relationship,
    require_id,
    require_str,
    string_prop,
    summarize,
    to_text,
)
from app_store_connect.tools.confirm import change_line, confirm_change
from app_store_connect.tools.locales import valid_locale

VERSION_TEXT_FIELDS: Final = (
    "description", "keywords", "whatsNew", "promotionalText", "marketingUrl", "supportUrl",
)  # fmt: skip
# Longest allowed value per field. Apple's OpenAPI spec has no maxLength, so these are the
# limits Apple documents in App Store Connect Help ("Platform version information" and
# "App information", checked 2026-11-03); the URL limit is our own conservative cap (Apple
# documents none). Characters, except fields in BYTE_LIMITED_FIELDS. A value over the limit
# is refused before anything is read or shown: Apple would reject it anyway.
FIELD_LIMITS: Final = {
    "description": 4000,
    "keywords": 100,
    "whatsNew": 4000,
    "promotionalText": 170,
    "marketingUrl": 2048,
    "supportUrl": 2048,
    "name": 30,
    "subtitle": 30,
    "privacyPolicyUrl": 2048,
}
# Apple counts keywords in bytes ("up to 100 bytes"), so å, ä and ö count as 2 each.
BYTE_LIMITED_FIELDS: Final = frozenset({"keywords"})
# Shortest allowed value per field (Help: the name "must be at least two characters").
FIELD_MINIMUMS: Final = {"name": 2}
VERSION_FIELDS: Final = ("locale", *VERSION_TEXT_FIELDS)
INFO_TEXT_FIELDS: Final = ("name", "subtitle", "privacyPolicyUrl")
INFO_FIELDS: Final = ("locale", *INFO_TEXT_FIELDS)


async def get_version_localizations(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Return the store text of every language of an App Store version."""
    items = await ctx.client.paginate(
        "/v1/appStoreVersions/{id}/appStoreVersionLocalizations",
        item_id=require_str(args, "version_id"),
    )
    return to_text([summarize(item, VERSION_FIELDS, wrap=VERSION_TEXT_FIELDS) for item in items])


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
                # Apple's own values (state, age ratings): nobody types them, so no wrapping.
                "attributes": info.get("attributes") or {},
                "localizations": [
                    summarize(item, INFO_FIELDS, wrap=INFO_TEXT_FIELDS) for item in items
                ],
            }
        )
    return to_text(result)


def check_limit(name: str, value: str) -> None:
    """Refuse a value that is longer (or shorter) than Apple allows for `name`."""
    if name in BYTE_LIMITED_FIELDS:
        size, unit = len(value.encode("utf-8")), "bytes (letters like å, ä, ö count as 2)"
    else:
        size, unit = len(value), "characters"
    if size > FIELD_LIMITS[name]:
        raise ToolInputError(f"{name} is {size} {unit}; Apple allows at most {FIELD_LIMITS[name]}.")
    minimum = FIELD_MINIMUMS.get(name, 0)
    if len(value) < minimum:
        raise ToolInputError(f"{name} is {len(value)} characters; Apple needs at least {minimum}.")


def requested_changes(args: dict[str, Any], fields: tuple[str, ...]) -> dict[str, str]:
    """Collect the text fields the model wants to change; at least one is required."""
    changes = {name: value for name in fields if (value := optional_str(args, name)) is not None}
    if not changes:
        raise ToolInputError(f"Give at least one of: {', '.join(fields)}.")
    for name, value in changes.items():
        check_limit(name, value)
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
    return "Updated: " + to_text(summarize(data["data"], tuple(changed), wrap=tuple(changed)))


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


VERSION_CREATE_ARGS: Final = {
    "description": "description",
    "keywords": "keywords",
    "whats_new": "whatsNew",
    "promotional_text": "promotionalText",
    "marketing_url": "marketingUrl",
    "support_url": "supportUrl",
}  # tool argument -> Apple attribute
INFO_CREATE_ARGS: Final = {
    "subtitle": "subtitle",
    "privacy_policy_url": "privacyPolicyUrl",
}


def collect_attributes(args: dict[str, Any], mapping: dict[str, str]) -> dict[str, str]:
    """Collect the optional text arguments that were given (non-empty), checked against limits."""
    attributes: dict[str, str] = {}
    for arg, attribute in mapping.items():
        value = optional_str(args, arg)
        if value:
            check_limit(attribute, value)
            attributes[attribute] = value
    return attributes


async def confirm_creation(
    ctx: ToolContext, title: str, context_lines: list[str], attributes: dict[str, str]
) -> None:
    """Show every field that will be set (in full, escaped), and ask the human."""
    lines = [*context_lines]
    lines += [change_line(n, None, v) for n, v in attributes.items() if n != "locale"]
    await confirm_change(ctx, title, lines)


def creation_body(
    kind: str, attributes: dict[str, str], parent: tuple[str, str, str]
) -> dict[str, Any]:
    """The JSON:API body for creating a localization under its parent."""
    relation, parent_kind, parent_id = parent
    return {
        "data": {
            "type": kind,
            "attributes": attributes,
            "relationships": {relation: relationship(parent_kind, parent_id)},
        }
    }


def created_text(data: dict[str, Any], attributes: dict[str, str]) -> str:
    """The result shown to the model: what Apple created (text fields marked untrusted)."""
    return "Created: " + to_text(summarize(data["data"], tuple(attributes), wrap=tuple(attributes)))


async def create_version_localization(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Add a new language to an App Store version."""
    version_id = require_id(args, "version_id")
    locale = valid_locale(require_str(args, "locale"))
    attributes = {"locale": locale, **collect_attributes(args, VERSION_CREATE_ARGS)}
    context = [f"Version ID: {version_id}", f"New language: {locale}"]
    await confirm_creation(ctx, "Add a language to an App Store version", context, attributes)
    parent = ("appStoreVersion", "appStoreVersions", version_id)
    body = creation_body("appStoreVersionLocalizations", attributes, parent)
    data = await ctx.client.request("POST", "/v1/appStoreVersionLocalizations", body=body)
    return created_text(data, attributes)


async def create_app_info_localization(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Add a new language (name, subtitle, privacy URL) to an app's info."""
    info_id = require_id(args, "app_info_id")
    locale = valid_locale(require_str(args, "locale"))
    name = require_str(args, "name")
    check_limit("name", name)
    attributes = {"locale": locale, "name": name, **collect_attributes(args, INFO_CREATE_ARGS)}
    context = [f"App info ID: {info_id}", f"New language: {locale}"]
    await confirm_creation(ctx, "Add a language to the app name / subtitle", context, attributes)
    body = creation_body("appInfoLocalizations", attributes, ("appInfo", "appInfos", info_id))
    data = await ctx.client.request("POST", "/v1/appInfoLocalizations", body=body)
    return created_text(data, attributes)


READ_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="get_version_localizations",
        description=(
            "Get the App Store text (description, keywords, what's new, promotional text, URLs) "
            "for every language of an App Store version."
        ),
        properties={"version_id": string_prop("The App Store version's ID.")},
        required=("version_id",),
        handler=get_version_localizations,
    ),
    ToolSpec(
        name="get_app_info_localizations",
        description=(
            "Get the app name, subtitle and privacy policy URL for every language of an app."
        ),
        properties={"app_id": string_prop("The app's ID.")},
        required=("app_id",),
        handler=get_app_info_localizations,
    ),
]


WRITE_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="update_version_localization",
        description="Change store text of one language of an App Store version (use "
        "get_version_localizations to find the localization ID). Asks the user to confirm.",
        properties={
            "localization_id": string_prop("The version localization's ID."),
            **{
                name: string_prop(f"New {name}.", maxLength=FIELD_LIMITS[name])
                for name in VERSION_TEXT_FIELDS
            },
        },
        required=("localization_id",),
        handler=update_version_localization,
        write=True,
    ),
    ToolSpec(
        name="update_app_info_localization",
        description="Change the app name, subtitle or privacy policy URL of one language (use "
        "get_app_info_localizations to find the ID). Asks the user to confirm.",
        properties={
            "localization_id": string_prop("The app info localization's ID."),
            **{
                name: string_prop(f"New {name}.", maxLength=FIELD_LIMITS[name])
                for name in INFO_TEXT_FIELDS
            },
        },
        required=("localization_id",),
        handler=update_app_info_localization,
        write=True,
    ),
    ToolSpec(
        name="create_version_localization",
        description=(
            "Add a new language to an App Store version (POST). Give a locale such as 'sv' or "
            "'en-US' and any of the texts. Asks the user to confirm."
        ),
        properties={
            "version_id": string_prop("The App Store version's ID."),
            "locale": string_prop("App Store locale code, e.g. sv, en-US, zh-Hans."),
            **{
                arg: string_prop(f"The {attr}.", maxLength=FIELD_LIMITS[attr])
                for arg, attr in VERSION_CREATE_ARGS.items()
            },
        },
        required=("version_id", "locale"),
        handler=create_version_localization,
        write=True,
    ),
    ToolSpec(
        name="create_app_info_localization",
        description=(
            "Add a new language to an app's info (name, subtitle, privacy policy URL). Apple "
            "requires a name. Asks the user to confirm."
        ),
        properties={
            "app_info_id": string_prop("The app info's ID (see get_app_info_localizations)."),
            "locale": string_prop("App Store locale code, e.g. sv, en-US, zh-Hans."),
            "name": string_prop("The app name in that language.", maxLength=FIELD_LIMITS["name"]),
            **{
                arg: string_prop(f"The {attr}.", maxLength=FIELD_LIMITS[attr])
                for arg, attr in INFO_CREATE_ARGS.items()
            },
        },
        required=("app_info_id", "locale", "name"),
        handler=create_app_info_localization,
        write=True,
    ),
]
