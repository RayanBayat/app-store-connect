# How it works

A short guide for contributors: how the server works, where the code lives, and the facts about Apple and MCP that shape it. Plain words on purpose. If something here becomes untrue, fix it in the same pull request.

## The flow of one tool call

1. **Start.** The MCP client starts the server with `uvx`. The server reads its settings from environment variables (`config.py`) and refuses to run if the key file can be read by other users.
2. **Gate.** Before any Apple tool runs, `server.py` checks the monthly review (`review.py`). If it's missing or older than `ASC_REVIEW_MAX_DAYS`, the call is refused and Claude is told to run the review.
3. **Sign.** `auth.py` makes a short-lived signed pass (a JWT, valid for 15 minutes) from the `.p8` key. It is sent only to `api.appstoreconnect.apple.com`.
4. **Call Apple.** `client.py` sends the request. It only allows endpoints listed in `ENDPOINTS_USED` (`spec.py`), checks every ID, never follows redirects, and limits response size.
5. **Read tools** return Apple's data. Text written by people outside the team is wrapped in untrusted-content markers carrying a random value (`tools/base.py`), so it can't fake the end of the marker.
6. **Change tools** first ask the human (`confirmflow.py`, `tools/confirm.py`). The form shows the exact old and new values, in full, with hidden characters made visible. Only an explicit accept sends the request, and only once.

## Tools

- **Read (always on):** `list_apps`, `get_app`, `list_app_store_versions`, `get_version_status`,
  `list_builds`, `get_build`, `list_beta_groups`, `list_beta_testers`,
  `get_version_localizations`, `get_app_info_localizations`, `list_screenshot_sets`,
  `list_customer_reviews`, `get_review_submissions`, `get_app_availability`, `get_age_rating`.
- **Change (only with `ASC_ALLOW_WRITES`, each asks the human):** `update_version_localization`,
  `update_app_info_localization`, `create_version_localization`, `create_app_info_localization`,
  `update_app_primary_locale`, `create_app_store_version`, `select_build_for_version`,
  `add_beta_tester`, `remove_beta_tester`, `add_build_to_beta_group`,
  `submit_build_for_beta_review`, `submit_for_app_review`, `reply_to_customer_review`,
  `upload_screenshot`, `set_territory_availability`, `set_free_price`, `update_age_rating`.
- **Maintenance:** `maintenance_status`, `maintenance_report`, `maintenance_confirm_review`.

## Asking the human: two protocol versions

- **MCP protocol `2026-07-28`** (what current Claude Code uses): a server can't send a form in the middle of a tool call. The tool call returns `InputRequiredResult` with the form and a sealed `request_state`. The client shows the form and calls the tool again with the answer.
- **Older versions:** the server sends the form during the call (`elicit_form`).

The forms have **no input fields**: the text shows the change, and the human pressing Accept is the approval. A required checkbox turned out to be impossible to tick in Claude Code's form, so it was removed.

The sealed state is encrypted, tied to the exact tool, arguments and form text, single-use, and expires after 10 minutes. On the retry everything is re-read from Apple. If anything changed, the user gets a fresh form instead of a write. Both paths are tested in `tests/test_confirm_eras.py`.

## Facts about Apple that shape the code

- **Field limits come from App Store Connect Help, not the API spec.** The spec has no length limits. Current limits: name 2–30 characters, subtitle 30, description 4000, What's New 4000, promotional text 170, keywords 100 **bytes** (letters like å, ä, ö count as 2). See `tools/localizations.py`.
- **`appStoreState` is deprecated.** Use `appVersionState`.
- **Language codes** live in `tools/locales.py` (50 codes, matching Apple's 50 App Store languages).
- **Many Apple documentation pages are rendered with JavaScript** and can't be read by scripts. The monthly report therefore links to Apple's API release notes for a human to skim.
- **The API spec** is published as a zip on developer.apple.com. The `network` test checks every endpoint we use against it, and the report compares versions.

## Availability and price

An app can be "Ready for Sale" and still be missing from a country's App Store. Two things cause it, and `get_app_availability` shows both.

- **Availability** (`tools/availability.py`): one record per territory (`/v1/apps/{id}/appAvailabilityV2`, then `/v2/appAvailabilities/{id}/territoryAvailabilities`). Each has `available`, `releaseDate` and `contentStatuses`, Apple's reasons (for example `MISSING_RATING`). `STATUS_HINTS` turns every status in spec 4.5.1 into plain words; an unknown status says so instead of guessing.
- **Price** (`tools/pricing.py`): an app with no price schedule has no price. The schedule has a base territory and a manual price there; Apple derives the other countries from it.

Changing it: `set_territory_availability` patches each territory (`PATCH /v1/territoryAvailabilities/{id}`, attribute `available`). If the app has no availability yet there is nothing to patch, so it uses the create call (`POST /v2/appAvailabilities`) and only to switch territories on. Territory codes are checked against what Apple returns before any form is shown. `set_free_price` posts a price schedule whose base price is the price point with customer price 0 (`POST /v1/appPriceSchedules`), and refuses to replace an existing paid price.

## Age rating

Apple will not accept an update until every age-rating question is answered. `tools/agerating.py` holds the question list with plain-English labels (yes/no questions, frequency questions, and a few optional choices). `get_age_rating` reads `GET /v1/appInfos/{id}/ageRatingDeclaration` for each app info and lists the unanswered ones. `update_age_rating` checks every name and value against the spec before anything else, refuses app infos that are not in `PREPARE_FOR_SUBMISSION`, `DEVELOPER_REJECTED` or `REJECTED`, shows each change as old -> new, and sends one `PATCH /v1/ageRatingDeclarations/{id}` with only the changed answers. The form says the answers are the developer's legal declaration. `all_others: "none"` fills every yes/no question not listed with false and every frequency question not listed with NONE. The deprecated `ageRatingOverride` is refused (use `ageRatingOverrideV2`).

## The monthly report

`report.py` builds it:
- the spec version, plus missing or deprecated endpoints;
- outdated direct dependencies, read from PyPI's small per-project RSS feed (the full JSON pages are too big for some packages);
- known vulnerabilities in all installed packages (OSV);
- changes in the tool list since the last review.

Every fetch has a size cap and a timeout. Anything that couldn't be checked is shown as **NOT CHECKED**, never as OK.

## Still to verify

- **Screenshot upload host.** Uploads may only go to hosts ending in `.apple.com` (`UPLOAD_HOST_SUFFIX` in `client.py`). Confirm this on a real upload.
- **Availability and price writes** have only been tested against mocks. Check on a real app: that the territory availability IDs pass `valid_id` (they are encoded by Apple), that `availableInNewTerritories: false` is the right default for the create call, and that a schedule with a free base price really makes every country free.
- **Some newer language codes** in `tools/locales.py` (for example `bn-BD`, `or-IN`), which haven't been checked against Apple's code table.

## Where things are

| Path | What it does |
|---|---|
| `server.py` | Tool list, the monthly gate, dispatching calls |
| `tools/` | One file per area: apps, versions, builds, beta, localizations, screenshots, reviews, availability (with territories), pricing |
| `confirmflow.py`, `tools/confirm.py` | Human approval on both protocol versions |
| `client.py`, `spec.py` | Talking to Apple; the allowed endpoint list |
| `auth.py`, `config.py` | Signing; settings and key checks |
| `review.py`, `report.py`, `maintenance.py` | The monthly review |
| `cli.py` | `serve`, `status`, `review`, `doctor` |

## Words used here

- **MCP:** a standard way to give an AI assistant tools.
- **Elicitation:** a question the server asks the human, shown by the client.
- **JWT:** a short-lived signed pass made from your key.
- **Endpoint:** one address in Apple's API, such as `GET /v1/apps`.
- **OSV:** a public database of known security problems in libraries.
