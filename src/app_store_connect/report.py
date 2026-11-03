"""Build the monthly maintenance report (markdown) a human reads before confirming a review.

The report only gathers facts. It never changes anything and never decides: the closing
"What to decide" section lists what is for the human to judge.

Each section is its own small function. When a lookup fails (offline, rate limit) the section
says so plainly instead of pretending everything is fine.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from importlib import metadata
from typing import Any, Final

import httpx

from app_store_connect import osv
from app_store_connect.httpio import DownloadError, fetch_limited
from app_store_connect.identity import CodeIdentity, compare_with_review, current_identity
from app_store_connect.review import Review
from app_store_connect.spec import (
    APPLE_API_SPEC_VERSION,
    ENDPOINTS_USED,
    RELEASE_NOTES_URL,
    SPEC_URL,
    Endpoint,
)
from app_store_connect.tools import (
    agerating,
    apps,
    beta,
    builds,
    localizations,
    reviews,
    versions,
)

MAX_SPEC_ZIP_BYTES: Final = 30 * 1024 * 1024
MAX_SPEC_JSON_BYTES: Final = 60 * 1024 * 1024
MAX_PYPI_BYTES: Final = 1024 * 1024  # a releases feed is about 15 KB
# Apple's spec URL needs no redirect today; if it ever does, only Apple's own site is allowed.
SPEC_REDIRECT_HOSTS: Final = ("developer.apple.com",)
SPEC_MEMBER: Final = "openapi.oas.json"
PYPI_FEED_URL: Final = "https://pypi.org/rss/project/{name}/releases.xml"
OWN_PACKAGE: Final = "app-store-connect"
METHODS: Final = ("get", "post", "patch", "put", "delete")
EXAMPLES_SHOWN: Final = 8

# Apple schema name -> the attribute names our tools read or write.
USED_ATTRIBUTES: Final[dict[str, tuple[str, ...]]] = {
    "App": apps.APP_FIELDS,
    "AppStoreVersion": versions.VERSION_FIELDS,
    "Build": builds.BUILD_FIELDS,
    "BetaGroup": beta.GROUP_FIELDS,
    "BetaTester": beta.TESTER_FIELDS,
    "AppStoreVersionLocalization": localizations.VERSION_FIELDS,
    "AppInfoLocalization": localizations.INFO_FIELDS,
    "CustomerReview": (*reviews.REVIEW_FIELDS, "title", "body"),
    "ReviewSubmission": ("platform", "state", "submittedDate"),
    "AppAvailabilityV2": ("availableInNewTerritories",),
    "TerritoryAvailability": ("available", "releaseDate", "contentStatuses"),
    "Territory": ("currency",),
    "AppPricePointV3": ("customerPrice",),
    "AppPriceV2": ("startDate", "endDate"),
    "AppInfo": ("state",),
    "AgeRatingDeclaration": agerating.DECLARATION_FIELDS,
}


@dataclass(frozen=True, slots=True)
class ReportFacts:
    """The report's own figures. The server (not the model) shows them in the review form."""

    spec_checked: bool
    missing_endpoints: int
    deprecated_endpoints: int
    outdated: int | None  # None: the lookup failed
    vulnerabilities: int | None  # None: the lookup failed

    @property
    def not_checked(self) -> int:
        """How many parts of the report could not be checked."""
        return sum(not ok for ok in (self.spec_checked, self.outdated is not None,
                                     self.vulnerabilities is not None))  # fmt: skip


@dataclass(frozen=True, slots=True)
class ReportResult:
    """The report text and the figures taken from it."""

    text: str
    facts: ReportFacts


class ReportError(Exception):
    """A lookup needed for the report failed."""


@dataclass(frozen=True, slots=True)
class SpecInfo:
    """What we need from Apple's OpenAPI spec."""

    version: str
    operations: dict[Endpoint, bool]  # (METHOD, path) -> deprecated?
    attributes: dict[str, dict[str, bool]]  # schema -> {attribute -> deprecated?}


@dataclass(frozen=True, slots=True)
class ReportInputs:
    """Everything the report needs, so callers (and tests) can supply it explicitly."""

    client: httpx.Client
    now: datetime
    review: Review | None
    current_tools: list[str]
    packages: list[osv.Query]  # every installed runtime package (for the vulnerability scan)
    direct_packages: list[osv.Query] = field(default_factory=list)  # our own == pins
    code: CodeIdentity = field(default_factory=current_identity)


# ---------------------------------------------------------------- Apple's spec


def read_limited(client: httpx.Client, url: str, limit: int) -> bytes:
    """GET Apple's spec: size-capped, and redirects only to developer.apple.com hosts."""
    try:
        return fetch_limited(client, "GET", url, limit=limit, redirect_hosts=SPEC_REDIRECT_HOSTS)
    except DownloadError as exc:
        raise ReportError(str(exc)) from exc


def parse_spec(spec: dict[str, Any]) -> SpecInfo:
    """Reduce Apple's OpenAPI document to the facts the report needs."""
    operations: dict[Endpoint, bool] = {}
    for path, item in (spec.get("paths") or {}).items():
        for method in METHODS:
            if method in item:
                operations[(method.upper(), path)] = bool(item[method].get("deprecated"))
    schemas = (spec.get("components") or {}).get("schemas") or {}
    attributes = {name: _attributes_of(schemas, name) for name in USED_ATTRIBUTES}
    return SpecInfo(str((spec.get("info") or {}).get("version", "unknown")), operations, attributes)


def _attributes_of(schemas: dict[str, Any], name: str) -> dict[str, bool]:
    """Map attribute name -> deprecated flag for one resource schema."""
    attrs = ((schemas.get(name) or {}).get("properties") or {}).get("attributes") or {}
    if "$ref" in attrs:
        attrs = schemas.get(attrs["$ref"].rsplit("/", 1)[-1]) or {}
    return {
        key: bool(value.get("deprecated")) for key, value in (attrs.get("properties") or {}).items()
    }


def load_spec(client: httpx.Client) -> SpecInfo:
    """Download Apple's published spec zip, read the JSON inside, and parse it."""
    raw = read_limited(client, SPEC_URL, MAX_SPEC_ZIP_BYTES)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            info = archive.getinfo(SPEC_MEMBER)
            if info.file_size > MAX_SPEC_JSON_BYTES:
                raise ReportError("The spec file inside the zip is unexpectedly large.")
            data = json.loads(archive.read(info))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ReportError(f"Apple's spec download could not be read: {exc}") from exc
    return parse_spec(data)


def endpoint_problems(spec: SpecInfo) -> tuple[list[Endpoint], list[Endpoint]]:
    """Return (missing, deprecated) endpoints among those we use, each sorted."""
    missing = sorted(e for e in ENDPOINTS_USED if e not in spec.operations)
    deprecated = sorted(e for e in ENDPOINTS_USED if spec.operations.get(e) is True)
    return missing, deprecated


def spec_section(spec: SpecInfo) -> list[str]:
    """Versions plus the endpoint check."""
    state = "same version" if spec.version == APPLE_API_SPEC_VERSION else "NEWER SPEC PUBLISHED"
    lines = [
        "## Apple API spec",
        "",
        f"- Checked against: `{APPLE_API_SPEC_VERSION}`",
        f"- Latest published: `{spec.version}` ({state})",
        "",
        "### Endpoints we use",
        "",
    ]
    missing, deprecated = endpoint_problems(spec)
    if not missing and not deprecated:
        return [*lines, f"All {len(ENDPOINTS_USED)} endpoints exist and none is deprecated.", ""]
    lines += [f"- **MISSING**: `{m} {p}`" for m, p in missing]
    lines += [f"- **DEPRECATED**: `{m} {p}`" for m, p in deprecated]
    return [*lines, ""]


def attribute_section(spec: SpecInfo) -> list[str]:
    """Attributes we use that vanished or are deprecated, and attributes we do not use."""
    lines = ["### Attributes in the resources we use", ""]
    notes: list[str] = []
    for schema, used in USED_ATTRIBUTES.items():
        available = spec.attributes.get(schema, {})
        gone = [name for name in used if name not in available]
        old = [name for name in used if available.get(name)]
        unused = sorted(n for n, dep in available.items() if n not in used and not dep)
        if gone:
            notes.append(f"- **{schema}: no longer in the spec**: {', '.join(gone)}")
        if old:
            notes.append(f"- **{schema}: deprecated**: {', '.join(old)}")
        if unused:
            notes.append(f"- {schema}: available but unused: {', '.join(unused[:EXAMPLES_SHOWN])}")
    return [*lines, *(notes or ["No changes affecting us."]), ""]


def area_section(spec: SpecInfo) -> list[str]:
    """Other endpoints in the same areas as ours (for awareness; they are not used)."""
    roots = {path.split("/")[2] for _, path in ENDPOINTS_USED}
    lines = ["### Other endpoints in the areas we use", ""]
    for root in sorted(roots):
        extra = sorted(
            {p for _, p in spec.operations if p.split("/")[2:3] == [root]}
            - {p for _, p in ENDPOINTS_USED}
        )
        if extra:
            shown = ", ".join(f"`{p}`" for p in extra[:3])
            lines.append(f"- `{root}`: {len(extra)} not used, e.g. {shown}")
    return [*lines, ""]


# ---------------------------------------------------------------- dependencies


def installed_packages() -> list[osv.Query]:
    """(name, version) of every installed distribution except this package, sorted by name."""
    found = {
        (dist.metadata["Name"], dist.version)
        for dist in metadata.distributions()
        if dist.metadata["Name"] and dist.metadata["Name"].lower() != OWN_PACKAGE
    }
    return sorted(found, key=lambda item: item[0].lower())


def direct_packages() -> list[osv.Query]:
    """(name, installed version) of our direct runtime dependencies, from our own metadata."""
    try:
        requirements = metadata.requires(OWN_PACKAGE) or []
    except metadata.PackageNotFoundError:
        return []
    names = {
        match.group(0)
        for text in requirements
        if "extra ==" not in text and (match := re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", text))
    }
    installed = dict(installed_packages())
    by_key = {key.lower().replace("_", "-"): (key, ver) for key, ver in installed.items()}
    found = [
        by_key[n.lower().replace("_", "-")] for n in names if n.lower().replace("_", "-") in by_key
    ]
    return sorted(found, key=lambda item: item[0].lower())


def version_tuple(text: str) -> tuple[int, ...]:
    """The leading numeric part of a version ('1.2.3rc1' -> (1, 2, 3))."""
    match = re.match(r"\d+(?:\.\d+)*", text)
    return tuple(int(p) for p in match.group(0).split(".")) if match else ()


def is_outdated(installed: str, latest: str) -> bool:
    """True when `latest` is a higher version than `installed`."""
    return version_tuple(latest) > version_tuple(installed)


_FEED_TITLE = re.compile(r"<item>\s*<title>([^<]+)</title>")
_STABLE_VERSION = re.compile(r"\d+(?:\.\d+)*(?:\.post\d+)?")


def latest_stable(feed: str) -> str | None:
    """The highest stable version named in a PyPI releases feed (pre-releases are ignored).

    The feed is read with a plain pattern, not an XML parser, so nothing in it can trigger
    entity tricks; we only ever look at the `<title>` of each `<item>`.
    """
    stable = [t.strip() for t in _FEED_TITLE.findall(feed) if _STABLE_VERSION.fullmatch(t.strip())]
    return max(stable, key=version_tuple, default=None)


def latest_on_pypi(client: httpx.Client, name: str) -> str:
    """Return the newest stable release version of `name` on PyPI.

    Uses the small per-project releases feed. PyPI's JSON API lists every file of every
    release, which is megabytes for big packages such as cryptography or pydantic-core.
    """
    try:
        raw = fetch_limited(client, "GET", PYPI_FEED_URL.format(name=name), limit=MAX_PYPI_BYTES)
    except DownloadError as exc:
        raise ReportError(f"PyPI lookup for {name} failed: {exc}") from exc
    latest = latest_stable(raw.decode("utf-8", "replace"))
    if latest is None:
        raise ReportError(f"PyPI lookup for {name} failed: no stable release in the feed.")
    return latest


def outdated_section(inputs: ReportInputs) -> tuple[list[str], int]:
    """List our direct dependencies (the == pins) that have a newer release, and how many.

    Packages below them are kept current by `uv lock --upgrade` and are covered by the
    vulnerability scan, so asking PyPI about every one of them is not needed.
    """
    lines = ["## Our dependencies", "", "### Outdated", ""]
    outdated: list[str] = []
    for name, installed in inputs.direct_packages:
        latest = latest_on_pypi(inputs.client, name)
        if is_outdated(installed, latest):
            outdated.append(f"- `{name}` {installed} -> {latest}")
    note = "Fix with `uv lock --upgrade` (see MAINTENANCE.md)." if outdated else ""
    text = [
        *lines,
        *(outdated or ["All installed packages are on their latest release."]),
        "",
        note,
        "",
    ]
    return text, len(outdated)


def vulnerability_section(inputs: ReportInputs) -> tuple[list[str], int]:
    """OSV results for the installed packages, and how many vulnerabilities were found."""
    vulns = osv.check_packages(inputs.client, inputs.packages)
    text = ["### Known vulnerabilities (OSV, PyPI)", "", osv.format_vulnerabilities(vulns), ""]
    return text, len(vulns)


# ---------------------------------------------------------------- the rest


def tools_section(inputs: ReportInputs) -> list[str]:
    """Compare our tool list with the snapshot of the last review."""
    lines = ["## Tool changes", ""]
    if inputs.review is None or not inputs.review.tools:
        return [*lines, f"No earlier snapshot. Tools now: {len(inputs.current_tools)}.", ""]
    old, new = set(inputs.review.tools), set(inputs.current_tools)
    added, removed = sorted(new - old), sorted(old - new)
    if not added and not removed:
        return [*lines, "No changes since the last review.", ""]
    lines += [f"- added: `{name}`" for name in added]
    return [*lines, *(f"- removed: `{name}`" for name in removed), ""]


def notes_section() -> list[str]:
    """Pointer to Apple's own release notes (read them for changes the spec cannot show)."""
    return [
        "## Apple API release notes",
        "",
        f"Skim the entries since the last review: {RELEASE_NOTES_URL}",
        "",
    ]


def decide_section() -> list[str]:
    """The closing questions, which only a human can answer."""
    return [
        "## What to decide",
        "",
        "1. Is any endpoint missing or deprecated? If so the maintainer must update the tools.",
        "2. Is a newer Apple spec published? The maintainer checks it, then bumps the pin.",
        "3. Are dependencies outdated or vulnerable? The maintainer runs `uv lock --upgrade`.",
        "4. Do the Apple release notes mention anything that affects how you use the API?",
        "",
        "Nothing here is changed automatically. If you are happy to continue, confirm the review "
        "to start a new review period. Details: MAINTENANCE.md.",
    ]


def guarded(
    section: Callable[[ReportInputs], tuple[list[str], int]], inputs: ReportInputs
) -> tuple[list[str], int | None]:
    """Run a section builder; if its lookup fails, return a plain 'NOT CHECKED' note instead.

    The number is the section's count, or None when it could not be checked.
    """
    try:
        return section(inputs)
    except (ReportError, osv.OsvError) as exc:
        return [f"**NOT CHECKED** (never read this as OK): {exc}", ""], None


def code_section(inputs: ReportInputs) -> list[str]:
    """Which code is running, and whether it is the code that was last reviewed."""
    reviewed = inputs.review.code if inputs.review else None
    return [
        "## Running code",
        "",
        *compare_with_review(reviewed, inputs.code, has_review=inputs.review is not None),
        "",
    ]


def build_report_with_facts(inputs: ReportInputs) -> ReportResult:
    """Return the full report as markdown, plus the figures the confirmation form repeats."""
    lines = [f"# App Store Connect maintenance report ({inputs.now.date().isoformat()})", ""]
    lines += code_section(inputs)
    missing = deprecated = 0
    try:
        spec = load_spec(inputs.client)
    except ReportError as exc:
        spec_checked = False
        lines += ["## Apple API spec", "", f"**NOT CHECKED** (never read this as OK): {exc}", ""]
    else:
        spec_checked = True
        found_missing, found_deprecated = endpoint_problems(spec)
        missing, deprecated = len(found_missing), len(found_deprecated)
        lines += spec_section(spec) + attribute_section(spec) + area_section(spec)
    lines += notes_section()
    outdated_lines, outdated = guarded(outdated_section, inputs)
    vuln_lines, vulns = guarded(vulnerability_section, inputs)
    lines += outdated_lines + vuln_lines
    lines += tools_section(inputs)
    lines += decide_section()
    facts = ReportFacts(spec_checked, missing, deprecated, outdated, vulns)
    return ReportResult("\n".join(lines) + "\n", facts)


def build_report(inputs: ReportInputs) -> str:
    """Return the full report as markdown."""
    return build_report_with_facts(inputs).text
