# Maintenance guide

For the person who looks after this repository. The server stops working for users once their
last review is 30 days old, so the report below is read at least monthly.

## Getting the report

In a terminal:

```
uv run app-store-connect review
```

Or ask Claude to run `maintenance_report`. The report only gathers facts; nothing is changed.

## Reading the report

| Section | What to look for | What to do |
|---|---|---|
| **Apple API spec** | "NEWER SPEC PUBLISHED" next to the latest version | Read Apple's release notes, then follow "Bumping the spec version" below |
| **Endpoints we use** | Any `MISSING` or `DEPRECATED` line | Update the tools to the replacement endpoint, update `ENDPOINTS_USED`, add tests. Do this first: Apple removes deprecated endpoints eventually |
| **Attributes** | "no longer in the spec" or "deprecated" | Stop reading/writing that attribute (for example `appStoreState` was replaced by `appVersionState`). "available but unused" lines are ideas, not problems |
| **Other endpoints in our areas** | A count of endpoints we do not use | Only for awareness. New features may be worth a tool |
| **Apple API release notes** | The link | Skim the entries since last month. The spec cannot show behaviour changes |
| **Running code** | "NOT the code that was last reviewed" | Expected after an update. Read what changed, then confirm a new review |
| **Our dependencies** | Outdated packages | `uv lock --upgrade`, update the `==` pins, run all checks, commit |
| **Known vulnerabilities** | Any OSV id | Upgrade that package at once (`uv lock --upgrade-package <name>`) |
| **Tool changes** | Added or removed tools since the last review | Make sure each change was intended |
| `NOT CHECKED` anywhere | A lookup failed (offline, rate limit) | Never read it as "fine". Run the report again |

## Bumping the spec version (`APPLE_API_SPEC_VERSION`)

Only after checking, never blindly:

1. Run `uv run pytest -m network`. It downloads Apple's published OpenAPI spec and fails if any
   endpoint in `ENDPOINTS_USED` is missing or deprecated, or any attribute we use is gone.
2. Read the new entries in Apple's release notes that touch apps, versions, builds, TestFlight,
   localizations, screenshots, reviews and review submissions.
3. Check that request bodies we send still match the spec (look at the schemas of the write
   endpoints in `spec.py`'s list).
4. Change `APPLE_API_SPEC_VERSION` in `src/app_store_connect/spec.py` to the spec's
   `info.version`.
5. Run the full checks (below) and commit with a message that says what you verified.

## Keeping our own dependencies current

Runtime dependencies (`mcp`, `httpx`, `pyjwt[crypto]`, `cryptography`) are pinned **exactly**
(`==`) in `pyproject.toml`, and `uv.lock` pins everything below them. Why: users install from
git, and a loose range would let a new upstream release reach their machines (and their API key)
before anyone here has looked at it. An exact pin means the code users run only changes when a
maintainer changes it and cuts a release.

Dependabot opens weekly pull requests for Python packages (uv) and GitHub Actions, and waits 7
days after an upstream release before proposing it (`cooldown` in `.github/dependabot.yml`), so
a freshly published, possibly malicious release is not proposed on day one. For a Python
package, the pull request edits the `==` pin and `uv.lock`. Merge when CI is green (including
the `audit` job) and after skimming the upstream changelog. Pinned action SHAs in
`.github/workflows/ci.yml` must stay full commit SHAs.

To update by hand:

```
uv lock --upgrade
uv run pytest -m "not network" && uv run mypy && uv run ruff check
```

then edit the `==` pins in `pyproject.toml` to the versions now in `uv.lock`, run `uv lock`
again, and commit. A test fails if a pin and the lock file disagree.

## Cutting a release

Users who pin a tag only get what you release, so release deliberately:

1. Make sure `main` is green: all five checks below.
2. Run `uv run app-store-connect review` and read the report (see above).
3. Bump `version` in `pyproject.toml` (for example `0.2.0` to `0.2.1`) and add a `CHANGELOG.md`
   entry. Update the tag in the README install command (`@v0.2.0`).
4. Run `uv lock` (it records the new project version) and commit:
   `chore(release): v0.2.1`.
5. Tag the commit and push the tag: `git tag v0.2.1 && git push origin main v0.2.1`.
6. Create the GitHub release from the tag (`gh release create v0.2.1 --notes-from-tag` or the
   web UI) and paste the changelog entry.
7. Afterwards, run the `review` command once from the new tag so `review.json` records the
   released code identity.

## The rules that never loosen

- Read-only by default. Never register a write tool when `ASC_ALLOW_WRITES` is off.
- Every write tool must call `confirm_change` before sending anything. Never add a "confirm"
  argument.
- No tool for arbitrary endpoints, and no web/Apple ID login.
- Every new Apple call must be added to `ENDPOINTS_USED` and be verified against the spec first.
- Never log or return tokens, keys or the issuer ID.

## Checks before every commit

```
uv run ruff check && uv run ruff format --check
uv run mypy
uv run pytest -m "not network"
uv run pytest -m network
```
