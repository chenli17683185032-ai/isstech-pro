# Endpoint matrix

Verification states:

- `observed`: seen in live browser traffic.
- `replayed`: reproduced through direct HTTP from a clean client session.
- `served-shape`: present in HTML/JavaScript served by the live target, but the
  specific request was not transmitted.
- `blocked-write`: identified as mutating and intentionally not sent.
- `pending`: a known evidence gap.
- `superseded`: historical evidence replaced by a stronger capture.

Raw evidence under `captures/raw/` is mode `0600` and gitignored. This matrix
contains no credential, Cookie value, employee identifier, project identifier,
requisition identifier, or attachment body.

## Authentication / session

| UI action | Method | Endpoint | Side effect | State | Evidence |
| --- | --- | --- | --- | --- | --- |
| Open purchase requisition while anonymous | GET | `http://ipsapro.isstech.com/WebTP/PurchaseRequisition` | Redirect to Passport | observed | `captures/playwright/unauth.har` |
| Load Passport login | GET | `https://passport.isstech.com/?DomainUrl=...&ReturnUrl=...` | Creates Passport session | observed | unauthenticated HAR and login HTML |
| Submit credentials in Chrome | POST | `https://passport.isstech.com/?DomainUrl=...&ReturnUrl=...` | Browser session transition | observed | `captures/raw/20260715-login-attempt-01.cdp.json`, redacted protocol JSON |
| Reach authenticated Portal | GET | `http://ipsapro.isstech.com/portal` | None | observed, HTTP 200 | same CDP capture |
| Reuse an observed browser ticket with `httpx` | GET | `/WebTP/PurchaseRequisition` | None | replayed with imported ticket | `captures/redacted/auth-cookie-probe.json` |
| Obtain a ticket using credentials in a clean HTTP process | POST + redirects | Passport to business host | Creates HTTP client session | replayed | 2026-07-16 Keychain-backed dry-run and persisted sync; counts only |

The captured browser credential POST contained these form fields only:

```text
emp_DomainName
emp_Password
RemeberMe
DomainUrl
ReturnUrl
```

`flag`, `uname`, `ctip`, `etip`, and `bgstr` are page-level values outside the
form and must not be added to the credential POST.

The CDP summary proves a manual credential POST followed by an authenticated
Portal response. It also records that `.iPSA` was already among the browser's
request Cookie names, while no `.iPSA` Set-Cookie was observed in that chain.
Therefore this capture does **not** prove fresh ticket issuance. The clean
pure-HTTP live smoke remains a separate gate.

## Purchase requisition application view

| UI action | Method | Endpoint | Side effect | State | Evidence |
| --- | --- | --- | --- | --- | --- |
| Default entry | GET | `/WebTP/PurchaseRequisition` | None | observed, HTTP 200 | `20260715-purchase-initial-01.cdp.json` |
| Explicit Index | GET | `/WebTP/PurchaseRequisition/Index` | None | served-shape | authenticated form action and pager assets |
| Filter | POST | `/WebTP/PurchaseRequisition` | Read-only filter | served-shape | live form has `data-ajax-method=Post` |
| Page/sort | GET | `/WebTP/PurchaseRequisition/Index/{route-values}` | Read-only page/sort | served-shape | live pager links and Index JavaScript |
| Page script | GET | `/WebTP/PurchaseRequisition/JS/Index` | None | observed | initial CDP and saved script |
| New/project selection | navigation | `/WebTP/PurchaseRequisition/ProjectSelection` | Write preparation | served-shape; transport blocked in `CTF_SAFE` | Index JavaScript |
| Edit | navigation | `/WebTP/PurchaseRequisition/Edit/{id}` | Write preparation | served-shape; transport blocked in `CTF_SAFE` | Index JavaScript |
| Delete | GET AJAX | `/WebTP/PurchaseRequisition/Delete/{id}` | **Mutating** | blocked-write | Index JavaScript; policy tests prove zero transport hits |

## Read-only runtime views

| View/action | Method | Endpoint | Side effect | State | Evidence |
| --- | --- | --- | --- | --- | --- |
| Search initial list | GET | `/WebTP/PurchaseRequisition/SearchIndex` | None | observed, HTTP 200 | `20260715-purchase-search-index-01.cdp.json` |
| Search empty filter | POST | `/WebTP/PurchaseRequisition/SearchIndex` | Read-only filter | observed, HTTP 200 | `20260715-purchase-search-submit-01.cdp.json` |
| Search page 2 | POST | `/WebTP/PurchaseRequisition/SearchIndex/0/1/False/2` | Read-only pagination | observed, HTTP 200 | `20260715-purchase-search-page2-01.cdp.json` |
| Approval list | GET | `/WebTP/PurchaseRequisition/ApprovalIndex` | None | observed, HTTP 200; list empty for current account | `20260715-purchase-approval-index-01.cdp.json` |
| Adjustment list | GET | `/WebTP/PurchaseRequisition/AdjustIndex` | None | observed, HTTP 200 | `20260715-purchase-adjust-index-01.cdp.json` |
| Revocation list | GET | `/WebTP/PurchaseRequisition/RevocationIndex` | None | observed, HTTP 200 | `20260715-purchase-revocation-index-01.cdp.json` |
| Approval/adjustment/revocation filter forms | POST | Corresponding exact `*Index` path | Expected read-only filter | served-shape, not replayed | forms in actively served pages |

Observed `SearchIndex` runtime facts, recorded only as counts and schema:

```text
total_count = 78
first_page_items = 10
columns include status and next approver
```

## Account-visible procurement SearchIndex streams

| Workflow | Method and exact path shape | State | 2026-07-16 complete count |
| --- | --- | --- | --- |
| Purchase requisition | POST `/WebTP/PurchaseRequisition/SearchIndex/0/1/False/{page}/{size}` | replayed | 78/78 |
| Procurement contract | POST `/WebTP/ProcurementContract/SearchIndex/0/1/False/{page}/{size}` | replayed | 79/79 |
| Procurement order | POST `/WebTP/ProcurementOrder/SearchIndex/0/1/False/{page}/{size}` | replayed | 6/6 |
| Cost confirmation | POST `/WebTP/CostConfirmation/SearchIndex/0/1/False/{page}/{size}` | replayed | 133/133 |
| Check acceptance | POST `/WebTP/CheckAcceptance/SearchIndex/0/1/False/{page}/{size}` | replayed | 57/57 |

All five streams accepted page size 50 and declared stable totals through their
last page. The four added workflows use an explicit empty filter body; replaying
display-oriented form values can accidentally apply filters and is not part of
the runtime adapter. List schemas are fixed by observed column headers, and each
row must expose exactly one `ajax-data` stable ID. The four added flows cache
their list fields locally and enrich only records in the derived personal scope.

## Detail and approval trail

| Action | Method | Endpoint | Side effect | State | Evidence |
| --- | --- | --- | --- | --- | --- |
| Open saved detail | GET | `/WebTP/PurchaseRequisition/Detail/{id}` | None | observed, HTTP 200 | `20260715-purchase-detail-01.cdp.json` |
| Open in-progress detail | GET | `/WebTP/PurchaseRequisition/Detail/{id}` | None | observed, HTTP 200 | `20260715-purchase-in-progress-detail-01.cdp.json` |
| Detail script | GET | `/WebTP/PurchaseRequisition/JS/Detail` | None | observed | both Detail captures |
| Procurement contract detail | GET | `/WebTP/ProcurementContract/SearchDetail/{id}` | None | observed, HTTP 200 | 2026-07-16 live structure probe |
| Procurement order detail | GET | `/WebTP/ProcurementOrder/SearchDetail/{id}` | None | observed, HTTP 200 | 2026-07-16 live structure probe |
| Cost confirmation detail | GET | `/WebTP/CostConfirmation/Detail/{id}` | None | observed, HTTP 200 | 2026-07-16 live structure probe |
| Check acceptance detail | GET | `/WebTP/CheckAcceptance/Detail/{id}` | None | observed, HTTP 200 | 2026-07-16 live structure probe |

The in-progress Detail page yielded 11 basic fields and five approval-trail rows.
Approval-trail columns are sequence, time, approver, position, action, and
comment. Values remain in raw evidence only.

The four added detail routes were derived from their served SearchIndex view
actions and then verified with one fixed read-only request per workflow. All
four returned the same six approval-trail columns. The local sync enriches only
the derived personal scope; a 2026-07-16 replay classified all 32 personal rows,
with 30 non-empty trails and two upstream-empty trails.

## Attachments

| Action | Method | Endpoint | Side effect | State | Evidence |
| --- | --- | --- | --- | --- | --- |
| Detail download | GET | `/WebTP/PurchaseRequisition/Download/{id}` | Read attachment | served-shape from live Detail; not transmitted in capture | saved Detail CDP; five IDs parsed without outputting values |
| Legacy generic download | GET | `/WebTP/Attachment/Download/{id}` | Read attachment | served-shape only | attachment JavaScript |
| Upload | POST | `/WebTP/Attachment/Upload/{route-values}` | **Mutating** | blocked-write | attachment JavaScript and policy tests |
| Delete | any | `/WebTP/Attachment/Delete/{route-values}` | **Mutating** | blocked-write | attachment JavaScript and policy tests |

## Write actions

| Action family | Example path | State |
| --- | --- | --- |
| Create/save/edit | `/WebTP/PurchaseRequisition/Edit/...` or form save action | blocked-write / preview only |
| Delete | `/WebTP/PurchaseRequisition/Delete/{id}` | blocked-write, including GET |
| Submit/approve | paths containing `Submit` or `Approve` | blocked-write / preview only |
| Adjust/revoke | mutating action paths containing `Adjust` or `Revocation` | blocked-write / preview only |
| Attachment upload/delete | `/WebTP/Attachment/Upload/...`, `/Delete/...` | blocked-write |
| Added procurement workflow writes | `Create/Edit/Save/Submit/Approve/Adjust/Delete/Revoke/RollBack` | blocked-write before SearchIndex read rules |

Request bodies for these actions have not been transmitted or captured. Any
future shape discovery must use request-stage pause plus abort and remain in
`CTF_SAFE`.

## Safety notes for implementers

1. Classify by exact origin, method, and normalized path. HTTP method alone is
   insufficient: Search uses read-only POST, while Delete is a mutating GET.
2. Mutating path rules must run before read allow rules.
3. Unknown origins, methods, paths, encoded separators, dot segments, and
   userinfo default to deny.
4. Live enablement is limited to the exact read paths above. Do not infer a
   broader controller-prefix allowlist.
5. The remaining protocol gap is intercepted-and-aborted write shape; runtime
   credentials and authenticated response bodies remain intentionally uncommitted.
