# Security checklist for app-store-connect-mcp updates

Use this in step 4 of the review. It comes from a full security audit of **v0.22.0** (commit `069af75`, 2026-11-03). For each path that changed between your installed version and the candidate, read the diff and answer the questions next to it. If a file was moved or renamed, find where its logic went: the questions still apply.

## Baseline: what v0.22.0 looked like

**Verdict:** pass with conditions. The conditions are the deny rules and "never" items in SKILL.md.

**Known issues** (check whether a new release fixes or worsens them):

| # | Severity | Issue in v0.22.0 | Our mitigation |
|---|---|---|---|
| 1 | Medium | `toolsets` lets the model switch on any toolset, including web, admin and signing, without human approval (`internal/mcpserver/toolsets_tool.go`). | `mcp__asc__toolsets` denied |
| 2 | Medium | `web_login` counts as a read tool and opens a real Apple ID sign-in window (`internal/tools/web/login.go`). | `mcp__asc__web_login` denied; never run `web login` |
| 3 | Medium | `ASC_MCP_ALLOW_WRITES=0` does not lock writes. Only a pin file with `allow_writes = false` does (`internal/config/resolve.go`, `pin.go`). | Writes are on deliberately; server started from a neutral directory |
| 4 | Low | The key file is read with no permission check, despite what SAFETY.md says (`internal/auth/source.go`). | Key file kept at mode 0600 in a 0700 folder |
| 5 | Low | The presigned-URL fetcher has no host allowlist, only https, port 443 and public IPs (`internal/asc/fetch.go`). | Accepted: it never sends the token |
| 6 | Low | The web portal client allows POSTs without the write gate (`internal/app/portal.go`). | The web session is never created |
| 7 | Low | The `config snippet claude-code` allowlist pre-approves `toolsets` and `web_login`. | Never paste that snippet |

**Confirmed good in v0.22.0** (make sure these still hold):
- **No telemetry, no update checks.**
- **The JWT** (ES256, `aud=appstoreconnect-v1`, 19-minute lifetime) goes only to the API base URL.
- **Read-only gates:** write tools are unregistered while writes are off; calling one anyway is refused; the API client refuses any non-GET request without write mode and a confirmed call.
- **Approvals** are HMAC-signed, single-use, bound to the tool and its arguments, and answered by the client's form, never by tool arguments.
- **Credentials** are written only by the `auth` CLI, never by an MCP tool.
- **Releases** are built in CI with SHA-pinned actions, carry build provenance and are immutable.

## Paths and the questions to ask

| Path (v0.22.0 layout) | Questions |
|---|---|
| `internal/asc/client.go` | Does every non-GET request still need write mode plus a confirmed call? Does the token still go only to the API base URL (https or loopback)? |
| `internal/asc/fetch.go` | Still https, port 443 and public IPs only? Still refuses `Authorization`/`Cookie` headers and never attaches the token? |
| `internal/asc/paginate.go` | Are next-page links still checked against the API host? |
| `internal/asc/redact.go`, `internal/mcpserver/handler.go` | Is redaction of tokens and keys in results and errors still applied? |
| `internal/auth/` | JWT algorithm, audience and lifetime unchanged (≤ 20 minutes)? Is the key or JWT ever logged, returned or written to disk? Does key-file loading now check permissions (fixes issue 4)? |
| `internal/config/` | Config still written 0600 and refused when others can write it? How are writes enabled, and did the pin-file lookup change? |
| `internal/tool/approval.go`, `internal/tool/tool.go`, `internal/tool/hidden.go` | Can a tool call approve itself or skip the approval form? Are write tools still hidden when writes are off? |
| `internal/mcpserver/toolsets_tool.go`, `server.go`, `instructions.go` | Can the model still enable toolsets or writes without a human? (If yes, keep `toolsets` denied.) Do the server instructions now tell the model to do anything risky? |
| `internal/tools/core/api.go` | Is `asc_api_get` still GET-only? Does `asc_api_request` still always require approval? |
| `internal/tools/web/`, `internal/app/portal.go`, `internal/browser/` | Is the web toolset still off by default? Any new path that creates or uses an Apple ID session? |
| `internal/app/env.go`, `internal/app/serve.go` | Any new network host, download or background job? Do logs still go to stderr only? |
| `internal/tool/untrusted.go`, `sanitize.go` | Is user-generated text (reviews, feedback, tester names) still marked as untrusted? |
| `cmd/app-store-connect-mcp/auth.go`, `configcmd.go` | Can anything besides the `auth` CLI write credentials? Does the generated client config pre-approve risky tools? |
| `go.mod`, `go.sum` | Handled in step 3: new or unusual modules, `replace` lines. |
| `.github/workflows/release.yml`, `install.sh` | Actions still pinned to SHAs? Provenance and signing still produced? Releases still immutable? |

## Network hosts seen in v0.22.0

- `api.appstoreconnect.apple.com`: the API.
- `appstoreconnect.apple.com`, `idmsa.apple.com`, `developer.apple.com`: web toolset only.
- `itunes.apple.com`: store toolset.
- `storage.googleapis.com`: Chrome for Testing download, web login only.
- Your app's support, marketing and privacy URLs: `release_status` with `check_urls`.

**Any new host is a finding.** Explain what it is for before recommending the upgrade.
