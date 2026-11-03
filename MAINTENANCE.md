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
| **Our dependencies** | Outdated packages | `uv lock --upgrade`, run all checks, commit |
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

```
uv lock --upgrade
uv run pytest -m "not network" && uv run mypy && uv run ruff check
```

Dependabot also opens weekly pull requests for Python packages (uv) and GitHub Actions. Merge them
when CI is green. Pinned action SHAs in `.github/workflows/ci.yml` must stay full commit SHAs.

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
