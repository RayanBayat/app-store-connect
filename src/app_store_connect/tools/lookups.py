"""Lookups that several tools need: names for confirmation forms, and the app's current price.

These read from Apple and never change anything. They live here, and not in a tool module, so
that no tool module has to import another one.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app_store_connect.client import AscApiError
from app_store_connect.tools.base import ToolContext, attributes_of, resource_of
from app_store_connect.tools.territories import describe


async def group_name(ctx: ToolContext, group_id: str) -> str:
    """Look up a beta group's name (shown in confirmation forms)."""
    data = await ctx.client.request("GET", "/v1/betaGroups/{id}", item_id=group_id)
    return str(attributes_of(resource_of(data)).get("name"))


async def app_label(ctx: ToolContext, app_id: str) -> tuple[str, str]:
    """(name, 'Name (ID)') of an app, for forms. The name is escaped when the form is built."""
    app = resource_of(await ctx.client.request("GET", "/v1/apps/{id}", item_id=app_id))
    name = str((app.get("attributes") or {}).get("name") or app_id)
    return name, f"{name} ({app_id})"


async def data_or_none(request: Awaitable[dict[str, Any]]) -> dict[str, Any] | None:
    """Await a GET and return its `data`; None when Apple says 404 (nothing set up yet).

    The caller writes the request itself, so the endpoint stays visible in the tool's code.
    """
    try:
        data = (await request).get("data")
    except AscApiError as exc:
        if exc.status == 404:
            return None
        raise
    return data if isinstance(data, dict) else None


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
