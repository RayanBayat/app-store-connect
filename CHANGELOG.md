# Changelog

## 0.3.0 (unreleased)

- Contributor rules: everything in the repository must be readable by a beginner (AGENTS.md).
- New `get_age_rating` and `update_age_rating`: see and answer Apple's age rating questionnaire,
  which must be fully answered before an update can be submitted (Apple otherwise refuses with
  "You must provide a value for the attribute 'socialMedia'"). The read tool lists every
  unanswered question. The change tool only exists with `ASC_ALLOW_WRITES`, only works on an
  editable app info, shows each answer as old -> new, says that the answers are your legal
  declaration, and waits for your Accept. `all_others: "none"` answers "No / None" to every
  question you did not list.
- When Apple refuses a request, the error now includes Apple's detailed list of problems
  (for example a missing copyright), not just the headline.
- `submit_for_app_review` reuses an open, empty draft submission left by an earlier failed attempt
  instead of trying to create a second one.
- Docs: the README is now plain reference documentation (what it does, setup, tools, settings).
- New `get_app_availability`: see in which countries an app is on sale, and why not. It lists
  every territory with Apple's reasons in plain English, a one-line summary, and the app's
  price (or "no price set"), so you can see why the App Store shows "not found" for a
  "Ready for Sale" app.
- New change tools `set_territory_availability` (switch the app on or off in chosen countries)
  and `set_free_price` (make a free app free in every country). Both show exactly what will
  change and wait for your Accept; neither exists unless `ASC_ALLOW_WRITES` is on.
- `get_app_info_localizations` no longer marks Apple's own values (such as states and age
  ratings) as untrusted text; only text that people write is marked.

## 0.2.0 (2026-11-03)

Security fixes from an independent review.

- Confirmation forms no longer ask you to tick a box: they show exactly what will happen, and
  pressing **Accept** is your approval. Claude Code's forms could not tick the required box, so
  every confirmation was blocked.
- Docs: the README now leads with what you can do and why it is safe, with a 3-step start;
  `docs/design.md` explains how it works for contributors.

- Supply chain: runtime dependencies (mcp, httpx, pyjwt, cryptography) are pinned exactly; CI
  installs with `--locked`, does not persist checkout credentials, pins the uv version, audits
  dependencies with pip-audit, and Dependabot waits 7 days before proposing updates.
- review.json records the version and git commit of the reviewed code; the report, status and
  review form say plainly when the running code is not the reviewed code.
- Confirmation forms show the full new and old values with a character count, escape control,
  format and bidi characters, render newlines visibly, and refuse values over Apple's limits.
- Untrusted text is wrapped in per-call random markers with bracket look-alikes neutralised,
  and every free-text field from the API is wrapped (not only review text).
- `maintenance_confirm_review` needs a report built in the same session in the last 30
  minutes and shows the server's own figures next to Claude's labelled summary.
- Screenshot upload: the file is opened once (no symlink, no FIFO) and verified; upload parts
  must be PUTs to https apple.com hosts, must not overlap and must add up to the file size;
  `ASC_ROOT` may not be `/`, your home folder or group/other-writable.
- The key file is checked (regular file, mode 600, yours, small) at signing time, encrypted or
  unsupported keys give a clear message, and `Config` no longer shows IDs in its repr.
- IDs may not contain dots or underscores (`..` escaped the path); pagination links must match
  an allowed GET endpoint; a version must belong to the app before it is submitted.
- Report downloads are size-capped, OSV paging is limited and redirects are not followed.
- Confirmation forms now work on MCP protocol 2026-07-28 (what Claude Code negotiates), where a
  server cannot ask during a call: the tool returns `input_required` and the retry is checked
  (sealed, single-use, bound to the exact change; a changed situation gets a fresh form). The
  older back-channel path still works, and refusals say why.
- The report checks only our direct dependencies for new releases, using PyPI's small releases
  feed (the JSON API was too large for big packages); the vulnerability scan still covers every
  installed package. A first review says "No earlier review".
- New write tools: `create_version_localization`, `create_app_info_localization` (Apple requires
  a name) and `update_app_primary_locale` (refused unless the app already has an app info
  localization in that language). Locale codes are checked against a documented list.
- Docs say plainly that the protections assume the AI agent cannot run commands as your user.

## 0.1.0

- First release: a self-contained MCP server for Apple's App Store Connect API.
- 13 read tools; 11 write tools that are off by default and always confirmed by the human.
- Monthly review gate with a report (Apple spec, endpoints, dependencies, vulnerabilities).
- Checked against Apple's OpenAPI spec 4.5.1.
