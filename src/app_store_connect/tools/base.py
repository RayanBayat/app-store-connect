"""Building blocks shared by all tools: the tool description type, argument checks, output.

A tool is a `ToolSpec`: a name, a description for the model, a JSON input schema, honest MCP
annotations (read-only or destructive), and an async handler that returns text.

Handlers raise `ToolInputError` for bad arguments and `ToolRefusedError` when something must not
happen (for example the human declined). Both become a normal error result for the model.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import mcp_types as types
from mcp.server.session import ServerSession

from app_store_connect.client import AscClient
from app_store_connect.config import Config
from app_store_connect.confirmflow import ConfirmationBook
from app_store_connect.textsafe import wrap_strings

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200


class ToolInputError(Exception):
    """The model passed a missing or invalid argument."""


class ToolRefusedError(Exception):
    """The action was not carried out (declined, unsupported, or against a safety rule)."""


@dataclass(frozen=True, slots=True)
class ToolContext:
    """What a handler needs: the Apple client, the settings, and the current MCP session."""

    client: AscClient
    config: Config
    session: ServerSession | None = None
    # What the confirmation step needs (see confirmflow): the call being served, and, on a retry
    # under protocol 2026-07-28, the answer and state the client sent back.
    tool_name: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)
    input_responses: Mapping[str, Any] | None = None
    request_state: str | None = None
    confirmations: ConfirmationBook = field(default_factory=ConfirmationBook)


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One MCP tool and how to run it."""

    name: str
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    handler: Handler
    write: bool = False
    destructive: bool = False

    def to_tool(self) -> types.Tool:
        """Convert to the MCP `Tool` shown to the client, with annotations that match reality."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": self.properties,
            "required": list(self.required),
            "additionalProperties": False,
        }
        return types.Tool(
            name=self.name,
            description=self.description,
            input_schema=schema,
            annotations=types.ToolAnnotations(
                read_only_hint=not self.write,
                destructive_hint=self.destructive if self.write else False,
                idempotent_hint=not self.write,
                open_world_hint=True,
            ),
        )


def string_prop(description: str, **extra: Any) -> dict[str, Any]:
    """JSON-schema fragment for a string argument."""
    return {"type": "string", "description": description, **extra}


LIMIT_PROP: Final = {
    "type": "integer",
    "description": f"Maximum items to return (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).",
    "minimum": 1,
    "maximum": MAX_LIMIT,
}


def require_str(args: dict[str, Any], name: str) -> str:
    """Return a required, non-empty string argument."""
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"Argument '{name}' is required and must be a non-empty string.")
    return value.strip()


def optional_str(args: dict[str, Any], name: str) -> str | None:
    """Return an optional string argument (None when absent)."""
    value = args.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolInputError(f"Argument '{name}' must be a string.")
    return value


def limit_arg(args: dict[str, Any]) -> int:
    """Return the `limit` argument, defaulting and clamping to Apple's maximum."""
    value = args.get("limit", DEFAULT_LIMIT)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ToolInputError("Argument 'limit' must be a positive integer.")
    return min(value, MAX_LIMIT)


def summarize(
    item: dict[str, Any], fields: tuple[str, ...] | None = None, *, wrap: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Reduce an Apple resource to its id, type and (selected) attributes.

    Fields named in `wrap` are free text that someone else can write; they are returned as
    untrusted content (see `untrusted`). Missing values stay None.
    """
    attributes = item.get("attributes") or {}
    chosen = attributes if fields is None else {k: attributes.get(k) for k in fields}
    for name in wrap:
        if chosen.get(name) is not None:
            chosen[name] = wrap_strings(chosen[name])
    return {"id": item.get("id"), "type": item.get("type"), **chosen}


def to_text(value: Any) -> str:
    """Render a result as readable JSON text."""
    return json.dumps(value, indent=2, ensure_ascii=False)


def relationship(kind: str, item_id: str) -> dict[str, Any]:
    """A JSON:API relationship body: {"data": {"type": kind, "id": item_id}}."""
    return {"data": {"type": kind, "id": item_id}}
