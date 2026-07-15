# Architecture

## Goal

Provide a browser-independent, read-only-first HTTP facade over the authorized
iPSA Purchase Requisition CTF target. Browser tooling is analysis-only; the
final runtime uses direct HTTP through a single guarded transport.

## Target surfaces

| Surface | URL | Role |
| --- | --- | --- |
| Business entry | `http://ipsapro.isstech.com/WebTP/PurchaseRequisition` | Purchase requisition UI and AJAX endpoints |
| Portal | `http://ipsapro.isstech.com/Portal` | Portal / SSO entry |
| Passport | `https://passport.isstech.com/` | Credential POST, redirects, session cookies |
| Local API | `http://127.0.0.1:8000` | Stable REST facade (`/docs` for OpenAPI) |

## Component diagram

```text
Chrome/CDP (analysis only)
        |
        v
captures/raw  --redact-->  captures/redacted (fixtures)
                                |
                                v
FastAPI /v1  -->  session store  -->  IsstechClient
     |                                   |
     +--> material service --> immutable originals
     |          |
     |          v
     |     structured document --> extraction provider
     |                                  |
     |                                  v
     |                         evidence/threshold gate
     |                                  |
     v                                  v
sync service  --------------------> SQLite state/audit
     |
     +---------------------------> complete SearchIndex measurement
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
| `api.py` | FastAPI app assembly and `/health` |
| `config.py` | Base URLs, timeouts, session TTL |
| `auth.py` | Pure HTTP login and auth detection |
| `client.py` | Upstream business client, exact read methods, and bounded pagination |
| `policy.py` | Method + host + path + side-effect policy |
| `transport.py` | Sole real network egress |
| `request_builders.py` | Offline construction of mutating requests |
| `session_store.py` | Short-lived local Bearer handles (never return `.iPSA`) |
| `sync.py` | Complete read, normalization, run lifecycle, and failure recording |
| `storage.py` + `schema.sql` | Versioned SQLite snapshots, current state, and events |
| `materials.py` | Streaming hash, MIME gate, content-addressed originals, and deduplication |
| `extraction.py` | Bounded format parsers, immutable structured artifacts, extraction run lifecycle |
| `field_mapping.py` | Workflow field profiles plus required/evidence/confidence gates |
| `ai/base.py`, `ai/provider.py` | Extraction-only provider protocol, local rules, bounded HTTP JSON |
| `models/` | Auth, purchase, attachment, preview, and normalized work-item models |
| `parsers/` | Login / purchase / attachment HTML parsers |
| `routes/` | sessions, purchase-requisitions, attachments, previews, work items |
| `tools/sync_work_items.py` | Manual/LaunchAgent-compatible sync, JSON summary, CSV export |
| `tools/ingest_materials.py` | Offline file/directory inbox ingestion |
| `tools/extract_material.py` | Offline parsing and evidence-backed proposal extraction |

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

## Evidence pipeline

1. Capture raw HAR/HTML/JS under `captures/raw/` with mode `0600`.
2. Redact secrets and business identifiers into `captures/redacted/`.
3. Record every artifact in `docs/evidence-manifest.json` with SHA-256, source,
   captured time, sensitivity, and status.
4. Tests load only redacted fixtures (or synthetic HTML).

## Evidence gates

Exact GET paths for application, approval, adjustment, revocation, search, and
Detail are runtime-captured and live-enabled. Search filter and pagination POST
shapes are captured; the other list forms are restricted to their exact served
read-only paths. Write-preparation pages and all mutating actions remain blocked.
Full-list reads fail closed when a declared total cannot be satisfied, a page
repeats, totals drift, or the configured page ceiling is reached; partial lists
are never returned as successful work-item output.
Write previews stay explicitly inferred until request-stage interception plus
abort supplies their real shape. Clean-process credential login is still a
separate live-smoke gate because the browser capture already carried `.iPSA`.

## Snapshot transaction

1. Create and commit one `sync_runs` row with status `running` before the read.
2. Fetch every SearchIndex page; incomplete pagination raises and records the run
   as `failed` without snapshot rows.
3. Normalize every source record into a canonical payload and SHA-256 state hash.
4. In one SQLite transaction, append history, derive events, update current state,
   and mark the run `succeeded`.
5. If any item fails, rollback the whole state transaction, then mark the run
   `failed` in a separate transaction.

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
  -> P6 human review (no upstream submission in P5)
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
