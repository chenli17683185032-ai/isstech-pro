# Successful login capture runbook (step 2)

**Goal:** Capture one clean successful login protocol without committing secrets.

**Output files (do not overwrite existing raw files):**

- `captures/raw/YYYYMMDD-login-success.har` (mode `0600`)
- `captures/redacted/login-success-protocol.json` (no password, no cookie/ticket values)
- Update `docs/evidence-manifest.json` with SHA-256 and status

## Preconditions

- Use a **fresh browser profile** or clear site data for `isstech.com` / `passport.isstech.com`.
- Account holder enters credentials **manually** (never paste into chat, git, or fixtures).
- Chrome DevTools → Network: enable **Preserve log**, **Disable cache**.
- Optional: CDP `Fetch.enable` with request-stage pause for any unexpected POST to business write paths (should not fire during login-only flow).

## Capture sequence

1. Open DevTools Network **before** navigation.
2. Visit: `http://ipsapro.isstech.com/WebTP/PurchaseRequisition`
3. Confirm 302 → `https://passport.isstech.com/?DomainUrl=...&ReturnUrl=%2fWebTP%2fPurchaseRequisition`
4. Manually submit valid credentials once.
5. Wait until the authenticated Purchase Requisition shell loads (title contains `软通智慧科技专业服务系统`, form `formPurchaseRequisitionIndex` present).
6. Export HAR: save as  
   `captures/raw/$(date +%Y%m%d)-login-success.har`
7. Immediately: `chmod 600 captures/raw/*login-success*.har`

## Redact into protocol JSON

Record **only**:

| Item | What to store |
| --- | --- |
| Request chain | method, URL template, status, redirect Location **host+path** (strip query ticket values if any) |
| Form | action URL template, field **names** |
| Cookies set | name, domain, path, secure, httpOnly, sameSite, session/persistent — **never values** |
| Success signals | final business URL path, presence of `.iPSA` name, absence of login form markers |
| Timing | ISO timestamp |

Suggested shape (fill from HAR with a local script; do not commit intermediate dumps with values):

```json
{
  "capturedAt": "YYYY-MM-DDTHH:MM:SSZ",
  "sourceHar": "captures/raw/YYYYMMDD-login-success.har",
  "steps": [
    {"method": "GET", "url": "http://ipsapro.isstech.com/WebTP/PurchaseRequisition", "status": 302, "locationHost": "passport.isstech.com"},
    {"method": "GET", "url": "https://passport.isstech.com/?DomainUrl=...&ReturnUrl=...", "status": 200},
    {"method": "POST", "url": "https://passport.isstech.com/...", "status": 302, "setCookieNames": ["..."]},
    {"method": "GET", "url": "http://ipsapro.isstech.com/...", "status": 200, "authenticatedPage": true}
  ],
  "loginForm": {
    "actionTemplate": "...",
    "fields": ["emp_DomainName", "emp_Password", "DomainUrl", "ReturnUrl", "RemeberMe (if checked)"],
    "pageOnlyFieldsOutsideForm": ["flag", "uname", "ctip", "etip", "bgstr"]
  },
  "cookies": [],
  "notes": []
}
```

Helper (run locally after HAR exists; prints names only):

```bash
cd /Users/ethan/Documents/isstech
uv run python tools/redact_login_har.py captures/raw/YYYYMMDD-login-success.har \
  > captures/redacted/login-success-protocol.json
chmod 600 captures/raw/*login-success*.har
shasum -a 256 captures/raw/*login-success*.har captures/redacted/login-success-protocol.json
```

## Live pure-HTTP smoke (after capture)

```bash
export ISSTECH_USERNAME='...'   # runtime only
export ISSTECH_PASSWORD='...'
uv run python - <<'PY'
import os
from isstech_replay.auth import login_with_settings
from isstech_replay.models.purchase import PurchaseView
client, result = login_with_settings(os.environ["ISSTECH_USERNAME"], os.environ["ISSTECH_PASSWORD"])
assert result.success and result.session.has_ipsa_cookie
listing = client.list_view(PurchaseView.APPLICATION)
print("items", len(listing.items), "total", listing.total_count)
# prove write still blocked
from isstech_replay.policy import PolicyViolation
try:
    client.get(client.settings.base_url + "/WebTP/PurchaseRequisition/Delete/0")
except PolicyViolation as e:
    print("delete_blocked", e.decision.rule_id)
client.close()
print("OK")
PY
unset ISSTECH_PASSWORD
```

## Do not

- Commit the HAR or any file containing `emp_Password=...` / `.iPSA=...` values.
- Re-use failed-login HTML as success evidence (`superseded` for success path).
- Click delete/submit/approve during this capture.
