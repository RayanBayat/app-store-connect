"""Tests for report.py with Apple's spec, PyPI and OSV mocked."""

from __future__ import annotations

import json
from dataclasses import replace
from importlib import metadata

import httpx
import pytest

from app_store_connect import report
from app_store_connect.maintenance_record import MaintenanceRecord
from app_store_connect.spec import ENDPOINTS_USED

from .conftest import NOW
from .report_fake import feed, make_inputs, make_spec, report_text


def test_report_has_all_sections_and_apple_link() -> None:
    text = report_text(make_inputs())
    for heading in (
        "## Apple API spec",
        "### Endpoints we use",
        "## Apple API release notes",
        "## Our dependencies",
        "## Tool changes",
        "## What to decide",
    ):
        assert heading in text
    assert "https://developer.apple.com/documentation/appstoreconnectapi/" in text


def test_all_endpoints_ok_when_spec_matches() -> None:
    text = report_text(make_inputs())
    assert f"All {len(ENDPOINTS_USED)} endpoints exist and none is deprecated." in text
    assert "same version" in text


def test_missing_and_deprecated_endpoints_flagged() -> None:
    spec = make_spec(drop=("GET", "/v1/apps"), deprecate=("POST", "/v1/appStoreVersions"))
    text = report_text(make_inputs(spec))
    assert "**MISSING**: `GET /v1/apps`" in text
    assert "**DEPRECATED**: `POST /v1/appStoreVersions`" in text


def test_newer_spec_version_is_called_out() -> None:
    assert "NEWER SPEC PUBLISHED" in report_text(make_inputs(make_spec(version="9.9.9")))


def test_attribute_changes_and_unused_attributes() -> None:
    spec = make_spec(attribute_deprecated="copyright")
    text = report_text(make_inputs(spec))
    assert "brandNewField" in text
    assert "AppStoreVersion: deprecated" not in text  # copyright is not an attribute we use
    spec = make_spec(attribute_deprecated="platform")
    assert "AppStoreVersion: deprecated**: platform" in report_text(make_inputs(spec))


def test_other_endpoints_in_our_areas_are_summarised() -> None:
    assert "`apps`: 1 not used" in report_text(make_inputs())


def test_outdated_dependencies_listed() -> None:
    text = report_text(make_inputs(latest={"httpx": "2.0.0"}))
    assert "`httpx` 1.0.0 -> 2.0.0" in text
    assert "pyjwt` 1.0.0 ->" not in text


def test_only_direct_dependencies_are_checked_for_new_releases_but_osv_sees_all() -> None:
    inputs = make_inputs(vulns=True)
    asked: list[str] = []
    osv_names: list[str] = []
    inner = inputs.client._transport  # the mock transport built in make_inputs

    def spy(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pypi.org":
            asked.append(request.url.path)
        if request.url.host == "api.osv.dev" and request.method == "POST":
            osv_names.extend(q["package"]["name"] for q in json.loads(request.content)["queries"])
        return inner.handle_request(request)

    spied = replace(inputs, client=httpx.Client(transport=httpx.MockTransport(spy)))
    report_text(spied)
    assert sorted(asked) == ["/rss/project/httpx/releases.xml", "/rss/project/pyjwt/releases.xml"]
    assert "pydantic_core" in osv_names and "httpx" in osv_names


def test_latest_stable_ignores_prereleases_and_order() -> None:
    text = feed("2.0.0rc1", "1.9.0", "3.0.0.dev1", "1.10.0", "2.0.0a1", "1.10.0b2").decode()
    assert report.latest_stable(text) == "1.10.0"
    assert report.latest_stable(feed("1.0rc1").decode()) is None


def test_feed_problems_are_not_checked_not_ok() -> None:
    for response in (httpx.Response(500), httpx.Response(200, content=feed("1.0.0rc1"))):
        client = httpx.Client(transport=httpx.MockTransport(lambda _r, r=response: r))
        with pytest.raises(report.ReportError, match="PyPI lookup"):
            report.latest_on_pypi(client, "httpx")
    broken = replace(
        make_inputs(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: (
                    httpx.Response(500)
                    if r.url.host == "pypi.org"
                    else httpx.Response(200, json={"results": [{}, {}, {}]})
                )
            )
        ),
    )
    assert "NOT CHECKED" in report_text(broken)


def test_direct_packages_are_our_pins_from_package_metadata() -> None:
    names = {name.lower() for name, _ in report.direct_packages()}
    assert {"httpx", "mcp", "pyjwt", "cryptography"} <= names
    assert "pydantic-core" not in names and "pydantic_core" not in names


def test_missing_package_metadata_shows_not_checked_never_all_up_to_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_metadata(_name: str) -> list[str]:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(metadata, "requires", no_metadata)
    with pytest.raises(report.ReportError, match="cannot be listed"):
        report.direct_packages()
    text = report_text(replace(make_inputs(), read_direct_packages=report.direct_packages))
    assert "NOT CHECKED" in text
    assert "All installed packages are on their latest release" not in text


def test_area_of_an_endpoint_is_the_part_after_the_version() -> None:
    assert report.area_of("/v1/apps/{id}/appInfos") == "apps"
    assert report.area_of("/v2/appAvailabilities/{id}") == "appAvailabilities"
    assert report.area_of("/v1") == ""


def test_vulnerabilities_listed() -> None:
    assert "PYSEC-9" in report_text(make_inputs(vulns=True))


def test_tool_changes_against_snapshot() -> None:
    snapshot = MaintenanceRecord(NOW, "4.5.1", "ok", tools=("a", "b"))
    text = report_text(make_inputs(review=snapshot, tools=["b", "c"]))
    assert "- added: `c`" in text
    assert "- removed: `a`" in text


def test_no_snapshot_message() -> None:
    assert "No earlier snapshot" in report_text(make_inputs())


def test_spec_download_failure_is_not_reported_as_ok() -> None:
    text = report_text(make_inputs(spec_status=500))
    assert "NOT CHECKED" in text
    assert "exist and none is deprecated" not in text
    assert "## What to decide" in text


def test_osv_failure_is_not_reported_as_clean() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.osv.dev":
            return httpx.Response(500)
        return httpx.Response(200, json={"info": {"version": "1.0.0"}})

    broken = replace(make_inputs(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert "NOT CHECKED" in report_text(broken)


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
