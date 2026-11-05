"""App Review: Apple's check of an app version before it can go on the App Store.

Not to be confused with customer reviews (see `customer_reviews.py`) or the monthly
maintenance record (see `maintenance_record.py`). Submitting is public and cannot be undone.
"""

from __future__ import annotations

from typing import Any, Final

from app_store_connect.client import AscError
from app_store_connect.tools.base import (
    MAX_LIMIT,
    PLATFORMS,
    ToolContext,
    ToolRefusedError,
    ToolSpec,
    attributes_of,
    choice,
    optional_str,
    relationship,
    require_id,
    resource_of,
    string_prop,
    summarize,
    to_text,
)
from app_store_connect.tools.confirm import confirm_change


async def get_review_submissions(ctx: ToolContext, args: dict[str, Any]) -> str:
    """List an app's App Review submissions with their status."""
    params: dict[str, str] = {}
    if state := optional_str(args, "state"):
        params["filter[state]"] = state
    items = await ctx.client.paginate(
        "/v1/apps/{id}/reviewSubmissions", item_id=require_id(args, "app_id"), params=params
    )
    return to_text([summarize(item, ("platform", "state", "submittedDate")) for item in items])


async def submit_for_app_review(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Submit an App Store version to Apple App Review (three API steps, one confirmation)."""
    app_id, version_id = require_id(args, "app_id"), require_id(args, "version_id")
    platform = choice(args, "platform", PLATFORMS, "IOS")
    app = resource_of(await ctx.client.request("GET", "/v1/apps/{id}", item_id=app_id))
    await require_version_of_app(ctx, app_id, version_id)
    version = (await ctx.client.request("GET", "/v1/appStoreVersions/{id}", item_id=version_id))[
        "data"
    ]
    attributes = version.get("attributes") or {}
    lines = [
        f"App: {attributes_of(app).get('name')} ({app_id})",
        f"Version: {attributes.get('versionString')} ({version_id}), "
        f"now {attributes.get('appVersionState')}",
        f"Platform: {platform}",
        f"Release after approval: {attributes.get('releaseType')}",
        "Action: send this version to Apple App Review. It cannot be unsent; "
        "if approved it can go live.",
    ]
    await confirm_change(ctx, "Submit for App Review", lines, public=True)
    return await run_submission(ctx, app_id, version_id, platform)


async def require_version_of_app(ctx: ToolContext, app_id: str, version_id: str) -> None:
    """Refuse unless `version_id` is one of the app's own versions (so the form is truthful)."""
    versions = await ctx.client.paginate(
        "/v1/apps/{id}/appStoreVersions", item_id=app_id, max_items=MAX_LIMIT
    )
    if version_id not in {str(item.get("id")) for item in versions}:
        raise ToolRefusedError(f"Version {version_id} does not belong to app {app_id}.")


async def open_draft_submission(ctx: ToolContext, app_id: str, platform: str) -> str | None:
    """Return an existing draft submission (state READY_FOR_REVIEW) for this platform, if any.

    An earlier attempt that failed halfway leaves such a draft behind, and Apple allows only one
    open draft per platform, so we reuse it instead of creating a second one.
    """
    submissions = await ctx.client.paginate(
        "/v1/apps/{id}/reviewSubmissions", item_id=app_id, max_items=MAX_LIMIT
    )
    for submission in submissions:
        attributes = submission.get("attributes") or {}
        if attributes.get("state") == "READY_FOR_REVIEW" and attributes.get("platform") == platform:
            return str(submission["id"])
    return None


async def run_submission(ctx: ToolContext, app_id: str, version_id: str, platform: str) -> str:
    """Create (or reuse) the review submission, add the version to it, and submit it."""
    existing = await open_draft_submission(ctx, app_id, platform)
    if existing is not None:
        return await add_and_submit(ctx, existing, version_id)
    create = {
        "data": {
            "type": "reviewSubmissions",
            "attributes": {"platform": platform},
            "relationships": {"app": relationship("apps", app_id)},
        }
    }
    submission = resource_of(await ctx.client.request("POST", "/v1/reviewSubmissions", body=create))
    return await add_and_submit(ctx, str(submission["id"]), version_id)


async def add_and_submit(ctx: ToolContext, submission_id: str, version_id: str) -> str:
    """Add the version to the submission and send it to App Review."""
    try:
        item = {
            "data": {
                "type": "reviewSubmissionItems",
                "relationships": {
                    "reviewSubmission": relationship("reviewSubmissions", submission_id),
                    "appStoreVersion": relationship("appStoreVersions", version_id),
                },
            }
        }
        await ctx.client.request("POST", "/v1/reviewSubmissionItems", body=item)
        submit = {
            "data": {
                "type": "reviewSubmissions",
                "id": submission_id,
                "attributes": {"submitted": True},
            }
        }
        await ctx.client.request(
            "PATCH", "/v1/reviewSubmissions/{id}", item_id=submission_id, body=submit
        )
    except AscError as exc:
        raise AscError(
            f"Review submission {submission_id} was created but NOT submitted: {exc} "
            "Check App Store Connect before trying again."
        ) from exc
    return f"Submitted version {version_id} to App Review (submission {submission_id})."


READ_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="get_review_submissions",
        description="List an app's App Review submissions and their status.",
        properties={
            "app_id": string_prop("The app's ID."),
            "state": string_prop("Optional state filter."),
        },
        required=("app_id",),
        handler=get_review_submissions,
    ),
]


WRITE_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="submit_for_app_review",
        description=(
            "Submit an App Store version to Apple App Review. This is PUBLIC and IRREVERSIBLE: it "
            "cannot be unsent and an approved version can go live. Asks the user to confirm."
        ),
        properties={
            "app_id": string_prop("The app's ID."),
            "version_id": string_prop("The App Store version's ID."),
            "platform": string_prop("IOS (default), MAC_OS, TV_OS or VISION_OS."),
        },
        required=("app_id", "version_id"),
        handler=submit_for_app_review,
        write=True,
        destructive=True,
    ),
]
