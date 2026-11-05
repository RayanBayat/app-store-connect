"""All App Store Connect tools. Read tools are always on; write tools only when writes are on.

How to add a tool:

1. Pick the module for its area (for example `tools/builds.py`), or copy a small one such as
   `tools/apps.py` for a new area.
2. Write an `async def my_tool(ctx, args) -> str` handler. Check the arguments with the helpers in
   `tools/base.py`. A write tool must call `confirm_change` (`tools/confirm.py`) before it sends
   anything to Apple.
3. Describe it with a `ToolSpec(name=..., description=..., properties=..., required=...,
   handler=...)` and put it in the module's `READ_TOOLS` list, or in `WRITE_TOOLS` with
   `write=True` (and `destructive=True` when it removes or publishes something).
4. Every module keeps the same layout: header docstring, handlers, then `READ_TOOLS`, then
   `WRITE_TOOLS` at the very end. A new module must define both lists (`[]` when it has none)
   and be added to `_MODULES` below.
5. If the tool calls an Apple endpoint that is not yet in `ENDPOINTS_USED` (`spec.py`), verify it
   against Apple's spec and add it, then add tests in `tests/`.
"""

from __future__ import annotations

from typing import Final

from app_store_connect.tools import (
    agerating,
    app_review,
    apps,
    availability,
    beta,
    builds,
    customer_reviews,
    localizations,
    pricing,
    screenshots,
    versions,
)
from app_store_connect.tools.base import ToolSpec

_MODULES: Final = (
    apps,
    versions,
    builds,
    beta,
    localizations,
    screenshots,
    app_review,
    customer_reviews,
    availability,
    pricing,
    agerating,
)


def read_tools() -> list[ToolSpec]:
    """Tools that never change anything."""
    return [tool for module in _MODULES for tool in module.READ_TOOLS]


def write_tools() -> list[ToolSpec]:
    """Tools that change things (each asks the human to confirm first)."""
    return [tool for module in _MODULES for tool in module.WRITE_TOOLS]


def all_tools(*, allow_writes: bool) -> list[ToolSpec]:
    """The tools to register: read tools, plus write tools only when `allow_writes` is true."""
    return [*read_tools(), *(write_tools() if allow_writes else [])]
