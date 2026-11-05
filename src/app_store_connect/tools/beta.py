"""TestFlight: read beta groups and testers (read), add or remove testers (write)."""

from __future__ import annotations

import re
from typing import Any, Final

from app_store_connect.textsafe import untrusted
from app_store_connect.tools.base import (
    LIMIT_PROP,
    ToolContext,
    ToolInputError,
    ToolSpec,
    limit_arg,
    optional_str,
    require_str,
    string_prop,
    summarize,
    to_text,
)
from app_store_connect.tools.confirm import confirm_change

EMAIL_PATTERN: Final = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
GROUP_FIELDS = ("name", "isInternalGroup", "publicLinkEnabled", "createdDate")
GROUP_WRAPPED = ("name",)
TESTER_WRAPPED = ("firstName", "lastName", "email")
TESTER_FIELDS = ("firstName", "lastName", "email", "inviteType", "state")


async def list_beta_groups(ctx: ToolContext, args: dict[str, Any]) -> str:
    """List an app's TestFlight beta groups."""
    items = await ctx.client.paginate(
        "/v1/apps/{id}/betaGroups", item_id=require_str(args, "app_id"), max_items=limit_arg(args)
    )
    return to_text([summarize(item, GROUP_FIELDS, wrap=GROUP_WRAPPED) for item in items])


async def list_beta_testers(ctx: ToolContext, args: dict[str, Any]) -> str:
    """List beta testers of a group or an app (one of the two is required)."""
    params: dict[str, str] = {}
    if group := optional_str(args, "beta_group_id"):
        params["filter[betaGroups]"] = group
    if app := optional_str(args, "app_id"):
        params["filter[apps]"] = app
    if not params:
        raise ToolInputError("Give beta_group_id or app_id so the list is not the whole account.")
    if email := optional_str(args, "email"):
        params["filter[email]"] = email
    items = await ctx.client.paginate("/v1/betaTesters", params=params, max_items=limit_arg(args))
    return to_text([summarize(item, TESTER_FIELDS, wrap=TESTER_WRAPPED) for item in items])


async def group_name(ctx: ToolContext, group_id: str) -> str:
    """Look up a beta group's name (shown in confirmation forms)."""
    data = await ctx.client.request("GET", "/v1/betaGroups/{id}", item_id=group_id)
    return str(data["data"]["attributes"].get("name"))


async def add_beta_tester(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Invite a tester (by email) and add them to a beta group."""
    group_id, email = require_str(args, "beta_group_id"), require_str(args, "email")
    if not EMAIL_PATTERN.fullmatch(email):
        raise ToolInputError(f"{email!r} does not look like an email address.")
    first, last = optional_str(args, "first_name"), optional_str(args, "last_name")
    name = await group_name(ctx, group_id)
    lines = [f"Tester email: {email}", f"Name: {first or ''} {last or ''}".strip(),
             f"Beta group: {name} ({group_id})"]  # fmt: skip
    await confirm_change(ctx, "Add a TestFlight tester to a beta group", lines)
    attributes = {"email": email}
    if first:
        attributes["firstName"] = first
    if last:
        attributes["lastName"] = last
    body = {
        "data": {
            "type": "betaTesters",
            "attributes": attributes,
            "relationships": {"betaGroups": {"data": [{"type": "betaGroups", "id": group_id}]}},
        }
    }
    data = await ctx.client.request("POST", "/v1/betaTesters", body=body)
    return to_text(summarize(data["data"], TESTER_FIELDS, wrap=TESTER_WRAPPED))


async def remove_beta_tester(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Remove a tester from a beta group (the tester is not deleted from the account)."""
    group_id, tester_id = require_str(args, "beta_group_id"), require_str(args, "beta_tester_id")
    name = await group_name(ctx, group_id)
    tester = await ctx.client.request("GET", "/v1/betaTesters/{id}", item_id=tester_id)
    email = tester["data"]["attributes"].get("email")
    lines = [f"Tester: {email} ({tester_id})", f"Removed from beta group: {name} ({group_id})"]
    await confirm_change(ctx, "Remove a tester from a TestFlight beta group", lines)
    body = {"data": [{"type": "betaTesters", "id": tester_id}]}
    await ctx.client.request(
        "DELETE", "/v1/betaGroups/{id}/relationships/betaTesters", item_id=group_id, body=body
    )
    return f"Removed {untrusted(str(email))} from beta group {untrusted(name)}."


READ_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="list_beta_groups",
        description="List the TestFlight beta groups of an app.",
        properties={"app_id": string_prop("The app's ID."), "limit": LIMIT_PROP},
        required=("app_id",),
        handler=list_beta_groups,
    ),
    ToolSpec(
        name="list_beta_testers",
        description="List TestFlight beta testers of a beta group or of an app.",
        properties={
            "beta_group_id": string_prop("Only testers in this beta group."),
            "app_id": string_prop("Only testers of this app."),
            "email": string_prop("Only the tester with this email."),
            "limit": LIMIT_PROP,
        },
        required=(),
        handler=list_beta_testers,
    ),
]


WRITE_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="add_beta_tester",
        description="Add a tester (by email) to a TestFlight beta group. Asks the user to confirm.",
        properties={
            "beta_group_id": string_prop("The beta group's ID."),
            "email": string_prop("The tester's email address."),
            "first_name": string_prop("Optional first name."),
            "last_name": string_prop("Optional last name."),
        },
        required=("beta_group_id", "email"),
        handler=add_beta_tester,
        write=True,
    ),
    ToolSpec(
        name="remove_beta_tester",
        description="Remove a tester from a TestFlight beta group. Asks the user to confirm.",
        properties={
            "beta_group_id": string_prop("The beta group's ID."),
            "beta_tester_id": string_prop("The beta tester's ID."),
        },
        required=("beta_group_id", "beta_tester_id"),
        handler=remove_beta_tester,
        write=True,
        destructive=True,
    ),
]
