# app-store-connect

App Store Connect in Claude, safely. This is an [MCP](https://modelcontextprotocol.io) server that
talks directly to Apple's official [App Store Connect API](https://developer.apple.com/documentation/appstoreconnectapi).

- **Read-only by default.** Writes are opt-in, and even then Claude must ask you in a
  confirmation form before anything is sent.
- **Small and auditable.** Plain Python, no other server in between, no web/Apple ID login,
  no "send any request" tool.
- **Monthly review built in.** Tools stop working once a month has passed without a review, so
  the code never silently goes stale (see [The monthly review](#the-monthly-review)).

## What you need

- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and `git`
- An App Store Connect account that can create API keys

## 1. Create an App Store Connect API key

1. Sign in to App Store Connect, then **Users and Access → Integrations → App Store Connect API**.
2. Open **Team Keys** and click **+**.
3. Name it (for example "Claude") and pick the **App Manager** role. It is the smallest role that
   can do everything this server offers.
4. Click **Generate**, then **Download** the `.p8` file. Apple lets you download it **only once**.
5. Note the **Key ID** (next to the key) and the **Issuer ID** (top of the page).
6. Put the key in a private folder:

   ```
   mkdir -p ~/.config/app-store-connect && chmod 700 ~/.config/app-store-connect
   mv ~/Downloads/AuthKey_XXXXXXXXXX.p8 ~/.config/app-store-connect/ && chmod 600 ~/.config/app-store-connect/AuthKey_XXXXXXXXXX.p8
   ```

The server refuses to start if the key file or its folder can be read by other users.

## 2. Install

This server holds a key that can change your App Store listing, so which code you run matters.
Pick one of the two forms below. Replace the three values in `<...>`.

**Recommended for most people: a pinned release.** You run exactly the version you chose. Nothing
changes until you change the tag yourself.

```
claude mcp add app-store-connect --scope user -e ASC_KEY_ID=<key id> -e ASC_ISSUER_ID=<issuer id> -e ASC_PRIVATE_KEY_PATH=<path to AuthKey_XXXXXXXXXX.p8> -- uvx --quiet --from git+https://github.com/RayanBayat/app-store-connect@v0.2.0 app-store-connect
```

**Always latest (the maintainer's own setup).** Every start fetches whatever is on the `main`
branch right now:

```
claude mcp add app-store-connect --scope user -e ASC_KEY_ID=<key id> -e ASC_ISSUER_ID=<issuer id> -e ASC_PRIVATE_KEY_PATH=<path to AuthKey_XXXXXXXXXX.p8> -- uvx --refresh --quiet --from git+https://github.com/RayanBayat/app-store-connect@main app-store-connect
```

The trade-off, in plain words: with `@main --refresh` you get fixes at once, but you also run
new code the moment it is pushed, before anyone but the author has looked at it, and a mistake
or a compromised account on the repository would reach your key on the next start. With a
pinned tag you only run code you chose to run, and you pay for that by updating by hand.
Both forms pin the Python libraries exactly (see `uv.lock`); the choice is only about this
repository's own code.

To let Claude make changes, add `-e ASC_ALLOW_WRITES=1`. Claude then shows you a form that says
exactly what will change, and nothing happens unless you accept it. Public actions (submitting for
review, replying to a review) are labelled **PUBLIC AND IRREVERSIBLE** in that form.

To let Claude upload screenshots, add `-e ASC_ROOT=<folder with your screenshots>`. Claude can
only read files inside that folder.

### Read this before you turn writes on

The protections in this project (confirmation forms, the monthly review, the key checks) assume
**the AI agent cannot run arbitrary commands as your user**. That is not the default in Claude
Code. An agent that can run shell commands or write files as you could read the `.p8` key, edit
`review.json`, fake a terminal to pass the terminal check, or edit the MCP configuration to turn
writes on. Before you set `ASC_ALLOW_WRITES`, read
[docs/security.md](docs/security.md#what-this-does-not-protect-against-and-what-to-do-about-it)
and do at least these:

- Add Claude Code permission deny rules for the key folder and the state folder (exact rules in
  that document).
- Do not use auto-approve or bypass-permissions modes while writes are on.
- Never answer these confirmation forms automatically with a hook.
- For strong isolation, run the server as a separate OS user or inside a sandbox.

### All settings

| Variable | Required | Meaning |
|---|---|---|
| `ASC_KEY_ID` | yes | Key ID of your API key |
| `ASC_ISSUER_ID` | yes | Issuer ID (team keys) |
| `ASC_PRIVATE_KEY_PATH` | yes | Path to the `.p8` file. The key contents are never accepted as a variable (`ASC_PRIVATE_KEY` is refused). |
| `ASC_KEY_KIND` | no | `team` (default) or `individual` |
| `ASC_VENDOR_NUMBER` | no | Your vendor number |
| `ASC_ALLOW_WRITES` | no | `1`, `true` or `yes` turns on the write tools. Default: off |
| `ASC_ROOT` | no | The only folder file tools may read |
| `ASC_REVIEW_MAX_DAYS` | no | Days between reviews, 1 to 365. Default: 30 |

## What Claude can do

**Always available (read-only):** `list_apps`, `get_app`, `list_app_store_versions`,
`get_version_status`, `list_builds`, `get_build`, `list_beta_groups`, `list_beta_testers`,
`get_version_localizations`, `get_app_info_localizations`, `list_screenshot_sets`,
`list_customer_reviews`, `get_review_submissions`.

**Only with `ASC_ALLOW_WRITES=1` (each one asks you first):** `update_version_localization`,
`update_app_info_localization`, `create_version_localization`, `create_app_info_localization`,
`update_app_primary_locale`, `create_app_store_version`, `select_build_for_version`,
`add_beta_tester`, `remove_beta_tester`, `add_build_to_beta_group`, `submit_build_for_beta_review`,
`submit_for_app_review`, `reply_to_customer_review`, `upload_screenshot`.

**Maintenance:** `maintenance_status`, `maintenance_report`, `maintenance_confirm_review`.

Customer reviews are written by strangers, so their text is marked as untrusted content and
Claude is told never to follow instructions found in it.

## What is deliberately not here

- No tool that sends a raw request to any endpoint.
- No Apple ID or web login of any kind.
- No way for Claude to confirm its own changes (there is no "confirm" argument).
- Your key, tokens and issuer ID are never returned in a result or written to a log.

The full explanation is in [docs/security.md](docs/security.md).

## The monthly review

Apple changes its API, and libraries get security fixes. So tools stop working once the last
review is older than 30 days (or if there never was one) and Claude is told what to do:

1. Claude runs `maintenance_report` and shows you the report: is the Apple API spec newer, are
   any endpoints we use missing or deprecated, are our dependencies outdated or vulnerable, did
   the tool list change.
2. You read it. Then Claude calls `maintenance_confirm_review`, and a form appears **in your
   client**. Only if you accept it (and tick "I have read the maintenance report") is the review
   recorded. Claude cannot do that for you.

If your client cannot show forms, run the review in a terminal instead (see below).

## Updating

- **Pinned install:** change the tag in the command, for example `@v0.2.0` to `@v0.3.0`, and run
  it again (`claude mcp remove app-store-connect --scope user`, then the install command with
  the new tag). Read [CHANGELOG.md](CHANGELOG.md) first.
- **`@main --refresh` install:** automatic. Every start fetches the latest `main`.

The monthly review records which version (and git commit) you reviewed. If the running code is
not the reviewed code, the report and `maintenance_status` say so.

## Terminal commands

```
uvx --from git+https://github.com/RayanBayat/app-store-connect@v0.2.0 app-store-connect status
uvx --from git+https://github.com/RayanBayat/app-store-connect@v0.2.0 app-store-connect review
uvx --from git+https://github.com/RayanBayat/app-store-connect@v0.2.0 app-store-connect doctor
```

`doctor` reads the same `ASC_*` variables, so run it like this:

```
ASC_KEY_ID=<key id> ASC_ISSUER_ID=<issuer id> ASC_PRIVATE_KEY_PATH=<path> uvx --from git+https://github.com/RayanBayat/app-store-connect@v0.2.0 app-store-connect doctor --online
```

`--online` makes one read-only call to Apple to prove the credentials work.

## Uninstall

```
claude mcp remove app-store-connect --scope user
rm -rf ~/.local/state/app-store-connect
```

## Development

```
uv sync
uv run pytest -m "not network"   # fast tests, no internet
uv run pytest -m network         # downloads Apple's published OpenAPI spec
uv run ruff check && uv run ruff format --check
uv run mypy
```

See [MAINTENANCE.md](MAINTENANCE.md) for the monthly routine and [AGENTS.md](AGENTS.md) for the
rules for AI agents working on this repository.

## Credits and legal

MIT licensed, see [LICENSE](LICENSE). Not affiliated with or endorsed by Apple. "App Store
Connect" is a trademark of Apple Inc.
