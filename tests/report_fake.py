"""Fake inputs for the monthly report: a fake Apple spec, PyPI feed and OSV, all in memory.

Used by `test_report.py` and `test_review_identity.py`, so neither test file has to import from
the other.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import httpx

from app_store_connect.maintenance_record import MaintenanceRecord
from app_store_connect.report import ReportInputs
from app_store_connect.spec import APPLE_API_SPEC_VERSION, ENDPOINTS_USED
from app_store_connect.tools import versions

from .conftest import NOW


def make_spec(
    *,
    version: str = APPLE_API_SPEC_VERSION,
    drop: tuple[str, str] | None = None,
    deprecate: tuple[str, str] | None = None,
    attribute_deprecated: str | None = None,
) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for method, path in ENDPOINTS_USED:
        if (method, path) == drop:
            continue
        paths.setdefault(path, {})[method.lower()] = {"deprecated": (method, path) == deprecate}
    paths["/v1/apps/{id}/extraThing"] = {"get": {}}
    version_attrs: dict[str, Any] = {name: {"type": "string"} for name in versions.VERSION_FIELDS}
    version_attrs["brandNewField"] = {"type": "string"}
    if attribute_deprecated:
        version_attrs[attribute_deprecated] = {"$ref": "#/x", "deprecated": True}
    schemas = {"AppStoreVersion": {"properties": {"attributes": {"properties": version_attrs}}}}
    return {"info": {"version": version}, "paths": paths, "components": {"schemas": schemas}}


def zipped(spec: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("openapi.oas.json", json.dumps(spec))
    return buffer.getvalue()


def feed(*versions: str) -> bytes:
    """A PyPI releases feed with the given versions (in the given order)."""
    items = "".join(f"<item><title>{v}</title><link>x</link></item>" for v in versions)
    return f"<?xml version='1.0'?><rss><channel><title>t</title>{items}</channel></rss>".encode()


def make_inputs(
    spec: dict[str, Any] | None = None,
    *,
    latest: dict[str, str] | None = None,
    vulns: bool = False,
    review: MaintenanceRecord | None = None,
    tools: list[str] | None = None,
    spec_status: int = 200,
) -> ReportInputs:
    spec_bytes = zipped(spec or make_spec())
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "developer.apple.com":
            return httpx.Response(spec_status, content=spec_bytes)
        if host == "pypi.org":
            asked.append(path)
            name = path.split("/")[3]
            return httpx.Response(200, content=feed((latest or {}).get(name, "1.0.0")))
        if request.method == "POST":
            count = len(json.loads(request.content)["queries"])
            one = {"vulns": [{"id": "PYSEC-9"}]} if vulns else {}
            return httpx.Response(200, json={"results": [one] * count})
        return httpx.Response(200, json={"summary": "Boom", "affected": []})

    return ReportInputs(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=NOW,
        review=review,
        current_tools=tools or ["list_apps"],
        packages=[("httpx", "1.0.0"), ("pyjwt", "1.0.0"), ("pydantic_core", "2.0.0")],
        read_direct_packages=lambda: [("httpx", "1.0.0"), ("pyjwt", "1.0.0")],
    )
