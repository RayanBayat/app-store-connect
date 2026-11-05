# ruff: noqa: E501  (the status table below reads best one entry per line)
"""Where an app is sold: read availability per territory, and change it.

How Apple models it (API 4.5.1): an app has one *availability* (`/v1/apps/{id}/appAvailabilityV2`)
with a flag for new territories, and one *territory availability* per territory. Each holds
`available`, `releaseDate` and `contentStatuses`: Apple's reasons why the app can or cannot be
sold there (for example a missing age rating).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Final

from app_store_connect.client import AscError
from app_store_connect.tools.base import (
    ToolContext,
    ToolInputError,
    ToolSpec,
    relationship,
    require_id,
    string_prop,
    to_text,
)
from app_store_connect.tools.confirm import confirm_change
from app_store_connect.tools.lookups import app_label, data_or_none, price_overview
from app_store_connect.tools.territories import describe, territory_of, valid_code

MAX_TERRITORIES: Final = 400  # Apple has about 175
NAMED_IN_SUMMARY: Final = 10
UNKNOWN_STATUS: Final = "A status this server does not know yet; see App Store Connect for details."
NO_STATUS: Final = "Apple gave no reason."

# Apple's `contentStatuses` values (spec 4.5.1) with a plain-English meaning each.
STATUS_HINTS: Final = {
    "AVAILABLE": "On sale in this territory.",
    "AVAILABLE_FOR_PREORDER_ON_DATE": "Open for pre-order; it goes on sale on the release date.",
    "PROCESSING_TO_NOT_AVAILABLE": "Being taken off sale; Apple is still processing it.",
    "PROCESSING_TO_AVAILABLE": "Going on sale; Apple is still processing it. Check again later.",
    "PROCESSING_TO_PRE_ORDER": "Being set up for pre-order; Apple is still processing it.",
    "AVAILABLE_FOR_SALE_UNRELEASED_APP": "Ready to sell here, but the app version is not yet released.",
    "PREORDER_ON_UNRELEASED_APP": "Pre-order is set up, but the app version is not yet released.",
    "AVAILABLE_FOR_PREORDER": "Open for pre-order here.",
    "MISSING_RATING": "The app has no age rating for this territory. Complete the age rating questions.",
    "CANNOT_SELL_RESTRICTED_RATING": "The age rating is too high to sell in this territory.",
    "BRAZIL_REQUIRED_TAX_ID": "Brazil needs a tax ID for this app; add it in App Store Connect.",
    "BRAZIL_GAMBLING_NOT_VERIFIED": "Brazil has not verified the gambling licence for this app.",
    "MISSING_GRN": "A game rating number (GRN, South Korea) is missing.",
    "UNVERIFIED_GRN": "The game rating number (GRN, South Korea) has not been verified yet.",
    "ICP_NUMBER_INVALID": "The China ICP filing number is not valid.",
    "ICP_NUMBER_MISSING": "A China ICP filing number is required and missing.",
    "TRADER_STATUS_NOT_PROVIDED": "The EU trader status is not provided; declare it in App Store Connect.",
    "TRADER_STATUS_VERIFICATION_FAILED": "The EU trader status could not be verified.",
    "TRADER_STATUS_VERIFICATION_STATUS_MISSING": "The EU trader status verification is missing.",
    "CANNOT_SELL_SEVENTEEN_PLUS_APPS": "Apps rated 17+ cannot be sold in this territory.",
    "CANNOT_SELL_SEXUALLY_EXPLICIT": "Sexually explicit apps cannot be sold in this territory.",
    "CANNOT_SELL_NON_IOS_GAMES": "Games that are not iOS games cannot be sold in this territory.",
    "CANNOT_SELL_SEVENTEEN_PLUS_GAMES": "Games rated 17+ cannot be sold in this territory.",
    "CANNOT_SELL_CASINO": "Casino apps cannot be sold in this territory.",
    "CANNOT_SELL_CASINO_WITHOUT_GRAC": "Casino apps need a GRAC rating (South Korea).",
    "CANNOT_SELL_CASINO_WITHOUT_AGE_VERIFICATION": "Casino apps need age verification here.",
    "CANNOT_SELL_ADULT_ONLY": "Adult-only apps cannot be sold in this territory.",
    "CANNOT_SELL_GAMBLING_CONTESTS": "Gambling and contest apps cannot be sold in this territory.",
    "CANNOT_SELL_GAMBLING": "Gambling apps cannot be sold in this territory.",
    "CANNOT_SELL_CONTESTS": "Contest apps cannot be sold in this territory.",
    "CANNOT_SELL_NINETEEN_PLUS_WITHOUT_GRAC": "Apps rated 19+ need a GRAC rating (South Korea).",
    "CANNOT_SELL": "Apple does not allow this app to be sold here (no further detail given).",
    "CANNOT_SELL_FREQUENT_INTENSE_GAMBLING": "Frequent or intense gambling content: not allowed here.",
    "CANNOT_SELL_FREQUENT_INTENSE_ALCOHOL_TOBACCO_DRUGS": "Frequent or intense alcohol, tobacco or drug content: not allowed here.",
    "CANNOT_SELL_FREQUENT_INTENSE_VIOLENCE": "Frequent or intense violence: not allowed here.",
    "CANNOT_SELL_FREQUENT_INTENSE_SEXUAL_CONTENT_NUDITY": "Frequent or intense sexual content or nudity: not allowed here.",
    "CANNOT_SELL_INFREQUENT_MILD_ALCOHOL_TOBACCO_DRUGS": "Infrequent or mild alcohol, tobacco or drug content: not allowed here.",
    "CANNOT_SELL_INFREQUENT_MILD_SEXUAL_CONTENT_NUDITY": "Infrequent or mild sexual content or nudity: not allowed here.",
    "CANNOT_SELL_FREQUENT_INTENSE": "Frequent or intense mature content: not allowed here.",
    "CANNOT_SELL_FREQUENT_INTENSE_WITHOUT_GRAC": "Frequent or intense content needs a GRAC rating (South Korea).",
    "CANNOT_SELL_FREQUENT_GAMBLING": "Frequent gambling content: not allowed here.",
    "CANNOT_SELL_FREQUENT_ALCOHOL_TOBACCO_DRUGS": "Frequent alcohol, tobacco or drug content: not allowed here.",
    "CANNOT_SELL_FREQUENT_VIOLENCE": "Frequent violence: not allowed here.",
    "CANNOT_SELL_FREQUENT_SEXUAL_CONTENT_NUDITY": "Frequent sexual content or nudity: not allowed here.",
    "CANNOT_SELL_INFREQUENT_ALCOHOL_TOBACCO_DRUGS": "Infrequent alcohol, tobacco or drug content: not allowed here.",
    "CANNOT_SELL_INFREQUENT_SEXUAL_CONTENT_NUDITY": "Infrequent sexual content or nudity: not allowed here.",
    "CANNOT_SELL_FREQUENT": "Frequent mature content: not allowed here.",
    "CANNOT_SELL_FREQUENT_WITHOUT_GRAC": "Frequent mature content needs a GRAC rating (South Korea).",
}  # fmt: skip


def hint(status: str) -> str:
    """The plain-English meaning of one Apple status value."""
    return STATUS_HINTS.get(status, UNKNOWN_STATUS)


async def fetch_availability(
    ctx: ToolContext, app_id: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The app's availability and all its territory availabilities (None, [] if none exists)."""
    availability = await data_or_none(
        ctx.client.request("GET", "/v1/apps/{id}/appAvailabilityV2", item_id=app_id)
    )
    if availability is None:
        return None, []
    # include=territory makes Apple fill in each item's `territory` link (its 3-letter code).
    items = await ctx.client.paginate(
        "/v2/appAvailabilities/{id}/territoryAvailabilities",
        item_id=str(availability["id"]),
        params={"include": "territory"},
        max_items=MAX_TERRITORIES,
    )
    return availability, items


def territory_row(item: dict[str, Any]) -> dict[str, Any]:
    """One territory in plain terms: code, available, releaseDate and the reasons."""
    attributes = item.get("attributes") or {}
    statuses = [str(s) for s in attributes.get("contentStatuses") or []]
    return {
        "territory": territory_of(item),
        "available": attributes.get("available"),
        "releaseDate": attributes.get("releaseDate"),
        "contentStatuses": [{"status": s, "meaning": hint(s)} for s in statuses],
    }


def group_by_status(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Territories grouped by each status value, with the meaning next to it."""
    groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for entry in row["contentStatuses"]:
            groups[entry["status"]].append(row["territory"])
    return {s: {"meaning": hint(s), "territories": codes} for s, codes in sorted(groups.items())}


def availability_summary(rows: list[dict[str, Any]]) -> str:
    """'Available in 0 of 175 territories. Sweden (SWE): not available - reasons: ...'."""
    on_sale = sum(1 for row in rows if row["available"])
    parts = [f"Available in {on_sale} of {len(rows)} territories."]
    blocked: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for row in rows:
        if not row["available"]:
            blocked[tuple(e["status"] for e in row["contentStatuses"])].append(row["territory"])
    for statuses, codes in blocked.items():
        named = ", ".join(describe(c) for c in codes[:NAMED_IN_SUMMARY])
        more = f" and {len(codes) - NAMED_IN_SUMMARY} more" if len(codes) > NAMED_IN_SUMMARY else ""
        reasons = "; ".join(f"{s} ({hint(s)})" for s in statuses) or NO_STATUS
        parts.append(f"{named}{more}: not available - reasons: {reasons}")
    return " ".join(parts)


async def get_app_availability(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Where the app is on sale, why not, and its price: the answer to 'why is it a 404?'."""
    app_id = require_id(args, "app_id")
    availability, items = await fetch_availability(ctx, app_id)
    price = await price_overview(ctx, app_id)
    rows = [territory_row(item) for item in items]
    if availability is None:
        summary = "The app has no availability set up yet, so it is not on sale anywhere."
    else:
        summary = availability_summary(rows)
    return to_text(
        {
            "summary": f"{summary} {price['summary']}",
            "availableInNewTerritories": ((availability or {}).get("attributes") or {}).get(
                "availableInNewTerritories"
            ),
            "price": price,
            "territoriesByStatus": group_by_status(rows),
            "territories": rows,
        }
    )


def requested_codes(args: dict[str, Any]) -> list[str]:
    """The territory codes asked for: 1 or more 3-letter codes, in capitals, without repeats."""
    raw = args.get("territories")
    if not isinstance(raw, list) or not raw:
        raise ToolInputError("Argument 'territories' must be a non-empty list of 3-letter codes.")
    return list(dict.fromkeys(valid_code(code) for code in raw))


def state_word(available: bool | None) -> str:
    return "available" if available else "not available"


async def create_availability(ctx: ToolContext, app_id: str, codes: list[str], label: str) -> str:
    """The app has no availability yet: create it with the wanted territories on sale.

    Apple's documented way (POST /v2/appAvailabilities): the request lists the territories and
    the `included` section creates one territory availability for each, linked by a local
    `${...}` ID. Territories left out are not on sale. `availableInNewTerritories` is required
    by Apple; we send False so that nothing is sold anywhere that the human did not see.
    """
    known = {
        t["id"] for t in await ctx.client.paginate("/v1/territories", max_items=MAX_TERRITORIES)
    }
    unknown = [c for c in codes if c not in known]
    if unknown:
        raise ToolInputError(f"Apple does not know these territory codes: {', '.join(unknown)}.")
    lines = [
        f"App: {label}",
        "The app has no availability yet, so it is on sale nowhere.",
        *(f"{describe(c)}: not available  ->  available" for c in codes),
        "All other territories stay not available, and new territories Apple adds later "
        "are not switched on automatically.",
    ]
    await confirm_change(ctx, "Put the app on sale in these territories", lines)
    refs = [f"${{ta{i}}}" for i in range(len(codes))]
    body = {
        "data": {
            "type": "appAvailabilities",
            "attributes": {"availableInNewTerritories": False},
            "relationships": {
                "app": relationship("apps", app_id),
                "territoryAvailabilities": {
                    "data": [{"type": "territoryAvailabilities", "id": r} for r in refs]
                },
            },
        },
        "included": [
            {
                "type": "territoryAvailabilities",
                "id": ref,
                "attributes": {"available": True},
                "relationships": {"territory": relationship("territories", code)},
            }
            for ref, code in zip(refs, codes, strict=True)
        ],
    }
    await ctx.client.request("POST", "/v2/appAvailabilities", body=body)
    return f"Created the availability: on sale in {', '.join(describe(c) for c in codes)}."


async def set_territory_availability(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Switch the app on or off in the given territories.

    The way Apple documents it for an app that already has an availability: change one
    territory at a time with PATCH /v1/territoryAvailabilities/{id} and the attribute
    `available`. If the app has no availability yet there is nothing to patch, so the create
    flow (POST /v2/appAvailabilities) is used instead, and only to switch territories on.
    """
    app_id = require_id(args, "app_id")
    codes = requested_codes(args)
    wanted = args.get("available")
    if not isinstance(wanted, bool):
        raise ToolInputError("Argument 'available' must be true or false.")
    _, name_label = await app_label(ctx, app_id)
    availability, items = await fetch_availability(ctx, app_id)
    if availability is None:
        if not wanted:
            return "Nothing to change: the app has no availability, so it is on sale nowhere."
        return await create_availability(ctx, app_id, codes, name_label)
    by_code = {territory_of(item): item for item in items}
    unknown = [c for c in codes if c not in by_code]
    if unknown:
        raise ToolInputError(f"Apple does not list these territories: {', '.join(unknown)}.")
    changes = [
        by_code[c]
        for c in codes
        if bool((by_code[c].get("attributes") or {}).get("available")) != wanted
    ]
    if not changes:
        return "Nothing to change: every territory already has that setting."
    lines = [f"App: {name_label}"]
    for code in codes:
        old = bool((by_code[code].get("attributes") or {}).get("available"))
        lines.append(f"{describe(code)}: {state_word(old)}  ->  {state_word(wanted)}")
    await confirm_change(ctx, "Change where the app is on sale", lines)
    done: list[str] = []
    for item in changes:
        body = {
            "data": {
                "type": "territoryAvailabilities",
                "id": str(item["id"]),
                "attributes": {"available": wanted},
            }
        }
        try:
            await ctx.client.request(
                "PATCH", "/v1/territoryAvailabilities/{id}", item_id=str(item["id"]), body=body
            )
        except AscError as exc:
            changed = ", ".join(done) or "none"
            raise AscError(f"{exc} Already changed before the error: {changed}.") from exc
        done.append(territory_of(item))
    return f"The app is now {state_word(wanted)} in: {', '.join(describe(c) for c in done)}."


READ_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="get_app_availability",
        description="Find out where an app is on sale and why not: whether it is available in new "
        "territories, every territory with available, releaseDate and Apple's reasons "
        "(contentStatuses, with plain-English hints, grouped by status), a short summary, and "
        "the app's price schedule (base territory and current price, or 'no price set').",
        properties={"app_id": string_prop("The app's ID.")},
        required=("app_id",),
        handler=get_app_availability,
    ),
]


WRITE_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="set_territory_availability",
        description=(
            "Switch an app on or off in specific territories (3-letter codes such as SWE, USA, "
            "GBR). Unknown codes are refused. The form lists every territory with old -> new. "
            "Asks the user to confirm."
        ),
        properties={
            "app_id": string_prop("The app's ID."),
            "territories": {
                "type": "array",
                "description": "3-letter territory codes, e.g. ['SWE', 'USA', 'GBR'].",
                "items": {"type": "string", "pattern": "^[A-Za-z]{3}$"},
                "minItems": 1,
                "maxItems": MAX_TERRITORIES,
            },
            "available": {"type": "boolean", "description": "true to sell there, false to stop."},
        },
        required=("app_id", "territories", "available"),
        handler=set_territory_availability,
        write=True,
    ),
]
