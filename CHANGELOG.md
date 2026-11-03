# Changelog

## 0.2.0

Security fixes from an independent review.

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
- Docs say plainly that the protections assume the AI agent cannot run commands as your user.

## 0.1.0

- First release: a self-contained MCP server for Apple's App Store Connect API.
- 13 read tools; 11 write tools that are off by default and always confirmed by the human.
- Monthly review gate with a report (Apple spec, endpoints, dependencies, vulnerabilities).
- Checked against Apple's OpenAPI spec 4.5.1.
