"""Tests for report.py with Apple's spec, PyPI and OSV mocked."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from typing import Any

import httpx
import pytest

from app_store_connect import report
from app_store_connect.report import ReportInputs, build_report
from app_store_connect.review import Review
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


def make_inputs(
    spec: dict[str, Any] | None = None,
    *,
    latest: dict[str, str] | None = None,
    vulns: bool = False,
    review: Review | None = None,
    tools: list[str] | None = None,
    spec_status: int = 200,
) -> ReportInputs:
    spec_bytes = zipped(spec or make_spec())

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "developer.apple.com":
            return httpx.Response(spec_status, content=spec_bytes)
        if host == "pypi.org":
            name = path.split("/")[2]
            return httpx.Response(
                200, json={"info": {"version": (latest or {}).get(name, "1.0.0")}}
            )
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
        packages=[("httpx", "1.0.0"), ("pyjwt", "1.0.0")],
    )


def test_report_has_all_sections_and_apple_link() -> None:
    text = build_report(make_inputs())
    for heading in ("## Apple API spec", "### Endpoints we use", "## Apple API release notes",
                    "## Our dependencies", "## Tool changes", "## What to decide"):  # fmt: skip
        assert heading in text
    assert "https://developer.apple.com/documentation/appstoreconnectapi/" in text


def test_all_endpoints_ok_when_spec_matches() -> None:
    text = build_report(make_inputs())
    assert f"All {len(ENDPOINTS_USED)} endpoints exist and none is deprecated." in text
    assert "same version" in text


def test_missing_and_deprecated_endpoints_flagged() -> None:
    spec = make_spec(drop=("GET", "/v1/apps"), deprecate=("POST", "/v1/appStoreVersions"))
    text = build_report(make_inputs(spec))
    assert "**MISSING**: `GET /v1/apps`" in text
    assert "**DEPRECATED**: `POST /v1/appStoreVersions`" in text


def test_newer_spec_version_is_called_out() -> None:
    assert "NEWER SPEC PUBLISHED" in build_report(make_inputs(make_spec(version="9.9.9")))


def test_attribute_changes_and_unused_attributes() -> None:
    spec = make_spec(attribute_deprecated="copyright")
    text = build_report(make_inputs(spec))
    assert "brandNewField" in text
    assert "AppStoreVersion: deprecated" not in text  # copyright is not an attribute we use
    spec = make_spec(attribute_deprecated="platform")
    assert "AppStoreVersion: deprecated**: platform" in build_report(make_inputs(spec))


def test_other_endpoints_in_our_areas_are_summarised() -> None:
    assert "`apps`: 1 not used" in build_report(make_inputs())


def test_outdated_dependencies_listed() -> None:
    text = build_report(make_inputs(latest={"httpx": "2.0.0"}))
    assert "`httpx` 1.0.0 -> 2.0.0" in text
    assert "pyjwt` 1.0.0 ->" not in text


def test_vulnerabilities_listed() -> None:
    assert "PYSEC-9" in build_report(make_inputs(vulns=True))


def test_tool_changes_against_snapshot() -> None:
    snapshot = Review(NOW, "4.5.1", "ok", tools=("a", "b"))
    text = build_report(make_inputs(review=snapshot, tools=["b", "c"]))
    assert "- added: `c`" in text
    assert "- removed: `a`" in text


def test_no_snapshot_message() -> None:
    assert "No earlier snapshot" in build_report(make_inputs())


def test_spec_download_failure_is_not_reported_as_ok() -> None:
    text = build_report(make_inputs(spec_status=500))
    assert "NOT CHECKED" in text
    assert "exist and none is deprecated" not in text
    assert "## What to decide" in text


def test_osv_failure_is_not_reported_as_clean() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.osv.dev":
            return httpx.Response(500)
        return httpx.Response(200, json={"info": {"version": "1.0.0"}})

    broken = replace(make_inputs(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert "NOT CHECKED" in build_report(broken)


def test_spec_zip_with_bad_content_is_a_report_error() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=b"nope"))
    )
    with pytest.raises(report.ReportError, match="could not be read"):
        report.load_spec(client)


def test_is_outdated() -> None:
    assert report.is_outdated("1.9.0", "1.10.0")
    assert not report.is_outdated("2.0.0rc1", "2.0.0")
    assert not report.is_outdated("1.0", "1.0")


def test_installed_packages_excludes_this_package() -> None:
    names = {name.lower() for name, _ in report.installed_packages()}
    assert "httpx" in names
    assert "app-store-connect" not in names
