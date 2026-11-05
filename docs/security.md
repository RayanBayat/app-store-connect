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
2. **A human confirms every change.** Step by step, for every write tool:
   1. The tool reads the current data from Apple and builds a form that shows the exact change:
      the full old value and the full new value (or the action and its target). Public and
      irreversible actions say so at the top of the form.
   2. There is no `confirm` argument, so the model cannot say yes for you.
   3. The form is shown by your own client (MCP elicitation). How it gets there depends on the
      protocol version the client negotiated. Both ways are supported and tested:
      - **Older protocol (up to 2025-11-25): the server asks during the call.** It sends the form
        to the client in the middle of the tool call (the "back-channel") and waits for the
        answer.
      - **Protocol 2026-07-28: the call returns "input required".** A server may not send
        requests during a call. The tool returns an `input_required` result that carries the
        form; the client shows it and calls the same tool again with your answer and an opaque
        `request_state`.
   4. With protocol 2026-07-28 the retry comes from outside, so it is not trusted:
      1. `request_state` is encrypted and authenticated (the SDK's AES-GCM codec, a random key
         per process), bound to the tool name and the exact arguments, and expires after 10
         minutes.
      2. It also holds a single-use nonce and a fingerprint of the exact form you saw.
      3. On the retry the server rebuilds the change from the current arguments and the current
         data at Apple. If anything differs from what you saw (for example the old value changed
         meanwhile), it shows you a fresh form instead of acting.
   5. Only an explicit press of Accept in your own client proceeds (the form has no fields to
      fill in). No answer, a decline, a cancel, a client without form support, a missing or wrong
      answer, or a tampered, expired or replayed state all mean nothing is sent, and the error
      says which of these it was.
3. **No arbitrary requests.** The HTTP client refuses any (method, path) that is not in
   `ENDPOINTS_USED`. A test checks that this list matches the code.
4. **No Apple ID login.** Only the official API key is used. There is no web session anywhere.
5. **The token only goes to Apple.** The `Authorization` header is only added for
   `api.appstoreconnect.apple.com`. Pagination links from a response are followed only if they
   stay on that host. Redirects are not followed. Presigned screenshot upload URLs must be
   https and get no `Authorization` header at all.
6. **Your key stays out of the config.** You pass a *path* (`ASC_PRIVATE_KEY_PATH`). The key
   contents (`ASC_PRIVATE_KEY`) are refused. At start-up and again each time a token is signed,
   the open file must be a regular file owned by you, mode 600, under 8 KiB (and in a private
   folder). The key is read only when a token is signed (every 15 minutes at most) and is never
   logged.
7. **Secrets never come back.** Results and error messages never contain the token, the key,
   or the issuer ID. The key ID is shown at most as its last 4 characters.
8. **Files are confined.** Screenshot paths are resolved (symlinks followed) and must end up
   inside `ASC_ROOT` (which may not be `/`, your home folder or writable by others); the file is
   then opened once, without following symlinks, and checked, and exactly those bytes are sent.
   Upload instructions from Apple are only used for PUTs to https `apple.com` hosts.
9. **Untrusted text stays untrusted.** Customer reviews are written by strangers. Their text is
   wrapped in random per-call markers that tell the model to treat it as data, not instructions.
   Because writes need your confirmation anyway, an injected instruction cannot change anything
   by itself (as long as you read the form: it shows the full, escaped value).
10. **Friendly to Apple.** Failed requests (429, 5xx) are retried at most 3 times with backoff
    and `Retry-After` is respected. A POST is never repeated after a 5xx, in case it was
    already processed.

## The monthly review

Tools stop working after 30 days without a human-confirmed review. The review record
(`~/.local/state/app-store-connect/review.json`) is written atomically with mode 600. A missing,
corrupt or future-dated record counts as "overdue". The record can only be written after you
accept a form in your client (which needs a report built in the same session in the last 30
minutes), or by you in a terminal. It also stores the version and git commit of the code that
was reviewed, and the report says when the running code is different.

## What this does not protect against, and what to do about it

**All of the protections above assume the AI agent cannot run arbitrary commands as your OS
user.** They stop a model that is confused, tricked by text in a review, or simply wrong. They do
not stop an agent that has a shell or file-write access as the same user, which is the default in
Claude Code. That agent could:

- read the `.p8` key file (it is mode 600, but it is *your* file and the agent runs as you);
- edit `~/.local/state/app-store-connect/review.json` to fake a fresh review;
- fake a terminal (for example with a pseudo-terminal) to pass the `review` command's TTY check;
- edit the MCP configuration (for example `~/.claude.json`) to add `ASC_ALLOW_WRITES=1`, or to
  start a different server with your key.

What to do, from cheapest to strongest:

1. **Claude Code permission deny rules** for the key folder, the state folder and the MCP
   configuration. In `~/.claude/settings.json`:

   ```json
   {
     "permissions": {
       "deny": [
         "Read(~/.config/app-store-connect/**)",
         "Edit(~/.config/app-store-connect/**)",
         "Read(~/.local/state/app-store-connect/**)",
         "Edit(~/.local/state/app-store-connect/**)",
         "Edit(~/.claude.json)",
         "Edit(~/.claude/settings.json)"
       ]
     }
   }
   ```

   Change the first four paths if you keep the key elsewhere. These rules apply to Claude's file
   tools. A shell command can reach the same files in many ways, so treat the rules as a speed
   bump, not a wall, unless you also use the sandbox below.
2. **No auto-approve or bypass permission modes while writes are on.** Every prompt you skip is
   a place where an injected instruction could run a command without you seeing it.
3. **Never answer elicitation forms automatically.** Do not write a hook (or any script) that
   accepts these confirmation forms for you. A form that answers itself is no confirmation.
4. **For strong isolation, take the key out of the agent's reach:** run the server as a
   separate OS user (the key and the state folder belong to that user, mode 600/700, and the agent's user
   cannot read them), or run the agent in a sandbox (for example Claude Code's sandbox, a
   container or a VM) that has no access to those folders.

## What else is not protected

- A key with a powerful role can do powerful things; use the smallest role that works.
- The server trusts Apple's API and the Python packages it depends on. These are pinned exactly
  in `pyproject.toml` and `uv.lock`; the monthly report checks them for known vulnerabilities and
  newer releases, and CI audits them.
- Text from the API is wrapped in random per-call markers so the model treats it as data. This
  lowers the risk of prompt injection; it cannot remove it. Confirmation forms are the real
  barrier, and they only help if you read them.
- Which code runs is your choice: a pinned release tag runs only what you chose, while
  `@main --refresh` runs whatever is on `main` at each start (see the README).
