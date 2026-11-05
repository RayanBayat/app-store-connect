"""App price schedule: read the current price, and make a free app free everywhere.

How Apple models price (API 4.5.1): an app has one *price schedule* with a *base territory*
and *manual prices* (a price point you choose, for the base territory). Apple derives the
prices in all other territories from the base one ("automatic prices"). So a free app needs a
schedule whose base price is the price point whose customer price is 0. An app with no
schedule has no price at all, which can keep it off sale even when it is "Ready for Sale".
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from app_store_connect.tools.base import (
    ToolContext,
    ToolRefusedError,
    ToolSpec,
    relationship,
    require_id,
    string_prop,
    to_text,
)
from app_store_connect.tools.confirm import confirm_change
from app_store_connect.tools.territories import app_label, data_or_none, describe, valid_code

MAX_PRICE_POINTS = 2000  # Apple has under a thousand price points per territory


def is_current(price: dict[str, Any], today: str) -> bool:
    """True when a manual price applies today (no start date means 'from the beginning')."""
    attributes = price.get("attributes") or {}
    start, end = attributes.get("startDate"), attributes.get("endDate")
    return (start is None or start <= today) and (end is None or end >= today)


def is_zero(customer_price: object) -> bool:
    """True when a customer price such as '0.0' or '0.00' means free."""
    try:
        return Decimal(str(customer_price)) == 0
    except InvalidOperation:
        return False


def price_summary(price: dict[str, Any] | None, base: str | None) -> str:
    """One plain sentence about the current price."""
    if price is None:
        return "Price: no price set. An app without a price cannot be sold, even when it is free."
    amount, currency = price["customerPrice"], price["currency"]
    where = describe(price["territory"])
    kind = "free" if is_zero(amount) else f"{amount} {currency}"
    base_note = f" (base territory: {describe(base)})" if base else ""
    return f"Price: {kind} in {where}{base_note}; Apple sets the other territories from it."


async def price_overview(ctx: ToolContext, app_id: str) -> dict[str, Any]:
    """Read the app's price schedule: base territory and the manual price that applies today."""
    schedule = await data_or_none(
        ctx.client.request("GET", "/v1/apps/{id}/appPriceSchedule", item_id=app_id)
    )
    if schedule is None:
        return {
            "hasPriceSchedule": False,
            "baseTerritory": None,
            "currentManualPrice": None,
            "summary": price_summary(None, None),
        }
    schedule_id = str(schedule["id"])
    base_data = await data_or_none(
        ctx.client.request("GET", "/v1/appPriceSchedules/{id}/baseTerritory", item_id=schedule_id)
    )
    base = str(base_data["id"]) if base_data else None
    # Manual prices are few (one per price change of the base territory), so one page is enough.
    page = await ctx.client.request(
        "GET",
        "/v1/appPriceSchedules/{id}/manualPrices",
        item_id=schedule_id,
        params={"include": "appPricePoint,territory", "limit": "200"},
    )
    included = {(i.get("type"), i.get("id")): i for i in page.get("included", [])}
    today = date.today().isoformat()
    current = sorted(
        (p for p in page.get("data", []) if is_current(p, today)),
        key=lambda p: (p.get("attributes") or {}).get("startDate") or "",
    )
    manual = None
    if current:
        price = current[-1]
        relations = price.get("relationships") or {}
        point_id = ((relations.get("appPricePoint") or {}).get("data") or {}).get("id")
        territory = ((relations.get("territory") or {}).get("data") or {}).get("id") or base
        point = included.get(("appPricePoints", point_id)) or {}
        territory_item = included.get(("territories", territory)) or {}
        manual = {
            "territory": territory,
            "customerPrice": (point.get("attributes") or {}).get("customerPrice"),
            "currency": (territory_item.get("attributes") or {}).get("currency"),
            "startDate": (price.get("attributes") or {}).get("startDate"),
        }
    return {
        "hasPriceSchedule": True,
        "baseTerritory": base,
        "currentManualPrice": manual,
        "summary": price_summary(manual, base),
    }


async def find_free_price_point(ctx: ToolContext, app_id: str, territory: str) -> str:
    """The ID of the price point whose customer price is 0 in `territory`."""
    points = await ctx.client.paginate(
        "/v1/apps/{id}/appPricePoints",
        item_id=app_id,
        params={"filter[territory]": territory},
        max_items=MAX_PRICE_POINTS,
    )
    for point in points:
        if is_zero((point.get("attributes") or {}).get("customerPrice")):
            return str(point["id"])
    raise ToolRefusedError(f"Apple lists no free (price 0) price point for {describe(territory)}.")


def free_price_body(app_id: str, territory: str, point_id: str) -> dict[str, Any]:
    """POST body for a new schedule: base territory plus one manual price (created inline).

    `${price0}` is a local placeholder ID that ties the relationship to the `included` item.
    """
    return {
        "data": {
            "type": "appPriceSchedules",
            "relationships": {
                "app": relationship("apps", app_id),
                "baseTerritory": relationship("territories", territory),
                "manualPrices": {"data": [{"type": "appPrices", "id": "${price0}"}]},
            },
        },
        "included": [
            {
                "type": "appPrices",
                "id": "${price0}",
                "attributes": {"startDate": None},
                "relationships": {"appPricePoint": relationship("appPricePoints", point_id)},
            }
        ],
    }


async def set_free_price(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Make the app free in every territory (POST /v1/appPriceSchedules with a price of 0)."""
    app_id = require_id(args, "app_id")
    territory = valid_code(args.get("base_territory", "SWE"), "base_territory")
    overview = await price_overview(ctx, app_id)
    current = overview["currentManualPrice"]
    if current is not None:
        if is_zero(current["customerPrice"]):
            return f"Nothing to change: the app is already free. {overview['summary']}"
        raise ToolRefusedError(
            "This tool only sets a price for apps that have none, so it will not replace "
            f"an existing paid price. {overview['summary']}"
        )
    point_id = await find_free_price_point(ctx, app_id, territory)
    name, label = await app_label(ctx, app_id)
    now = (
        "a price schedule exists but has no current price"
        if overview["hasPriceSchedule"]
        else "no price set (no price schedule)"
    )
    lines = [
        f"App: {label}",
        f"Now: {now}",
        f"After: price 0 (free) in {describe(territory)}; Apple applies the free price to every "
        "other country too.",
    ]
    title = f"Make {name} free in all countries (base: {describe(territory)})"
    await confirm_change(ctx, title, lines)
    body = free_price_body(app_id, territory, point_id)
    await ctx.client.request("POST", "/v1/appPriceSchedules", body=body)
    return to_text(
        {"result": f"{name} is now free in all countries (base: {describe(territory)})."}
    )


READ_TOOLS: Final[list[ToolSpec]] = []


WRITE_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="set_free_price",
        description=(
            "Make a free app free in every country by giving it a price schedule with the free "
            "(0) price point. Only for apps that have no price yet; it refuses to replace a paid "
            "price. Asks the user to confirm."
        ),
        properties={
            "app_id": string_prop("The app's ID."),
            "base_territory": string_prop(
                "3-letter code of the base territory (default SWE).", default="SWE"
            ),
        },
        required=("app_id",),
        handler=set_free_price,
        write=True,
    ),
]
