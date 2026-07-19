# Scope and evidence policy

## In scope

- `http://ipsapro.isstech.com/Portal`
- `http://ipsapro.isstech.com/WebTP/PurchaseRequisition` and its five views:
  `Index`, `ApprovalIndex`, `AdjustIndex`, `RevocationIndex`, `SearchIndex`
- Read-only `SearchIndex` and observed pagination under `ProcurementContract`,
  `ProcurementOrder`, `CostConfirmation`, and `CheckAcceptance`
- Read-only personal-scope details at observed `SearchDetail/{id}` or
  `Detail/{id}` paths under those four modules
- Exact read-only Payment and BizCase list protocols plus the four identity-bound
  Fee Management application lists (`DailyExpense`, `EvectionLoan`,
  `EvectionSubsidy`, and `EvectionSubsidy2`)
- Downstream purchase flows discovered from those views (e.g. `ProjectSelection`,
  `Edit/{id}`, detail, dictionary, attachment list/download)
- Authentication redirects and callbacks on `https://passport.isstech.com`
- Static assets and AJAX endpoints loaded by the in-scope pages
- Local FastAPI facade at `http://127.0.0.1:8000` (development only)
- Local material originals, derived structured documents, extraction runs, and SQLite state
- Local macOS Keychain items and LaunchAgent for the read-only sync CLI
- Browser/CDP analysis limited to the authorized iPSA CTF target
- Fixed browser-only handoffs from the local workspace to seven proven IPSA
  application/start pages; no local token, form value, or business data is appended

## Out of scope

- Other WebTP modules or views not listed above, including FrameworkAgreement and
  create/edit/approval/adjustment/export surfaces of the four added workflows
- Using browser automation as a runtime dependency of the final API
- Committing passwords, cookie values, `.iPSA`, employee names, project numbers,
  or attachment content
- Sending create / edit / delete / submit / approve / adjust / revoke / upload
  requests from the local client, sync process, tests, or browser automation

## Non-mutation rule

Automated verification and the local runtime must not send requests that create,
edit, delete, submit, approve, adjust, revoke, import, or upload business data. A request is
considered safe for live verification only when its observed behavior is
read-only. Unknown endpoints default to blocked until their purpose is
established.

The launch catalog is navigation, not an automated write path. After handoff, the
account holder may use the original IPSA form for an explicitly authorized business
action; the local service does not observe, proxy, retry, or confirm that submission.

Important special case: `GET /WebTP/PurchaseRequisition/Delete/{id}` is a
**mutating** operation (jQuery `$.ajax` without an explicit method defaults to
GET). Method alone is never sufficient for safety classification.

Mutation-capable operations are analyzed by:

1. Reading served HTML/JavaScript, or
2. Enabling network-level request interception (CDP `Fetch` / equivalent),
   triggering the UI action, and **aborting** the request before it reaches the
   server.

Their request builders are tested only against a local mock transport and must
never call `.send()` against a real host.

## Evidence precedence

Use evidence in this order:

1. Live runtime behavior
2. Captured network traffic
3. Actively served HTML and scripts
4. Persisted challenge artifacts
5. Source comments and inferred behavior

## Evidence storage

| Location | Contents | Git |
| --- | --- | --- |
| `captures/raw/` | Original HAR/HTML/JS; may contain secrets | Ignored (`0600` on disk) |
| `captures/playwright/` | Historical automation captures | Ignored |
| `captures/login_fail_*.html` | Failed-login residuals | Ignored; status `superseded` for success path |
| `captures/redacted/` | Reviewed fixtures (names/attrs only for secrets) | Allowed |
| `docs/evidence-manifest.json` | Inventory with SHA-256, sensitivity, status | Allowed |
| `docs/endpoint-matrix.md` | Protocol inventory and verification state | Allowed |
| `docs/architecture.md` | Runtime and safety architecture | Allowed |

Do not record credential values. Cookie and ticket values must be replaced with
stable placeholders while preserving their names, domains, paths, and flags.
New captures use a date prefix (`YYYYMMDD-…`) and must not overwrite existing
raw files.

## Delivery status

The repository currently has:

- A FastAPI `/v1` facade with in-memory local session handles
- Exact-origin, canonical-path `EndpointPolicy` and mandatory `GuardedTransport`
- Mock-verified pure HTTP login implementation and credentialed clean-process live reads
- Evidence-backed application Index parsing, detail/attachment parsing, and
  size-bounded attachment downloads
- Runtime-captured PurchaseRequisition views plus replayed five-workflow SearchIndex
  pagination with per-stream declared-total reconciliation
- Offline write request previews that cannot reach the live transport
- Immutable local material ingestion and format-specific PDF/Office/text parsing
- Evidence-backed local/HTTP-JSON field extraction with confidence and review gates
- Versioned human review drafts with immutable AI proposals and append-only local audit
- A same-origin local Web workspace for materials, evidence review, ready state,
  all unapproved personal workflows, a seven-item IPSA launch catalog, per-stream
  checkpoints, and policy-gated read-only sync
- Redacted evidence inventory, endpoint matrix, and vulnerability notes

It does **not** yet have intercepted-and-aborted write bodies or second-role IDOR
evidence. Credentialed clean-process reads are operationally replayed through the
Keychain-backed CLI, but credential values and raw authenticated responses remain
outside Git.
