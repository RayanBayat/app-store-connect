"""Small helpers shared by the availability and pricing tools: territory codes and names.

Apple identifies a territory (a country or region where the App Store sells) by a three-letter
code such as SWE. The API gives only the code and the currency, not a country name, so a short
table of well-known names makes the output easier to read. An unknown code is shown as is.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable
from typing import Any, Final

from app_store_connect.client import AscApiError
from app_store_connect.tools.base import ToolContext, ToolInputError, resource_of

CODE_PATTERN: Final = re.compile(r"[A-Z]{3}")
# Only for readability. Not a list of valid territories: Apple's own answer decides that.
TERRITORY_NAMES: Final = {
    "AUS": "Australia", "AUT": "Austria", "BEL": "Belgium", "BRA": "Brazil", "CAN": "Canada",
    "CHE": "Switzerland", "CHN": "China mainland", "DEU": "Germany", "DNK": "Denmark",
    "ESP": "Spain", "FIN": "Finland", "FRA": "France", "GBR": "United Kingdom",
    "GRC": "Greece", "HKG": "Hong Kong", "IND": "India", "IRL": "Ireland", "ISL": "Iceland",
    "ISR": "Israel", "ITA": "Italy", "JPN": "Japan", "KOR": "South Korea", "MEX": "Mexico",
    "NLD": "Netherlands", "NOR": "Norway", "NZL": "New Zealand", "POL": "Poland",
    "PRT": "Portugal", "RUS": "Russia", "SAU": "Saudi Arabia", "SGP": "Singapore",
    "SWE": "Sweden", "TUR": "Turkey", "TWN": "Taiwan", "UKR": "Ukraine",
    "USA": "United States", "ZAF": "South Africa",
}  # fmt: skip


def describe(code: str) -> str:
    """'Sweden (SWE)' for a known code, otherwise just the code."""
    name = TERRITORY_NAMES.get(code)
    return f"{name} ({code})" if name else code


def valid_code(value: object, name: str = "territory") -> str:
    """Return a three-letter territory code in capitals, or refuse (before anything is sent)."""
    code = value.strip().upper() if isinstance(value, str) else ""
    if not CODE_PATTERN.fullmatch(code):
        raise ToolInputError(f"{name} {value!r} must be a 3-letter territory code such as SWE.")
    return code


def territory_of(item: dict[str, Any]) -> str:
    """The territory code a resource points at (its `territory` relationship; codes are the IDs)."""
    data = ((item.get("relationships") or {}).get("territory") or {}).get("data") or {}
    return str(data.get("id", "?"))


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


async def app_label(ctx: ToolContext, app_id: str) -> tuple[str, str]:
    """(name, 'Name (ID)') of an app, for forms. The name is escaped when the form is built."""
    app = resource_of(await ctx.client.request("GET", "/v1/apps/{id}", item_id=app_id))
    name = str((app.get("attributes") or {}).get("name") or app_id)
    return name, f"{name} ({app_id})"
