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
| `models/` | Auth, purchase, attachment, preview, and normalized work-item models |
| `parsers/` | Login / purchase / attachment HTML parsers |
| `routes/` | sessions, purchase-requisitions, attachments, previews, work items |

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
