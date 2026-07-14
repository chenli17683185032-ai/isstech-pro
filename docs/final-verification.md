# Final verification checklist

## Automated (clean environment)

```bash
cd /Users/ethan/Documents/isstech
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run python tools/export_openapi.py --check
uv run python tools/verify_no_secrets.py
git check-ignore -v captures/raw/auth_purchase_requisition.html
```

Pass criteria: all tests pass, Ruff is clean, committed OpenAPI exactly matches
the runtime schema, the secret scanner exits zero, and raw paths are ignored.
Last operator run before the baseline commit: **81 passed**, Ruff clean, no
warnings.

## Operator evidence check

Run in the original workspace that contains ignored raw evidence; a clean clone
is not expected to contain those files:

```bash
uv run python tools/verify_evidence.py
```

## Local API smoke (no upstream credentials)

```bash
uv run isstech-api
# other terminal
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/openapi.json | head
# unauthenticated business call must 401
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/v1/purchase-requisitions
```

Expected: health `ok`; openapi lists `/v1/*`; list returns **401** with `AUTH_EXPIRED`.

## Pure HTTP login smoke (account holder only)

Requires real credentials supplied **only at runtime** (env or prompt). Do not
write them to files under the repo.

```bash
export ISSTECH_USERNAME='...'
export ISSTECH_PASSWORD='...'
uv run python - <<'PY'
import os
from isstech_replay.auth import login_with_settings
user = os.environ["ISSTECH_USERNAME"]
password = os.environ["ISSTECH_PASSWORD"]
client, result = login_with_settings(user, password)
print("success", result.success)
print("cookie_names", result.session.cookie_names_present)
print("has_ipsa", result.session.has_ipsa_cookie)
print("final_url_host_only", result.final_url.split('/')[2] if '//' in result.final_url else result.final_url)
# read-only list
from isstech_replay.models.purchase import PurchaseListQuery, PurchaseView
listing = client.list_view(PurchaseView.APPLICATION)
print("count_items", len(listing.items), "total", listing.total_count)
client.close()
PY
```

Pass criteria:

- New process, **no** Chrome cookie import
- `has_ipsa` true after login
- List returns without raising
- No password printed

## Zero write egress check

```bash
uv run python - <<'PY'
import httpx
from isstech_replay.client import IsstechClient
from isstech_replay.policy import PolicyViolation

seen = []
def handler(req: httpx.Request) -> httpx.Response:
    seen.append(f"{req.method} {req.url}")
    return httpx.Response(200, request=req)

with IsstechClient(transport=httpx.MockTransport(handler)) as c:
    for method, url in [
        ("GET", "http://ipsapro.isstech.com/WebTP/PurchaseRequisition/Delete/1"),
        ("GET", "http://ipsapro.isstech.com/WebTP/PurchaseRequisition/Edit/%2e%2e/Delete/1"),
        ("GET", "http://evil.ipsapro.isstech.com/WebTP/PurchaseRequisition"),
        ("POST", "http://ipsapro.isstech.com/WebTP/Attachment/Upload/1"),
        ("POST", "http://ipsapro.isstech.com/WebTP/PurchaseRequisition/Submit/1"),
    ]:
        try:
            c.request(method, url)
        except PolicyViolation as e:
            print("blocked", e.decision.rule_id)
print("transport_hits", seen)
assert seen == []
print("OK zero write egress")
PY
```

## Sensitive information scan

```bash
uv run python tools/verify_no_secrets.py
```

The scanner excludes raw/Playwright evidence and fails on likely live API keys,
`.iPSA` values, and credential form values. Explicit `TEST_` placeholders are
allowed for synthetic tests.

## Browser capture still required (not automatable here)

| Capture | Path | Why |
| --- | --- | --- |
| Successful login | `captures/raw/YYYYMMDD-login-success.har` + redacted protocol JSON | Completes auth evidence; failed logins are `superseded` for success path |
| Five views | `captures/redacted/purchase-*.json` | Approval/Adjust/Revocation/Search still nav-only |
| Intercepted writes | redacted request templates | Upgrade builder notes from `inferred` → `observed` |

Procedure reminder: enable CDP/Fetch abort **before** clicking delete/submit;
unknown requests default abort; raw files mode `0600`; never commit values.

## Git baseline commit (if still uncommitted)

Run only after every automated gate above passes:

```bash
bash tools/first-commit.sh
```

## Honest completion statement

| Area | Done in-repo | Still needs human/target |
| --- | --- | --- |
| Evidence baseline docs | Yes | — |
| Policy + transport | Yes, including adversarial URL tests | — |
| Pure HTTP login code | Yes (mocked) | Success HAR + live smoke |
| Application Index/detail/attachment | Yes (mocked) | Live field parity check |
| Other four views | Live-blocked (`NOT_CAPTURED`) | View-specific captures |
| Write previews | Inferred and non-sendable | Intercepted bodies |
| FastAPI /v1 | Yes; runtime OpenAPI exported | Live session smoke |
| Vulnerability report | Draft from evidence | Second role, open redirect proof |
| Clean acceptance | Automated parts | Credentialed smoke |

Do **not** describe the project as “fully production-finished against live iPSA”
until the credentialed smoke rows pass and the success-login HAR is inventoried.
