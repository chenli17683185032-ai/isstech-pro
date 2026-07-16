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
| SQLite snapshots + change events + manual sync CLI | Yes; account-visible, per-stream transactional checkpoints |
| Weekday scheduled sync facility | Yes; Keychain, bounded wrapper, reversible LaunchAgent installer |
| Local material ingestion | Yes; streaming SHA-256, atomic originals, MIME review gate, deduplication |
| Document parsing + field extraction | Yes; PDF/Office/text, exact source evidence, confidence/review gates |
| Human review + local draft state | Yes; version locks, immutable AI proposal, append-only audit, ready gate |
| Local Web workspace | Yes; login, overview, materials, evidence review, ready, sync, and follow-up views |
| Automated tests | Run `uv run pytest -q`; exact count is recorded in final verification |

## Safety boundary

- Browser / CDP / Playwright / mitmproxy are **analysis-only**, not runtime deps.
- Live verification is read-only: page loads, filters, details, attachment downloads.
- Create / edit / delete / submit / approve / adjust / revoke / upload are **not**
  sent to the target. Use intercept+abort for capture; use `/v1/previews/*` locally.
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

The root URL is the operational workspace, not a landing page. Its four views
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
contains no iPSA create, save, submit, approve, delete, or upload action. Manual
and scheduled workflow synchronization use only the five explicitly policy-gated
procurement `SearchIndex` read paths.

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
ProcurementOrder, CostConfirmation, and CheckAcceptance SearchIndex streams.
Portal identity adds relation labels when it matches a trustworthy applicant field;
it is not a record-discard gate. Each stream owns an independent checkpoint, so a
declared-total mismatch, repeated/short page, schema drift, stale measurement, or
local transaction error preserves that stream's previous complete current state.

### Weekday scheduled sync

The committed default is Monday-Friday at 08:30 local time. The facility is not
activated until the account holder configures Keychain and runs the installer.
Neither tool accepts a password command-line argument.

```bash
cd /Users/ethan/Documents/isstech

# /usr/bin/security presents two secure prompts: iPSA username, then password
.venv/bin/python tools/configure_sync_keychain.py

# verify values without printing them
.venv/bin/python tools/configure_sync_keychain.py --verify-only

# inspect a valid rendered plist without writing/loading it
.venv/bin/python tools/install_launch_agent.py --dry-run | plutil -lint -

# atomically install and bootstrap the default weekday 08:30 agent
.venv/bin/python tools/install_launch_agent.py

# inspect loaded schedule and private outcome log
launchctl print gui/$(id -u)/com.isstech.workflow-center.sync
tail -n 20 data/logs/scheduled-sync.log
```

Use `--hour H --minute M` on the installer to choose another local time. The
installed plist is mode `0600` under `~/Library/LaunchAgents/`; it contains no
username, password, Cookie, ticket, or API key. Scheduled execution calls the
same `tools/sync_work_items.py --json --csv` path as manual sync, with a 10-second
Keychain timeout and 15-minute sync timeout.

The wrapper captures detailed CLI output in memory and appends only timestamp,
run ID, status, counts, exit code, and a redacted error to
`data/logs/scheduled-sync.log` (mode `0600`). Full run summaries, SQLite state,
and CSV output remain under the configured account's private
`data/accounts/<sha256-account-scope>/` path.

```bash
# stop future runs and remove the installed plist; local data and backup remain
.venv/bin/python tools/install_launch_agent.py --uninstall

# optionally remove only the two scheduled-sync Keychain items
.venv/bin/python tools/configure_sync_keychain.py --delete
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
  request_builders.py session_store.py storage.py sync.py materials.py extraction.py
  field_mapping.py workflow_state.py schema.sql migration_002_materials.sql
  migration_003_extraction.sql migration_004_review.sql
  ai/ models/ parsers/ routes/ web_dist/
web/                   # React/Vite source; build-time only
tests/                 # unit + API tests (redacted fixtures only)
captures/raw/          # gitignored originals
captures/redacted/     # commit-safe fixtures
docs/                  # architecture, matrix, vulns, verification, openapi path list
tools/first-commit.sh  # baseline commit helper if .git is locked in a sandbox
tools/sync_work_items.py # manual/daily sync entry; credentials from env only
tools/scheduled_sync.py # Keychain-backed bounded LaunchAgent entrypoint
tools/install_launch_agent.py # render/install/rollback/uninstall LaunchAgent
tools/configure_sync_keychain.py # secure interactive credential provisioning
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
