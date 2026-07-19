# iSStech Unified Workflow Center

Local-first AI-assisted material review, workflow status, and follow-up center,
backed by a **read-only-first** HTTP facade for the authorized CTF target:

- Business: `http://ipsapro.isstech.com/WebTP/PurchaseRequisition`
- Passport: `https://passport.isstech.com/`
- Local workspace: `http://127.0.0.1:8000/`
- Local API: `http://127.0.0.1:8000` (`/docs`, `/openapi.json`)

## What works today

| Capability | Status |
| --- | --- |
| Evidence inventory + endpoint matrix | Yes (`docs/`) |
| Endpoint policy (deny-by-default, Delete-as-GET = write) | Yes |
| Browser login protocol | Captured and reproducibly redacted; browser already carried `.iPSA` |
| Pure HTTP login | Mock-verified; clean-process live smoke still needs runtime credentials |
| Five procurement SearchIndex streams + PR Detail + bounded attachment download | Implemented; live schema/count parity checked without printing values |
| Approval / adjustment / revocation | Initial GET captured; exact read-only paths enabled |
| Write request **previews** (never sent) | Inferred builders; intercepted bodies pending |
| FastAPI `/v1` sessions, lists, attachments, previews, work items | Yes; five-stream incomplete pagination fails closed per stream |
| Payment + BizCase + Fee Management read-only queries | Yes; six independent checkpoints, personal scope, cached lists, manual/UI/scheduled sync |
| SQLite snapshots + change events + manual sync CLI | Yes; account-visible, per-stream transactional checkpoints |
| Daily sync + follow-up briefing | Yes; seven-day schedule, Keychain, deterministic fallback, bounded model rerank, automatic page open |
| Local material ingestion | Yes; streaming SHA-256, atomic originals, MIME review gate, deduplication |
| Document parsing + field extraction | Yes; PDF/Office/text, exact source evidence, confidence/review gates |
| Human review + local draft state | Yes; version locks, immutable AI proposal, append-only audit, ready gate |
| Local Web workspace | Yes; all unapproved workflows, right-column daily assistant, business queries, and seven-item IPSA launch catalog |
| Automated tests | Run `uv run pytest -q`; exact count is recorded in final verification |

## Safety boundary

- Browser / CDP / Playwright / mitmproxy are **analysis-only**, not runtime deps.
- Live verification is read-only: page loads, filters, details, attachment downloads.
- Create / edit / delete / submit / approve / adjust / revoke / upload are **not**
  sent by the local service or automated verification. The launch catalog only hands
  the browser to IPSA; form actions remain user-controlled in the original system.
- Passwords, cookie values, `.iPSA`, employee names, project numbers, and attachment
  bodies must not enter git, test logs, or committed OpenAPI examples.
- Raw captures: `captures/raw/**` (mode `0600`, gitignored). Redacted: `captures/redacted/`.

See `docs/scope.md`, `docs/architecture.md`, `docs/endpoint-matrix.md`,
`docs/vulnerability-report.md`, and `docs/final-verification.md`.

## Quick start

```bash
cd /Users/ethan/Documents/isstech
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests
uv run isstech-api
# Open http://127.0.0.1:8000/
```

### Local Web workspace

The root URL is the operational workspace, not a landing page. Its five views
cover the minimum closed loop:

```text
local material -> local_rules extraction -> evidence review
-> validated -> ready -> read-only status sync -> follow-up list
```

The browser stores only the short-lived local Bearer handle in same-origin
`localStorage`; it never receives the upstream `.iPSA` value. Refresh restores
materials, extraction runs, drafts, current account-visible snapshots, and sync runs
from SQLite. Stale draft writes return `409 CONFLICT`, after which the UI reloads
the newer version instead of overwriting it.

`web/` contains the React/Vite source. `src/isstech_replay/web_dist/` is the
checked-in production build served by FastAPI and included in the wheel, so
Node is not required at runtime. Rebuild only after frontend changes:

```bash
cd /Users/ethan/Documents/isstech/web
npm ci
npm run build
```

The material picker uploads only to the local `/v1/materials` endpoint. The UI
contains no local API that creates, saves, submits, approves, deletes, or uploads
iPSA business data. Its top-bar launcher contains seven fixed `ipsapro.isstech.com`
browser handoffs for Purchase Requisition, Payment, BizCase, and four Fee Management
flows. Purchase and Payment open their proven first step; BizCase and Fee Management
open the original application page so its stateful controls create the actual form.
Existing work-item and scheduled synchronization use only the five explicitly policy-gated
procurement `SearchIndex` read paths, the exact body-gated Payment personal query,
the exact BizCase pager, the identity-bound travel pager, the exact GET-only
daily-expense and travel-reimbursement lists, and the body-validated travel-subsidy
pager. All six read-only modules keep independent checkpoints and do not enter
`/v1/work-items/current`; their list APIs separately fail closed to the same
personal-scope contract. The workspace presents Payment, BizCase, and Fee Management
as peers. Fee Management dynamically shows only non-empty personal categories and
the overview groups every non-approved local workflow by business category.
The overview's right column also reads the latest account-scoped assistant briefing
from SQLite. It shows at most five priorities with explicit date-estimate labels,
accepts a short local priority preference, and never calls iPSA or a model during
ordinary page loading.

### Durable manual sync

Credentials are read from the current process only. They are never accepted as
CLI arguments or written to SQLite/run summaries.

```bash
cd /Users/ethan/Documents/isstech
export ISSTECH_USERNAME='...'
export ISSTECH_PASSWORD='...'

# Prove fetch + normalization without creating data files
uv run python tools/sync_work_items.py --dry-run --json

# Persist account-visible snapshots, print JSON, and export the current list
uv run python tools/sync_work_items.py --json --csv

unset ISSTECH_PASSWORD ISSTECH_USERNAME
```

Runtime outputs:

```text
data/accounts/<sha256-account-scope>/workflow-center.sqlite3
data/accounts/<sha256-account-scope>/runs/<run-id>/summary.json
data/accounts/<sha256-account-scope>/exports/YYYY-MM-DD-work-items.csv
```

`data/` is gitignored. SQLite, summary, and CSV files are created with mode
`0600`. The account scope is a normalized SHA-256 key; the raw username is not
used in paths. Legacy `data/workflow-center.sqlite3` data is retained for audit
but is not shown as any logged-in account's current work-item state. The sync reads
the complete account-visible PurchaseRequisition, ProcurementContract,
ProcurementOrder, CostConfirmation, and CheckAcceptance SearchIndex streams, then
uses the same authenticated client for Payment personal queries and the BizCase
source checkpoint, followed by the four identity-bound Fee Management lists.
Portal identity adds relation labels when it matches a trustworthy applicant field;
it is not a record-discard gate. Each stream owns an independent checkpoint, so a
declared-total mismatch, repeated/short page, schema drift, stale measurement, or
local transaction error preserves that stream's previous complete current state.

The first LaunchAgent installation copies this repository data into the private
`~/Library/Application Support/com.isstech.workflow-center/data/` runtime root using
SQLite's online backup API. After activation, the Web service and daily job share that
runtime copy. Repository `data/` remains the pre-deployment source/backup; it is not
silently overwritten. Code runs from a content-addressed immutable release under the
same Application Support root because macOS blocks background LaunchAgents from
reading Python environments inside protected `Documents` folders.

### Daily sync and follow-up briefing

The committed default is every day at 08:30 local time. The job runs the
eleven-stream sync, generates a briefing from the newest complete local snapshots,
and opens `http://127.0.0.1:8000/`. Sync, briefing, and page open are independent
bounded stages: a sync failure still briefs from the prior snapshot, and any earlier
failure still reaches the final open stage.

```bash
cd /Users/ethan/Documents/isstech

# /usr/bin/security presents two secure prompts: iPSA username, then password
.venv/bin/python tools/configure_sync_keychain.py

# verify values without printing them
.venv/bin/python tools/configure_sync_keychain.py --verify-only

# inspect a valid rendered plist without writing/loading it
.venv/bin/python tools/install_launch_agent.py --dry-run | plutil -lint -

# install the persistent loopback Web service and daily 08:30 job
.venv/bin/python tools/install_web_launch_agent.py
.venv/bin/python tools/install_launch_agent.py

# inspect both loaded services and the private outcome log
launchctl print gui/$(id -u)/com.isstech.workflow-center.web
launchctl print gui/$(id -u)/com.isstech.workflow-center.sync
tail -n 20 "$HOME/Library/Application Support/com.isstech.workflow-center/data/logs/scheduled-sync.log"
```

Use `--hour H --minute M` on the installer to choose another local time. The
installed plist is mode `0600` under `~/Library/LaunchAgents/`; it contains no
username, password, Cookie, ticket, or API key. Scheduled execution calls the same
eleven-stream `tools/sync_work_items.py --json --csv` path as manual sync, with one
login, a 10-second Keychain timeout, and a 15-minute sync timeout. The briefing child
has a 75-second ceiling and emits only source/count status before the wrapper opens
the local page with a 10-second command timeout.

Both installers derive the same release hash, install a non-editable environment from
`uv.lock`, and run an import smoke before changing launchd state. Installed plists
reference only the immutable release and Application Support data root. Re-running an
installer reuses an already validated release; a failed plist/bootstrap/PID/health
gate restores the prior loaded plist and its prior immutable release.

The wrapper captures detailed CLI output in memory and appends only timestamp,
run ID, status, counts, exit code, and a redacted error to
`~/Library/Application Support/com.isstech.workflow-center/data/logs/scheduled-sync.log`
(mode `0600`). Full run summaries, SQLite state, and CSV output remain under the
runtime root's private `accounts/<sha256-account-scope>/` path.

The model rerank is optional. Without configuration the same daily run writes a
deterministic `fallback` briefing. To enable a chat-capable OpenAI-compatible
Chat Completions endpoint, store its full endpoint, model, and API key through
three secure Keychain prompts:

```bash
.venv/bin/python tools/configure_assistant_keychain.py
.venv/bin/python tools/configure_assistant_keychain.py --verify-only
```

Only the current unapproved item key, category, reference, title, project, status,
approver, date, and estimated wait are sent. Cookies, credentials, raw payloads,
attachments, applicants, and approval comments are excluded. Non-loopback HTTP,
iPSA/Passport hosts, image endpoints, and `gpt-image-2` are rejected. Provider
timeouts, HTTP failures, invalid JSON, unknown keys, and duplicate keys all return
to the deterministic local order.

```bash
# stop future runs and remove the installed plist; runtime data/releases remain
.venv/bin/python tools/install_launch_agent.py --uninstall
.venv/bin/python tools/install_web_launch_agent.py --uninstall

# optionally remove only the related Keychain items
.venv/bin/python tools/configure_sync_keychain.py --delete
.venv/bin/python tools/configure_assistant_keychain.py --delete
```

### Local material ingestion

This path is offline and does not require iPSA credentials:

```bash
# one or more files
uv run python tools/ingest_materials.py /path/to/file.pdf --json

# all files below an incoming directory
uv run python tools/ingest_materials.py /path/to/incoming --recursive --json
```

Original bytes are stored once at
`data/materials/originals/<sha256>/blob` with mode `0400`. Original filenames,
declared/detected MIME, review state, and references live in SQLite. Parsed and
AI-derived output goes under `data/materials/derived/<material-id>/`; it never
overwrites the original blob.

### Document parsing and evidence-backed extraction

This path is also offline with the default deterministic provider. Use the
material ID returned by ingestion:

```bash
uv run python tools/extract_material.py MATERIAL_ID --json
```

Supported first-pass parsers are PDF text layers, DOCX, XLSX, PPTX, UTF-8 text,
and JSON. Each proposed field records the material ID, source kind, source
index, label, and exact source excerpt. Missing required fields, confidence
below `0.85`, invalid source references, truncated documents, MIME review, and
PDFs without a text layer remain `needs_review`.

The optional `http_json` provider is enabled only through runtime environment
configuration:

```bash
export ISSTECH_AI_ENDPOINT='https://...'
export ISSTECH_AI_MODEL='...'
export ISSTECH_AI_API_KEY='...'
uv run python tools/extract_material.py MATERIAL_ID --provider http_json --json
unset ISSTECH_AI_API_KEY ISSTECH_AI_MODEL ISSTECH_AI_ENDPOINT
```

Plain HTTP is accepted only on loopback. The iPSA and Passport hosts are always
rejected as AI endpoints. Provider output is size-bounded and treated as
untrusted; no provider has access to an adapter submit method.

### Human review and local draft state

One extraction has at most one local workflow draft. AI proposal values and
evidence remain read-only; reviewers set a separate decision, confirmed value,
and optional corrected evidence. Every mutation requires the draft's current
`expected_version`, records the session username, and appends an audit event.

The only P6 forward path is:

```text
extracted | needs_review -> validated -> ready
```

`ready` means the local required/evidence/review gates passed. It does **not**
send, upload, save, or submit anything to iPSA. A stale version returns
`409 CONFLICT` instead of overwriting another review.

### Local API (examples)

```bash
# health
curl -s http://127.0.0.1:8000/health

# login — credentials only at runtime; response is a local Bearer token
curl -s http://127.0.0.1:8000/v1/sessions \
  -H 'Content-Type: application/json' \
  -d '{"username":"USER","password":"PASS"}'

# list (application view)
curl -s 'http://127.0.0.1:8000/v1/purchase-requisitions?view=application' \
  -H "Authorization: Bearer $TOKEN"

# account-visible five-workflow list (read-only, no local persistence)
curl -s 'http://127.0.0.1:8000/v1/work-items' \
  -H "Authorization: Bearer $TOKEN"

# full five-stream read + per-stream transactional local checkpoints
curl -s -X POST 'http://127.0.0.1:8000/v1/sync/work-items?max_pages=20' \
  -H "Authorization: Bearer $TOKEN"

# latest local assistant briefing; this GET never calls iPSA or a model
curl -s http://127.0.0.1:8000/v1/assistant/brief \
  -H "Authorization: Bearer $TOKEN"

# store one priority preference and immediately regenerate the local briefing
curl -s -X POST http://127.0.0.1:8000/v1/assistant/preferences \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"text":"付款申请优先"}'

# local multipart upload; uses the local Bearer session, no upstream write
curl -s -X POST http://127.0.0.1:8000/v1/materials \
  -H "Authorization: Bearer $TOKEN" \
  -F 'file=@/path/to/file.pdf'

# parse locally and propose purchase-requisition fields with source evidence
curl -s -X POST http://127.0.0.1:8000/v1/materials/MATERIAL_ID/extractions \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"provider":"local_rules","confidence_threshold":0.85}'

# read the immutable proposal/audit record
curl -s http://127.0.0.1:8000/v1/extractions/EXTRACTION_ID \
  -H "Authorization: Bearer $TOKEN"

# idempotently create the one local draft for an extraction
curl -s -X POST http://127.0.0.1:8000/v1/extractions/EXTRACTION_ID/drafts \
  -H "Authorization: Bearer $TOKEN"

# confirm one field; VERSION must be the latest draft version
curl -s -X PUT http://127.0.0.1:8000/v1/drafts/DRAFT_ID/fields/PR_PrjNo \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"decision":"confirmed","confirmed_value":"PRJ-001","expected_version":VERSION}'

# after every proposed/required field has a human decision
curl -s -X POST http://127.0.0.1:8000/v1/drafts/DRAFT_ID/validate \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"expected_version":VERSION}'

# only a validated draft can become locally ready
curl -s -X POST http://127.0.0.1:8000/v1/drafts/DRAFT_ID/ready \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"expected_version":VERSION}'

# delete is preview-only
curl -s -X POST http://127.0.0.1:8000/v1/previews/purchase-requisitions/ID/delete \
  -H "Authorization: Bearer $TOKEN"
```

Error codes: `AUTH_EXPIRED`, `UPSTREAM_ERROR`, `PARSE_ERROR`, `WRITE_BLOCKED`,
`BAD_REQUEST`, `NOT_FOUND`, `CONFLICT`, `PAYLOAD_TOO_LARGE`, `LOCAL_STORAGE_ERROR`,
`NOT_CAPTURED`.

### Layout

```text
src/isstech_replay/
  api.py policy.py transport.py client.py auth.py
  request_builders.py session_store.py storage.py sync.py assistant.py materials.py extraction.py
  runtime_deployment.py # immutable Application Support runtime and SQLite seed
  field_mapping.py workflow_state.py schema.sql migration_002_materials.sql
  migration_003_extraction.sql migration_004_review.sql migration_009_assistant.sql
  ai/ models/ parsers/ routes/ web_dist/
web/                   # React/Vite source; build-time only
tests/                 # unit + API tests (redacted fixtures only)
captures/raw/          # gitignored originals
captures/redacted/     # commit-safe fixtures
docs/                  # architecture, matrix, vulns, verification, openapi path list
tools/first-commit.sh  # baseline commit helper if .git is locked in a sandbox
tools/sync_work_items.py # manual/daily sync entry; credentials from env only
tools/scheduled_sync.py # bounded daily sync/brief/open LaunchAgent entrypoint
tools/generate_daily_brief.py # account-scoped fallback/model briefing CLI
tools/install_launch_agent.py # render/install/rollback/uninstall LaunchAgent
tools/install_web_launch_agent.py # persistent local Web service LaunchAgent
tools/configure_sync_keychain.py # secure interactive credential provisioning
tools/configure_assistant_keychain.py # secure optional chat provider provisioning
tools/ingest_materials.py # offline file/directory material ingestion
tools/extract_material.py # offline parse + evidence-backed field extraction
data/                  # ignored SQLite, run summaries, and CSV exports
```

## Delivery order (from the handoff brief)

Evidence baseline → success login capture → pure HTTP login → safety policy →
endpoint matrix → read-only client → write previews → FastAPI → vulnerability
report → clean-environment acceptance.

**Remaining evidence-dependent steps:** (1) credentialed clean-process pure-HTTP
smoke; (2) request-stage intercepted-and-aborted write bodies; (3) optional
second-role read-only IDOR comparison. Browser login and the four additional
list-view GETs are already captured and inventoried.

## License / authorization

Use only against the authorized CTF / lab target described in the project brief.
Do not point this client at production systems without explicit permission.
