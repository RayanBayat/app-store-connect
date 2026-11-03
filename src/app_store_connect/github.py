"""Read-only helpers for the GitHub REST API (releases, repo info, advisories, files).

Calls are unauthenticated unless GITHUB_TOKEN is set; a token only raises the rate limit.
Nothing here ever writes to GitHub.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from app_store_connect.upstream import UPSTREAM_REPO

API_URL: Final = "https://api.github.com"
TIMEOUT_SECONDS: Final = 20.0
RELEASES_PER_PAGE: Final = 30

Version = tuple[int, ...]


class GitHubError(Exception):
    """A GitHub request failed. The message tells the user what to do next."""


@dataclass(frozen=True, slots=True)
class Release:
    """One published GitHub release."""

    tag: str
    name: str
    body: str
    published_at: datetime | None
    prerelease: bool

    @property
    def version(self) -> str:
        """The tag without its leading 'v'."""
        return self.tag.removeprefix("v")


@dataclass(frozen=True, slots=True)
class RepoInfo:
    """The few repository facts the report cares about."""

    archived: bool
    pushed_at: datetime | None
    license_id: str | None


@dataclass(frozen=True, slots=True)
class Advisory:
    """A published security advisory and the version ranges it says are vulnerable."""

    ghsa_id: str
    severity: str
    summary: str
    published_at: str
    vulnerable_ranges: tuple[str, ...]


def parse_version(text: str) -> Version | None:
    """Turn 'v1.2.3' (or '1.2') into (1, 2, 3); None when it is not numeric dotted."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    """True when `candidate` is a higher version than `current` (unparseable means False)."""
    new, old = parse_version(candidate), parse_version(current)
    return new is not None and old is not None and new > old


def range_affects(version: str, vulnerable_range: str) -> bool:
    """Check a GitHub advisory range such as '>= 0.1, < 0.5' against `version`.

    Ranges we cannot understand count as "affects", because missing a real advisory is worse
    than showing a false alarm.
    """
    current = parse_version(version)
    if current is None:
        return True
    for clause in vulnerable_range.split(","):
        match = re.fullmatch(r"\s*(<=|>=|<|>|=)?\s*v?(\d+(?:\.\d+)*)\s*", clause)
        if match is None:
            return True
        bound = tuple(int(p) for p in match.group(2).split("."))
        op = match.group(1) or "="
        ok = {
            "<": current < bound,
            "<=": current <= bound,
            ">": current > bound,
            ">=": current >= bound,
            "=": current == bound,
        }[op]
        if not ok:
            return False
    return True


def _parse_date(value: object) -> datetime | None:
    """Parse GitHub's ISO timestamps (ending in Z); None when absent."""
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _release_from_json(raw: dict[str, Any]) -> Release:
    return Release(
        tag=str(raw.get("tag_name", "")),
        name=str(raw.get("name") or raw.get("tag_name") or ""),
        body=str(raw.get("body") or ""),
        published_at=_parse_date(raw.get("published_at")),
        prerelease=bool(raw.get("prerelease")),
    )


class GitHubClient:
    """A tiny GitHub REST client bound to the upstream repository."""

    def __init__(self, client: httpx.Client | None = None, repo: str = UPSTREAM_REPO) -> None:
        self._repo = repo
        self._client = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True)

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get(
        self, path: str, accept: str = "application/vnd.github+json", **params: str
    ) -> httpx.Response:
        """GET one API path and turn every failure into a `GitHubError` with advice."""
        url = f"{API_URL}/repos/{self._repo}{path}"
        try:
            response = self._client.get(url, headers=self._headers(accept), params=params)
        except httpx.HTTPError as exc:
            raise GitHubError(f"Could not reach GitHub ({url}): {exc}") from exc
        if (
            response.status_code in (403, 429)
            and response.headers.get("x-ratelimit-remaining") == "0"
        ):
            raise GitHubError(
                "GitHub's rate limit for anonymous requests is used up. Wait a while, or set "
                "the GITHUB_TOKEN environment variable (any token with no permissions works)."
            )
        if response.status_code >= 400:
            raise GitHubError(f"GitHub answered {response.status_code} for {url}.")
        return response

    def releases(self) -> list[Release]:
        """Return the newest published, non-draft releases (newest first)."""
        data = self._get("/releases", per_page=str(RELEASES_PER_PAGE)).json()
        return [_release_from_json(item) for item in data if not item.get("draft")]

    def latest_release(self) -> Release:
        """Return the latest stable release."""
        return _release_from_json(self._get("/releases/latest").json())

    def repo_info(self) -> RepoInfo:
        """Return archived flag, last push date and licence id."""
        data = self._get("").json()
        licence = data.get("license") or {}
        return RepoInfo(
            archived=bool(data.get("archived")),
            pushed_at=_parse_date(data.get("pushed_at")),
            license_id=licence.get("spdx_id"),
        )

    def advisories(self) -> list[Advisory]:
        """Return published security advisories for the repository."""
        data = self._get("/security-advisories").json()
        return [
            Advisory(
                ghsa_id=str(item.get("ghsa_id", "")),
                severity=str(item.get("severity", "unknown")),
                summary=str(item.get("summary", "")),
                published_at=str(item.get("published_at", "")),
                vulnerable_ranges=tuple(
                    str(v.get("vulnerable_version_range", ""))
                    for v in item.get("vulnerabilities") or []
                ),
            )
            for item in data
        ]

    def file_at_tag(self, path: str, tag: str) -> str:
        """Return the text of `path` at git tag `tag` (for example go.mod at v0.22.0)."""
        response = self._get(f"/contents/{path}", "application/vnd.github.raw+json", ref=tag)
        return response.text
