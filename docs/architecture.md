# Architecture

## Goal

Provide a local AI-assisted workflow center over the authorized iPSA Purchase
Requisition CTF target. Browser tooling is analysis-only; the final runtime is
a same-origin Web workspace plus direct HTTP through a single guarded transport.

## Target surfaces

| Surface | URL | Role |
| --- | --- | --- |
| Business entry | `http://ipsapro.isstech.com/WebTP/PurchaseRequisition` | Purchase requisition UI and AJAX endpoints |
| Portal | `http://ipsapro.isstech.com/Portal` | Portal / SSO entry |
| Passport | `https://passport.isstech.com/` | Credential POST, redirects, session cookies |
| Local workspace | `http://127.0.0.1:8000/` | Materials, evidence review, ready state, sync, follow-up list |
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
     |
     +---------------------------> five complete SearchIndex streams
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
| `storage.py` + `schema.sql` | Versioned SQLite snapshots, current state, and events |
| `materials.py` | Streaming hash, MIME gate, content-addressed originals, and deduplication |
| `extraction.py` | Bounded format parsers, immutable structured artifacts, extraction run lifecycle |
| `field_mapping.py` | Workflow field profiles plus required/evidence/confidence gates |
| `ai/base.py`, `ai/provider.py` | Extraction-only provider protocol, local rules, bounded HTTP JSON |
| `workflow_state.py` | Human review, exact-evidence revalidation, optimistic state transitions |
| `scheduler.py` | Keychain reads, bounded manual-CLI child process, private outcome log |
| `web/` | React/Vite source for overview, materials, drafts, and follow-up views |
| `web_dist/` | Hashed production assets served by FastAPI and packaged in the wheel |
| `models/` | Auth, purchase, attachment, preview, and normalized work-item models |
| `parsers/` | Login / Portal identity / purchase / attachment HTML parsers |
| `routes/` | sessions, materials, extractions, drafts, purchase reads, previews, work items |
| `tools/sync_work_items.py` | Manual/LaunchAgent-compatible sync, JSON summary, CSV export |
| `tools/ingest_materials.py` | Offline file/directory inbox ingestion |
| `tools/extract_material.py` | Offline parsing and evidence-backed proposal extraction |
| `tools/scheduled_sync.py` | LaunchAgent entrypoint; invokes the existing manual sync CLI |
| `tools/install_launch_agent.py` | Atomic plist render/install/rollback/uninstall |
| `tools/configure_sync_keychain.py` | Interactive Keychain provisioning without credential CLI args |

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
10. The Web workspace has no upstream execution control. Local material upload,
   field review, ready transitions, and SQLite sync are the only mutations it can request.

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
mutating actions remain blocked.
Each of PurchaseRequisition, ProcurementContract, ProcurementOrder,
CostConfirmation, and CheckAcceptance fails closed when its declared total cannot
be satisfied, a page repeats, totals drift, schema changes, or the configured page
ceiling is reached. A failed stream keeps its prior complete checkpoint while
other streams can advance; authentication failure stops the batch immediately.
Write previews stay explicitly inferred until request-stage interception plus
abort supplies their real shape. Clean-process credential reads are operationally
replayed; the older browser capture remains insufficient by itself to prove fresh
ticket issuance.

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

After login, the UI loads five independent local measurements concurrently:
materials, extraction runs, draft summaries, current account-visible snapshots, and
sync runs. SQLite schema initialization is serialized by a process thread lock
and a mode `0600` per-database advisory lock with a 10-second ceiling. This
prevents first-run requests or a simultaneous scheduler process from racing
migrations or moving `PRAGMA user_version` backwards.

The browser holds the local Bearer token only in `sessionStorage`. All draft
mutations include `expected_version`; a 409 response shows a conflict notice and
reloads the authoritative draft before another action. A successful sync keeps
its observation time even when it produces zero actionable items, so an empty
follow-up list is distinguishable from a workspace that has never synchronized.

Desktop and mobile layouts share the same data boundary. Tables that cannot
preserve useful column width on mobile scroll inside their own container; the
page itself remains fixed to the viewport width. String-labelled common buttons
retain an accessible name when compact CSS hides their visible label.

## Scheduled measurement loop

The weekday LaunchAgent does not embed a scheduler in FastAPI and does not need
the app window to remain open. It starts `tools/scheduled_sync.py`, which performs
bounded Keychain reads and launches the existing `tools/sync_work_items.py`
subprocess with credentials only in that child environment.

The child still uses the exact P3 complete-read and transactional SQLite path.
Launchd stdout/stderr go to `/dev/null`; the wrapper parses child JSON and writes
only safe counts/outcome data to a mode `0600` JSON-lines log. Child stderr is
redacted before failure logging. Keychain lookup, sync, `plutil`, and `launchctl`
commands all have explicit timeouts.

Installation is reversible: an existing plist is copied to a mode `0600`
`.backup`; the new plist is atomically written and linted before the old service
is stopped. Any later bootstrap/enable/print failure restores the old file and,
if it had been loaded, attempts to bootstrap it again. The current deployment
does not activate this agent until runtime credentials are configured locally.
