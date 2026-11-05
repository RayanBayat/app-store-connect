"""Which code is running? The version, plus the git commit when installed from git.

Why: the monthly review says "a human looked at this code". If the code later changes (a new
tag, a new commit on main), that review does not cover it. We record the identity of the code
at review time and compare it with the running code, so a difference is visible, not silent.

The commit comes from `direct_url.json`, which pip and uv write for installs from a git URL
(PEP 610): `vcs_info.commit_id` is the exact commit that was installed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import metadata
from typing import Final

from app_store_connect import APP_NAME, __version__

SHORT_COMMIT: Final = 12


@dataclass(frozen=True, slots=True)
class CodeIdentity:
    """The version and (when known) the git commit of a copy of this program."""

    version: str
    commit: str = ""

    def describe(self) -> str:
        """Readable form, e.g. 'version 0.2.0, commit 1a2b3c4d5e6f'."""
        commit = f"commit {self.commit[:SHORT_COMMIT]}" if self.commit else "commit unknown"
        return f"version {self.version}, {commit}"


def commit_from_direct_url(text: str | None) -> str:
    """Return vcs_info.commit_id from the text of direct_url.json, or '' when absent."""
    if not text:
        return ""
    try:
        commit = json.loads(text)["vcs_info"]["commit_id"]
    except (ValueError, KeyError, TypeError):
        return ""
    return commit if isinstance(commit, str) else ""


def _direct_url_text() -> str | None:
    """The installed package's direct_url.json, or None (not installed, or not from a URL)."""
    try:
        return metadata.distribution(APP_NAME).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None


def current_identity() -> CodeIdentity:
    """Identity of the code that is running right now."""
    return CodeIdentity(__version__, commit_from_direct_url(_direct_url_text()))


def compare_with_review(
    reviewed: CodeIdentity | None, running: CodeIdentity, *, has_review: bool = True
) -> list[str]:
    """Plain lines saying whether the running code is the code that was last reviewed.

    `has_review` is False when no review exists at all (a first review is coming), which is
    different from an old review record that did not store the code identity.
    """
    if not has_review:
        return [
            f"Running code: {running.describe()}.",
            "No earlier review; this will be the first.",
        ]
    if reviewed is None or not reviewed.version:
        return [
            f"Running code: {running.describe()}.",
            "The last review did not record which code it covered, so nobody can say "
            "whether the running code was reviewed.",
        ]
    if reviewed == running:
        return [f"Running code: {running.describe()}. This is the code that was last reviewed."]
    return [
        f"Running code: {running.describe()}.",
        f"WARNING: the running code is NOT the code that was last reviewed "
        f"({reviewed.describe()}). The review does not cover what is running now; "
        "read what changed before you confirm a new review.",
    ]
