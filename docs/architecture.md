# Architecture

## Goal

Provide a local AI-assisted workflow center over the authorized iPSA CTF target.
Browser tooling is analysis-only; the final runtime is
a same-origin Web workspace plus direct HTTP through a single guarded transport.

## Target surfaces

| Surface | URL | Role |
| --- | --- | --- |
| Business entry | `http://ipsapro.isstech.com/WebTP/PurchaseRequisition` | Purchase requisition UI and AJAX endpoints |
| Portal | `http://ipsapro.isstech.com/Portal` | Portal / SSO entry |
| Passport | `https://passport.isstech.com/` | Credential POST, redirects, session cookies |
| Local workspace | `http://127.0.0.1:8000/` | All unapproved workflows, daily follow-up assistant, local review/sync, and IPSA launch catalog |
| Local API | `http://127.0.0.1:8000` | Stable REST facade (`/docs` for OpenAPI) |

## Component diagram

```text
Chrome/CDP (analysis only)
        |
        v
captures/raw  --redact-->  captures/redacted (fixtures)
                                |
                                v
React workspace --> FastAPI /v1 --> session store --> IsstechClient
     |                                   |
     +--> fixed browser handoff --> IPSA original application/form UI
     |        (no local token/data; user-controlled submit)
     |
     +--> material service --> immutable originals
     |          |
     |          v
     |     structured document --> extraction provider
     |                                  |
     |                                  v
     |                         evidence/threshold gate
     |                                  v
     |                         human review/state gate
     |                                  |
     v                                  v
sync service  --------------------> SQLite state/audit
     |                                  |
     |                                  v
     |                         daily follow-up assistant
     |                           /                 \
     |                    local stable rank   optional bounded chat rerank
     |                           \                 /
     |                            -> validated briefing -> React right column
     |
     +---------------------------> five complete SearchIndex streams
     +---------------------------> Payment personal queries + BizCase source
     +---------------------------> identity-bound Fee Management lists
                                         |
                                         v
                              account-visible records + relation labels
                                         |
                                         v
                                   EndpointPolicy
                                      /        \
                               read-only      write path
                                  |              |
                                  v              v
                           httpx Transport   RequestPreview
                                  |         (build only, never send)
                                  v
                         passport + iPSA target
```

## Runtime modules

| Module | Responsibility |
| --- | --- |
| `api.py` | FastAPI app assembly, `/health`, and root static workspace mount |
| `config.py` | Base URLs, timeouts, session TTL |
| `auth.py` | Pure HTTP login and auth detection |
| `client.py` | Upstream business client, exact read methods, and bounded pagination |
| `policy.py` | Method + host + path + side-effect policy |
| `transport.py` | Sole real network egress |
| `request_builders.py` | Offline construction of mutating requests |
| `session_store.py` | Short-lived local Bearer handles (never return `.iPSA`) |
| `account_scope.py` | Normalized SHA-256 account scope and isolated runtime paths |
| `sync.py` | Complete read, normalization, run lifecycle, and failure recording |
| `assistant.py` | Personal unapproved candidates, estimated wait, deterministic rank, preference feedback, persisted briefing |
| `storage.py` + `schema.sql` | Versioned SQLite snapshots, current state, and events |
| `materials.py` | Streaming hash, MIME gate, content-addressed originals, and deduplication |
| `extraction.py` | Bounded format parsers, immutable structured artifacts, extraction run lifecycle |
| `field_mapping.py` | Workflow field profiles plus required/evidence/confidence gates |
| `ai/base.py`, `ai/provider.py` | Extraction-only provider protocol, local rules, bounded HTTP JSON |
| `ai/briefing.py` | Tool-free Chat Completions rerank with request minimization, output whitelist, and fallback |
| `workflow_state.py` | Human review, exact-evidence revalidation, optimistic state transitions |
| `scheduler.py` | Independent bounded sync/brief/open stages, Keychain reads, private outcome log |
| `runtime_deployment.py` | Content-addressed Application Support release, locked install smoke, SQLite online seed |
| `web/` | React/Vite source for overview, launch catalog, materials, drafts, and follow-up views |
| `web_dist/` | Hashed production assets served by FastAPI and packaged in the wheel |
| `models/` | Auth, purchase, attachment, preview, and normalized work-item models |
| `parsers/` | Login / Portal identity / purchase / attachment HTML parsers |
| `routes/` | sessions, materials, extractions, drafts, purchase reads, previews, work items |
| `tools/sync_work_items.py` | Eleven-stream manual/LaunchAgent sync, combined JSON summary, CSV export |
| `tools/ingest_materials.py` | Offline file/directory inbox ingestion |
| `tools/extract_material.py` | Offline parsing and evidence-backed proposal extraction |
| `tools/scheduled_sync.py` | Daily LaunchAgent entrypoint; syncs, briefs, and opens the workspace |
| `tools/generate_daily_brief.py` | Account-scoped local/model briefing child with safe count-only stdout |
| `tools/install_launch_agent.py` | Atomic daily plist render/install/rollback against the immutable runtime |
| `tools/install_web_launch_agent.py` | Persistent loopback service install, target PID/health gate, and rollback |
| `tools/configure_sync_keychain.py` | Interactive Keychain provisioning without credential CLI args |
| `tools/configure_assistant_keychain.py` | Optional chat endpoint/model/key provisioning without value CLI args |

## Safety model

1. Callers must not self-declare `READ_ONLY`. Safety is decided by policy matching
   host, method, path template, and business action.
2. Hosts are exact-origin matched. Encoded separators, dot segments, URL
   userinfo, unexpected schemes/ports, and unknown endpoints default to deny.
3. `GET /WebTP/PurchaseRequisition/Delete/{id}` is a **write** (observed as
   `$.ajax('/WebTP/PurchaseRequisition/Delete/'+id)` with no method override).
4. `Edit/{id}` and `ProjectSelection` are write-preparation UIs and remain
   transport-blocked in `CTF_SAFE`, even though page navigation itself is GET.
5. Mutating builders return a redacted `httpx.Request` preview and never call
   `.send()`.
6. Local API sessions are random Bearer tokens mapping to in-memory upstream
   cookie jars. Upstream `.iPSA` is never returned to API clients.
7. Persisted work-item snapshots, sync runs, summaries, and default CSV exports
   are isolated by a normalized SHA-256 account scope. Legacy unscoped workflow
   data is retained but never attributed to a logged-in account.
8. Five fixed procurement workflow specifications define exact controller slugs,
   list schemas, page shapes, and field mappings. Every account-visible row enters
   that account's local scope only after its stream proves `observed == declared`.
9. Portal `#AccountGreetings #Greeting p` supplies an optional display identity.
   Exact normalized applicant matches add relation labels; identity mismatch or a
   missing applicant column never discards an otherwise visible row.
10. Payment, BizCase, travel applications, daily expenses, travel reimbursements,
    and travel subsidies have independent
    checkpoints. Their API admits only exact personal relations; BizCase
    application-view visibility is not ownership, and account-holder object
    assertions remain only in the mode `0600` account database.
11. The Web workspace has no upstream execution API. Local material upload, field
    review, ready transitions, and SQLite sync are the only mutations it can request.
12. The workflow launcher is a fixed browser-only handoff. It appends no local
    Bearer token, Cookie, form value, ViewState, or business identifier; new tabs use
    `noopener noreferrer`. `GuardedTransport` never receives these navigations.
13. The follow-up assistant has no tools or workflow executor. Normal GET reads its
    account SQLite only. A configured model receives at most 100 minimal unapproved
    records, and its item keys must close over that input set before persistence.
    Any model/config/network/JSON failure writes the deterministic local order.

## Evidence pipeline

1. Capture raw HAR/HTML/JS under `captures/raw/` with mode `0600`.
2. Redact secrets and business identifiers into `captures/redacted/`.
3. Record every artifact in `docs/evidence-manifest.json` with SHA-256, source,
   captured time, sensitivity, and status.
4. Tests load only redacted fixtures (or synthetic HTML).

## Evidence gates

Exact GET paths for application, approval, adjustment, revocation, search, and
the five workflow Detail views are runtime-observed and live-enabled. Search
filter and pagination POST shapes are captured; the other list forms are
restricted to their exact served read-only paths. Write-preparation pages and all
mutating actions remain blocked in the local HTTP client. The separate browser launch
catalog does not weaken or bypass that policy.
Each of PurchaseRequisition, ProcurementContract, ProcurementOrder,
CostConfirmation, and CheckAcceptance fails closed when its declared total cannot
be satisfied, a page repeats, totals drift, schema changes, or the configured page
ceiling is reached. A failed stream keeps its prior complete checkpoint while
other streams can advance; authentication failure stops the batch immediately.
Write previews stay explicitly inferred until request-stage interception plus
abort supplies their real shape. Clean-process credential reads are operationally
replayed; the older browser capture remains insufficient by itself to prove fresh
ticket issuance.

Payment is limited to its proven personal list queries. BizCase uses an exact
query entry plus body-validated pager postbacks for its source checkpoint, but
application-view visibility does not qualify a row for personal display. Travel
applications use the exact `helpmenucode=92` list GET and a body-validated
WebForms pager that consumes the newest hidden state on every page. Its fixed
nine-column schema, page shape, unique identities, and applicant identity must all
hold before a checkpoint commits. Daily expenses use only the exact
`DailyExpense/List.aspx?helpmenucode=90` GET; the fixed nine-column schema, empty
filters, disabled single-page pager, unique identities, and applicant identity
must all hold. Any active pager fails closed instead of guessing a POST. Travel
reimbursement is likewise exact-GET-only. Travel subsidy alone permits its exact
`GridPager1` numeric postback after validating empty filters, fixed ordering, opaque
state, numeric row state, page shape, stable identity, and applicant identity. All
six details are local SQLite snapshots; upstream Payment Edit, BizCase mixed edit
forms, and every Fee Management `Add.aspx?oper=edit` path remain blocked.

## Snapshot transaction

1. Create one `sync_runs` row per workflow stream before its read.
2. Fetch every page at a fixed page size and reconcile unique stable IDs against
   that stream's declared total.
3. Normalize every visible record into payload v3, preserving trustworthy cached
   PR detail/relations and using list fields as the minimum detail for other flows.
4. Classify approval-in-progress rows with a named approver as `follow_up`, approval
   completion as `approved`, and retain saved/rejected/unknown rows as `other`.
5. In one per-stream SQLite transaction, append history, derive events, replace
   only that adapter's current state, and mark the run `succeeded`.
6. If a stream fails, rollback its state transaction, record a redacted failure,
   retain its previous current rows, and continue the batch unless auth expired.

Absence from one measurement is not treated as completion. A `completed` event
requires an observed transition from an active status to a terminal status.
Derived `waiting_days` is excluded from the state hash, so the daily age increase
does not create false workflow-change events.

## Material boundary

```text
untrusted file/UploadFile
  -> size-bounded staging file (0600)
  -> SHA-256 + signature/MIME inspection
  -> atomic move to originals/<sha256>/blob (0400)
  -> SQLite blob + material reference
  -> needs_review or ready

derived/<material-id>/
  <- parsers/OCR/AI only; never writes originals
```

Exact duplicate content/name is idempotent. Different names for the same bytes
create separate material references but share one blob. Extension, declared
MIME, and detected content conflicts enter `needs_review`; they are not silently
accepted as ready.

## Extraction control loop

```text
immutable original
  -> format-specific parser with unit/document character limits
  -> StructuredDocument(page/document/sheet/slide)
  -> deterministic local rules OR explicitly configured HTTP JSON provider
  -> field whitelist + required + confidence + exact-source validation
  -> SQLite extraction run + pending extracted fields
  -> human confirmed/rejected decisions + optional corrected evidence
  -> exact-source revalidation
  -> local validated -> ready (no upstream submission)
```

Structured documents are content-hashed and atomically stored at
`data/materials/derived/<material-id>/documents/<document-hash>/`. Existing
hash directories are byte-compared against regenerated deterministic output;
corruption is not accepted as an idempotent hit. Extraction results are mode
`0600` under `derived/<material-id>/extractions/<extraction-id>/result.json`.

The default provider is local and repeatable. The optional HTTP provider streams
and caps its response, requires HTTPS outside loopback, rejects workflow target
hosts, and strictly parses JSON types. Its output cannot reach
`WorkflowAdapter`, `GuardedTransport`, or any submit action. Every stored field
starts with `review_status='pending'`; P6 alone may confirm or reject it.

## Human review transaction

`workflow_drafts`, `draft_fields`, and `draft_audit_events` are introduced by
schema v4. One extraction maps to at most one draft. `draft_fields` references
the P5 source field, so proposed value/confidence/evidence remain unchanged while
the confirmed value and human evidence are stored separately.

Every field review, validation attempt, and ready transition supplies
`expected_version`. The SQLite transaction checks state and version, applies the
change, increments the version, and appends an audit event with the same sequence
number. Stale callers fail with a conflict. SQLite triggers reject UPDATE or
DELETE against audit events.

Validation reparses the immutable original and reuses the P5 source validator.
Missing or rejected required fields, pending proposed fields, invalid excerpts,
and document issues hold the draft in `needs_review`. Human confirmation may
resolve low model confidence, but cannot replace exact evidence. Only
`validated -> ready` is implemented; there is no P6 route to preview, submit, or
reach `GuardedTransport`.

## Local workspace feedback loop

The Vite build is mounted after all `/v1`, `/health`, `/docs`, and OpenAPI routes,
so API routing takes precedence. Runtime deployment needs only the Python wheel;
Node, browser automation, and third-party CDNs are absent from the serving path.

After login, the UI loads the existing workspace/read-only datasets plus the latest
account-scoped assistant briefing concurrently. The overview uses independent left
and right vertical stacks: all-unapproved groups stay on the left, while draft review,
the daily assistant, and sync history stay contiguous on the right. SQLite schema
initialization is serialized by a process thread lock
and a mode `0600` per-database advisory lock with a 10-second ceiling. This
prevents first-run requests or a simultaneous scheduler process from racing
migrations or moving `PRAGMA user_version` backwards.

The browser holds the local Bearer token only in same-origin `localStorage`. All draft
mutations include `expected_version`; a 409 response shows a conflict notice and
reloads the authoritative draft before another action. A successful sync keeps
its observation time even when it produces zero actionable items, so an empty
follow-up list is distinguishable from a workspace that has never synchronized.

Desktop and mobile layouts share the same data boundary. Tables that cannot
preserve useful column width on mobile scroll inside their own container; the
page itself remains fixed to the viewport width. String-labelled common buttons
retain an accessible name when compact CSS hides their visible label.

The top bar keeps one primary `发起流程` action on every authenticated view. A native
modal dialog groups seven evidence-backed IPSA destinations. Purchase Requisition and
Payment go to their proven first step; the legacy BizCase and Fee Management entries
go to their original application pages because their server-side add controls depend
on current WebForms state. Closing the dialog restores focus to its trigger.

The assistant `GET` never loads Keychain and never calls a model or iPSA. Its v9
tables retain preference versions and idempotent daily briefings. Preference POST,
clear, manual refresh, and the daily CLI may attempt the optional provider; old
briefing content remains visible until a replacement is validated and committed.
Wait days derived from an application/submission date are visibly marked as an
estimate rather than asserted as exact current-node dwell time.

## Scheduled measurement loop

The daily LaunchAgent does not embed a scheduler in FastAPI. At 08:30 on all seven
days it starts `tools/scheduled_sync.py`, which runs three independent bounded stages:
the existing sync child, a daily briefing child, and `/usr/bin/open` for the local
workspace. Credentials exist only in the sync/brief child environments and are
cleared immediately afterward.

The child still uses the exact P3 complete-read and transactional SQLite path,
then reuses the same authenticated client and account storage for the Payment,
BizCase, travel-application, daily-expense, travel-reimbursement, and travel-subsidy
checkpoints. Each of the eleven
streams preserves its own current snapshot on failure; the combined CLI status is
non-success when any group is partial or failed.
Launchd stdout/stderr go to `/dev/null`; the wrapper parses child JSON and writes
only safe counts/outcome data to a mode `0600` JSON-lines log. Child stderr is
redacted before failure logging. Keychain lookup, sync, `plutil`, and `launchctl`
commands all have explicit timeouts.

The briefing stage reads the newest complete account SQLite state even when the
new sync failed. It performs a deterministic stable sort first, then optionally
lets a chat model reorder only known keys. The final open stage executes after sync
setup, sync, briefing, provider, or briefing-CLI failures. A separate Web LaunchAgent
keeps FastAPI alive on loopback and requires `/health` before installation is accepted.

Installation is reversible: an existing plist is copied to a mode `0600`
`.backup`; the new plist is atomically written and linted before the old service
is stopped. Any later bootstrap/enable/print failure restores the old file and,
if it had been loaded, attempts to bootstrap it again. Both installed plists are
credential-free and mode `0600`; runtime credentials and optional model configuration
remain only in the local Keychain.
