# Security model

This server holds a key that can change a real App Store listing, so the design is built around
"what is the worst thing that can happen, and who has to say yes first?".

## Who can do what

| Action | Allowed when |
|---|---|
| Read data | Always (after the monthly review is current) |
| Change data | `ASC_ALLOW_WRITES` is on **and** you accept a form describing the exact change |
| Read a local file | `ASC_ROOT` is set and the file really lives inside it |
| Call an Apple endpoint | It is listed in `ENDPOINTS_USED` (`src/app_store_connect/spec.py`) |

## The rules, in plain words

1. **Read-only by default.** Without `ASC_ALLOW_WRITES`, the write tools are not even registered.
   The model cannot see them or call them.
2. **A human confirms every change.** Write tools use MCP elicitation: your client shows a form
   with the old value and the new value (or the action and its target). There is no `confirm`
   argument, so the model cannot say yes for you. No answer, a decline, a cancel or a client
   without form support all mean nothing is sent. Public and irreversible actions say so in
   the form.
3. **No arbitrary requests.** The HTTP client refuses any (method, path) that is not in
   `ENDPOINTS_USED`. A test checks that this list matches the code.
4. **No Apple ID login.** Only the official API key is used. There is no web session anywhere.
5. **The token only goes to Apple.** The `Authorization` header is only added for
   `api.appstoreconnect.apple.com`. Pagination links from a response are followed only if they
   stay on that host. Redirects are not followed. Presigned screenshot upload URLs must be
   https and get no `Authorization` header at all.
6. **Your key stays out of the config.** You pass a *path* (`ASC_PRIVATE_KEY_PATH`). The key
   contents (`ASC_PRIVATE_KEY`) are refused, and the file must be readable only by you
   (mode 600 in a private folder). The key is read only when a token is signed (every 15
   minutes at most) and is never logged.
7. **Secrets never come back.** Results and error messages never contain the token, the key,
   or the issuer ID. The key ID is shown at most as its last 4 characters.
8. **Files are confined.** Screenshot paths are resolved (symlinks followed) and must end up
   inside `ASC_ROOT`; otherwise nothing is read and nothing is sent.
9. **Untrusted text stays untrusted.** Customer reviews are written by strangers. Their text is
   wrapped in markers that tell the model to treat it as data, not instructions. Because writes
   need your confirmation anyway, an injected instruction cannot change anything by itself.
10. **Friendly to Apple.** Failed requests (429, 5xx) are retried at most 3 times with backoff
    and `Retry-After` is respected. A POST is never repeated after a 5xx, in case it was
    already processed.

## The monthly review

Tools stop working after 30 days without a human-confirmed review. The review record
(`~/.local/state/app-store-connect/review.json`) is written atomically with mode 600. A missing,
corrupt or future-dated record counts as "overdue". The record can only be written after you
accept a form in your client, or by you in a terminal.

## What is not protected

- Anyone who can run commands as your user can read your key file and the review record.
- A key with a powerful role can do powerful things; use the smallest role that works.
- The server trusts Apple's API and the Python packages it depends on. The monthly report checks
  those packages for known vulnerabilities and newer releases.
