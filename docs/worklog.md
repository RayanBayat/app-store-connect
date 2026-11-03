# Work log

A short, dated list of what was done and why. Newest first. Add an entry for every piece of work: one or two lines are enough. Explain the *why*, not just the *what*.

## 2026-11-03

- Wrote `docs/project-knowledge.md` (everything learned so far, in plain words) and this log. Added the "always log, keep docs current" rule to `AGENTS.md`. *Why:* the owner wants the knowledge kept in the repo and kept up to date.
- Keywords are now limited in **bytes** and app names need at least 2 characters. *Why:* Apple's Help says so, and Swedish letters count twice.
- Added the tools `create_version_localization`, `create_app_info_localization` and `update_app_primary_locale`. *Why:* the KörUppNu store listing needs Swedish added and Swedish as the primary language.
- Confirmation forms now work on MCP protocol `2026-07-28`, using `input_required`. *Why:* in real use with Claude Code no form appeared, because the old way can't work on the new protocol.
- Report fixes: the "outdated" check uses PyPI's small RSS feed and only our direct dependencies. A first review says "No earlier review". *Why:* found during the first real review.
- Fixed every finding of the independent security review (version 0.2.0): exact forms, untrusted-text markers that can't be faked, pinned dependencies, honest docs, tighter ID, screenshot and upload checks, and key file checks at signing time.
- Made the repo public after a full-history secret scan. Added GitHub rules protecting `main` and `v*` tags.
- Rebuilt the project as our **own self-contained Python server**, replacing the earlier plan of wrapping a third-party Go server. *Why:* the owner wants no dependency on a stranger's code.
