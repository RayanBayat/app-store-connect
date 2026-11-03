# What we know about this project

This file explains the project in plain words: what it is, why it is built this way, and what we learned along the way. If you are new, read this first, then `README.md`, then `AGENTS.md`.

**Keep it up to date.** When you learn something new or a decision changes, update this file in the same commit (see "Keeping this file useful" at the bottom).

---

## The short version

- This is an **MCP server**: a small program that gives an AI assistant (like Claude) extra tools. Our tools talk to **Apple's App Store Connect**, the website where developers manage their iPhone apps.
- With it, Claude can **read** your apps, builds, TestFlight testers, store texts, screenshots and customer reviews.
- Claude can also **change** things, but only if you turn that on (`ASC_ALLOW_WRITES=1`), and **every change shows you a form first**. Nothing changes unless you click yes.
- Once a month, the server stops and asks you to do a short **maintenance review**, so the project never quietly goes out of date.
- It is written in **Python**, talks to Apple **directly**, and has **no other company's server code inside**. We call that "self-contained".

## Why it exists

The owner (Rayan) builds an iPhone app called KörUppNu. Doing App Store work by hand (texts, screenshots, testers, submitting for review) is slow. An MCP server lets Claude do that work, while the owner stays in control of every change.

## How it works, step by step

1. **Install:** you run one `claude mcp add ... uvx ...` command (see README). Claude Code saves it.
2. **Start:** every time Claude Code starts, it runs our server with `uvx`. `uvx` downloads the code from GitHub and runs it. You don't install anything else.
3. **Login to Apple:** the server reads your private key file (`AuthKey_XXXX.p8`) and makes a short-lived pass called a **JWT**. Apple accepts it for 20 minutes; we make a new one every 15 minutes. The key never leaves your computer, and the pass only goes to `api.appstoreconnect.apple.com`.
4. **A tool is called:** first the server checks the monthly review. If it's too old, it refuses and tells Claude to run the review.
5. **Reading:** the server asks Apple and returns the answer. Text written by other people (like customer reviews) is wrapped in "untrusted" markers, so Claude knows not to follow instructions hidden inside it.
6. **Changing:** the server first shows **you** a form with the exact old value and new value, full length, with hidden characters made visible. Only if you accept does it send the change to Apple, exactly once.

## Important decisions, and why we made them

The newest decisions are at the bottom.

| Date | Decision | Why |
|---|---|---|
| 2026-11-03 | First tried a third-party server (`idoo/app-store-connect-mcp`, written in Go) | It existed and had 40 tools. A security audit said "OK with conditions". |
| 2026-11-03 | **Replaced it with our own Python server** | The owner wants the MCP to be **self-contained**: no dependency on one stranger's code (that repo was 9 days old, with one maintainer). |
| 2026-11-03 | Install with **one command** using `uvx --from git+https://...` | The same simple install style the owner uses for other MCP servers. |
| 2026-11-03 | **Read-only by default.** Changes need `ASC_ALLOW_WRITES=1` **and** a form per change | A wrong change can be public (store text, review replies) or hard to undo (submitting for review). |
| 2026-11-03 | **Monthly review built in** (30 days, `ASC_REVIEW_MAX_DAYS`) | Apple changes its API and libraries get security fixes. The review makes someone look at least once a month. |
| 2026-11-03 | **No "send any request" tool and no Apple ID login**, ever | Those would let Claude reach far more than it needs. |
| 2026-11-03 | **Exact version pins** (`==`) for our libraries, kept current by Dependabot | So everybody runs the same, tested library versions. |
| 2026-11-03 | README recommends `@vX.Y.Z` (a fixed version) for other people. The owner uses `@main --refresh` (always the newest) | A fixed version can't change under you. "Always newest" gets fixes faster but trusts every new commit. |
| 2026-11-03 | **Repo made public** after scanning the whole git history for secrets | So other people can use it too. The scan found no key, no key ID and no issuer ID. |
| 2026-11-03 | GitHub rules: `main` can't be force-pushed or deleted; `v*` tags can't be moved or deleted | A published version must stay exactly what was reviewed. |
| 2026-11-03 | An independent **security review** (Opus) said "ship after fixes". All findings were fixed (version 0.2.0) | See `CHANGELOG.md` for the list. |

## Things we learned the hard way

These surprised us. Each one cost time, so remember them.

1. **Claude Code uses the newest MCP protocol (`2026-07-28`).** In this version a server **cannot send a form in the middle of a tool call**. Instead the tool call must answer "I need input first" (`InputRequiredResult`). Claude Code then shows the form, and calls the tool again with the answer. The old way failed instantly, so no form appeared at all. The code now supports both ways (`confirmflow.py`).
2. **Apple's keyword limit is 100 *bytes*, not 100 letters.** Swedish letters like å, ä and ö take 2 bytes each, so a Swedish keyword list fills up faster.
3. **Apple's API description (the OpenAPI "spec") has no length limits.** The real limits come from App Store Connect Help (checked 2026-11-03): name 2–30 characters, subtitle 30, description 4000, What's New 4000, promotional text 170, keywords 100 bytes.
4. **Many Apple documentation pages are built with JavaScript**, so automatic tools can't read them (for example the API release notes and the locale code table). A human has to skim the release notes during the monthly review.
5. **Some PyPI pages are huge** (over 5 MB for big packages), because they list every file of every version. We read PyPI's small RSS feed instead, and only for our own direct dependencies.
6. **Apple deprecated `appStoreState`.** Use `appVersionState` instead.
7. **Claude Code's safety system blocks Claude** from reading the credentials file or running the server with real keys. That is on purpose. A human runs those steps: with a `!` in front inside Claude Code, or in their own terminal.
8. **A `!` command in Claude Code has no keyboard input (no TTY).** The `app-store-connect review` command asks a yes/no question, so run it in a normal terminal window.
9. **Closing Claude Code (`/exit`) stops background agents.** To use the server while agents run, open a second terminal tab and start a new `claude` there. To continue a conversation with a newly installed server loaded, use `claude -c`.
10. **`uvx` caches downloads.** `--refresh` makes it fetch the newest code from GitHub every start.

## Not verified yet

Check these when you get the chance, then move them to the list above.

- **Screenshot upload host.** We only allow uploads to hosts ending in `.apple.com` (`UPLOAD_HOST_SUFFIX` in `client.py`). The first real screenshot upload will show whether that's right. If Apple uses another host, the upload fails safely and this one constant needs changing.
- **Some language codes.** The 50 codes in `tools/locales.py` match Apple's 50 languages, but the codes for some newer languages (for example `bn-BD`, `or-IN`) were not checked against Apple's code table, because that page can't be read automatically. A wrong code only makes a tool refuse.
- **Forms in Claude Code on the new protocol.** The new form flow passes the tests, with the official SDK client playing Claude Code. The first real check in Claude Code is still to do.

## Where things are

| Path | What it is |
|---|---|
| `src/app_store_connect/server.py` | The MCP server: the list of tools, the monthly gate, and calling tools |
| `src/app_store_connect/tools/` | One file per area (apps, versions, builds, beta, localizations, screenshots, reviews) |
| `src/app_store_connect/confirmflow.py`, `tools/confirm.py` | The "ask the human first" forms, on both protocol versions |
| `src/app_store_connect/client.py` | Talks to Apple. It only allows endpoints listed in `spec.py` |
| `src/app_store_connect/auth.py` | Makes the JWT pass from your key |
| `src/app_store_connect/config.py` | Reads the settings (environment variables) and checks the key file is private |
| `src/app_store_connect/review.py`, `report.py`, `maintenance.py` | The monthly review: record, report and tools |
| `docs/security.md` | The safety rules, in plain words |
| `MAINTENANCE.md` | What to do in the monthly review and how to release |
| `AGENTS.md` (+ `CLAUDE.md`) | Rules for AI agents working on this repo |
| `docs/worklog.md` | Dated log of what was done |

## Words explained

- **MCP (Model Context Protocol):** a standard way to give an AI assistant tools.
- **Tool:** one action the AI can take, such as "list apps".
- **Form / elicitation:** a question the server asks *you* (not the AI), shown by Claude Code.
- **App Store Connect API:** Apple's official way for programs to manage apps.
- **API key (.p8 file):** your secret key from App Store Connect. Never share it or put it in chat.
- **Key ID / Issuer ID:** the names of your key and your team. They aren't the secret itself, but keep them private anyway.
- **JWT:** a short-lived signed pass made from your key.
- **Endpoint:** one address in Apple's API, for example `GET /v1/apps`.
- **Spec (OpenAPI):** Apple's machine-readable description of all endpoints.
- **uv / uvx:** a fast Python tool. `uvx` runs a program straight from a source like GitHub.
- **Pin:** fixing a library to one exact version.
- **OSV:** a public database of known security problems in libraries.
- **CI:** the automatic checks GitHub runs on every push (lint, types, tests, security audit).
- **Dependabot:** a GitHub robot that opens pull requests when a library has a new version.

## Keeping this file useful

- **Learned something surprising?** Add it to "Things we learned the hard way".
- **Made or changed a decision?** Add a row to the decisions table, with the date and the reason.
- **Verified something from "Not verified yet"?** Move it to the right place.
- **Every piece of work** also gets a short entry in `docs/worklog.md`.
- Keep the language simple: short sentences, no unexplained jargon.
