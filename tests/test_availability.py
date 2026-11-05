"""Availability and pricing: get_app_availability, set_territory_availability, set_free_price."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app_store_connect.tools import read_tools, write_tools
from app_store_connect.tools.availability import STATUS_HINTS

from .apple_fake import (
    PROTOCOLS,
    FakeApple,
    FormAction,
    FormAnswerer,
    connected,
    listing,
    one,
    resource,
    run,
    text_of,
)

API = "https://api.appstoreconnect.apple.com"
AV_PATH = "/v2/appAvailabilities/av1/territoryAvailabilities"


def ta(code: str, available: bool, statuses: tuple[str, ...] = (), **extra: Any) -> dict[str, Any]:
    item = resource(
        "territoryAvailabilities", f"ta-{code}", available=available,
        contentStatuses=list(statuses), **extra,
    )  # fmt: skip
    item["relationships"] = {"territory": {"data": {"type": "territories", "id": code}}}
    return item


def av_fake(*items: dict[str, Any], new_territories: bool = False) -> FakeApple:
    fake = FakeApple()
    fake.on("GET", "/v1/apps/1", one(resource("apps", "1", name="KörUppNu")))
    fake.on(
        "GET",
        "/v1/apps/1/appAvailabilityV2",
        one(resource("appAvailabilities", "av1", availableInNewTerritories=new_territories)),
    )
    fake.on("GET", AV_PATH, listing(*items))
    return fake


def add_price(fake: FakeApple, customer_price: str | None = "0.0") -> None:
    """A price schedule with base territory SWE and (optionally) one current manual price."""
    fake.on("GET", "/v1/apps/1/appPriceSchedule", one({"type": "appPriceSchedules", "id": "ps1"}))
    fake.on(
        "GET",
        "/v1/appPriceSchedules/ps1/baseTerritory",
        one(resource("territories", "SWE", currency="SEK")),
    )
    prices: list[dict[str, Any]] = []
    included: list[dict[str, Any]] = [resource("territories", "SWE", currency="SEK")]
    if customer_price is not None:
        price = resource("appPrices", "mp1", manual=True)
        price["relationships"] = {
            "appPricePoint": {"data": {"type": "appPricePoints", "id": "pp1"}},
            "territory": {"data": {"type": "territories", "id": "SWE"}},
        }
        prices.append(price)
        included.append(resource("appPricePoints", "pp1", customerPrice=customer_price))
    fake.on("GET", "/v1/appPriceSchedules/ps1/manualPrices", {"data": prices, "included": included})


async def read(tmp_path: Path, fake: FakeApple, **args: Any) -> dict[str, Any]:
    async with connected(tmp_path, fake) as client:
        result = await client.call_tool("get_app_availability", {"app_id": "1", **args})
    assert not result.is_error, text_of(result)
    out: dict[str, Any] = json.loads(text_of(result))
    return out


# ---------------------------------------------------------------- get_app_availability


async def test_read_shape_summary_and_status_hints(tmp_path: Path) -> None:
    fake = av_fake(
        ta("SWE", False, ("MISSING_RATING",)),
        ta("USA", False, ("MISSING_RATING", "SOMETHING_NEW")),
        ta("NOR", True, ("AVAILABLE",), releaseDate="2026-11-01"),
        new_territories=True,
    )
    add_price(fake, None)
    out = await read(tmp_path, fake)
    assert out["availableInNewTerritories"] is True
    assert out["summary"].startswith("Available in 1 of 3 territories.")
    assert "Sweden (SWE)" in out["summary"] and "MISSING_RATING" in out["summary"]
    assert STATUS_HINTS["MISSING_RATING"] in out["summary"]
    rows = {row["territory"]: row for row in out["territories"]}
    assert rows["NOR"]["available"] is True and rows["NOR"]["releaseDate"] == "2026-11-01"
    assert rows["SWE"]["contentStatuses"] == [
        {"status": "MISSING_RATING", "meaning": STATUS_HINTS["MISSING_RATING"]}
    ]
    unknown = rows["USA"]["contentStatuses"][1]
    assert unknown["status"] == "SOMETHING_NEW" and "does not know" in unknown["meaning"]
    group = out["territoriesByStatus"]["MISSING_RATING"]
    assert group["territories"] == ["SWE", "USA"] and group["meaning"]
    assert out["price"]["currentManualPrice"] is None
    assert "no price set" in out["summary"].lower()


async def test_every_known_status_has_a_plain_hint() -> None:
    assert len(STATUS_HINTS) >= 40
    assert all(len(hint) > 15 for hint in STATUS_HINTS.values())


async def test_read_uses_include_territory_and_follows_pagination(tmp_path: Path) -> None:
    fake = av_fake()

    def pages(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "2":
            return httpx.Response(200, json=listing(ta("USA", False, ("CANNOT_SELL",))))
        nxt = f"{API}{AV_PATH}?cursor=2"
        return httpx.Response(200, json={**listing(ta("SWE", True)), "links": {"next": nxt}})

    fake.on("GET", AV_PATH, pages)
    out = await read(tmp_path, fake)
    assert [row["territory"] for row in out["territories"]] == ["SWE", "USA"]
    assert out["summary"].startswith("Available in 1 of 2 territories.")
    first = next(r for r in fake.seen if r.path == AV_PATH)
    assert first.params["include"] == "territory"


async def test_read_price_free_and_paid(tmp_path: Path) -> None:
    fake = av_fake(ta("SWE", True))
    add_price(fake, "0.0")
    price = (await read(tmp_path, fake))["price"]
    assert price["baseTerritory"] == "SWE"
    assert price["currentManualPrice"] == {
        "territory": "SWE", "customerPrice": "0.0", "currency": "SEK", "startDate": None,
    }  # fmt: skip
    assert "free" in price["summary"].lower()
    fake = av_fake(ta("SWE", True))
    add_price(fake, "9.0")
    assert "9.0 SEK" in (await read(tmp_path, fake))["price"]["summary"]


async def test_read_without_availability_or_price_schedule(tmp_path: Path) -> None:
    fake = FakeApple()  # every route is a 404
    out = await read(tmp_path, fake)
    assert "no availability" in out["summary"].lower()
    assert out["territories"] == []
    assert out["price"]["hasPriceSchedule"] is False
    assert "no price set" in out["price"]["summary"]


# ---------------------------------------------------------------- registration


def test_registration_split() -> None:
    assert "get_app_availability" in {t.name for t in read_tools()}
    names = {t.name for t in write_tools()}
    assert {"set_territory_availability", "set_free_price"} <= names
    assert all(t.write for t in write_tools() if t.name in names)


async def test_write_tools_absent_when_writes_are_off(tmp_path: Path) -> None:
    async with connected(tmp_path, av_fake()) as client:
        listed = {t.name for t in (await client.list_tools()).tools}
    assert "get_app_availability" in listed
    assert not {"set_territory_availability", "set_free_price"} & listed


# ---------------------------------------------------------------- writes


def patch_fake() -> FakeApple:
    fake = av_fake(
        ta("SWE", False, ("MISSING_RATING",)), ta("USA", False), ta("GBR", True), ta("NOR", True)
    )
    for code in ("SWE", "USA", "GBR", "NOR"):
        fake.on("PATCH", f"/v1/territoryAvailabilities/ta-{code}", one(ta(code, True)))
    return fake


def patch_body(code: str, available: bool) -> dict[str, Any]:
    return {
        "data": {
            "type": "territoryAvailabilities",
            "id": f"ta-{code}",
            "attributes": {"available": available},
        }
    }


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_set_availability_accept_patches_each_changed_territory_once(
    tmp_path: Path, protocol: str
) -> None:
    fake, answer = patch_fake(), FormAnswerer()
    args = {"app_id": "1", "territories": ["swe", "USA", "GBR"], "available": True}
    result = await run(tmp_path, fake, "set_territory_availability", args, answer, protocol)
    assert not result.is_error, text_of(result)
    # GBR is already available: no request for it. Requests go out in the order given.
    assert [(w.method, w.path, w.body) for w in fake.writes()] == [
        ("PATCH", "/v1/territoryAvailabilities/ta-SWE", patch_body("SWE", True)),
        ("PATCH", "/v1/territoryAvailabilities/ta-USA", patch_body("USA", True)),
    ]
    assert len(answer.forms) == 1
    form = answer.forms[0]
    assert "Sweden (SWE): not available  ->  available" in form
    assert "United States (USA): not available  ->  available" in form
    assert "United Kingdom (GBR): available  ->  available" in form
    assert "KörUppNu" in form


@pytest.mark.parametrize("action", ["decline", "cancel"])
@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_set_availability_decline_or_cancel_sends_nothing(
    tmp_path: Path, action: FormAction, protocol: str
) -> None:
    fake = patch_fake()
    args = {"app_id": "1", "territories": ["SWE"], "available": True}
    result = await run(
        tmp_path, fake, "set_territory_availability", args, FormAnswerer(action), protocol
    )
    assert result.is_error and "Nothing was changed" in text_of(result)
    assert fake.writes() == []


@pytest.mark.parametrize(
    "territories", [["XXX"], ["SWE", "ZZZ"], ["se"], [], "SWE", ["SWE", 5]], ids=str
)
async def test_invalid_codes_are_refused_before_any_form(tmp_path: Path, territories: Any) -> None:
    fake, answer = patch_fake(), FormAnswerer()
    args = {"app_id": "1", "territories": territories, "available": False}
    result = await run(tmp_path, fake, "set_territory_availability", args, answer)
    assert result.is_error
    assert answer.forms == [] and fake.writes() == []


async def test_unknown_code_message_names_it(tmp_path: Path) -> None:
    args = {"app_id": "1", "territories": ["SWE", "ZZZ"], "available": True}
    result = await run(tmp_path, patch_fake(), "set_territory_availability", args, FormAnswerer())
    assert "ZZZ" in text_of(result)


async def test_set_availability_nothing_to_change_asks_nothing(tmp_path: Path) -> None:
    fake, answer = patch_fake(), FormAnswerer()
    args = {"app_id": "1", "territories": ["GBR"], "available": True}
    result = await run(tmp_path, fake, "set_territory_availability", args, answer)
    assert not result.is_error and "Nothing to change" in text_of(result)
    assert answer.forms == [] and fake.writes() == []


async def test_set_availability_available_must_be_a_boolean(tmp_path: Path) -> None:
    args = {"app_id": "1", "territories": ["SWE"], "available": "yes"}
    result = await run(tmp_path, patch_fake(), "set_territory_availability", args, FormAnswerer())
    assert result.is_error


def create_fake() -> FakeApple:
    """An app that has no availability yet: Apple answers 404, so the create flow is used."""
    fake = FakeApple()
    fake.on("GET", "/v1/apps/1", one(resource("apps", "1", name="KörUppNu")))
    fake.on(
        "GET",
        "/v1/territories",
        listing(*(resource("territories", c, currency="XXX") for c in ("SWE", "USA", "GBR"))),
    )
    fake.on("POST", "/v2/appAvailabilities", one(resource("appAvailabilities", "av9")), 201)
    return fake


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_create_flow_when_the_app_has_no_availability_yet(
    tmp_path: Path, protocol: str
) -> None:
    fake, answer = create_fake(), FormAnswerer()
    args = {"app_id": "1", "territories": ["SWE", "USA"], "available": True}
    result = await run(tmp_path, fake, "set_territory_availability", args, answer, protocol)
    assert not result.is_error, text_of(result)
    assert [(w.method, w.path, w.body) for w in fake.writes()] == [
        (
            "POST",
            "/v2/appAvailabilities",
            {
                "data": {
                    "type": "appAvailabilities",
                    "attributes": {"availableInNewTerritories": False},
                    "relationships": {
                        "app": {"data": {"type": "apps", "id": "1"}},
                        "territoryAvailabilities": {
                            "data": [
                                {"type": "territoryAvailabilities", "id": "${ta0}"},
                                {"type": "territoryAvailabilities", "id": "${ta1}"},
                            ]
                        },
                    },
                },
                "included": [
                    {
                        "type": "territoryAvailabilities",
                        "id": f"${{ta{i}}}",
                        "attributes": {"available": True},
                        "relationships": {"territory": {"data": {"type": "territories", "id": c}}},
                    }
                    for i, c in enumerate(("SWE", "USA"))
                ],
            },
        )
    ]
    assert "no availability yet" in answer.forms[0].lower()
    assert "Sweden (SWE)" in answer.forms[0]


async def test_create_flow_validates_codes_and_handles_decline(tmp_path: Path) -> None:
    fake, answer = create_fake(), FormAnswerer()
    args = {"app_id": "1", "territories": ["ZZZ"], "available": True}
    assert (await run(tmp_path, fake, "set_territory_availability", args, answer)).is_error
    args["territories"] = ["SWE"]
    declined = await run(
        tmp_path, fake, "set_territory_availability", args, FormAnswerer("decline")
    )
    assert declined.is_error and fake.writes() == [] and answer.forms == []


async def test_create_flow_not_needed_to_make_unavailable(tmp_path: Path) -> None:
    fake, answer = create_fake(), FormAnswerer()
    args = {"app_id": "1", "territories": ["SWE"], "available": False}
    result = await run(tmp_path, fake, "set_territory_availability", args, answer)
    assert "Nothing to change" in text_of(result)
    assert fake.writes() == [] and answer.forms == []


# ---------------------------------------------------------------- set_free_price


def free_fake(*, schedule: str | None = None) -> FakeApple:
    fake = FakeApple()
    fake.on("GET", "/v1/apps/1", one(resource("apps", "1", name="KörUppNu")))
    fake.on(
        "GET",
        "/v1/apps/1/appPricePoints",
        listing(
            resource("appPricePoints", "pp-paid", customerPrice="9.0"),
            resource("appPricePoints", "pp-free", customerPrice="0.0"),
        ),
    )
    fake.on("POST", "/v1/appPriceSchedules", one({"type": "appPriceSchedules", "id": "ps9"}), 201)
    if schedule is not None:
        add_price(fake, schedule)
    return fake


FREE_BODY = {
    "data": {
        "type": "appPriceSchedules",
        "relationships": {
            "app": {"data": {"type": "apps", "id": "1"}},
            "baseTerritory": {"data": {"type": "territories", "id": "SWE"}},
            "manualPrices": {"data": [{"type": "appPrices", "id": "${price0}"}]},
        },
    },
    "included": [
        {
            "type": "appPrices",
            "id": "${price0}",
            "attributes": {"startDate": None},
            "relationships": {
                "appPricePoint": {"data": {"type": "appPricePoints", "id": "pp-free"}}
            },
        }
    ],
}


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_set_free_price_accept_sends_one_post(tmp_path: Path, protocol: str) -> None:
    fake, answer = free_fake(), FormAnswerer()
    result = await run(tmp_path, fake, "set_free_price", {"app_id": "1"}, answer, protocol)
    assert not result.is_error, text_of(result)
    assert [(w.method, w.path, w.body) for w in fake.writes()] == [
        ("POST", "/v1/appPriceSchedules", FREE_BODY)
    ]
    points = next(r for r in fake.seen if r.path == "/v1/apps/1/appPricePoints")
    assert points.params["filter[territory]"] == "SWE"
    assert len(answer.forms) == 1
    form = answer.forms[0]
    assert "Make KörUppNu free in all countries (base: Sweden (SWE))" in form
    assert "no price set" in form.lower()


@pytest.mark.parametrize("action", ["decline", "cancel"])
@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_set_free_price_decline_or_cancel_sends_nothing(
    tmp_path: Path, action: FormAction, protocol: str
) -> None:
    fake = free_fake()
    result = await run(
        tmp_path, fake, "set_free_price", {"app_id": "1"}, FormAnswerer(action), protocol
    )
    assert result.is_error and "Nothing was changed" in text_of(result)
    assert fake.writes() == []


async def test_set_free_price_other_base_territory_and_bad_code(tmp_path: Path) -> None:
    fake = free_fake()
    ok = await run(
        tmp_path, fake, "set_free_price", {"app_id": "1", "base_territory": "usa"}, FormAnswerer()
    )
    assert not ok.is_error
    assert fake.writes()[0].body["data"]["relationships"]["baseTerritory"]["data"]["id"] == "USA"
    fake, answer = free_fake(), FormAnswerer()
    bad = await run(
        tmp_path, fake, "set_free_price", {"app_id": "1", "base_territory": "US"}, answer
    )
    assert bad.is_error and answer.forms == [] and fake.writes() == []


async def test_set_free_price_refuses_to_replace_a_paid_price(tmp_path: Path) -> None:
    fake, answer = free_fake(schedule="9.0"), FormAnswerer()
    result = await run(tmp_path, fake, "set_free_price", {"app_id": "1"}, answer)
    assert result.is_error and "9.0" in text_of(result)
    assert answer.forms == [] and fake.writes() == []


async def test_set_free_price_already_free_changes_nothing(tmp_path: Path) -> None:
    fake, answer = free_fake(schedule="0.0"), FormAnswerer()
    result = await run(tmp_path, fake, "set_free_price", {"app_id": "1"}, answer)
    assert not result.is_error and "Nothing to change" in text_of(result)
    assert answer.forms == [] and fake.writes() == []


async def test_set_free_price_without_a_free_price_point_is_refused(tmp_path: Path) -> None:
    fake = free_fake()
    fake.on(
        "GET",
        "/v1/apps/1/appPricePoints",
        listing(resource("appPricePoints", "p", customerPrice="1.0")),
    )
    answer = FormAnswerer()
    result = await run(tmp_path, fake, "set_free_price", {"app_id": "1"}, answer)
    assert result.is_error and answer.forms == [] and fake.writes() == []
