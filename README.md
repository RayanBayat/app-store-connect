# app-store-connect

An [MCP](https://modelcontextprotocol.io) server for Apple's [App Store Connect API](https://developer.apple.com/documentation/appstoreconnectapi). It lets Claude (or another MCP client) read your apps, builds, TestFlight testers and store listings, and change them if you allow it.

## How it works

- **Read-only by default.** Changes are off until you turn them on. When they're on, each change shows a form with the old and new values, and nothing is sent until you press Accept. Public actions, such as submitting for review or replying to a review, are marked as public in the form.
- **Talks only to Apple.** Your `.p8` key stays on your computer. The server signs short-lived tokens locally and sends them only to `api.appstoreconnect.apple.com`. There is no Apple ID login and no tool that sends arbitrary requests.
- **Monthly review.** Every 30 days the tools pause until you have read a short report: Apple API changes, deprecated endpoints, outdated or vulnerable dependencies, and tool changes.

## Requirements

- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and `git`
- Claude Code (or another MCP client)
- An App Store Connect account that can create API keys

## Setup

1. **Create an API key.** In App Store Connect, go to **Users and Access → Integrations → App Store Connect API → Team Keys → +**, choose the **App Manager** role, then **Download** the `.p8` file. You can only download it once. Note the **Key ID** and the **Issuer ID**.
2. **Store the key so only you can read it:**

   ```
   mkdir -p ~/.config/app-store-connect && chmod 700 ~/.config/app-store-connect
   mv ~/Downloads/AuthKey_XXXXXXXXXX.p8 ~/.config/app-store-connect/ && chmod 600 ~/.config/app-store-connect/AuthKey_XXXXXXXXXX.p8
   ```

3. **Add the server.** Replace the three `<...>` values:

   ```
   claude mcp add app-store-connect --scope user -e ASC_KEY_ID=<key id> -e ASC_ISSUER_ID=<issuer id> -e ASC_PRIVATE_KEY_PATH=<path to AuthKey_XXXXXXXXXX.p8> -- uvx --quiet --from git+https://github.com/RayanBayat/app-store-connect@v0.2.0 app-store-connect
   ```

Restart Claude Code. On first use, Claude asks you to do the monthly review.

## Allowing changes

- Add `-e ASC_ALLOW_WRITES=1` to the install command.
- For screenshot uploads, also add `-e ASC_ROOT=<folder>`. Files are only read from that folder.

The confirmation forms stop Claude from making changes on its own. They don't protect against an AI agent that can run any command on your computer as you. While changes are on:
- don't use auto-approve or bypass-permissions modes;
- add the deny rules from [docs/security.md](docs/security.md#what-this-does-not-protect-against-and-what-to-do-about-it);
- don't answer the forms automatically with a hook.

## Tools

| | |
|---|---|
| **Read** (always on) | Apps, App Store versions and their status, builds, TestFlight groups and testers, store texts per language, screenshot sets, customer reviews, review submissions, availability per country (with Apple's reasons when it isn't available), price, and the age rating answers (with unanswered questions listed) |
| **Change** (with `ASC_ALLOW_WRITES`, each confirmed) | Edit store texts, add a language, change the primary language, create a version, pick its build, add or remove TestFlight testers, add a build to a group, submit for beta review, submit for App Review, reply to a customer review, upload screenshots, turn an app on or off in chosen countries, make a free app free in all countries, answer the age rating questions |
| **Maintenance** | Status, monthly report, confirm the review |

Text written by people outside your team, such as customer reviews, is marked as untrusted, so Claude won't follow instructions inside it. The full tool names are in [docs/design.md](docs/design.md#tools). Every tool argument is written in snake_case (for example `whats_new` and `privacy_policy_url`).

## Monthly review

After 30 days, the tools return an error until the review is done:
1. Claude runs `maintenance_report` and shows it to you.
2. You confirm in a form. Claude can't confirm for you.

If your client can't show forms, confirm in a terminal instead:

```
uvx --from git+https://github.com/RayanBayat/app-store-connect@v0.2.0 app-store-connect review
```

## Updating

The install command pins a version (`@v0.2.0`). To update, check [CHANGELOG.md](CHANGELOG.md), run `claude mcp remove app-store-connect --scope user`, then reinstall with the new tag.

To always run the newest code instead, use `@main` and add `--refresh` after `uvx`. You get fixes immediately, but you also run new commits before you've seen them.

## Settings

| Variable | Required | Meaning |
|---|---|---|
| `ASC_KEY_ID` | yes | Key ID of the API key |
| `ASC_ISSUER_ID` | yes | Issuer ID of your team |
| `ASC_PRIVATE_KEY_PATH` | yes | Path to the `.p8` file. Must be private (`chmod 600`). The key itself is never accepted as a variable. |
| `ASC_KEY_KIND` | no | `team` (default) or `individual` |
| `ASC_ALLOW_WRITES` | no | `1` turns on the change tools. Default: off |
| `ASC_ROOT` | no | The only folder screenshots may be uploaded from |
| `ASC_VENDOR_NUMBER` | no | Your vendor number |
| `ASC_REVIEW_MAX_DAYS` | no | Days between reviews, 1–365. Default: 30 |

## Troubleshooting

Check the settings and make one read-only call to Apple:

```
ASC_KEY_ID=<key id> ASC_ISSUER_ID=<issuer id> ASC_PRIVATE_KEY_PATH=<path> uvx --from git+https://github.com/RayanBayat/app-store-connect@v0.2.0 app-store-connect doctor --online
```

`app-store-connect status` shows when the next review is due.

## Uninstall

```
claude mcp remove app-store-connect --scope user
rm -rf ~/.local/state/app-store-connect
```

## Contributing

See [AGENTS.md](AGENTS.md) for the rules, [docs/design.md](docs/design.md) for how it works and [MAINTENANCE.md](MAINTENANCE.md) for the monthly routine.

---

MIT licensed. Not affiliated with Apple. "App Store Connect" is a trademark of Apple Inc.
