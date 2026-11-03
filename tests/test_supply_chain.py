"""H1: runtime dependencies are pinned exactly and CI/Dependabot follow the hardening rules."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_runtime_dependencies_are_pinned_exactly_and_include_cryptography() -> None:
    deps = tomllib.loads(read("pyproject.toml"))["project"]["dependencies"]
    names = {re.split(r"[\[=<>~!]", dep, maxsplit=1)[0] for dep in deps}
    assert {"mcp", "httpx", "pyjwt", "cryptography"} <= names
    for dep in deps:
        assert re.fullmatch(r"[A-Za-z0-9_.-]+(\[[a-z,]+\])?==\d+(\.\d+)*", dep), dep


def test_lock_file_matches_the_pins() -> None:
    lock = read("uv.lock")
    for dep in tomllib.loads(read("pyproject.toml"))["project"]["dependencies"]:
        name, version = dep.split("==")
        name = name.split("[")[0]
        assert re.search(rf'name = "{name}"\nversion = "{re.escape(version)}"', lock), dep


def test_version_is_0_2_0_and_changelog_has_an_entry() -> None:
    assert tomllib.loads(read("pyproject.toml"))["project"]["version"] == "0.2.0"
    assert "## 0.2.0" in read("CHANGELOG.md")


def test_ci_uses_locked_installs_and_no_persisted_credentials() -> None:
    ci = read(".github/workflows/ci.yml")
    assert "uv run ruff" not in ci.replace("uv run --locked ruff", "")
    assert "uv run pytest" not in ci.replace("uv run --locked pytest", "")
    assert "uv run mypy" not in ci.replace("uv run --locked mypy", "")
    checkouts = ci.count("actions/checkout@")
    assert checkouts >= 5
    assert ci.count("persist-credentials: false") == checkouts
    setups = ci.count("astral-sh/setup-uv@")
    assert len(re.findall(r'version: "\d+\.\d+\.\d+"', ci)) == setups


def test_ci_audits_dependencies_for_known_vulnerabilities() -> None:
    ci = read(".github/workflows/ci.yml")
    assert "uv export --frozen --no-dev --no-hashes" in ci
    assert re.search(r"pip-audit==\d+\.\d+\.\d+", ci)


def test_dependabot_waits_7_days_for_both_ecosystems() -> None:
    text = read(".github/dependabot.yml")
    assert text.count("cooldown:") == 2
    assert text.count("default-days: 7") == 2
    assert "github-actions" in text and "package-ecosystem: uv" in text


def test_readme_recommends_the_pinned_form_without_refresh() -> None:
    readme = read("README.md")
    pinned = [ln for ln in readme.splitlines() if "claude mcp add" in ln and "@v0.2.0" in ln]
    assert len(pinned) == 1 and "--refresh" not in pinned[0]
    latest = [ln for ln in readme.splitlines() if "claude mcp add" in ln and "@main" in ln]
    assert len(latest) == 1 and "--refresh" in latest[0]
    assert "Always latest (the maintainer's own setup)" in readme
    assert "change the tag" in readme.lower()


def test_docs_say_the_protections_assume_the_agent_cannot_run_commands() -> None:
    for name in ("README.md", "docs/security.md"):
        text = read(name)
        assert "cannot run arbitrary commands as your" in text, name
    security = " ".join(read("docs/security.md").split())
    for needle in ("Read(~/.config/app-store-connect/**)", "Edit(~/.local/state/app-store-connect/**)",
                   "bypass", "hook", "separate OS user", "sandbox"):  # fmt: skip
        assert needle in security, needle


def test_maintenance_guide_covers_releases_pins_and_dependabot() -> None:
    text = read("MAINTENANCE.md")
    for needle in ("Cutting a release", "uv lock", "vX", "pinned **exactly**", "Dependabot"):
        if needle != "vX":
            assert needle in text, needle
    assert "git tag v" in text and "gh release create" in text
