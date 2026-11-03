# asc-mcp-maintenance

A Claude Code plugin that keeps the [App Store Connect MCP server](https://github.com/idoo/app-store-connect-mcp) current and safe.

That server gives Claude an App Store Connect API key, which can change your store listing, your testers and your releases. It is third-party code and it changes fast. This plugin makes sure you **look at what changed at least once a month** before you keep using it.

## What it does

- **Blocks the server's tools when a review is due.** Every `mcp__asc__*` tool call is stopped once your last review is 30 days old, or if you have never done one. Claude sees why and what to do next.
- **Guides the review.** The included skill walks Claude (and you) through new releases, dependency changes, known vulnerabilities, security-relevant code changes, new or deprecated tools, and changes on Apple's side. It ends with a short report and a question: upgrade or stay?
- **Upgrades only verified releases.** Installs check the release checksum and GitHub's build provenance, and abort on any mismatch.
- **Remembers each review.** The date, version, outcome and tool list are saved, so the next review can show exactly which tools were added or removed.

## Requirements

- [Claude Code](https://claude.com/claude-code).
- The App Store Connect MCP server, registered in Claude Code under the name **`asc`** (the hook matches `mcp__asc__*`).
- `python3` 3.12 or newer. The hook uses only the standard library.
- [`gh`](https://cli.github.com/), logged in (`gh auth login`). The review uses it to read releases and verify downloads.
- [`uv`](https://docs.astral.sh/uv/), only for `scripts/probe.py`, which lists the server's tools.

Linux and macOS are supported.

## Install

```
claude plugin marketplace add RayanBayat/asc-mcp-maintenance
claude plugin install asc-mcp-maintenance@asc-mcp-maintenance
```

Restart Claude Code. Your first `mcp__asc__*` call is blocked until you have done one review. That's on purpose: the first review records what you are running.

To update the plugin later: `claude plugin marketplace update asc-mcp-maintenance`.

## How it works

```
Claude wants to call mcp__asc__something
        │
        ▼
hook: scripts/asc_maint.py gate ── last review < 30 days ago? ── yes ──▶ call goes ahead
        │ no (or never reviewed, or the check itself failed)
        ▼
call blocked: "review overdue, run the asc-mcp-maintenance skill"
        │
        ▼
the review (SKILL.md): releases → dependencies + vulnerabilities → security diff
                       → new/removed tools → Apple changes → report → your decision
        │
        ▼
asc_maint.py stamp ──▶ tools unblock for another 30 days
```

The gate **fails closed**: if the check can't run or crashes, the call is blocked rather than allowed.

## Doing a review

Ask Claude something like "review the App Store Connect MCP", or simply use it. When a review is due, Claude is told to run it. You'll get a report table and a recommendation, and nothing gets installed until you say yes.

Useful commands, run from the plugin folder:

| Command | What it does |
|---|---|
| `python3 scripts/asc_maint.py status` | Last review, days left, installed and latest version |
| `python3 scripts/osv_check.py go.mod` | Checks a go.mod against the OSV vulnerability database |
| `uv run scripts/probe.py` | Starts the server and lists its tools (`[write]` marks write tools) |
| `bash scripts/install_release.sh 0.23.0` | Verifies and installs that release |
| `bash scripts/install_release.sh 0.23.0 --extract-only /tmp/x` | Verifies and unpacks it without installing |
| `python3 scripts/asc_maint.py stamp --server-version 0.23.0 --outcome "..."` | Records a finished review |

## Settings

| Environment variable | Default | Meaning |
|---|---|---|
| `ASC_MCP_REVIEW_MAX_DAYS` | `30` | Days a review stays valid (1–365) |
| `ASC_MCP_MAINT_STATE_DIR` | `~/.local/state/asc-mcp-maintenance` | Where the review record is kept |
| `ASC_MCP_BIN` | `app-store-connect-mcp` on your PATH | The server binary that `status` checks |
| `ASC_MCP_INSTALL_DIR` | `~/.local/bin` | Where `install_release.sh` installs |

## Recommended safety settings for the server

A security audit of v0.22.0 found that the model can switch on extra toolsets, including an Apple ID web login, without asking you. Add these to `permissions.deny` in `~/.claude/settings.json` (merge them with what's already there):

```json
"mcp__asc__toolsets",
"mcp__asc__web_login",
"mcp__asc__asc_api_request"
```

Keep your `.p8` key in a folder only you can read (`chmod 700` folder, `chmod 600` file), and add `Read(...)` deny rules for that folder too. The full findings and what to re-check on every update are in [`references/audit-checklist.md`](references/audit-checklist.md).

## Uninstall

```
claude plugin uninstall asc-mcp-maintenance@asc-mcp-maintenance
```

Your review history stays in `~/.local/state/asc-mcp-maintenance/`. Delete that folder to remove it.

## Development

```
uv sync                      # installs the dev tools from uv.lock
uv run pytest -q             # tests
uv run ruff check            # lint
uv run ruff format --check   # formatting
claude plugin validate .     # plugin manifest check
```

CI runs the same checks on every push. Dependabot keeps the GitHub Actions and Python dev tools up to date.

## License

MIT. Not affiliated with Apple or with the author of app-store-connect-mcp.
