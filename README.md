# App Store Connect for Claude

**Manage your App Store listing, builds and TestFlight by just asking Claude, and approve every change before it happens.**

> "Which build is waiting for review?"
> "Update the Swedish description and keywords for version 1.1."
> "Add anna@example.com to the External Testers group."
> "Show me this week's 1-star reviews and draft replies."

`app-store-connect` is an [MCP](https://modelcontextprotocol.io) server that connects Claude to Apple's official [App Store Connect API](https://developer.apple.com/documentation/appstoreconnectapi). It installs with one command.

## Why you can trust it with your account

- **You approve every change.** Reading is on by default; changing things is opt-in. Even then, each change opens a form showing the exact old and new values, and nothing is sent until you accept. Public actions, such as submitting for review or replying to a customer, are clearly marked as public.
- **Your key never leaves your computer.** The server signs short-lived passes locally and only ever talks to Apple. There is no Apple ID login, and no "send any request" tool.
- **It keeps itself current.** Once a month it asks you to review a short report: Apple API changes, deprecated endpoints, outdated or vulnerable libraries. A stale integration never runs silently.
- **Small, open and checked.** Plain Python, MIT-licensed, more than 400 automated tests, and an independent security review.

## Get started in 3 steps

You need [`uv`](https://docs.astral.sh/uv/getting-started/installation/), `git`, Claude Code, and an App Store Connect account that can create API keys.

**1. Create an API key.** In App Store Connect, go to **Users and Access → Integrations → App Store Connect API → Team Keys → +**, choose the **App Manager** role, then **Download** the `.p8` file (you can only download it once). Note the **Key ID** and the **Issuer ID**.

**2. Store the key privately.**

```
mkdir -p ~/.config/app-store-connect && chmod 700 ~/.config/app-store-connect
mv ~/Downloads/AuthKey_XXXXXXXXXX.p8 ~/.config/app-store-connect/ && chmod 600 ~/.config/app-store-connect/AuthKey_XXXXXXXXXX.p8
```

**3. Add it to Claude.** Replace the three `<...>` values:

```
claude mcp add app-store-connect --scope user -e ASC_KEY_ID=<key id> -e ASC_ISSUER_ID=<issuer id> -e ASC_PRIVATE_KEY_PATH=<path to AuthKey_XXXXXXXXXX.p8> -- uvx --quiet --from git+https://github.com/RayanBayat/app-store-connect@v0.2.0 app-store-connect
```

Restart Claude Code and ask "list my apps". The first time, Claude walks you through the monthly review.

## Let Claude make changes

Add `-e ASC_ALLOW_WRITES=1` to the install command. To allow screenshot uploads, also add `-e ASC_ROOT=<your screenshots folder>`; Claude can read files only inside that folder.

**Before you turn writes on:** the approval forms protect you from Claude acting on its own. They can't protect you from an AI agent that can run any command on your computer as you. So, while writes are on:
- don't use auto-approve or bypass-permissions modes;
- add the deny rules from [docs/security.md](docs/security.md#what-this-does-not-protect-against-and-what-to-do-about-it);
- never auto-answer the forms with a hook.

## What Claude can do

| | Tools |
|---|---|
| **Read** (always on) | Apps, App Store versions and their status, builds, TestFlight groups and testers, store texts per language, screenshot sets, customer reviews, review submissions |
| **Change** (opt-in, you approve each) | Edit store texts, add a language, change the primary language, create a version, pick its build, add or remove TestFlight testers, add a build to a group, submit for beta review, submit for App Review, reply to a customer review, upload screenshots |
| **Maintenance** | Status, the monthly report, and confirming the review |

Customer reviews and other text written by people outside your team are marked as untrusted, so Claude won't follow instructions hidden inside them.

## The monthly review

When a month has passed, the tools pause and Claude shows you a short report: is Apple's API spec newer, has anything the server uses been deprecated, are its libraries outdated or vulnerable, did the tools change? You read it and confirm in a form, and the tools work for another 30 days. Claude can't confirm on your behalf.

If your client can't show forms, confirm in a normal terminal window:

```
uvx --from git+https://github.com/RayanBayat/app-store-connect@v0.2.0 app-store-connect review
```

## Updating

You run exactly the version in your install command (`@v0.2.0`). To update, read [CHANGELOG.md](CHANGELOG.md), then reinstall with the new tag: `claude mcp remove app-store-connect --scope user`, followed by the install command.

Prefer the newest code on every start? Use `@main` and add `--refresh` after `uvx`. You get fixes immediately, but you also run every new commit before you've seen it.

## Settings

| Variable | Required | Meaning |
|---|---|---|
| `ASC_KEY_ID` | yes | Your API key's Key ID |
| `ASC_ISSUER_ID` | yes | Your team's Issuer ID |
| `ASC_PRIVATE_KEY_PATH` | yes | Path to the `.p8` file. Must be private (`chmod 600`). The key itself is never accepted as a variable. |
| `ASC_KEY_KIND` | no | `team` (default) or `individual` |
| `ASC_ALLOW_WRITES` | no | `1` turns on the change tools. Default: off |
| `ASC_ROOT` | no | The only folder screenshots may be uploaded from |
| `ASC_VENDOR_NUMBER` | no | Your vendor number |
| `ASC_REVIEW_MAX_DAYS` | no | Days between reviews, 1–365. Default: 30 |

## Troubleshooting

Check your setup and make one read-only call to Apple:

```
ASC_KEY_ID=<key id> ASC_ISSUER_ID=<issuer id> ASC_PRIVATE_KEY_PATH=<path> uvx --from git+https://github.com/RayanBayat/app-store-connect@v0.2.0 app-store-connect doctor --online
```

`status` shows when your next review is due.

## Uninstall

```
claude mcp remove app-store-connect --scope user
rm -rf ~/.local/state/app-store-connect
```

## Contributing

Pull requests are welcome. Start with [AGENTS.md](AGENTS.md) (the house rules), [docs/design.md](docs/design.md) (how it works) and [MAINTENANCE.md](MAINTENANCE.md).

---

MIT licensed. Not affiliated with or endorsed by Apple. "App Store Connect" is a trademark of Apple Inc.
