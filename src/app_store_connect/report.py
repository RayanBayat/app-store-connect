"""Build the monthly maintenance report (markdown) a human reads before confirming a review.

The report only gathers facts. It never installs anything and never decides: the closing
"What to decide" section tells the human what is theirs to judge.

Each section is its own small function. When a lookup fails (offline, rate limit) the section
says so plainly instead of pretending everything is fine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from app_store_connect import osv
from app_store_connect.github import (
    GitHubClient,
    GitHubError,
    Release,
    is_newer,
    parse_version,
    range_affects,
)
from app_store_connect.review import Review
from app_store_connect.upstream import UPSTREAM_REPO

APPLE_NOTES_URL = (
    "https://developer.apple.com/documentation/appstoreconnectapi/"
    "app-store-connect-api-release-notes"
)
EXPECTED_LICENSE = "MIT"
STALE_AFTER = timedelta(days=183)  # about six months
NOTE_LIMIT = 1500
MAX_NOTES = 10


@dataclass(frozen=True, slots=True)
class ReportInputs:
    """Everything the report needs, so callers (and tests) can supply it explicitly."""

    pinned: str
    github: GitHubClient
    osv_client: httpx.Client
    now: datetime
    review: Review | None = None
    current_tools: list[str] | None = None
    installed: str | None = None


def truncate(text: str, limit: int = NOTE_LIMIT) -> str:
    """Cut `text` to `limit` characters, marking the cut."""
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n[... truncated]"


def release_notes_section(pinned: str, releases: list[Release]) -> list[str]:
    """Notes of every stable release newer than `pinned`, newest first, each truncated."""
    newer = [r for r in releases if not r.prerelease and is_newer(r.version, pinned)]
    lines = ["## Release notes since the pinned version", ""]
    if not newer:
        return [*lines, "No newer releases.", ""]
    for release in newer[:MAX_NOTES]:
        date = release.published_at.date().isoformat() if release.published_at else "unknown date"
        lines += [f"### {release.tag} ({date})", "", truncate(release.body) or "(no notes)", ""]
    if len(newer) > MAX_NOTES:
        lines += [f"... and {len(newer) - MAX_NOTES} older releases not shown.", ""]
    return lines


def red_flags(inputs: ReportInputs, latest: Release) -> list[str]:
    """Return a bullet for each red flag found (empty when there are none)."""
    flags: list[str] = []
    info = inputs.github.repo_info()
    if info.archived:
        flags.append("The upstream repository is ARCHIVED (no more fixes will come).")
    if info.license_id != EXPECTED_LICENSE:
        flags.append(f"The licence is now {info.license_id!r}; it was {EXPECTED_LICENSE}.")
    if latest.published_at and inputs.now - latest.published_at > STALE_AFTER:
        flags.append(f"The latest release ({latest.tag}) is older than 6 months.")
    for advisory in inputs.github.advisories():
        if any(range_affects(inputs.pinned, r) for r in advisory.vulnerable_ranges):
            flags.append(
                f"Advisory {advisory.ghsa_id} ({advisory.severity}) may affect the pinned "
                f"version: {advisory.summary}"
            )
    return flags


def versions_section(inputs: ReportInputs, latest: Release | None) -> list[str]:
    """Pinned, installed and latest versions."""
    lines = ["## Versions", "", f"- Pinned upstream version: `{inputs.pinned}`"]
    if inputs.installed:
        lines.append(f"- Installed binary: `{inputs.installed}`")
    if latest is None:
        return [*lines, "- Latest release: could not be checked", ""]
    state = "an update is available" if is_newer(latest.version, inputs.pinned) else "up to date"
    return [*lines, f"- Latest release: `{latest.version}` ({state})", ""]


def red_flags_section(inputs: ReportInputs, latest: Release) -> list[str]:
    """Render the red flags section."""
    flags = red_flags(inputs, latest)
    lines = ["## Red flags", ""]
    if not flags:
        return [*lines, "None found (repository active, licence unchanged, no advisories).", ""]
    return [*lines, *(f"- **{flag}**" for flag in flags), ""]


def replace_lines(go_mod: str) -> list[str]:
    """Return every `replace` directive of a go.mod file, one per line."""
    found: list[str] = []
    in_block = False
    for raw in go_mod.splitlines():
        line = raw.strip()
        if in_block:
            if line == ")":
                in_block = False
            elif line:
                found.append(f"replace {line}")
        elif line.startswith("replace ("):
            in_block = True
        elif line.startswith("replace "):
            found.append(line)
    return found


@dataclass(frozen=True, slots=True)
class DepDiff:
    """The differences between two lists of Go requirements."""

    added: list[osv.Query]
    removed: list[osv.Query]
    major_bumps: list[tuple[str, str, str]]


def diff_requirements(old: list[osv.Query], new: list[osv.Query]) -> DepDiff:
    """Compare two requirement lists: added, removed and major-version bumps."""
    old_map, new_map = dict(old), dict(new)
    added = [(n, v) for n, v in new if n not in old_map]
    removed = [(n, v) for n, v in old if n not in new_map]
    bumps: list[tuple[str, str, str]] = []
    for name, new_version in new_map.items():
        old_version = old_map.get(name)
        if old_version is None or name == "stdlib":
            continue
        old_parsed, new_parsed = parse_version(old_version), parse_version(new_version)
        if old_parsed and new_parsed and old_parsed[0] != new_parsed[0]:
            bumps.append((name, old_version, new_version))
    return DepDiff(added, removed, bumps)


def _bullets(title: str, items: list[str]) -> list[str]:
    return [f"**{title}**", "", *(items or ["- none"]), ""]


def dependency_section(inputs: ReportInputs, latest: Release) -> list[str]:
    """go.mod diff pinned to latest, plus OSV results for both versions."""
    old_text = inputs.github.file_at_tag("go.mod", f"v{inputs.pinned}")
    lines = ["## Dependencies and known vulnerabilities", ""]
    lines += [f"**OSV, pinned `{inputs.pinned}`**", "", _osv(inputs, old_text), ""]
    if not is_newer(latest.version, inputs.pinned):
        return lines
    new_text = inputs.github.file_at_tag("go.mod", latest.tag)
    diff = diff_requirements(osv.parse_go_mod(old_text), osv.parse_go_mod(new_text))
    lines += [f"**go.mod changes `{inputs.pinned}` to `{latest.version}`**", ""]
    lines += _bullets("Added", [f"- `{n} {v}`" for n, v in diff.added])
    lines += _bullets("Removed", [f"- `{n} {v}`" for n, v in diff.removed])
    lines += _bullets(
        "Major version bumps", [f"- `{n}` {a} -> {b}" for n, a, b in diff.major_bumps]
    )
    replaces = [f"- `{line}`" for line in replace_lines(new_text)]
    lines += _bullets("`replace` lines in the new version (a red flag: ask why)", replaces)
    return [*lines, f"**OSV, latest `{latest.version}`**", "", _osv(inputs, new_text), ""]


def _osv(inputs: ReportInputs, go_mod_text: str) -> str:
    """Run OSV on one go.mod; failures become an explicit 'not checked' line."""
    try:
        return osv.format_vulnerabilities(osv.check_go_mod(inputs.osv_client, go_mod_text))
    except osv.OsvError as exc:
        return f"NOT CHECKED (never read this as clean): {exc}"


def tools_section(inputs: ReportInputs) -> list[str]:
    """Compare the current upstream tool list with the snapshot of the last review."""
    lines = ["## Tool changes", ""]
    if inputs.current_tools is None:
        return [
            *lines,
            "The current tool list is not available (the server could not be started).",
            "",
        ]
    if inputs.review is None or not inputs.review.tools:
        return [*lines, f"No earlier snapshot. Current tools: {len(inputs.current_tools)}.", ""]
    old, new = set(inputs.review.tools), set(inputs.current_tools)
    added, removed = sorted(new - old), sorted(old - new)
    if not added and not removed:
        return [*lines, "No changes since the last review.", ""]
    lines += _bullets("Added", [f"- `{name}`" for name in added])
    return [*lines, *_bullets("Removed", [f"- `{name}`" for name in removed])]


def apple_section() -> list[str]:
    """Pointer to Apple's own API changes (we cannot read them automatically)."""
    return [
        "## Apple API changes",
        "",
        f"Skim the entries since your last review: {APPLE_NOTES_URL}",
        "",
    ]


def decide_section() -> list[str]:
    """The closing questions, which only a human can answer."""
    return [
        "## What to decide",
        "",
        "1. Any red flag above? If yes, stop and look into it before using the tools.",
        "2. Do the release notes or dependency changes justify a newer pinned version?",
        "   If so, the maintainer follows MAINTENANCE.md. Nothing is upgraded automatically.",
        "3. Is every new tool one you are comfortable giving Claude?",
        "4. Anything in Apple's release notes that affects how you use the API?",
        "",
        "If you are happy to continue, confirm the review. This starts a new review period.",
    ]


def build_report(inputs: ReportInputs) -> str:
    """Return the full report as markdown."""
    lines = [f"# App Store Connect maintenance report ({inputs.now.date().isoformat()})", ""]
    latest: Release | None = None
    try:
        latest = inputs.github.latest_release()
        releases = inputs.github.releases()
        lines += versions_section(inputs, latest)
        lines += release_notes_section(inputs.pinned, releases)
        lines += red_flags_section(inputs, latest)
        lines += dependency_section(inputs, latest)
    except GitHubError as exc:
        lines += versions_section(inputs, latest)
        lines += ["## GitHub checks", "", f"NOT COMPLETED: {exc}", f"Source: {UPSTREAM_REPO}", ""]
    lines += tools_section(inputs)
    lines += apple_section()
    lines += decide_section()
    return "\n".join(lines) + "\n"
