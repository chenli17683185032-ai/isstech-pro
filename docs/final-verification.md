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

The current P9.14 automated gate has 432 passing tests, clean Ruff, deterministic
OpenAPI, secret/evidence verification, a 1,598-module React production build, an
85-file wheel, two valid committed/rendered plists, schema v8-to-v9 migration, and
`git diff --check`. The production API retains the P9.13 four non-empty Fee Management
categories/85 records and six homepage groups/21 unapproved records, then adds an
account-scoped assistant briefing and versioned preferences without changing any
upstream policy. A real local-only run upgraded the account database to v9 with
integrity `ok` and generated five fallback priorities from 21 candidates without a
model or iPSA request. The bundle still contains seven exact IPSA browser handoffs,
`noopener noreferrer`, and no local submission API.

Browser QA used synthetic local data only. At 1440x900 the right column order was
draft review, assistant, sync history; the assistant occupied y=318..744 with no
horizontal overflow. At 390x844 it was 366px wide in a 390px page, priority feedback
updated the reason and current-preference state, and page scrollWidth equaled
clientWidth. Desktop and mobile console warning/error counts were both zero. The
ignored screenshots are `outputs/p914-assistant-desktop-qa.png` and
`outputs/p914-assistant-mobile-qa.png`.

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
and all six Payment/BizCase/Fee Management streams are
`succeeded`; every stream has complete declared/observed parity; all files report
mode `600`; a second unchanged sync adds history but zero procurement events and
zero readonly changes.

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
-> daily local/model-optional priority briefing with user preference feedback
-> Payment/BizCase/Fee Management rows open account-scoped local snapshot details
-> top-bar launch catalog hands seven choices to IPSA in isolated browser tabs
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

P9.12 Browser QA verified exactly three peer systems: Payment, BizCase, and Fee
Management. Fee Management exposes Daily Expense and Travel as child tabs with
counts 1 and 54. Clicking the daily-expense row or eye control, or pressing Enter
or Space, opened the local snapshot drawer; closing restored focus to the row.
CDP observed no request for any of these detail interactions. Both desktop and
mobile had zero page-level horizontal overflow and no console warning/error.

P9.13 production API verification returns 10/1/54/1/2/28 personal records from the
six local read-only APIs. Fee Management derives four visible non-empty child tabs
and an 85-record total. The overview combines those already-loaded local results
with procurement current, excludes only the proven approved statuses and blank
statuses, and produces 21 unapproved rows in six business groups. No normal page
load or local detail path starts an upstream list pagination cycle. The rebuilt
1,596-module bundle is JS `271.75 kB` and CSS `37.84 kB`; `tests/test_ui.py` verifies
all seven destinations, visible launch copy, isolation attributes, and FastAPI's new
hashed asset responses. The launcher uses a native modal dialog, fixed-size grid
tracks, a `100dvh` ceiling, keyboard links, Escape close, and focus restoration.
The P9.13 launcher-only screenshot residual was closed during P9.14 using a local
same-origin QA service and synthetic data. Desktop 1440x900 and mobile 390x844 both
rendered without page overflow or console warning/error; no IPSA link was clicked.

The built root, hashed JS, and hashed CSS must return 200 from FastAPI. Common
icon buttons retain accessible names on mobile, the material button label remains
white on the primary background, and clipboard copy returns success or a bounded
failure within three seconds.

The in-app Browser cannot set local files. The local multipart upload route was
therefore exercised by API and automated tests before continuing the UI flow.
Chrome's native picker could select the QA file, but extension-driven upload
requires "Allow access to file URLs" and that browser permission was not enabled.
This is a browser-automation gate; no live iPSA upload endpoint was used.

## Daily sync, briefing, and Web service (offline)

```bash
uv run pytest -q \
  tests/test_assistant.py \
  tests/test_runtime_deployment.py \
  tests/test_scheduled_sync.py \
  tests/test_web_launch_agent.py
plutil -lint \
  ops/com.isstech.workflow-center.sync.plist \
  ops/com.isstech.workflow-center.web.plist
tmp_plist="$(mktemp)"
uv run python tools/install_launch_agent.py --dry-run > "$tmp_plist"
plutil -lint "$tmp_plist"
if plutil -p "$tmp_plist" | rg -i 'password|cookie|ticket|\.ipsa|api.key'; then
  exit 1
fi
rm -f "$tmp_plist"
tmp_web_plist="$(mktemp)"
uv run python tools/install_web_launch_agent.py --dry-run > "$tmp_web_plist"
plutil -lint "$tmp_web_plist"
rm -f "$tmp_web_plist"
```

Pass criteria: focused tests prove the existing manual CLI path is invoked;
sync/setup/briefing/model failures cannot suppress the final page-open stage;
private logs omit credentials and work-item content; model output closes over the
input key set; failed lint/bootstrap/health restores the previous plist/service;
both plists are valid and contain no credential-like values. Runtime deployment
copies only allowlisted source/tool files, installs from `uv.lock`, excludes SQLite
WAL/SHM/journal sidecars, and validates every seeded database before activation.

After the account holder configures Keychain, the live activation gate is:

```bash
uv run python tools/configure_sync_keychain.py --verify-only
uv run python tools/install_web_launch_agent.py
uv run python tools/install_launch_agent.py
launchctl print gui/$(id -u)/com.isstech.workflow-center.web
launchctl print gui/$(id -u)/com.isstech.workflow-center.sync
stat -f '%Lp %N' \
  "$HOME/Library/LaunchAgents/com.isstech.workflow-center.web.plist" \
  "$HOME/Library/LaunchAgents/com.isstech.workflow-center.sync.plist" \
  "$HOME/Library/Application Support/com.isstech.workflow-center" \
  "$HOME/Library/Application Support/com.isstech.workflow-center/data"
plutil -extract WorkingDirectory raw -o - \
  "$HOME/Library/LaunchAgents/com.isstech.workflow-center.web.plist"
plutil -extract EnvironmentVariables.ISSTECH_DATA_DIR raw -o - \
  "$HOME/Library/LaunchAgents/com.isstech.workflow-center.web.plist"
```

Expected: the Web service is healthy and self-recovering; the daily job is loaded
with seven 08:30 intervals; installed plists are mode `600` and credential-free.
A target `state = running` and positive PID must be present; a 200 response from a
different process on port 8000 is not sufficient. Both plist paths must resolve below
the mode `700` Application Support root, never the protected repository in `Documents`.
A scheduled run writes eleven stream results when upstream is available, then one
assistant briefing and safe per-stage log records before opening the workspace.
The raw username must not appear in a scoped path or log. A chat provider is optional;
without it the stored source is `fallback`.

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
| Payment, BizCase, and Fee Management lists | Yes; six independent checkpoints, personal scope, dynamic non-empty fee categories, and local snapshot details | Upstream edit-capable details remain disabled |
| Approval/adjustment/revocation views | Initial GET captured; exact read paths enabled | Non-empty role fixtures remain unavailable |
| Attachment path | Real Detail path parsed from live served HTML | Optional bounded live download smoke |
| Write previews | Inferred and non-sendable | Intercepted bodies |
| FastAPI `/v1` + root workspace | Yes; runtime OpenAPI, hashed SPA, all-unapproved overview, right-column assistant, and seven-item IPSA handoff are served | Local submit API remains absent |
| SQLite snapshot/diff | Yes; five procurement plus six read-only checkpoints, assistant preferences/briefings, schema v9 | — |
| Manual sync CLI | Yes; eleven-stream single-login dry-run/JSON/CSV/non-zero failures and live run | — |
| Material ingestion | Yes; file/directory/API, SHA dedup, MIME review | Real project sample acceptance |
| Document parsing and AI extraction | Yes; PDF/Office/text, strict evidence gates, API/CLI | OCR for image-only real samples |
| Human review, draft state, and local UI | Yes; version lock, corrected evidence, audit, ready, responsive workspace | Automated upstream submission remains blocked |
| Daily sync and assistant | Seven-day 08:30 sync/brief/open wrapper, fallback model boundary, and persistent Web LaunchAgent verified | Observe the next natural launchd trigger |
| Vulnerability report | Draft from evidence | Second role, open redirect proof |
| Clean acceptance | Automated gates plus credentialed read-only smoke | Write-side P7 excluded |

Do **not** describe the project as an automated live-iPSA submitter. The local client
still blocks P7 upstream execution. The new launch catalog only transfers the user to
the original IPSA application UI, and the four added workflows intentionally expose
cached list fields rather than unproven upstream Detail routes.
