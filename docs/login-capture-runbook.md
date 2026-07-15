# Login evidence and clean-HTTP validation runbook

## Purpose

Keep three distinct claims separate:

1. The Passport login form and credential POST shape are known.
2. A manual Chrome credential POST followed by an authenticated Portal response
   was captured on `2026-07-15`.
3. A new pure-HTTP process can obtain its own valid business ticket using runtime
   credentials.

Claims 1 and 2 are complete. Claim 3 remains pending because the captured Chrome
request already carried a Cookie named `.iPSA`; the capture did not observe a new
`.iPSA` Set-Cookie in that redirect chain.

## Current evidence

| Artifact | Storage | Rules |
| --- | --- | --- |
| Raw CDP login capture | `captures/raw/20260715-login-attempt-01.cdp.json` | Local only, mode `0600`, gitignored |
| Redacted protocol | `captures/redacted/login-success-protocol.json` | Commit eligible after secret scan |
| Inventory | `docs/evidence-manifest.json` | Contains hashes and metadata, never values |

The redacted protocol stores only capture time, allowed URL shapes, methods,
status codes, form field names, Cookie names/attributes, and the authenticated
page signal. It never stores credential or Cookie values.

## Reproduce the redacted protocol

Run from the repository root:

```bash
cd /Users/ethan/Documents/isstech
tmp_file="$(mktemp)"
.venv/bin/python tools/redact_login_cdp.py \
  captures/raw/20260715-login-attempt-01.cdp.json > "$tmp_file"
cmp "$tmp_file" captures/redacted/login-success-protocol.json
rm -f "$tmp_file"
shasum -a 256 \
  captures/raw/20260715-login-attempt-01.cdp.json \
  captures/redacted/login-success-protocol.json
```

Expected SHA-256 values are pinned in `docs/evidence-manifest.json`. `cmp` must
produce no output and exit zero.

## Run the clean pure-HTTP smoke

The account holder must place credentials only in the current terminal process.
Do not put them in chat, shell history, source files, `.env`, fixtures, or logs.

```bash
cd /Users/ethan/Documents/isstech
export ISSTECH_USERNAME='...'
export ISSTECH_PASSWORD='...'
.venv/bin/python tools/live_smoke.py
unset ISSTECH_PASSWORD ISSTECH_USERNAME
```

Pass signals:

```text
login_success True
has_ipsa True
list_items <count>
delete_blocked pr.delete
SMOKE_OK
```

The tool prints Cookie names and aggregate counts only. It starts a new `httpx`
session, performs one read-only list request, and proves that a Delete URL is
blocked before reaching transport. Failure exits non-zero.

## Optional clean-browser recapture

This is needed only if a clean browser issuance trace is required in addition to
the pure-HTTP smoke.

### Website and tools

| Purpose | Value |
| --- | --- |
| Start URL | `http://ipsapro.isstech.com/WebTP/PurchaseRequisition` |
| Login host | `https://passport.isstech.com/` |
| Success host/path | `http://ipsapro.isstech.com/portal` or the requested PurchaseRequisition path |
| Browser | Chrome with a fresh profile or cleared site data |
| Capture | Chrome DevTools Network or CDP |
| UI operation | Computer Use is allowed for navigation and read-only checks; the account holder enters credentials |

### Sequence

1. Create a fresh Chrome profile or clear site data for `isstech.com` and
   `passport.isstech.com`.
2. Open DevTools Network before navigation; enable **Preserve log** and
   **Disable cache**.
3. Navigate to the PurchaseRequisition start URL and confirm the Passport
   redirect.
4. The account holder enters credentials once.
5. Stop when the authenticated Portal or PurchaseRequisition shell loads.
6. Export to a new filename under `captures/raw/`; never overwrite existing raw
   evidence.
7. Immediately run `chmod 600 captures/raw/<new-file>`.
8. Redact locally, inspect names-only output, update the manifest hash, then run
   both verification tools.

For a HAR capture use:

```bash
.venv/bin/python tools/redact_login_har.py \
  captures/raw/YYYYMMDD-login-success.har \
  > /tmp/login-success-protocol.json
```

Review `/tmp/login-success-protocol.json` before replacing the committed CDP
summary. The CDP summary remains the baseline unless the new evidence is both
cleaner and reproducible.

## Prohibited actions

- Do not commit HAR/CDP, credentials, Cookie values, ticket values, or business
  record contents.
- Do not click create, save, submit, approve, adjust, revoke, delete, or upload.
- Do not broaden the endpoint allowlist to make a smoke test pass.
- Do not treat an authenticated page from an existing browser session as proof
  that a clean HTTP client can log in.
