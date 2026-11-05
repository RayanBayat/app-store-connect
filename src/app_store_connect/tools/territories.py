"""Small helpers shared by the availability and pricing tools: territory codes and names.

Apple identifies a territory (a country or region where the App Store sells) by a three-letter
code such as SWE. The API gives only the code and the currency, not a country name, so a short
table of well-known names makes the output easier to read. An unknown code is shown as is.
"""

from __future__ import annotations

import re
from typing import Any, Final

from app_store_connect.tools.base import ToolInputError, related_id

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
    return related_id(item, "territory") or "?"
