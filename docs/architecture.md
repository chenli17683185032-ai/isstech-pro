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
| `client.py` | Upstream business client |
| `policy.py` | Method + host + path + side-effect policy |
| `transport.py` | Sole real network egress |
| `request_builders.py` | Offline construction of mutating requests |
| `session_store.py` | Short-lived local Bearer handles (never return `.iPSA`) |
| `models/` | Auth, purchase, attachment, preview models |
| `parsers/` | Login / purchase / attachment HTML parsers |
| `routes/` | sessions, purchase-requisitions, attachments, previews |

## Safety model

1. Callers must not self-declare `READ_ONLY`. Safety is decided by policy matching
   host, method, path template, and business action.
2. Hosts are exact-origin matched. Encoded separators, dot segments, URL
   userinfo, unexpected schemes/ports, and unknown endpoints default to deny.
3. `GET /WebTP/PurchaseRequisition/Delete/{id}` is a **write** (observed as
   `$.ajax('/WebTP/PurchaseRequisition/Delete/'+id)` with no method override).
4. Mutating builders return a redacted `httpx.Request` preview and never call
   `.send()`.
5. Local API sessions are random Bearer tokens mapping to in-memory upstream
   cookie jars. Upstream `.iPSA` is never returned to API clients.

## Evidence pipeline

1. Capture raw HAR/HTML/JS under `captures/raw/` with mode `0600`.
2. Redact secrets and business identifiers into `captures/redacted/`.
3. Record every artifact in `docs/evidence-manifest.json` with SHA-256, source,
   captured time, sensitivity, and status.
4. Tests load only redacted fixtures (or synthetic HTML).

## Evidence gates

Only the application `Index` view is currently live-enabled. Approval,
adjustment, revocation, and search return `NOT_CAPTURED` until each has a
runtime capture and view-specific parser fixture. Write previews remain
explicitly inferred until request-stage interception supplies their real shape.
