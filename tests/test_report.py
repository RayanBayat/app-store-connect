"""Tests for report.py with GitHub and OSV mocked."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any

import httpx

from app_store_connect import report
from app_store_connect.github import GitHubClient
from app_store_connect.report import ReportInputs, build_report, diff_requirements, replace_lines
from app_store_connect.review import Review

from .conftest import NOW

OLD_MOD = "module m\ngo 1.24.0\nrequire (\n\tgithub.com/a/a v1.0.0\n\tgithub.com/b/b v1.2.0\n)\n"
NEW_MOD = (
    "module m\ngo 1.25.0\nrequire (\n\tgithub.com/a/a v2.0.0\n\tgithub.com/c/c v0.1.0\n)\n"
    "replace github.com/c/c => ../c\n"
)


def make_inputs(
    *,
    latest: str = "0.23.0",
    archived: bool = False,
    license_id: str = "MIT",
    published_days_ago: int = 10,
    advisories: list[dict[str, Any]] | None = None,
    vulns: bool = False,
    review: Review | None = None,
    tools: list[str] | None = None,
    fail_github: bool = False,
) -> ReportInputs:
    published = (NOW - timedelta(days=published_days_ago)).isoformat().replace("+01:00", "Z")

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "api.osv.dev":
            if request.method == "POST":
                n = len(json.loads(request.content)["queries"])
                one = {"vulns": [{"id": "GO-9"}]} if vulns else {}
                return httpx.Response(200, json={"results": [one] * n})
            return httpx.Response(200, json={"summary": "Boom", "affected": []})
        if fail_github:
            return httpx.Response(500)
        if path.endswith("/releases/latest"):
            return httpx.Response(200, json={"tag_name": f"v{latest}", "published_at": published})
        if path.endswith("/releases"):
            releases = [
                {
                    "tag_name": f"v{latest}",
                    "body": "NEWBODY " + "x" * 3000,
                    "published_at": published,
                },
                {"tag_name": "v0.22.0", "body": "OLDBODY", "published_at": published},
            ]
            return httpx.Response(200, json=releases)
        if path.endswith("/security-advisories"):
            return httpx.Response(200, json=advisories or [])
        if "/contents/go.mod" in path:
            tag = request.url.params["ref"]
            return httpx.Response(200, text=OLD_MOD if tag == "v0.22.0" else NEW_MOD)
        repo = {"archived": archived, "pushed_at": published, "license": {"spdx_id": license_id}}
        return httpx.Response(200, json=repo)

    transport = httpx.MockTransport(handler)
    return ReportInputs(
        pinned="0.22.0",
        github=GitHubClient(httpx.Client(transport=transport)),
        osv_client=httpx.Client(transport=transport),
        now=NOW,
        review=review,
        current_tools=tools,
        installed="0.22.0",
    )


def test_report_has_all_sections_and_apple_link() -> None:
    text = build_report(make_inputs())
    for heading in ("## Versions", "## Release notes", "## Red flags", "## Dependencies",
                    "## Tool changes", "## Apple API changes", "## What to decide"):  # fmt: skip
        assert heading in text
    assert report.APPLE_NOTES_URL in text
    assert "an update is available" in text


def test_notes_only_for_newer_releases_and_truncated() -> None:
    text = build_report(make_inputs())
    assert "NEWBODY" in text
    assert "OLDBODY" not in text
    assert "[... truncated]" in text


def test_no_red_flags_when_healthy() -> None:
    assert "None found" in build_report(make_inputs())


def test_red_flags_listed() -> None:
    text = build_report(
        make_inputs(
            archived=True,
            license_id="GPL-3.0",
            published_days_ago=400,
            advisories=[
                {
                    "ghsa_id": "GHSA-x",
                    "severity": "high",
                    "summary": "Bad",
                    "vulnerabilities": [{"vulnerable_version_range": "< 0.23.0"}],
                },
            ],
        )
    )
    assert "ARCHIVED" in text
    assert "licence is now 'GPL-3.0'" in text
    assert "older than 6 months" in text
    assert "GHSA-x" in text


def test_advisory_not_affecting_pinned_version_is_ignored() -> None:
    advisories = [
        {"ghsa_id": "GHSA-old", "vulnerabilities": [{"vulnerable_version_range": "< 0.1"}]}
    ]
    assert "GHSA-old" not in build_report(make_inputs(advisories=advisories))


def test_go_mod_diff_added_removed_bumps_and_replace() -> None:
    text = build_report(make_inputs())
    assert "`github.com/c/c 0.1.0`" in text
    assert "`github.com/b/b 1.2.0`" in text
    assert "`github.com/a/a` 1.0.0 -> 2.0.0" in text
    assert "replace github.com/c/c => ../c" in text


def test_osv_results_for_both_versions() -> None:
    text = build_report(make_inputs(vulns=True))
    assert text.count("GO-9") >= 2


def test_osv_failure_is_not_reported_as_clean() -> None:
    inputs = make_inputs()
    broken = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(500)))
    text = build_report(replace(inputs, osv_client=broken))
    assert "NOT CHECKED" in text


def test_up_to_date_skips_go_mod_diff() -> None:
    text = build_report(make_inputs(latest="0.22.0"))
    assert "up to date" in text
    assert "go.mod changes" not in text


def test_tool_changes_against_snapshot() -> None:
    snapshot = Review(NOW, "0.22.0", "ok", tools=("a", "b"))
    text = build_report(make_inputs(review=snapshot, tools=["b", "c"]))
    assert "- `c`" in text
    assert "- `a`" in text


def test_tool_section_without_snapshot_or_tools() -> None:
    assert "No earlier snapshot. Current tools: 2" in build_report(make_inputs(tools=["a", "b"]))
    assert "not available" in build_report(make_inputs())


def test_github_failure_is_reported_not_raised() -> None:
    text = build_report(make_inputs(fail_github=True))
    assert "NOT COMPLETED" in text
    assert "## What to decide" in text


def test_replace_lines_handles_blocks() -> None:
    text = "replace a => b\nreplace (\n\tc => d\n)\nrequire x v1\n"
    assert replace_lines(text) == ["replace a => b", "replace c => d"]


def test_diff_requirements_ignores_stdlib_bump() -> None:
    diff = diff_requirements([("stdlib", "1.24.0")], [("stdlib", "2.0.0")])
    assert diff.major_bumps == []
