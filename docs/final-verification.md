# Final verification checklist

## Automated (clean environment)

```bash
cd /Users/ethan/Documents/isstech
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests tools
uv run python tools/export_openapi.py --check
uv run python tools/verify_no_secrets.py
uv run python tools/verify_evidence.py
git check-ignore -v captures/raw/auth_purchase_requisition.html
git check-ignore -v captures/raw/20260715-login-attempt-01.cdp.json
git check-ignore -v data/workflow-center.sqlite3
git diff --check
```

Pass criteria: all tests pass, Ruff is clean, committed OpenAPI exactly matches
the runtime schema, both verification tools exit zero, every raw path is
ignored, and the diff has no whitespace errors.

Last complete P4 gate on `2026-07-15`: **158 passed**, Ruff clean, OpenAPI
matched runtime, both verification tools passed, raw permissions/ignore checks
passed, SQLite migration/wheel/API/CLI contracts passed, offline material smoke
passed, and `git diff --check` passed.

## Operator evidence check

Run in the original workspace that contains ignored raw evidence; a clean clone
is not expected to contain those files:

```bash
uv run python tools/verify_evidence.py
```

Reproduce the committed login protocol from ignored raw evidence:

```bash
tmp_file="$(mktemp)"
uv run python tools/redact_login_cdp.py \
  captures/raw/20260715-login-attempt-01.cdp.json > "$tmp_file"
cmp "$tmp_file" captures/redacted/login-success-protocol.json
rm -f "$tmp_file"
```

Pass criteria: every manifest hash matches, sensitive artifacts satisfy their
permission gate, and `cmp` exits zero without output.

## Local API smoke (no upstream credentials)

```bash
uv run isstech-api
# other terminal
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/openapi.json | head
# unauthenticated business call must 401
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/v1/purchase-requisitions
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/v1/work-items
```

Expected: health `ok`; OpenAPI lists `/v1/*`; both business calls return **401**
with `AUTH_EXPIRED`.

## Pure HTTP login smoke (account holder only)

Requires real credentials supplied **only at runtime** (env or prompt). Do not
write them to files under the repo.

```bash
export ISSTECH_USERNAME='...'
export ISSTECH_PASSWORD='...'
uv run python tools/live_smoke.py
unset ISSTECH_PASSWORD ISSTECH_USERNAME
```

Pass criteria:

- New process, **no** Chrome cookie import
- `has_ipsa True` after login
- List returns without raising
- `delete_blocked pr.delete` and `SMOKE_OK`
- No password or Cookie value printed

## Durable sync smoke (account holder only)

After the pure-HTTP login smoke succeeds:

```bash
export ISSTECH_USERNAME='...'
export ISSTECH_PASSWORD='...'

# Must not create the database
uv run python tools/sync_work_items.py --dry-run --json

# Creates one successful run and optional CSV
uv run python tools/sync_work_items.py --json --csv

sqlite3 data/workflow-center.sqlite3 \
  'select status, observed_count, actionable_count, event_count from sync_runs order by started_at desc limit 1;'
stat -f '%Lp %N' \
  data/workflow-center.sqlite3 \
  data/runs/*/summary.json \
  data/exports/*-work-items.csv

unset ISSTECH_PASSWORD ISSTECH_USERNAME
```

Pass criteria: dry-run leaves no new DB/run file; real sync is `succeeded`;
source count is complete; all files report mode `600`; a second unchanged sync
adds history but zero change events.

## Material ingestion smoke (offline)

```bash
tmp_dir="$(mktemp -d)"
printf '%%PDF-1.7\nREDACTED\n%%%%EOF\n' > "$tmp_dir/sample.pdf"
uv run python tools/ingest_materials.py "$tmp_dir/sample.pdf" \
  --data-dir "$tmp_dir/data" --json
uv run python tools/ingest_materials.py "$tmp_dir/sample.pdf" \
  --data-dir "$tmp_dir/data" --json
sqlite3 "$tmp_dir/data/workflow-center.sqlite3" \
  'select (select count(*) from material_blobs), (select count(*) from materials);'
stat -f '%Lp %N' "$tmp_dir"/data/materials/originals/*/blob
rm -rf "$tmp_dir"
```

Pass criteria: both CLI calls exit zero; the second reports deduplicated; the
database reports `1|1`; the original blob mode is `400`; no staging `.part`
remains.

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

## Remaining evidence-dependent checks

| Capture | Path | Why |
| --- | --- | --- |
| Clean pure-HTTP login | Runtime-only credentials; no saved credential artifact | Proves ticket issuance in a new process without Chrome Cookie import |
| Intercepted writes | Redacted request templates | Upgrade builder notes from `inferred` to `observed`; capture must pause and abort before send |
| Second role | Read-only comparison evidence | Evaluate read-side IDOR without modifying target state |

Successful Chrome credential POST, Portal response, Search GET/POST/pagination,
Detail, ApprovalIndex, AdjustIndex, and RevocationIndex were captured on
`2026-07-15` and are inventoried. No additional browser navigation is required
for P0-P2.

For future write-shape evidence, enable CDP/Fetch pause and abort **before** any
click. Unknown requests default abort; raw files remain mode `0600`; never commit
values.

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
| Browser login protocol | Yes, redacted CDP is reproducible | Capture does not prove clean-session ticket issuance |
| Pure HTTP login code | Yes (mocked) | Runtime credentialed live smoke |
| Application/Search list and Detail | Yes, with live schema/count parity | Pure-HTTP live smoke |
| Approval/adjustment/revocation views | Initial GET captured; exact read paths enabled | Non-empty role fixtures remain unavailable |
| Attachment path | Real Detail path parsed from live served HTML | Optional bounded live download smoke |
| Write previews | Inferred and non-sendable | Intercepted bodies |
| FastAPI `/v1` | Yes; runtime OpenAPI exported | Credentialed session smoke |
| SQLite snapshot/diff | Yes; transactional and version-gated | Credentialed live sync |
| Manual sync CLI | Yes; dry-run/JSON/CSV/non-zero failures | Credentialed live sync |
| Material ingestion | Yes; file/directory/API, SHA dedup, MIME review | Real project sample acceptance |
| Vulnerability report | Draft from evidence | Second role, open redirect proof |
| Clean acceptance | Automated parts | Credentialed smoke |

Do **not** describe the project as fully production-finished against live iPSA
until the credentialed pure-HTTP smoke passes and P3 persistence/diff recovery is
complete.
