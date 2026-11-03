# Rules for AI agents working on this repository

This is a Python MCP server for Apple's App Store Connect API. Read `docs/security.md` and
`MAINTENANCE.md` before changing anything.

## Standards

- Use the **latest stable** version of every library and its **current** API. Check PyPI and the
  library's docs; do not rely on memory (the MCP Python SDK in particular changes between major
  versions).
- Python >= 3.12, `src/` layout, `uv` for everything. Never use `pip install`.
- Everything is typed (`mypy --strict`). Short functions, docstrings that explain *why*, no
  clever tricks: a junior developer must be able to read it.
- Imports at the top of the file. No `print` outside the CLI.

## Commands

```
uv sync
uv run ruff check && uv run ruff format --check
uv run mypy
uv run pytest -m "not network"     # must pass
uv run pytest -m network           # downloads Apple's public spec; must pass before bumping it
uv lock --upgrade                  # refresh dependencies
```

Tests, lint and types must all pass before every commit. Write the test first. Commit small
and often with Conventional Commit messages.

## Never weaken safety

- Writes stay off unless `ASC_ALLOW_WRITES` is on, and every write tool asks the human through
  MCP elicitation first (`tools/confirm.py`). No `confirm` argument, ever.
- The client only sends endpoints listed in `ENDPOINTS_USED` (`spec.py`). Add new ones only
  after verifying them against Apple's current OpenAPI spec, and add tests.
- Confirmations must work on both protocol eras (back-channel up to 2025-11-25, `input_required` on
  2026-07-28); `tests/test_confirm_eras.py` covers both. Never act on an unvalidated retry.
- The `Authorization` header goes only to `api.appstoreconnect.apple.com`.
- Never return or log tokens, keys or the issuer ID.
- Never read real credentials, never contact the real Apple API from tests or tooling: use the
  mocked transport in `tests/apple_fake.py`.
- Never push, publish or change the user's MCP configuration unless the maintainer explicitly asks.

## Dependency and spec changes

Follow `MAINTENANCE.md`: bump `APPLE_API_SPEC_VERSION` only after `pytest -m network` passes and
the release notes were read.
