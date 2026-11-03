"""All tools. Read tools are always available; write tools only when writes are enabled."""

from __future__ import annotations

from app_store_connect.tools import (
    agerating,
    apps,
    availability,
    beta,
    builds,
    localizations,
    pricing,
    reviews,
    screenshots,
    versions,
)
from app_store_connect.tools.base import ToolSpec

_MODULES = (
    apps,
    versions,
    builds,
    beta,
    localizations,
    screenshots,
    reviews,
    availability,
    pricing,
    agerating,
)


def read_tools() -> list[ToolSpec]:
    """Tools that never change anything."""
    return [tool for module in _MODULES for tool in getattr(module, "READ_TOOLS", [])]


def write_tools() -> list[ToolSpec]:
    """Tools that change things (each asks the human to confirm first)."""
    return [tool for module in _MODULES for tool in getattr(module, "WRITE_TOOLS", [])]


def all_tools(*, allow_writes: bool) -> list[ToolSpec]:
    """The tools to register: read tools, plus write tools only when `allow_writes` is true."""
    return [*read_tools(), *(write_tools() if allow_writes else [])]
