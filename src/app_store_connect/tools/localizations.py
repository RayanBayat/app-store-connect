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
    resource_of,
    string_prop,
    summarize,
    to_text,
)
from app_store_connect.tools.confirm import change_line, confirm_change
from app_store_connect.tools.locales import valid_locale

# The one table that connects the name of a tool argument (snake_case, what the model types) to
# the name of Apple's attribute (camelCase, what goes in the request). Every update and create
# tool below reads it, so a field is spelled in exactly one place.
ARGUMENT_TO_ATTRIBUTE: Final = {
    "description": "description",
    "keywords": "keywords",
    "whats_new": "whatsNew",
    "promotional_text": "promotionalText",
    "marketing_url": "marketingUrl",
    "support_url": "supportUrl",
    "name": "name",
    "subtitle": "subtitle",
    "privacy_policy_url": "privacyPolicyUrl",
}
# The text arguments of each kind of localization.
VERSION_TEXT_ARGUMENTS: Final = (
    "description",
    "keywords",
    "whats_new",
    "promotional_text",
    "marketing_url",
    "support_url",
)
INFO_TEXT_ARGUMENTS: Final = ("name", "subtitle", "privacy_policy_url")
# When creating an app info localization the name is required, so only these two are optional.
INFO_OPTIONAL_ARGUMENTS: Final = ("subtitle", "privacy_policy_url")
# Longest allowed value per argument. Apple's OpenAPI spec has no maxLength, so these are the
# limits Apple documents in App Store Connect Help ("Platform version information" and
# "App information", checked 2026-11-03); the URL limit is our own conservative cap (Apple
# documents none). Characters, except arguments in BYTE_LIMITED_ARGUMENTS. A value over the
# limit is refused before anything is read or shown: Apple would reject it anyway.
FIELD_LIMITS: Final = {
    "description": 4000,
    "keywords": 100,
    "whats_new": 4000,
    "promotional_text": 170,
    "marketing_url": 2048,
    "support_url": 2048,
    "name": 30,
    "subtitle": 30,
    "privacy_policy_url": 2048,
}
# Apple counts keywords in bytes ("up to 100 bytes"), so å, ä and ö count as 2 each.
BYTE_LIMITED_ARGUMENTS: Final = frozenset({"keywords"})
# Shortest allowed value per argument (Help: the name "must be at least two characters").
FIELD_MINIMUMS: Final = {"name": 2}

# Apple's own attribute names, as they appear in what the read tools return.
VERSION_TEXT_FIELDS: Final = tuple(ARGUMENT_TO_ATTRIBUTE[arg] for arg in VERSION_TEXT_ARGUMENTS)
INFO_TEXT_FIELDS: Final = tuple(ARGUMENT_TO_ATTRIBUTE[arg] for arg in INFO_TEXT_ARGUMENTS)
VERSION_FIELDS: Final = ("locale", *VERSION_TEXT_FIELDS)
INFO_FIELDS: Final = ("locale", *INFO_TEXT_FIELDS)


def to_apple_attributes(values: dict[str, str]) -> dict[str, str]:
    """Rename a {tool argument: value} dict to {Apple attribute: value}."""
    return {ARGUMENT_TO_ATTRIBUTE[argument]: value for argument, value in values.items()}


def text_properties(argument_names: tuple[str, ...]) -> dict[str, Any]:
    """The tool-schema entries for text arguments, each with its length limit."""
    return {
        argument: string_prop(f"The {argument}.", maxLength=FIELD_LIMITS[argument])
        for argument in argument_names
    }


async def get_version_localizations(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Return the store text of every language of an App Store version."""
    items = await ctx.client.paginate(
        "/v1/appStoreVersions/{id}/appStoreVersionLocalizations",
        item_id=require_id(args, "version_id"),
    )
    return to_text([summarize(item, VERSION_FIELDS, wrap=VERSION_TEXT_FIELDS) for item in items])


async def get_app_info_localizations(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Return name, subtitle and privacy URL per language for each app info of an app."""
    infos = await ctx.client.paginate("/v1/apps/{id}/appInfos", item_id=require_id(args, "app_id"))
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


def check_limit(argument: str, value: str) -> None:
    """Refuse a value that is longer (or shorter) than Apple allows for this argument."""
    if argument in BYTE_LIMITED_ARGUMENTS:
        size, unit = len(value.encode("utf-8")), "bytes (letters like å, ä, ö count as 2)"
    else:
        size, unit = len(value), "characters"
    if size > FIELD_LIMITS[argument]:
        raise ToolInputError(
            f"{argument} is {size} {unit}; Apple allows at most {FIELD_LIMITS[argument]}."
        )
    minimum = FIELD_MINIMUMS.get(argument, 0)
    if len(value) < minimum:
        raise ToolInputError(
            f"{argument} is {len(value)} characters; Apple needs at least {minimum}."
        )


def requested_changes(args: dict[str, Any], argument_names: tuple[str, ...]) -> dict[str, str]:
    """Collect the text arguments the model wants to change (empty text clears a field).

    At least one is required. The result is keyed by argument name.
    """
    changes: dict[str, str] = {}
    for argument in argument_names:
        value = optional_str(args, argument)
        if value is not None:
            changes[argument] = value
    if not changes:
        raise ToolInputError(f"Give at least one of: {', '.join(argument_names)}.")
    for argument, value in changes.items():
        check_limit(argument, value)
    return changes


def given_texts(args: dict[str, Any], argument_names: tuple[str, ...]) -> dict[str, str]:
    """Collect the optional text arguments that were given (non-empty), checked against limits.

    Used when creating: an empty text is the same as not giving it. Keyed by argument name.
    """
    texts: dict[str, str] = {}
    for argument in argument_names:
        value = optional_str(args, argument)
        if value:
            check_limit(argument, value)
            texts[argument] = value
    return texts


async def update_localization(
    ctx: ToolContext,
    args: dict[str, Any],
    *,
    template: str,
    kind: str,
    argument_names: tuple[str, ...],
    title: str,
) -> str:
    """Show old -> new for each changed field, ask the human, then PATCH only those fields."""
    localization_id = require_id(args, "localization_id")
    changes = requested_changes(args, argument_names)
    current = resource_of(await ctx.client.request("GET", template, item_id=localization_id))
    old = current.get("attributes") or {}
    changed = {
        argument: value
        for argument, value in changes.items()
        if old.get(ARGUMENT_TO_ATTRIBUTE[argument]) != value
    }
    if not changed:
        return "Nothing to change: the new values are the same as the current ones."
    lines = [f"Language: {old.get('locale')}  (localization {localization_id})"]
    for argument, value in changed.items():
        lines.append(change_line(argument, old.get(ARGUMENT_TO_ATTRIBUTE[argument]), value))
    await confirm_change(ctx, title, lines)
    attributes = to_apple_attributes(changed)
    body = {"data": {"type": kind, "id": localization_id, "attributes": attributes}}
    data = await ctx.client.request("PATCH", template, item_id=localization_id, body=body)
    return "Updated: " + to_text(
        summarize(resource_of(data), tuple(attributes), wrap=tuple(attributes))
    )


async def update_version_localization(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Change the store text of one language of an App Store version."""
    return await update_localization(
        ctx,
        args,
        template="/v1/appStoreVersionLocalizations/{id}",
        kind="appStoreVersionLocalizations",
        argument_names=VERSION_TEXT_ARGUMENTS,
        title="Change the App Store text of a version",
    )


async def update_app_info_localization(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Change an app's name, subtitle or privacy policy URL for one language."""
    return await update_localization(
        ctx,
        args,
        template="/v1/appInfoLocalizations/{id}",
        kind="appInfoLocalizations",
        argument_names=INFO_TEXT_ARGUMENTS,
        title="Change the app name / subtitle / privacy URL",
    )


async def confirm_creation(
    ctx: ToolContext, title: str, context_lines: list[str], texts: dict[str, str]
) -> None:
    """Show every text that will be set (in full, escaped), and ask the human."""
    lines = [*context_lines]
    lines += [change_line(argument, None, value) for argument, value in texts.items()]
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
    return "Created: " + to_text(
        summarize(resource_of(data), tuple(attributes), wrap=tuple(attributes))
    )


async def create_version_localization(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Add a new language to an App Store version."""
    version_id = require_id(args, "version_id")
    locale = valid_locale(require_str(args, "locale"))
    texts = given_texts(args, VERSION_TEXT_ARGUMENTS)
    context = [f"Version ID: {version_id}", f"New language: {locale}"]
    await confirm_creation(ctx, "Add a language to an App Store version", context, texts)
    attributes = {"locale": locale, **to_apple_attributes(texts)}
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
    texts = {"name": name, **given_texts(args, INFO_OPTIONAL_ARGUMENTS)}
    context = [f"App info ID: {info_id}", f"New language: {locale}"]
    await confirm_creation(ctx, "Add a language to the app name / subtitle", context, texts)
    attributes = {"locale": locale, **to_apple_attributes(texts)}
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
            **text_properties(VERSION_TEXT_ARGUMENTS),
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
            **text_properties(INFO_TEXT_ARGUMENTS),
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
            **text_properties(VERSION_TEXT_ARGUMENTS),
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
            **text_properties(INFO_OPTIONAL_ARGUMENTS),
        },
        required=("app_info_id", "locale", "name"),
        handler=create_app_info_localization,
        write=True,
    ),
]
