# Rules for AI agents working on this repository

This is a Python MCP server for Apple's App Store Connect API. Before changing anything, read
`docs/design.md` (how it works), `docs/security.md` and `MAINTENANCE.md`.

## Always log, and keep the docs up to date (required)

This is a public repository: everything in it is written for the people who use or contribute
to it. This applies to every change, however small, and to every agent and human:

1. **Log it.** Add a line under "Unreleased" at the top of `CHANGELOG.md`: what changed for
   users, and why it matters to them.
2. **Keep the docs true.** If you changed how something works, learned a fact about Apple or MCP
   that shapes the code, or verified an item under "Still to verify", update `docs/design.md`
   in the same commit. If users are affected, also update `README.md`, `docs/security.md` or
   `MAINTENANCE.md`.
3. **Update the rules.** If a rule, command or workflow changed, update this file.
4. **Write for the reader.** Only what users or contributors need. No private notes, personal
   setups or internal history. Plain words a very junior developer understands: short sentences,
   explain technical words, and give the reason behind each rule.

A change without its changelog line and doc updates is not finished.

## Standards

- Use the **latest stable** version of every library and its **current** API. Check PyPI and the
  library's docs; do not rely on memory (the MCP Python SDK in particular changes between major
  versions).
- Python >= 3.12, `src/` layout, `uv` for everything. Never use `pip install`.
- Everything is typed (`mypy --strict`).
- Imports at the top of the file. No `print` outside the CLI.

## Readable by a beginner (required)

Every file in this repository (code, tests, scripts, CI configuration and these docs) must be
easy for a **beginner computer science student** to read, learn from and debug.

- **Clean code.** Small functions that each do one thing, and clear names (no abbreviations).
  Flat control flow with early returns. No clever tricks.
- **Explain why.** Each module starts with a short paragraph saying what it is for and how it fits
  in. Comments explain *why*, never *what*.
- **Debuggable.** Error messages say what went wrong and what to do next. Nothing fails silently.
  Tests read like small examples of how the code is used.
- **Consistent.** Follow the patterns already in the repo, so learning one tool teaches the rest.
  Every tool module has the same layout; the steps are in the `tools/__init__.py` header
  ("How to add a tool").
- **Review rule.** If a beginner couldn't follow a change, rewrite it before merging.

## Commands

```
uv sync
uv run ruff check && uv run ruff format --check
uv run mypy
uv run pytest -m "not network"     # must pass before every commit
uv run pytest -m network           # downloads Apple's public spec; run before a spec bump and before a release
uv lock --upgrade                  # refresh dependencies
```

Tests, lint and types must all pass before every commit. Write the test first. Commit small
and often with Conventional Commit messages.

## Never weaken safety

- Writes stay off unless `ASC_ALLOW_WRITES` is on, and every write tool asks the human through
  MCP elicitation first (`tools/confirm.py`). No `confirm` argument, ever.
- The client only sends endpoints listed in `ENDPOINTS_USED` (`spec.py`). Add new ones only
  after verifying them against Apple's current OpenAPI spec, and add tests.
- Confirmations must work on both protocols: the older one (the server asks during the call, up to
  2025-11-25) and protocol 2026-07-28 (the call returns "input required"); `tests/test_confirm_eras.py`
  covers both. Never act on an unvalidated retry.
- The `Authorization` header goes only to `api.appstoreconnect.apple.com`.
- Never return or log tokens, keys or the issuer ID.
- Never read real credentials, never contact the real Apple API from tests or tooling: use the
  mocked transport in `tests/apple_fake.py`.
- Never push, publish or change the user's MCP configuration unless the maintainer explicitly asks.

## Dependency and spec changes

Follow `MAINTENANCE.md`: bump `APPLE_API_SPEC_VERSION` only after `pytest -m network` passes and
the release notes were read.
