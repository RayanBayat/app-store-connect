---
name: asc-mcp-maintenance
description: Monthly maintenance review for the App Store Connect MCP server (server name `asc`, github.com/idoo/app-store-connect-mcp). Use when an mcp__asc__ tool call is blocked with "maintenance review is overdue", when the user asks to review, update, upgrade or check the App Store Connect MCP or the asc server (its releases, dependencies, vulnerabilities, or new and deprecated tools), or when `asc_maint.py status` says a review is due.
---

# App Store Connect MCP: monthly maintenance review

**Why this exists:** the App Store Connect MCP server holds an API key that can change your App Store listing, testers and releases. It is third-party code that changes fast. A hook in this plugin blocks every `mcp__asc__*` tool once the last review is **30 days** old. That covers both cases: a server nobody touched for a month, and one used every day that would otherwise never be re-checked. This document is the review. When you finish it and stamp the result, the tools unblock.

Scripts live next to this file, in `<base>/scripts/`, where `<base>` is the base directory shown when this skill loads. Put downloads and temporary files in the session's scratchpad (`<scratch>` below), never inside a project.

## Ground rules (read first)

1. **The human decides.** You gather facts and present the report (step 8). Never install a new version or change safety settings without an explicit "yes" from the user.
2. **Only verified installs.** Install only with `<base>/scripts/install_release.sh`, which aborts unless the checksum **and** GitHub's build provenance check out. Never `curl … | sh`, `go install` or a binary from anywhere else.
3. **Never weaken the safety setup.**
   - These stay in Claude Code's `permissions.deny`: `mcp__asc__toolsets`, `mcp__asc__web_login`, `mcp__asc__asc_api_request`, and `Read(...)` rules for the key folders.
   - Never set `ASC_MCP_CI`, `allow_no_elicit` or `--no-confirm`. Never run `app-store-connect-mcp web login`.
   - Never put the private key in an environment variable or in the chat.
4. **Latest stable, current APIs, exact versions.** Check registries and docs now. Don't trust memory or old notes.
5. **Every review ends with a stamp** (step 10), even when nothing changed. The stamp records what you checked and why you stayed or moved.
6. **Never build or run upstream source code.** Read it. Run only release binaries that `install_release.sh` has verified.

## Step 1: Where are we?

```
python3 <base>/scripts/asc_maint.py status
```

Write down the installed version, the date of the last review, its outcome, and the latest upstream release. In the steps below, `vOLD` is the installed version and `vNEW` is the newest release.

## Step 2: What changed upstream?

```
gh release list -R idoo/app-store-connect-mcp --limit 20
gh release view vNEW -R idoo/app-store-connect-mcp
gh repo view idoo/app-store-connect-mcp --json isArchived,pushedAt,licenseInfo,stargazerCount
gh api repos/idoo/app-store-connect-mcp/security-advisories --jq '.[] | [.ghsa_id, .severity, .published_at, .summary] | @tsv'
```

Read the notes of **every** release between `vOLD` and `vNEW`. List:
- new tools, toolsets, flags and environment variables;
- removed, renamed or deprecated tools;
- breaking changes;
- security fixes.

**Stop and report immediately if** the repo is archived, the licence changed, the maintainer changed, there has been no release for 6 months, or an advisory affects `vOLD`.

## Step 3: Dependencies and known vulnerabilities

```
gh api "repos/idoo/app-store-connect-mcp/contents/go.mod?ref=vOLD" --jq .content | base64 -d > <scratch>/go.mod.old
gh api "repos/idoo/app-store-connect-mcp/contents/go.mod?ref=vNEW" --jq .content | base64 -d > <scratch>/go.mod.new
diff <scratch>/go.mod.old <scratch>/go.mod.new
python3 <base>/scripts/osv_check.py <scratch>/go.mod.old
python3 <base>/scripts/osv_check.py <scratch>/go.mod.new
```

- **In the diff, flag:** new modules (what are they for, are they maintained?), major version bumps, a changed Go toolchain, and any `replace` line (a red flag: ask why).
- **`osv_check.py` exit codes:** 1 means known vulnerabilities and lists their IDs and fixed versions; 0 means clean; 2 means the check itself failed, so say so and never report "clean".
- **Compare the two scans.** If `vOLD` is vulnerable and `vNEW` fixes it, the upgrade is urgent.

## Step 4: Code changes that touch security

```
gh api repos/idoo/app-store-connect-mcp/compare/vOLD...vNEW --jq '.files[] | [.status, .changes, .filename] | @tsv'
```

`references/audit-checklist.md` lists the security-critical paths and the questions to answer for each. For every changed file on that list:
- read its diff (`gh api repos/idoo/app-store-connect-mcp/compare/vOLD...vNEW --jq '.files[] | select(.filename=="PATH") | .patch'`);
- answer the checklist questions.

If those paths changed by more than about 500 lines in total, hand them to a security-review subagent running the strongest available model. Give it the checklist and the compare URL, and tell it to read only, never build or run anything.

Always check that `.github/workflows/release.yml` still signs releases and publishes build provenance. Without that, `install_release.sh` cannot verify anything, and you must stop.

## Step 5: Tools that were added, removed or deprecated

Unpack the candidate without installing it, then list its tools:

```
bash <base>/scripts/install_release.sh VERSION --extract-only <scratch>/candidate
uv run <base>/scripts/probe.py --binary <scratch>/candidate/app-store-connect-mcp --allow-writes --names-only > <scratch>/tools-new.txt
python3 <base>/scripts/asc_maint.py diff-tools --tools-file <scratch>/tools-new.txt
uv run <base>/scripts/probe.py --binary <scratch>/candidate/app-store-connect-mcp --allow-writes
```

The last command marks write tools with `[write]`. For each **new** tool, work out whether it:
- writes;
- touches money, users, certificates or signing, the Apple ID web session, or arbitrary API endpoints.

If it widens access beyond what the user actually needs, propose a new `permissions.deny` rule.

For each **removed or renamed** tool, find everything that names it (deny rules in `~/.claude/settings.json`, notes, memory) and plan the update. Also search the release notes and tool descriptions for "deprecated".

## Step 6: Outside the server

- **Apple:** read the [App Store Connect API release notes](https://developer.apple.com/documentation/appstoreconnectapi/app-store-connect-api-release-notes) for entries since the last review. Compare them with the spec version that `app-store-connect-mcp doctor --profile main` prints, and note new endpoints the server doesn't cover yet and anything Apple deprecated.
- **Claude Code / MCP:** look for changes to MCP approvals (elicitation), permission rules or hooks that affect this setup.
- **Alternatives:** search briefly for an official Apple MCP server or a better-maintained alternative. Report what you find, but don't switch without the user.

## Step 7: This plugin itself

- Open Dependabot pull requests: `gh pr list -R RayanBayat/asc-mcp-maintenance`.
- Check that `scripts/probe.py` pins the latest `mcp` SDK and that CI is green.
- If you change the plugin, run its tests (`uv run pytest -q`, `uv run ruff check`) in the repo checkout before committing.

## Step 8: Report to the user

Present this table, then a one-line recommendation and a clear question:

| Check | Installed (vOLD) | Latest (vNEW) | Notes |
|---|---|---|---|
| Server version and release date | | | |
| Security advisories | | | |
| Known vulnerable dependencies | | | |
| Dependency changes | | | new, removed, major bumps, replace |
| Security-relevant code changes | | | per checklist |
| New tools | | | write? deny rule proposed? |
| Removed, renamed or deprecated tools | | | what references them |
| Apple API changes | | | |
| Alternatives worth knowing | | | |

> Recommendation: upgrade to vNEW / stay on vOLD because … Proposed settings changes: … **Install vNEW now?**

## Step 9: Upgrade (only after the user says yes)

1. `bash <base>/scripts/install_release.sh VERSION`, which verifies, installs and prints the version and sha256.
2. `app-store-connect-mcp doctor --profile main`: every line must say `ok`.
3. `uv run <base>/scripts/probe.py --allow-writes --names-only > <scratch>/tools-installed.txt`, then one read-only call: `uv run <base>/scripts/probe.py --call apps_list`.
4. Apply the deny-rule changes the user approved (merge into `~/.claude/settings.json`, never replace it).
5. Record the change wherever this machine records installs: for example its system changelog, package list and notes, including the new pinned version.
6. Tell the user to restart Claude Code so it starts the new server.

**Rollback** if anything misbehaves: `bash <base>/scripts/install_release.sh vOLD`, then repeat 2–3.

## Step 10: Stamp the review (always)

```
python3 <base>/scripts/asc_maint.py stamp --server-version INSTALLED_VERSION --outcome "ONE LINE" --tools-file <scratch>/tools-installed.txt
python3 <base>/scripts/asc_maint.py status
```

Good outcome lines are specific, for example "upgraded 0.22.0→0.24.1; 0 vulns; +2 tools (asc_x denied); Apple API 4.6 noted" or "stayed on 0.22.0: no new release; 0 vulns". If you stayed on the old version, use the tools list from step 5 for the installed binary, or leave out `--tools-file` to keep the old snapshot. The gate unblocks as soon as the stamp is written.
