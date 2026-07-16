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

The current P9.11 automated gate has 371 passing tests, clean Ruff, deterministic
OpenAPI, secret/evidence verification, a 1,595-module React production build, a
73-file wheel, plist lint, schema v5-to-v6 migration, and `git diff --check`.
Credentialed acceptance left Payment at 10 personal records, BizCase at 55 source
records and one locally asserted personal record, and returned 54 identity-bound
travel applications with zero changes on the second travel-only synchronization.
In-app Browser visual/network QA remains pending because its WebView exposed no
attachable tab after two bounded retries; do not treat that browser gate as passed.

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

find data/accounts -name workflow-center.sqlite3 -type f
sqlite3 data/accounts/<account-scope>/workflow-center.sqlite3 \
  'select adapter,status,source_total_count,observed_count from sync_runs order by started_at desc limit 5;'
stat -f '%Lp %N' \
  data/workflow-center.sqlite3 \
  data/runs/*/summary.json \
  data/exports/*-work-items.csv

unset ISSTECH_PASSWORD ISSTECH_USERNAME
```

Pass criteria: dry-run leaves no new DB/run file; all five procurement streams
and all three Payment/BizCase/travel-application streams are `succeeded`; every stream has complete
declared/observed parity; all files report mode `600`; a second unchanged sync
adds history but zero procurement events and zero readonly changes.

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

## Document extraction smoke (offline)

```bash
tmp_dir="$(mktemp -d)"
printf '项目编号：PRJ-001\n项目名称：REDACTED PROJECT\n采购方式：公开询价\n' \
  > "$tmp_dir/project.txt"
ingest_json="$(uv run python tools/ingest_materials.py "$tmp_dir/project.txt" \
  --data-dir "$tmp_dir/data" --json)"
material_id="$(printf '%s' "$ingest_json" | jq -r '.materials[0].material.id')"
uv run python tools/extract_material.py "$material_id" \
  --data-dir "$tmp_dir/data" --json
sqlite3 "$tmp_dir/data/workflow-center.sqlite3" \
  "select status, can_advance, field_count, issue_count from extraction_runs;"
sqlite3 "$tmp_dir/data/workflow-center.sqlite3" \
  "select field_name, evidence_valid, review_status from extracted_fields order by field_id;"
stat -f '%Lp %N' "$tmp_dir"/data/materials/derived/*/extractions/*/result.json
rm -rf "$tmp_dir"
```

Pass criteria: extraction is `succeeded|1|3|0`; every field has
`evidence_valid=1` and `review_status=pending`; result JSON mode is `600`; no
upstream credential or browser session is required.

## Human review/state smoke (offline)

```bash
uv run pytest -q tests/test_workflow_state.py
sqlite3 data/workflow-center.sqlite3 \
  'select draft_id, state, version, validated_at, ready_at from workflow_drafts;'
sqlite3 data/workflow-center.sqlite3 \
  'select draft_id, field_name, review_decision, reviewed_by from draft_fields;'
sqlite3 data/workflow-center.sqlite3 \
  'select draft_id, sequence, event_type, actor from draft_audit_events order by draft_id, sequence;'
```

Pass criteria: the focused suite passes; a complete reviewed draft advances only
through `validated` to `ready`; event sequence is contiguous with draft version;
the AI proposal/source columns remain unchanged; stale versions and direct ready
attempts return conflicts. These checks are local and send no iPSA request.

## Local Web workspace (offline/mocked upstream)

```bash
cd /Users/ethan/Documents/isstech/web
npm ci
npm run build
cd ..

.venv/bin/pytest -q tests/test_ui.py tests/test_api.py tests/test_storage.py
.venv/bin/uvicorn isstech_replay.api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. The verified local-only browser path was:

```text
mock login -> material in local store -> local_rules extraction -> draft
-> three required evidence-backed confirmations -> validated -> ready
-> mock Portal identity + five read-only SearchIndex streams
-> complete account-visible source records in adapter-scoped SQLite checkpoints
-> personal-project/submission scope derived by the local API
-> Payment/BizCase/travel rows open account-scoped local snapshot details
-> refresh recovery -> stale-version 409 refresh without overwrite
```

P9.6 Browser QA used `1280x720` and `390x844` against 353 persisted source rows
and the 32-row personal view. Scope filtering produced 32 project rows and 11
submitted rows; status filtering produced 5 pending rows, and contract filtering
produced 8 rows. Non-empty and upstream-empty approval trails both rendered with
accurate states. There was no page-level horizontal overflow, blank primary
view, overlap, clipped control, framework overlay, or console warning/error.
Wide tables scroll only inside their container on mobile, and the detail drawer
stays within the viewport.

The built root, hashed JS, and hashed CSS must return 200 from FastAPI. Common
icon buttons retain accessible names on mobile, the material button label remains
white on the primary background, and clipboard copy returns success or a bounded
failure within three seconds.

The in-app Browser cannot set local files. The local multipart upload route was
therefore exercised by API and automated tests before continuing the UI flow.
Chrome's native picker could select the QA file, but extension-driven upload
requires "Allow access to file URLs" and that browser permission was not enabled.
This is a browser-automation gate; no live iPSA upload endpoint was used.

## Scheduled sync facility (offline)

```bash
uv run pytest -q tests/test_scheduled_sync.py
plutil -lint ops/com.isstech.workflow-center.sync.plist
tmp_plist="$(mktemp)"
uv run python tools/install_launch_agent.py --dry-run > "$tmp_plist"
plutil -lint "$tmp_plist"
if plutil -p "$tmp_plist" | rg -i 'password|cookie|ticket|\.ipsa|api.key'; then
  exit 1
fi
rm -f "$tmp_plist"
```

Pass criteria: focused tests prove the existing manual CLI path is invoked;
Keychain and sync timeouts exit non-zero; private logs omit credentials/work-item
content; failed lint/bootstrap restores the previous plist/service; both plists
are valid and contain no credential-like values.

After the account holder configures Keychain, the live activation gate is:

```bash
uv run python tools/configure_sync_keychain.py --verify-only
uv run python tools/install_launch_agent.py
launchctl print gui/$(id -u)/com.isstech.workflow-center.sync
stat -f '%Lp %N' \
  "$HOME/Library/LaunchAgents/com.isstech.workflow-center.sync.plist"
```

Expected: agent is loaded with five weekday intervals, installed plist mode is
`600`, no credential appears in the plist, and a later scheduled run writes eight
successful stream results under `data/accounts/<sha256-account-scope>/` plus one
safe `scheduled-sync.log` line while the FastAPI app is closed. The raw username
must not appear in the scoped path or log. Do not activate before Keychain is configured.

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
| Browser login protocol | Yes, redacted CDP is reproducible | Historical browser capture already carried `.iPSA` |
| Pure HTTP login code | Yes, mocked and credentialed live reads | Credentials remain runtime-only |
| Five SearchIndex streams and PR Detail | Yes, with live 353/353 schema/count parity | Non-PR upstream Detail remains disabled |
| Payment, BizCase, and travel lists | Yes; independent checkpoints, personal scope, and local snapshot details | Upstream edit-capable details remain disabled |
| Approval/adjustment/revocation views | Initial GET captured; exact read paths enabled | Non-empty role fixtures remain unavailable |
| Attachment path | Real Detail path parsed from live served HTML | Optional bounded live download smoke |
| Write previews | Inferred and non-sendable | Intercepted bodies |
| FastAPI `/v1` + root workspace | Yes; runtime OpenAPI and hashed SPA are served | P7 write execution remains blocked |
| SQLite snapshot/diff | Yes; five procurement plus three read-only checkpoints, schema v6 | — |
| Manual sync CLI | Yes; eight-stream single-login dry-run/JSON/CSV/non-zero failures and live run | — |
| Material ingestion | Yes; file/directory/API, SHA dedup, MIME review | Real project sample acceptance |
| Document parsing and AI extraction | Yes; PDF/Office/text, strict evidence gates, API/CLI | OCR for image-only real samples |
| Human review, draft state, and local UI | Yes; version lock, corrected evidence, audit, ready, responsive workspace | P7 remains write-blocked |
| Weekday scheduled sync | Eight-stream wrapper verified and weekday 08:30 LaunchAgent loaded | Observe the next natural launchd trigger |
| Vulnerability report | Draft from evidence | Second role, open redirect proof |
| Clean acceptance | Automated gates plus credentialed read-only smoke | Write-side P7 excluded |

Do **not** describe the project as fully production-finished against live iPSA.
P7 upstream execution remains explicitly blocked by the current competition
rules, and the four added workflows intentionally expose cached list fields rather
than unproven upstream Detail routes.
