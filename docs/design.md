# How it works

A short guide for contributors: how the server works, where the code lives, and the facts about Apple and MCP that shape it. Plain words on purpose. If something here becomes untrue, fix it in the same pull request.

## Start here

A reading order for someone new to the code. Each step is small; stop and try things as you go.

1. Read the [README](../README.md), then "The flow of one tool call" and "Words used here" in this file.
2. `cli.py`: read `main()`. Then `server.py`: how a call is checked and dispatched.
3. `tools/base.py` (the building blocks every tool uses), then `tools/apps.py` (the smallest tool module, one read tool and one write tool).
4. `tools/confirm.py` (how a tool asks the human), then `confirmflow.py` (what makes that safe on both protocols).
5. `spec.py` (the endpoints we may call), `client.py` (how we call Apple), `auth.py` (the signed token), `config.py` (the settings).
6. `maintenance_record.py` (proof of the monthly review), `maintenance.py` (the three maintenance tools), `report.py` (the report itself).
7. Tests: read `tests/apple_fake.py` (the fake Apple and the fake human), then `tests/test_tools_read.py`. Run `uv run pytest -k list_apps -v` to watch one test pass. Then break an assertion on purpose (for example change an expected name) and run it again to see how a failure looks.
8. `tests/test_confirm_eras.py` (confirmations on both protocols) and [docs/security.md](security.md) (why the rules exist).

## The flow of one tool call

1. **Start.** The MCP client starts the server with `uvx`. The server reads its settings from environment variables (`config.py`) and refuses to run if the key file can be read by other users.
2. **Gate.** Before any Apple tool runs, `server.py` checks the maintenance record (`maintenance_record.py`). If it's missing or older than `ASC_REVIEW_MAX_DAYS`, the call is refused and Claude is told to run the review.
3. **Sign.** `auth.py` makes a short-lived signed pass (a JWT, valid for 15 minutes) from the `.p8` key. It is sent only to `api.appstoreconnect.apple.com`.
4. **Call Apple.** `client.py` sends the request. It only allows endpoints listed in `ENDPOINTS_USED` (`spec.py`), checks every ID, never follows redirects, and limits response size.
5. **Read tools** return Apple's data. Text written by people outside the team is wrapped in untrusted-content markers carrying a random value (`textsafe.py`), so it can't fake the end of the marker.
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
- **Argument names are always snake_case**, even for text fields that Apple spells in camelCase. The update and create tools for store text share one table (`ARGUMENT_TO_ATTRIBUTE` in `tools/localizations.py`): `whats_new` is sent as `whatsNew`, `promotional_text` as `promotionalText`, `marketing_url` as `marketingUrl`, `support_url` as `supportUrl` and `privacy_policy_url` as `privacyPolicyUrl`. The read tools still show Apple's own names.
- **Maintenance:** `maintenance_status`, `maintenance_report`, `maintenance_confirm_review`.

## Asking the human: two protocol versions

Two plain terms are used everywhere (code, tests and docs) for the two ways to ask:

- **Older protocol (up to 2025-11-25): the server asks during the call.** It sends the form to the client in the middle of the tool call (`elicit_form`, the "back-channel") and waits for the answer.
- **Protocol 2026-07-28 (what current Claude Code uses): the call returns "input required".** A server can't send a form in the middle of a tool call. The tool call returns `InputRequiredResult` with the form and a sealed `request_state`. The client shows the form and calls the tool again with the answer.

In the tests these are `OLD_PROTOCOL` and `NEW_PROTOCOL` (`tests/apple_fake.py`).

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

Paths are inside `src/app_store_connect/` unless they start with `tests/`.

| Path | What it does |
|---|---|
| `__init__.py` | The app name (`APP_NAME`, written once) and the version |
| `cli.py` | The commands: `serve` (the default), `status`, `review`, `doctor` |
| `server.py` | The tool list, the monthly gate, dispatching calls, the catch-all error message |
| `config.py` | Reads the settings (environment variables) and checks the key file |
| `auth.py` | Signs the short-lived token (JWT) from the `.p8` key |
| `client.py` | Talks to Apple: allowed endpoints only, ID checks, retries, size limit, readable errors |
| `spec.py` | `ENDPOINTS_USED` (the allowed endpoints) and the Apple spec version we checked |
| `confirmflow.py` | Asking the human, on both protocols, and never acting on a guess |
| `maintenance_record.py` | The proof that a human did the monthly review (the `review.json` file) |
| `maintenance.py` | The three maintenance tools and the form for confirming the review |
| `report.py` | Builds the monthly report (spec, endpoints, dependencies, tool changes) |
| `osv.py` | Asks the OSV database about known vulnerabilities in installed packages |
| `httpio.py` | Size-capped downloads for the report, careful with redirects |
| `identity.py` | Which code is running (version and git commit) and whether it is the reviewed code |
| `paths.py` | Where files are kept (XDG folders, private mode 0700) |
| `textsafe.py` | Makes text safe for a human (escaped for forms) and for the model (untrusted markers) |
| `tools/__init__.py` | The tool registry, and the "How to add a tool" guide |
| `tools/base.py` | Building blocks for every tool: `ToolSpec`, argument checks, `resource_of`, `text_result` |
| `tools/confirm.py` | `confirm_change`: the form a write tool shows before it sends anything |
| `tools/lookups.py` | Reads several tools need: app and group names for forms, the current price |
| `tools/apps.py`, `versions.py`, `builds.py`, `beta.py` | Apps, App Store versions, builds, TestFlight groups and testers |
| `tools/localizations.py` | Store text per language (update and create), with the argument-name table |
| `tools/locales.py` | The 50 App Store language codes |
| `tools/screenshots.py` | Screenshot sets and uploads (confined to `ASC_ROOT`) |
| `tools/customer_reviews.py` | Customer reviews and public replies |
| `tools/app_review.py` | Submissions to Apple's App Review |
| `tools/availability.py`, `tools/pricing.py`, `tools/territories.py` | Where the app is sold, its price, and territory codes and names |
| `tools/agerating.py` | The age rating questions: read the answers, change them |
| `tests/` | One test file per area. `apple_fake.py` has the fake Apple (`FakeApple`) and the fake human (`FormAnswerer`); `helpers.py` makes a test key and config; `report_fake.py` fakes the report's inputs; `conftest.py` isolates every test from your real files |

## Words used here

- **Three kinds of "review".** The word is used for three different things, and the code keeps them apart:
  - **Customer review:** a rating and text that a customer wrote on the App Store (`tools/customer_reviews.py`). You can read them and publish a public reply.
  - **App Review:** Apple's check of an app version before it can go on the App Store (`tools/app_review.py`). `submit_for_app_review` sends a version to it.
  - **Monthly maintenance review:** the human check of this server's own report (`maintenance_record.py` stores the proof, `report.py` builds the report). The CLI command `review` and the tool `maintenance_confirm_review` belong to it.
- **MCP:** a standard way to give an AI assistant tools.
- **Elicitation:** a question the server asks the human, shown by the client.
- **JWT:** a short-lived signed pass made from your key.
- **Endpoint:** one address in Apple's API, such as `GET /v1/apps`.
- **OSV:** a public database of known security problems in libraries.
- **Back-channel:** how the older protocol asks a question during a tool call: the server sends a request back to the client on the same connection.
- **Input required:** the answer of protocol 2026-07-28 when the server needs the human: the tool call returns the form instead of a result, and the client calls again with the answer.
- **Request state:** the sealed note (`request_state`) the server hands out with "input required". It comes back with the retry, so the server can tell the retry belongs to the form it showed.
- **Nonce:** a random one-time value inside the request state. Each can be used once, so an old answer cannot be replayed.
- **JSON:API:** the format of Apple's answers: a `data` object (or list) with an `id`, a `type` and `attributes`. `resource_of` and `attributes_of` (`tools/base.py`) read it.
- **Presigned URL:** an address that carries its own permission in the URL. Screenshot uploads go to one, so no `Authorization` header is sent there.
- **Territory:** a country or region where the App Store sells apps, written as a three-letter code such as SWE.
- **App info and version localization:** two kinds of store text per language. The app info localization holds name, subtitle and privacy policy URL and belongs to the app. The version localization holds description, keywords, what's new and URLs and belongs to one App Store version.
- **Fail closed:** when something is missing or unclear (no record, a broken file, no answer), the safe thing happens: tools refuse and nothing is sent.
